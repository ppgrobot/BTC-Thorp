"""
Kalshi XRP (Ripple) Hourly NO Contract Bot

Strategy: Buy NO contracts on strikes above current XRP price when our
volatility model shows the market is underpricing NO.

Trading Windows:
- Early window (:30-:45): Requires 12%+ edge
- Late window (:45-:00): Requires 4%+ edge

Position Sizing:
- Shares Kelly allocation with BTC/ETH bots via DynamoDB tracking
- Combined exposure across all assets capped at MAX_KELLY_FRACTION (0.10 for XRP)

Risk Controls:
- Minimum 50bp above spot for buffer
- Volatility floor of 0.20% prevents overconfidence in quiet markets
- Smaller Kelly fraction (0.10) for position sizing - higher volatility asset
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
MIN_BPS_ABOVE = 50  # Strike must be 0.50% above spot

# Minimum edge required to trade (model prob - market prob)
MIN_EDGE_PCT_EARLY = 12  # Early window (:30-:45) requires 12%+ edge
MIN_EDGE_PCT_LATE = 4   # Late window (:45-:00) requires 4%+ edge

# Maximum fraction of bankroll to risk per trade (Kelly scaling)
MAX_KELLY_FRACTION = 0.10  # Conservative for higher volatility altcoin

# Minimum volatility floor - prevents overconfidence in quiet markets
MIN_VOLATILITY_PCT = 0.20  # Higher floor for XRP's typical volatility

# Maximum contracts per trade
MAX_CONTRACTS = 999

# Minimum and maximum NO price to consider (sanity bounds)
MIN_NO_PRICE = 50   # Don't buy NO below 50c (too risky)
MAX_NO_PRICE = 99   # Don't buy NO above 99c (no profit)

# Minimum profit percentage required to trade
MIN_PROFIT_PCT = 4

# Maximum volatility threshold - halt trading if 15m volatility exceeds this
MAX_VOLATILITY_PCT = 15.0  # Higher threshold for XRP

# Kalshi event series for XRP hourly
XRP_SERIES = "KXXRPD"

# DynamoDB table for volatility data
VOL_TABLE = "XRPPriceHistory"

# DynamoDB table for trade logs
TRADE_LOG_TABLE = "XRPTradeLog"

# DynamoDB table for shared position tracking
POSITION_TABLE = "CryptoPositions"


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


def get_current_hour_key():
    """Get a unique key for the current trading hour (for position tracking)"""
    et_time = get_et_time()
    next_hour = et_time + timedelta(hours=1)
    return next_hour.strftime('%Y%m%d%H')


def get_hour_positions(hour_key):
    """
    Get all positions taken this hour across all assets.
    Returns dict with exposure fractions for each asset.
    """
    try:
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(POSITION_TABLE)

        response = table.get_item(
            Key={'pk': 'HOUR', 'sk': hour_key}
        )

        item = response.get('Item')
        if not item:
            return {'btc_exposure': 0.0, 'eth_exposure': 0.0, 'xrp_exposure': 0.0, 'sol_exposure': 0.0, 'total_exposure': 0.0}

        btc = float(item.get('btc_exposure', 0))
        eth = float(item.get('eth_exposure', 0))
        xrp = float(item.get('xrp_exposure', 0))
        sol = float(item.get('sol_exposure', 0))

        return {
            'btc_exposure': btc,
            'eth_exposure': eth,
            'xrp_exposure': xrp,
            'sol_exposure': sol,
            'total_exposure': btc + eth + xrp + sol
        }

    except Exception as e:
        print(f"Error getting hour positions: {e}")
        return {'btc_exposure': 0.0, 'eth_exposure': 0.0, 'xrp_exposure': 0.0, 'sol_exposure': 0.0, 'total_exposure': 0.0}


def update_hour_position(hour_key, asset, exposure_fraction):
    """
    Update the position tracking for this hour.
    """
    try:
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(POSITION_TABLE)

        current = get_hour_positions(hour_key)

        # Update the appropriate asset
        new_btc = current['btc_exposure']
        new_eth = current['eth_exposure']
        new_xrp = current['xrp_exposure']
        new_sol = current['sol_exposure']

        if asset == 'xrp':
            new_xrp = current['xrp_exposure'] + exposure_fraction
        elif asset == 'sol':
            new_sol = current['sol_exposure'] + exposure_fraction
        elif asset == 'eth':
            new_eth = current['eth_exposure'] + exposure_fraction
        else:
            new_btc = current['btc_exposure'] + exposure_fraction

        total = new_btc + new_eth + new_xrp + new_sol

        # TTL: delete after 2 hours
        ttl = int((datetime.utcnow() + timedelta(hours=2)).timestamp())

        table.put_item(Item={
            'pk': 'HOUR',
            'sk': hour_key,
            'btc_exposure': Decimal(str(round(new_btc, 4))),
            'eth_exposure': Decimal(str(round(new_eth, 4))),
            'xrp_exposure': Decimal(str(round(new_xrp, 4))),
            'sol_exposure': Decimal(str(round(new_sol, 4))),
            'total_exposure': Decimal(str(round(total, 4))),
            'updated_at': datetime.utcnow().isoformat(),
            'ttl': ttl
        })

        print(f"Updated positions for {hour_key}: BTC={new_btc:.2%}, ETH={new_eth:.2%}, XRP={new_xrp:.2%}, SOL={new_sol:.2%}, Total={total:.2%}")

    except Exception as e:
        print(f"Error updating hour position: {e}")
        traceback.print_exc()


def get_account_balance():
    """Get available cash balance from Kalshi account."""
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


def get_total_account_value():
    """
    Get total account value = available cash + market value of open positions.

    Returns (total_value, cash_balance, positions_value) tuple in dollars, or (None, None, None) on error.
    """
    if not KalshiClient:
        print("KalshiClient not available")
        return None, None, None

    try:
        kalshi = KalshiClient()

        # Get cash balance
        balance_data = kalshi.get_balance()
        cash_cents = balance_data.get('balance', 0)
        cash_dollars = cash_cents / 100

        # Get open positions (unsettled only)
        positions_data = kalshi.get_positions(settlement_status='unsettled')
        positions = positions_data.get('market_positions', [])

        # Calculate total position value
        positions_value_cents = 0
        position_count = 0

        for pos in positions:
            # Market exposure is the potential payout in cents
            market_exposure = pos.get('market_exposure', 0)

            # Use market_exposure as the current position value
            positions_value_cents += abs(market_exposure)
            position_count += 1

            ticker = pos.get('ticker', 'unknown')
            yes_qty = pos.get('position', 0)
            print(f"  Position: {ticker}, qty={yes_qty}, exposure=${market_exposure/100:.2f}")

        positions_value_dollars = positions_value_cents / 100
        total_value = cash_dollars + positions_value_dollars

        print(f"Account breakdown:")
        print(f"  Cash balance: ${cash_dollars:.2f}")
        print(f"  Positions ({position_count}): ${positions_value_dollars:.2f}")
        print(f"  Total account value: ${total_value:.2f}")

        return total_value, cash_dollars, positions_value_dollars

    except Exception as e:
        print(f"Error getting total account value: {e}")
        traceback.print_exc()
        return None, None, None


def get_volatility_from_dynamo():
    """Get latest volatility metrics from DynamoDB."""
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


def calculate_model_probability(xrp_price, strike_price, vol_std_pct, minutes_to_settlement):
    """
    Calculate our model's probability that XRP stays below strike.
    """
    if vol_std_pct <= 0 or minutes_to_settlement <= 0:
        return None

    vol_with_floor = max(vol_std_pct, MIN_VOLATILITY_PCT)
    if vol_std_pct < MIN_VOLATILITY_PCT:
        print(f"  Volatility floor applied: {vol_std_pct:.4f}% -> {vol_with_floor:.4f}%")

    vol_scaled = vol_with_floor * math.sqrt(minutes_to_settlement / 15)

    price_diff_pct = (strike_price - xrp_price) / xrp_price * 100
    std_devs_above = price_diff_pct / vol_scaled if vol_scaled > 0 else 0

    def norm_cdf(z):
        """Approximate standard normal CDF"""
        if z < -6:
            return 0.0
        if z > 6:
            return 1.0
        t = 1 / (1 + 0.2316419 * abs(z))
        d = 0.3989423 * math.exp(-z * z / 2)
        p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))))
        return 1 - p if z > 0 else p

    prob_below = norm_cdf(std_devs_above)

    print(f"Model calculation:")
    print(f"  Strike ${strike_price:,.4f} is {price_diff_pct:.3f}% above current ${xrp_price:,.4f}")
    print(f"  Scaled vol ({minutes_to_settlement}min): {vol_scaled:.4f}%")
    print(f"  Std devs above: {std_devs_above:.2f}")
    print(f"  Model P(NO wins): {prob_below*100:.1f}%")

    return prob_below


def calculate_kelly_bet(win_prob, market_no_price, bankroll, remaining_kelly_fraction):
    """Calculate optimal bet size using Kelly Criterion."""
    if market_no_price <= 0 or market_no_price >= 100:
        return None

    profit_cents = 100 - market_no_price
    risk_cents = market_no_price
    b = profit_cents / risk_cents

    p = win_prob
    q = 1 - win_prob

    kelly_fraction = (b * p - q) / b if b > 0 else 0
    kelly_fraction = max(0, min(kelly_fraction, remaining_kelly_fraction))

    bet_amount = bankroll * kelly_fraction
    num_contracts = int(bet_amount / (market_no_price / 100))
    num_contracts = min(num_contracts, MAX_CONTRACTS)

    return {
        'kelly_fraction': kelly_fraction,
        'bet_amount': bet_amount,
        'num_contracts': num_contracts,
        'risk_dollars': num_contracts * market_no_price / 100,
        'potential_profit': num_contracts * profit_cents / 100,
    }


def get_coinbase_xrp_price():
    """Fetch current XRP price from Coinbase API."""
    try:
        url = "https://api.coinbase.com/v2/prices/XRP-USD/spot"
        response = requests.get(url, timeout=10)

        if response.status_code != 200:
            print(f"Error fetching Coinbase price: {response.status_code}")
            return None

        data = response.json()
        price = float(data['data']['amount'])
        print(f"Coinbase XRP price: ${price:,.4f}")
        return price

    except Exception as e:
        print(f"Error fetching Coinbase XRP price: {e}")
        return None


def get_next_hour_event_ticker():
    """Generate the Kalshi event ticker for the NEXT hour's XRP contract."""
    et_time = get_et_time()
    next_hour_time = et_time + timedelta(hours=1)

    year = next_hour_time.strftime('%y')
    month = next_hour_time.strftime('%b').upper()
    day = next_hour_time.strftime('%d')
    hour = next_hour_time.strftime('%H')

    event_ticker = f"{XRP_SERIES}-{year}{month}{day}{hour}"
    print(f"Next hour event ticker: {event_ticker} (settles at {hour}:00 ET)")
    return event_ticker


