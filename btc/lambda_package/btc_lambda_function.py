"""
Kalshi Bitcoin Hourly NO Contract Bot

Strategy: Buy NO contracts on strikes above current BTC price when our
volatility model shows the market is underpricing NO.

At H:25 (25 min before settlement):
1. Check account balance
2. Get 15m realized volatility
3. Find strike 20bps+ above current BTC
4. Calculate model's fair NO price based on std devs
5. Compare to market - find edge
6. Size bet using Kelly criterion

Trades 24/7 - always targets the NEXT hour's contract.
"""

import json
import os
import math
import boto3
import requests
import traceback
from datetime import datetime, timedelta
from decimal import Decimal

# Import Kalshi client
try:
    from kalshi_client import KalshiClient
except ImportError as e:
    print(f"Warning: kalshi_client import failed: {e}")
    KalshiClient = None


# =============================================================================
# CONFIGURATION
# =============================================================================

# Minimum basis points above current price for strike selection
MIN_BPS_ABOVE = 20  # 0.20%

# Minimum edge required to trade (model prob - market prob)
MIN_EDGE_PCT = 3  # Only trade if we see 3%+ edge

# Maximum fraction of bankroll to risk per trade (Kelly scaling)
MAX_KELLY_FRACTION = 0.25  # Quarter Kelly for safety

# Maximum contracts per trade (no cap - let Kelly size it)
MAX_CONTRACTS = 999

# Minimum and maximum NO price to consider (sanity bounds)
MIN_NO_PRICE = 50   # Don't buy NO below 50¢ (too risky)
MAX_NO_PRICE = 99   # Don't buy NO above 99¢ (no profit)

# Minimum profit percentage required to trade
# Profit % = (100 - price) / price * 100
# At 9%, we skip trades where price > 91¢ (bad risk/reward)
MIN_PROFIT_PCT = 9

# Maximum volatility threshold - halt trading if 15m volatility exceeds this
# High volatility makes our normal distribution model unreliable
MAX_VOLATILITY_PCT = 11.0  # Stop trading if volatility >= 11%

# Kalshi event series for BTC hourly
BTC_SERIES = "KXBTCD"

# DynamoDB table for volatility data
VOL_TABLE = "BTCPriceHistory"

# DynamoDB table for trade logs
TRADE_LOG_TABLE = "BTCTradeLog"


class DecimalEncoder(json.JSONEncoder):
    """Helper class to convert Decimal and datetime to JSON serializable formats"""
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        elif isinstance(o, datetime):
            return o.isoformat()
        return super(DecimalEncoder, self).default(o)


def get_utc_time():
    """Get current UTC time"""
    return datetime.utcnow()


def get_et_time():
    """Get current Eastern Time (accounting for DST roughly)"""
    utc_now = datetime.utcnow()
    month = utc_now.month
    if 3 <= month <= 11:
        return utc_now - timedelta(hours=4)  # EDT
    else:
        return utc_now - timedelta(hours=5)  # EST


def get_account_balance():
    """
    Get available cash balance from Kalshi account.
    Returns balance in dollars or None on error.
    """
    if not KalshiClient:
        print("KalshiClient not available")
        return None

    try:
        kalshi = KalshiClient()
        balance_data = kalshi.get_balance()
        balance_cents = balance_data.get('balance', 0)
        balance_dollars = balance_cents / 100
        print(f"Account balance: ${balance_dollars:.2f}")
        return balance_dollars
    except Exception as e:
        print(f"Error getting account balance: {e}")
        return None


def get_volatility_from_dynamo():
    """
    Get latest volatility metrics from DynamoDB.
    Returns dict with vol_15m_std, vol_30m_std, etc. or None on error.
    """
    try:
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(VOL_TABLE)

        response = table.get_item(
            Key={'pk': 'VOL', 'sk': 'LATEST'}
        )

        item = response.get('Item')
        if not item:
            print("No volatility data found in DynamoDB")
            return None

        vol_data = {
            'updated_at': item.get('updated_at'),
            '5m_std': float(item.get('vol_5m_std', 0)),
            '5m_samples': int(item.get('vol_5m_samples', 0)),
            '7m_std': float(item.get('vol_7m_std', 0)),
            '7m_samples': int(item.get('vol_7m_samples', 0)),
            '10m_std': float(item.get('vol_10m_std', 0)),
            '10m_samples': int(item.get('vol_10m_samples', 0)),
            '12m_std': float(item.get('vol_12m_std', 0)),
            '12m_samples': int(item.get('vol_12m_samples', 0)),
            '15m_std': float(item.get('vol_15m_std', 0)),
            '15m_range': float(item.get('vol_15m_range', 0)),
            '15m_max_move': float(item.get('vol_15m_max_move', 0)),
            '15m_samples': int(item.get('vol_15m_samples', 0)),
            '30m_std': float(item.get('vol_30m_std', 0)),
        }

        print(f"Volatility data: 15m_std={vol_data['15m_std']:.4f}%, samples={vol_data['15m_samples']}")
        return vol_data

    except Exception as e:
        print(f"Error getting volatility from DynamoDB: {e}")
        return None


def get_dynamic_volatility(vol_data, minutes_to_settlement):
    """
    Get the appropriate volatility based on time remaining to settlement.

    Uses a sliding window approach:
    - At 15 min remaining: use 15m volatility
    - At 10 min remaining: use 10m volatility
    - At 5 min remaining: use 5m volatility

    Returns (volatility_std, window_minutes, samples) tuple.
    """
    # Map minutes remaining to volatility window
    # We want the window to match the time remaining (no scaling needed)
    if minutes_to_settlement >= 15:
        window = 15
    elif minutes_to_settlement >= 12:
        window = 12
    elif minutes_to_settlement >= 10:
        window = 10
    elif minutes_to_settlement >= 7:
        window = 7
    else:
        window = 5

    vol_key = f'{window}m_std'
    samples_key = f'{window}m_samples'

    vol_std = vol_data.get(vol_key, 0)
    samples = vol_data.get(samples_key, 0)

    print(f"Dynamic volatility ({minutes_to_settlement}min to settle): using {window}m window")
    print(f"  Volatility: {vol_std:.4f}%, Samples: {samples}")

    return vol_std, window, samples


def calculate_model_probability(btc_price, strike_price, vol_std_pct, minutes_to_settlement):
    """
    Calculate our model's probability that BTC stays below strike.

    Uses normal distribution assumption:
    - Project volatility to time remaining
    - Calculate how many std devs the strike is above current price
    - Convert to probability using normal CDF approximation

    Args:
        btc_price: Current BTC price
        strike_price: Strike price of the contract
        vol_std_pct: 15-minute realized volatility (std dev as %)
        minutes_to_settlement: Minutes until contract settles

    Returns:
        Probability (0-1) that BTC stays below strike (NO wins)
    """
    if vol_std_pct <= 0 or minutes_to_settlement <= 0:
        return None

    # Scale volatility to time remaining (sqrt of time)
    # If we have 15m vol and 25 min remaining, scale by sqrt(25/15)
    vol_scaled = vol_std_pct * math.sqrt(minutes_to_settlement / 15)

    # Calculate how many std devs the strike is above current price
    price_diff_pct = (strike_price - btc_price) / btc_price * 100
    std_devs_above = price_diff_pct / vol_scaled if vol_scaled > 0 else 0

    # Approximate normal CDF for probability BTC stays below strike
    # P(X < strike) where X ~ N(current, vol)
    # Using approximation: for z std devs above, P(below) ≈ norm_cdf(z)

    # Simple approximation of normal CDF
    def norm_cdf(z):
        """Approximate standard normal CDF"""
        if z < -6:
            return 0.0
        if z > 6:
            return 1.0
        # Approximation formula
        t = 1 / (1 + 0.2316419 * abs(z))
        d = 0.3989423 * math.exp(-z * z / 2)
        p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))))
        return 1 - p if z > 0 else p

    prob_below = norm_cdf(std_devs_above)

    print(f"Model calculation:")
    print(f"  Strike ${strike_price:,.2f} is {price_diff_pct:.3f}% above current ${btc_price:,.2f}")
    print(f"  Scaled vol ({minutes_to_settlement}min): {vol_scaled:.4f}%")
    print(f"  Std devs above: {std_devs_above:.2f}")
    print(f"  Model P(NO wins): {prob_below*100:.1f}%")

    return prob_below