def get_xrp_markets(event_ticker):
    """Fetch all markets for an XRP hourly event from Kalshi."""
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

        parsed_markets.sort(key=lambda x: x['floor_strike'] if x['floor_strike'] else 0)
        return parsed_markets

    except Exception as e:
        print(f"Error fetching XRP markets: {e}")
        traceback.print_exc()
        return []


def find_target_strike(markets, xrp_price, min_bps=MIN_BPS_ABOVE):
    """Find the first strike that is at least min_bps basis points above current XRP price."""
    min_strike = xrp_price * (1 + min_bps / 10000)
    print(f"Looking for first strike >= ${min_strike:,.4f} ({min_bps}bps above ${xrp_price:,.4f})")

    for market in markets:
        strike = market.get('floor_strike')
        if strike and strike >= min_strike:
            print(f"Found target strike: ${strike:,.4f} ({market['ticker']})")
            return market

    print("No suitable strike found above threshold")
    return None


def log_trade(trade_data):
    """Log a trade to DynamoDB for record keeping."""
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
            'xrp_price': Decimal(str(trade_data.get('xrp_price', 0))),
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
    """Execute a NO buy order on Kalshi."""
    if not KalshiClient:
        print("KalshiClient not available")
        return None

    try:
        kalshi = KalshiClient()

        print(f"Placing order: BUY {count} NO on {ticker} at {price}c")

        order_result = kalshi.create_order(
            ticker=ticker,
            side="no",
            count=count,
            price=price
        )

        order = order_result.get('order', {})
        order_id = order.get('order_id')
        status = order.get('status', 'unknown')

        print(f"Order placed! ID: {order_id}, Status: {status}")

        result = {
            'order_id': order_id,
            'ticker': ticker,
            'side': 'NO',
            'count': count,
            'price_cents': price,
            'status': status,
            'potential_profit_cents': (100 - price) * count,
        }

        if trade_context:
            trade_log_data = {
                **result,
                'xrp_price': trade_context.get('xrp_price'),
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

        if trade_context:
            trade_log_data = {
                'ticker': ticker,
                'side': 'NO',
                'count': count,
                'price_cents': price,
                'status': 'failed',
                'error': str(e),
                'xrp_price': trade_context.get('xrp_price'),
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
    Main Lambda handler - XRP Hourly NO Strategy
    """
    try:
        print(f"Event: {json.dumps(event)}")

        utc_time = get_utc_time()
        et_time = get_et_time()
        minutes_to_hour = 60 - et_time.minute
        print(f"Current time - ET: {et_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Minutes to next hour: {minutes_to_hour}")

        current_minute = et_time.minute
        print(f"Current minute: {current_minute}")

        if 30 <= current_minute < 45:
            trading_window = 'early'
            min_edge_pct = MIN_EDGE_PCT_EARLY
        elif current_minute >= 45:
            trading_window = 'late'
            min_edge_pct = MIN_EDGE_PCT_LATE
        else:
            trading_window = None
            min_edge_pct = None

        if trading_window is None and not event.get('force'):
            print(f"Outside trading window (minute {current_minute}, need 30-59)")
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'outside_trading_window',
                    'current_minute': current_minute,
                    'message': f'Trading only allowed between :30 and :00. Current: :{current_minute:02d}'
                })
            }

        if trading_window is None and event.get('force'):
            trading_window = 'force'
            min_edge_pct = MIN_EDGE_PCT_LATE

        print(f"In {trading_window} trading window (minute {current_minute}, min edge: {min_edge_pct}%)")

        # Step 1: Check account balance and existing positions
        print("\n=== Step 1: Account Balance & Position Check ===")

        # Get total account value (cash + positions) for Kelly sizing
        bankroll, cash_balance, positions_value = get_total_account_value()
        if bankroll is None:
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'status': 'balance_error',
                    'message': 'Could not fetch account value from Kalshi'
                })
            }

        print(f"Total account value for Kelly sizing: ${bankroll:.2f}")
        print(f"  (Cash: ${cash_balance:.2f}, Positions: ${positions_value:.2f})")

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

        hour_key = get_current_hour_key()
        positions = get_hour_positions(hour_key)
        print(f"Current hour positions: BTC={positions['btc_exposure']:.2%}, ETH={positions['eth_exposure']:.2%}, XRP={positions['xrp_exposure']:.2%}, SOL={positions['sol_exposure']:.2%}")

        remaining_kelly = MAX_KELLY_FRACTION - positions['total_exposure']
        if remaining_kelly <= 0.01:
            print(f"Kelly budget exhausted: {positions['total_exposure']:.2%} >= {MAX_KELLY_FRACTION:.2%}")
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'kelly_exhausted',
                    'total_exposure': positions['total_exposure'],
                    'max_kelly': MAX_KELLY_FRACTION,
                    'message': f'Combined exposure {positions["total_exposure"]:.1%} reached max {MAX_KELLY_FRACTION:.0%}'
                })
            }

        print(f"Remaining Kelly budget: {remaining_kelly:.2%}")

        # Step 2: Get volatility data
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

        vol_std = vol_data.get('15m_std', 0)
        vol_samples = vol_data.get('15m_samples', 0)

        if vol_samples < 10:
            print(f"Insufficient volatility data: {vol_samples} samples < 10 required")
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'insufficient_vol_data',
                    'samples': vol_samples,
                    'message': f'Need at least 10 samples, have {vol_samples}'
                })
            }

        if vol_std >= MAX_VOLATILITY_PCT:
            print(f"VOLATILITY STOP: 15m volatility {vol_std:.2f}% >= {MAX_VOLATILITY_PCT}% threshold")
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'volatility_stop',
                    'volatility': round(vol_std, 2),
                    'max_volatility': MAX_VOLATILITY_PCT,
                    'message': f'Trading halted: 15m volatility {vol_std:.2f}% exceeds {MAX_VOLATILITY_PCT}% threshold'
                })
            }

        # Step 3: Get XRP price and target contract
        print("\n=== Step 3: XRP Price & Contract ===")
        xrp_price = get_coinbase_xrp_price()
        if not xrp_price:
            return {
                'statusCode': 500,
                'body': json.dumps({'error': 'Could not fetch XRP price'})
            }

        event_ticker = get_next_hour_event_ticker()
        markets = get_xrp_markets(event_ticker)
        if not markets:
            return {
                'statusCode': 200,
                'body': json.dumps({'status': 'no_markets', 'event_ticker': event_ticker})
            }

        target_market = find_target_strike(markets, xrp_price)
        if not target_market:
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'no_target',
                    'xrp_price': xrp_price,
                    'message': f'No strike found {MIN_BPS_ABOVE}bps+ above current price'
                })
            }

        strike_price = target_market['floor_strike']
        market_no_price = target_market.get('no_ask', 0)
        print(f"Strike: ${strike_price:,.4f}, NO ask: {market_no_price}c")

        if market_no_price < MIN_NO_PRICE or market_no_price > MAX_NO_PRICE:
            print(f"NO price {market_no_price}c outside bounds {MIN_NO_PRICE}-{MAX_NO_PRICE}c")
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'price_out_of_bounds',
                    'market_no_price': market_no_price,
                    'message': f'NO price {market_no_price}c outside bounds {MIN_NO_PRICE}-{MAX_NO_PRICE}c'
                })
            }

        # Step 4: Calculate model probability
        print("\n=== Step 4: Model Calculation ===")
        model_prob = calculate_model_probability(
            xrp_price=xrp_price,
            strike_price=strike_price,
            vol_std_pct=vol_std,
            minutes_to_settlement=minutes_to_hour
        )

        if model_prob is None:
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'model_error',
                    'message': 'Could not calculate model probability'
                })
            }

        market_prob = market_no_price / 100
        edge = (model_prob - market_prob) * 100
        print(f"\nEdge Analysis:")
        print(f"  Model P(NO wins): {model_prob*100:.1f}%")
        print(f"  Market P(NO wins): {market_prob*100:.1f}%")
        print(f"  Edge: {edge:.1f} percentage points")

        if edge < min_edge_pct:
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'insufficient_edge',
                    'model_prob': round(model_prob * 100, 1),
                    'market_prob': round(market_prob * 100, 1),
                    'edge': round(edge, 1),
                    'min_edge_required': min_edge_pct,
                    'trading_window': trading_window,
                    'message': f'Edge {edge:.1f}% below minimum {min_edge_pct}% for {trading_window} window'
                })
            }

        # Step 5: Calculate Kelly bet size
        print("\n=== Step 5: Position Sizing (Kelly) ===")
        kelly = calculate_kelly_bet(model_prob, market_no_price, bankroll, remaining_kelly)

        if kelly is None or kelly['num_contracts'] < 1:
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'bet_too_small',
                    'kelly_fraction': kelly['kelly_fraction'] if kelly else 0,
                    'remaining_kelly': remaining_kelly,
                    'message': 'Kelly bet size is less than 1 contract'
                })
            }

        print(f"Kelly sizing (capped by remaining {remaining_kelly:.2%}):")
        print(f"  Bankroll: ${bankroll:.2f}")
        print(f"  Kelly fraction: {kelly['kelly_fraction']*100:.1f}%")
        print(f"  Contracts: {kelly['num_contracts']}")
        print(f"  Risk: ${kelly['risk_dollars']:.2f}")
        print(f"  Potential profit: ${kelly['potential_profit']:.2f}")

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
                    'message': f'Profit {profit_pct:.1f}% below minimum {MIN_PROFIT_PCT}% (price {market_no_price}c too high)'
                })
            }

        # Step 6: Execute the trade
        print("\n=== Step 6: Execute Trade ===")

        trade_context = {
            'xrp_price': xrp_price,
            'strike_price': strike_price,
            'model_prob': round(model_prob * 100, 2),
            'market_prob': round(market_prob * 100, 2),
            'edge': round(edge, 2),
            'kelly_fraction': round(kelly['kelly_fraction'], 4),
            'balance_before': bankroll,
            'potential_profit': kelly['potential_profit'],
            'minutes_to_settlement': minutes_to_hour,
            'volatility_15m': vol_std,
        }

        order_result = execute_no_trade(
            ticker=target_market['ticker'],
            count=kelly['num_contracts'],
            price=market_no_price,
            trade_context=trade_context
        )

        if order_result:
            update_hour_position(hour_key, 'xrp', kelly['kelly_fraction'])

            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'success',
                    'xrp_price': xrp_price,
                    'strike_price': strike_price,
                    'market_no_price': market_no_price,
                    'model_prob': round(model_prob * 100, 1),
                    'edge': round(edge, 1),
                    'kelly': kelly,
                    'order': order_result,
                    'combined_exposure': positions['total_exposure'] + kelly['kelly_fraction'],
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
    result = lambda_handler({'force': True}, None)
    print(json.dumps(json.loads(result['body']), indent=2))