def calculate_kelly_bet(win_prob, market_no_price, bankroll):
    """
    Calculate optimal bet size using Kelly Criterion.

    Kelly formula: f* = (bp - q) / b
    where:
        b = odds (profit / risk)
        p = probability of winning
        q = probability of losing

    Args:
        win_prob: Our model's probability NO wins (0-1)
        market_no_price: Market NO price in cents
        bankroll: Available bankroll in dollars

    Returns:
        dict with kelly_fraction, bet_amount, num_contracts
    """
    if market_no_price <= 0 or market_no_price >= 100:
        return None

    # Odds: if NO wins, we profit (100 - price) on a risk of (price)
    profit_cents = 100 - market_no_price
    risk_cents = market_no_price
    b = profit_cents / risk_cents  # odds ratio

    p = win_prob
    q = 1 - win_prob

    # Kelly fraction
    kelly_fraction = (b * p - q) / b if b > 0 else 0

    # Cap at MAX_KELLY_FRACTION for safety
    kelly_fraction = max(0, min(kelly_fraction, MAX_KELLY_FRACTION))

    bet_amount = bankroll * kelly_fraction
    num_contracts = int(bet_amount / (market_no_price / 100))

    # Apply contract cap
    num_contracts = min(num_contracts, MAX_CONTRACTS)

    return {
        'kelly_fraction': kelly_fraction,
        'bet_amount': bet_amount,
        'num_contracts': num_contracts,
        'risk_dollars': num_contracts * market_no_price / 100,
        'potential_profit': num_contracts * profit_cents / 100,
    }


def get_coinbase_btc_price():
    """
    Fetch current BTC price from Coinbase API.
    Returns price as float or None on error.
    """
    try:
        url = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
        response = requests.get(url, timeout=10)

        if response.status_code != 200:
            print(f"Error fetching Coinbase price: {response.status_code}")
            return None

        data = response.json()
        price = float(data['data']['amount'])
        print(f"Coinbase BTC price: ${price:,.2f}")
        return price

    except Exception as e:
        print(f"Error fetching Coinbase BTC price: {e}")
        return None


def get_next_hour_event_ticker():
    """
    Generate the Kalshi event ticker for the NEXT hour's BTC contract.
    Format: KXBTCD-YYMONDDHH (e.g., KXBTCD-25DEC1020 for Dec 10, 2025 8pm EST)

    BTC trades 24/7, so we need to handle:
    - Hour rollover (23 -> 00)
    - Day rollover (end of day -> next day)
    - Month rollover (end of month -> next month)

    The hour is in EST and uses 24-hour format starting from 00.
    """
    et_time = get_et_time()

    # Get the NEXT hour's contract (the one that settles at top of next hour)
    next_hour_time = et_time + timedelta(hours=1)

    year = next_hour_time.strftime('%y')
    month = next_hour_time.strftime('%b').upper()
    day = next_hour_time.strftime('%d')
    hour = next_hour_time.strftime('%H')  # 24-hour format

    event_ticker = f"{BTC_SERIES}-{year}{month}{day}{hour}"
    print(f"Next hour event ticker: {event_ticker} (settles at {hour}:00 ET)")
    return event_ticker


def get_btc_markets(event_ticker):
    """
    Fetch all markets for a BTC hourly event from Kalshi.
    Returns list of markets sorted by strike price.
    """
    try:
        url = f"https://api.elections.kalshi.com/trade-api/v2/events/{event_ticker}"
        print(f"Fetching markets for: {event_ticker}")

        response = requests.get(url, headers={'Accept': 'application/json'}, timeout=10)

        if response.status_code != 200:
            print(f"Error fetching markets: {response.status_code} - {response.text}")
            return []

        data = response.json()
        markets = data.get('markets', [])
        print(f"Retrieved {len(markets)} markets")

        # Parse and sort by floor_strike
        parsed_markets = []
        for market in markets:
            parsed_markets.append({
                'ticker': market.get('ticker'),
                'floor_strike': market.get('floor_strike'),
                'yes_bid': market.get('yes_bid', 0),
                'yes_ask': market.get('yes_ask', 0),
                'no_bid': market.get('no_bid', 0),
                'no_ask': market.get('no_ask', 0),
                'status': market.get('status'),
                'subtitle': market.get('subtitle', ''),
            })

        # Sort by strike price ascending
        parsed_markets.sort(key=lambda x: x['floor_strike'] if x['floor_strike'] else 0)

        return parsed_markets

    except Exception as e:
        print(f"Error fetching BTC markets: {e}")
        traceback.print_exc()
        return []


def find_target_strike(markets, btc_price, min_bps=MIN_BPS_ABOVE):
    """
    Find the first strike that is at least min_bps basis points above current BTC price.

    Args:
        markets: List of markets sorted by strike price
        btc_price: Current BTC price
        min_bps: Minimum basis points above current price (default 20 = 0.20%)

    Returns:
        Market dict or None if no suitable strike found
    """
    min_strike = btc_price * (1 + min_bps / 10000)
    print(f"Looking for first strike >= ${min_strike:,.2f} ({min_bps}bps above ${btc_price:,.2f})")

    for market in markets:
        strike = market.get('floor_strike')
        if strike and strike >= min_strike:
            print(f"Found target strike: ${strike:,.2f} ({market['ticker']})")
            return market

    print("No suitable strike found above threshold")
    return None


def should_buy_no(market, min_price=MIN_NO_PRICE, max_price=MAX_NO_PRICE):
    """
    Check if we should buy NO on this market.

    Returns (should_buy, no_ask_price) tuple.
    """
    no_ask = market.get('no_ask', 0)

    if no_ask is None or no_ask == 0:
        print(f"No NO ask available for {market['ticker']}")
        return False, 0

    print(f"NO ask: {no_ask}¢ (target range: {min_price}-{max_price}¢)")

    if min_price <= no_ask <= max_price:
        print(f"✅ NO price {no_ask}¢ is within target range")
        return True, no_ask
    elif no_ask < min_price:
        print(f"❌ NO price {no_ask}¢ is below minimum {min_price}¢ - too cheap, skipping")
        return False, no_ask
    else:
        print(f"❌ NO price {no_ask}¢ is above maximum {max_price}¢ - not enough margin")
        return False, no_ask


def log_trade(trade_data):
    """
    Log a trade to DynamoDB for record keeping.

    Args:
        trade_data: Dict with trade details
    """
    try:
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(TRADE_LOG_TABLE)

        timestamp = datetime.utcnow().isoformat()

        item = {
            'pk': 'TRADE',
            'sk': timestamp,
            'contract_ticker': trade_data.get('ticker'),
            'side': trade_data.get('side', 'NO'),
            'quantity': trade_data.get('count', 0),
            'price_cents': trade_data.get('price_cents', 0),
            'total_cost': Decimal(str(trade_data.get('count', 0) * trade_data.get('price_cents', 0) / 100)),
            'btc_price': Decimal(str(trade_data.get('btc_price', 0))),
            'strike_price': Decimal(str(trade_data.get('strike_price', 0))),
            'model_prob': Decimal(str(trade_data.get('model_prob', 0))),
            'market_prob': Decimal(str(trade_data.get('market_prob', 0))),
            'edge': Decimal(str(trade_data.get('edge', 0))),
            'kelly_fraction': Decimal(str(trade_data.get('kelly_fraction', 0))),
            'balance_before': Decimal(str(trade_data.get('balance_before', 0))),
            'order_id': trade_data.get('order_id'),
            'status': trade_data.get('status', 'unknown'),
            'potential_profit': Decimal(str(trade_data.get('potential_profit', 0))),
            'minutes_to_settlement': trade_data.get('minutes_to_settlement', 0),
            'volatility_15m': Decimal(str(trade_data.get('volatility_15m', 0))),
        }

        table.put_item(Item=item)
        print(f"Trade logged to DynamoDB: {timestamp}")

    except Exception as e:
        print(f"Error logging trade to DynamoDB: {e}")
        traceback.print_exc()


def execute_no_trade(ticker, count, price, trade_context=None):
    """
    Execute a NO buy order on Kalshi.

    Args:
        ticker: Market ticker
        count: Number of contracts
        price: Price in cents
        trade_context: Additional context for logging (btc_price, strike, etc.)

    Returns:
        Order result dict or None on error
    """
    if not KalshiClient:
        print("KalshiClient not available")
        return None

    try:
        kalshi = KalshiClient()

        print(f"Placing order: BUY {count} NO on {ticker} at {price}¢")

        order_result = kalshi.create_order(
            ticker=ticker,
            side="no",
            count=count,
            price=price
        )

        order = order_result.get('order', {})
        order_id = order.get('order_id')
        status = order.get('status', 'unknown')

        print(f"✅ Order placed! ID: {order_id}, Status: {status}")

        result = {
            'order_id': order_id,
            'ticker': ticker,
            'side': 'NO',
            'count': count,
            'price_cents': price,
            'status': status,
            'potential_profit_cents': (100 - price) * count,
        }

        # Log the trade to DynamoDB
        if trade_context:
            trade_log_data = {
                **result,
                'btc_price': trade_context.get('btc_price'),
                'strike_price': trade_context.get('strike_price'),
                'model_prob': trade_context.get('model_prob'),
                'market_prob': trade_context.get('market_prob'),
                'edge': trade_context.get('edge'),
                'kelly_fraction': trade_context.get('kelly_fraction'),
                'balance_before': trade_context.get('balance_before'),
                'potential_profit': trade_context.get('potential_profit'),
                'minutes_to_settlement': trade_context.get('minutes_to_settlement'),
                'volatility_15m': trade_context.get('volatility_15m'),
            }
            log_trade(trade_log_data)

        return result

    except Exception as e:
        print(f"Error placing order: {e}")
        traceback.print_exc()

        # Log failed trade attempt
        if trade_context:
            trade_log_data = {
                'ticker': ticker,
                'side': 'NO',
                'count': count,
                'price_cents': price,
                'status': 'failed',
                'error': str(e),
                'btc_price': trade_context.get('btc_price'),
                'strike_price': trade_context.get('strike_price'),
                'model_prob': trade_context.get('model_prob'),
                'market_prob': trade_context.get('market_prob'),
                'edge': trade_context.get('edge'),
                'kelly_fraction': trade_context.get('kelly_fraction'),
                'balance_before': trade_context.get('balance_before'),
                'potential_profit': trade_context.get('potential_profit'),
                'minutes_to_settlement': trade_context.get('minutes_to_settlement'),
                'volatility_15m': trade_context.get('volatility_15m'),
            }
            log_trade(trade_log_data)

        return None


def lambda_handler(event, context):
    """
    Main Lambda handler - Dynamic BTC Hourly NO Strategy

    At H:25 (or when triggered), evaluates whether to trade based on:
    1. Account balance
    2. Current volatility
    3. Model vs market pricing
    4. Kelly-optimal position sizing
    """
    try:
        print(f"Event: {json.dumps(event)}")

        utc_time = get_utc_time()
        et_time = get_et_time()
        minutes_to_hour = 60 - et_time.minute
        print(f"Current time - ET: {et_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Minutes to next hour: {minutes_to_hour}")

        # Trading window: only trade between :45 and :00 (last 15 minutes of each hour)
        current_minute = et_time.minute
        print(f"Current minute: {current_minute}")

        # Check if we're in the trading window (minutes 45-59)
        # Allow 'force' flag to bypass for testing
        if current_minute < 45 and not event.get('force'):
            print(f"⏰ Outside trading window (minute {current_minute}, need 45-59)")
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'outside_trading_window',
                    'current_minute': current_minute,
                    'message': f'Trading only allowed between :45 and :00. Current: :{current_minute:02d}'
                })
            }

        print(f"✅ In trading window (minute {current_minute})")

        # =========================================================================
        # Step 1: Check account balance
        # =========================================================================
        print("\n=== Step 1: Account Balance ===")

        # Get actual account balance for Kelly sizing
        bankroll = get_account_balance()
        if bankroll is None:
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'status': 'balance_error',
                    'message': 'Could not fetch account balance from Kalshi'
                })
            }

        print(f"Using account balance for Kelly: ${bankroll:.2f}")

        # Minimum balance check
        MIN_BALANCE = 1.00
        if bankroll < MIN_BALANCE:
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'insufficient_funds',
                    'balance': bankroll,
                    'message': f'Account balance ${bankroll:.2f} too low to trade (min: ${MIN_BALANCE:.2f})'
                })
            }

        # =========================================================================
        # Step 2: Get volatility data (dynamic window based on time to settlement)
        # =========================================================================
        print("\n=== Step 2: Volatility Data ===")
        vol_data = get_volatility_from_dynamo()
        if not vol_data:
            print("No volatility data found")
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'insufficient_vol_data',
                    'samples': 0,
                    'message': 'No volatility data available'
                })
            }

        # Get dynamic volatility based on time remaining
        # At :45, we have 15 min to settlement, use 15m vol
        # At :55, we have 5 min to settlement, use 5m vol
        vol_std, vol_window, vol_samples = get_dynamic_volatility(vol_data, minutes_to_hour)

        # Need at least some samples for the window we're using
        min_samples = max(3, vol_window // 2)  # At least 3 samples or half the window
        if vol_samples < min_samples:
            print(f"Insufficient volatility data: {vol_samples} samples < {min_samples} required")
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'insufficient_vol_data',
                    'samples': vol_samples,
                    'window': vol_window,
                    'min_samples': min_samples,
                    'message': f'Need at least {min_samples} samples for {vol_window}m window'
                })
            }

        # Check if volatility exceeds maximum threshold - halt trading
        if vol_std >= MAX_VOLATILITY_PCT:
            print(f"⚠️ VOLATILITY STOP: {vol_window}m volatility {vol_std:.2f}% >= {MAX_VOLATILITY_PCT}% threshold")
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'volatility_stop',
                    'volatility': round(vol_std, 2),
                    'window': vol_window,
                    'max_volatility': MAX_VOLATILITY_PCT,
                    'message': f'Trading halted: {vol_window}m volatility {vol_std:.2f}% exceeds {MAX_VOLATILITY_PCT}% threshold'
                })
            }

        # Store 15m vol for logging (still use dynamic vol for trading)
        vol_15m_std = vol_data.get('15m_std', vol_std)

        # =========================================================================
        # Step 3: Get BTC price and target contract
        # =========================================================================
        print("\n=== Step 3: BTC Price & Contract ===")
        btc_price = get_coinbase_btc_price()
        if not btc_price:
            return {
                'statusCode': 500,
                'body': json.dumps({'error': 'Could not fetch BTC price'})
            }

        event_ticker = get_next_hour_event_ticker()
        markets = get_btc_markets(event_ticker)
        if not markets:
            return {
                'statusCode': 200,
                'body': json.dumps({'status': 'no_markets', 'event_ticker': event_ticker})
            }

        target_market = find_target_strike(markets, btc_price)
        if not target_market:
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'no_target',
                    'btc_price': btc_price,
                    'message': 'No strike found 20bps+ above current price'
                })
            }

        strike_price = target_market['floor_strike']
        market_no_price = target_market.get('no_ask', 0)

        if market_no_price < MIN_NO_PRICE or market_no_price > MAX_NO_PRICE:
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'price_out_of_bounds',
                    'market_no_price': market_no_price,
                    'message': f'NO price {market_no_price}¢ outside bounds {MIN_NO_PRICE}-{MAX_NO_PRICE}¢'
                })
            }

        # =========================================================================
        # Step 4: Calculate model probability (using dynamic volatility)
        # =========================================================================
        print("\n=== Step 4: Model Calculation ===")
        print(f"Using {vol_window}m volatility: {vol_std:.4f}% for {minutes_to_hour}min to settlement")

        # Since we're using a dynamic window that matches time remaining,
        # we don't need to scale the volatility - just use it directly
        model_prob = calculate_model_probability(
            btc_price=btc_price,
            strike_price=strike_price,
            vol_std_pct=vol_std,  # Use dynamic volatility
            minutes_to_settlement=vol_window  # Use the window size to avoid double-scaling
        )

        if model_prob is None:
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'model_error',
                    'message': 'Could not calculate model probability'
                })
            }

        # Market's implied probability (NO price = implied prob of NO winning)
        market_prob = market_no_price / 100

        # Calculate edge
        edge = (model_prob - market_prob) * 100  # as percentage points
        print(f"\nEdge Analysis:")
        print(f"  Model P(NO wins): {model_prob*100:.1f}%")
        print(f"  Market P(NO wins): {market_prob*100:.1f}%")
        print(f"  Edge: {edge:.1f} percentage points")

        if edge < MIN_EDGE_PCT:
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'insufficient_edge',
                    'model_prob': round(model_prob * 100, 1),
                    'market_prob': round(market_prob * 100, 1),
                    'edge': round(edge, 1),
                    'min_edge_required': MIN_EDGE_PCT,
                    'message': f'Edge {edge:.1f}% below minimum {MIN_EDGE_PCT}%'
                })
            }

        # =========================================================================
        # Step 5: Calculate Kelly bet size
        # =========================================================================
        print("\n=== Step 5: Position Sizing (Kelly) ===")
        kelly = calculate_kelly_bet(model_prob, market_no_price, bankroll)

        if kelly is None or kelly['num_contracts'] < 1:
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'bet_too_small',
                    'kelly_fraction': kelly['kelly_fraction'] if kelly else 0,
                    'message': 'Kelly bet size is less than 1 contract'
                })
            }

        print(f"Kelly sizing:")
        print(f"  Bankroll: ${bankroll:.2f}")
        print(f"  Kelly fraction: {kelly['kelly_fraction']*100:.1f}%")
        print(f"  Contracts: {kelly['num_contracts']}")
        print(f"  Risk: ${kelly['risk_dollars']:.2f}")
        print(f"  Potential profit: ${kelly['potential_profit']:.2f}")

        # Check minimum profit percentage (risk/reward filter)
        profit_pct = (100 - market_no_price) / market_no_price * 100
        print(f"  Profit %: {profit_pct:.1f}%")

        if profit_pct < MIN_PROFIT_PCT:
            print(f"Profit {profit_pct:.1f}% below minimum {MIN_PROFIT_PCT}% - skipping")
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'insufficient_profit',
                    'market_no_price': market_no_price,
                    'profit_pct': round(profit_pct, 1),
                    'min_profit_required': MIN_PROFIT_PCT,
                    'message': f'Profit {profit_pct:.1f}% below minimum {MIN_PROFIT_PCT}% (price {market_no_price}¢ too high)'
                })
            }

        # =========================================================================
        # Step 6: Execute the trade
        # =========================================================================
        print("\n=== Step 6: Execute Trade ===")

        # Build trade context for logging
        trade_context = {
            'btc_price': btc_price,
            'strike_price': strike_price,
            'model_prob': round(model_prob * 100, 2),
            'market_prob': round(market_prob * 100, 2),
            'edge': round(edge, 2),
            'kelly_fraction': round(kelly['kelly_fraction'], 4),
            'balance_before': bankroll,
            'potential_profit': kelly['potential_profit'],
            'minutes_to_settlement': minutes_to_hour,
            'volatility_15m': vol_15m_std,
        }

        order_result = execute_no_trade(
            ticker=target_market['ticker'],
            count=kelly['num_contracts'],
            price=market_no_price,
            trade_context=trade_context
        )

        if order_result:
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'success',
                    'btc_price': btc_price,
                    'strike_price': strike_price,
                    'market_no_price': market_no_price,
                    'model_prob': round(model_prob * 100, 1),
                    'edge': round(edge, 1),
                    'kelly': kelly,
                    'order': order_result,
                }, cls=DecimalEncoder)
            }
        else:
            return {
                'statusCode': 500,
                'body': json.dumps({'error': 'Failed to place order'})
            }

    except Exception as e:
        print(f"Error in lambda_handler: {e}")
        traceback.print_exc()
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'traceback': traceback.format_exc()
            })
        }


# For local testing
if __name__ == "__main__":
    # Test with force flag
    result = lambda_handler({'force': True}, None)
    print(json.dumps(json.loads(result['body']), indent=2))
