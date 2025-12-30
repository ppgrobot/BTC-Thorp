"""
Kalshi Bitcoin Range Contract Bot (Hourly)

Strategy: Buy NO contracts on range contracts where the price range is unlikely
to be reached based on our volatility model.

Range contracts have:
- floor_strike: Lower bound of the range (e.g., $81,000)
- cap_strike: Upper bound of the range (e.g., $81,249.99)
- YES wins if BTC settles WITHIN the range
- NO wins if BTC settles OUTSIDE the range

We buy NO on ranges where:
1. The current BTC price is far from the range (multiple std devs away)
2. Our model shows low probability of BTC reaching that range
3. Market is offering favorable NO prices

This works for both:
- Low ranges (BTC would need to crash to reach them)
- High ranges (BTC would need to surge to reach them)

Settlement: KXBTC range contracts settle HOURLY (9pm, 10pm, 11pm, etc. EST)
The bot runs every hour to find opportunities in the next hourly contract.
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

# DRY RUN MODE - Set to True to collect data and log alerts without trading
DRY_RUN = True  # Set to False to enable live trading

# Minimum edge required to trade (model prob - market prob)
MIN_EDGE_PCT = 4  # 4% edge required

# Maximum fraction of bankroll to risk per trade (Kelly scaling)
MAX_KELLY_FRACTION = 0.10  # 10% Kelly for range contracts (more conservative)

# Maximum contracts per trade
MAX_CONTRACTS = 999

# Minimum and maximum NO price to consider
MIN_NO_PRICE = 50   # Don't buy NO below 50¢ (too risky)
MAX_NO_PRICE = 98   # Don't buy NO above 98¢ (no profit)

# Minimum profit percentage required to trade
MIN_PROFIT_PCT = 3  # Slightly lower than hourly since these are longer duration

# Maximum volatility threshold - halt trading if 15m volatility exceeds this
MAX_VOLATILITY_PCT = 10.0

# Minimum volatility floor
MIN_VOLATILITY_PCT = 0.15

# Maximum model probability
MAX_MODEL_PROB = 0.99

# Kalshi event series for BTC range contracts (daily)
BTC_RANGE_SERIES = "KXBTC"

# DynamoDB tables
VOL_TABLE = "BTCPriceHistory"
TRADE_LOG_TABLE = "BTCRangeTradeLog"
POSITION_TABLE = "CryptoPositions"


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        elif isinstance(o, datetime):
            return o.isoformat()
        return super(DecimalEncoder, self).default(o)


def get_utc_time():
    return datetime.utcnow()


def get_et_time():
    utc_now = datetime.utcnow()
    month = utc_now.month
    if 3 <= month <= 11:
        return utc_now - timedelta(hours=4)  # EDT
    else:
        return utc_now - timedelta(hours=5)  # EST


def get_coinbase_btc_price():
    """Fetch current BTC price from Coinbase API."""
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
    """Get total account value = available cash + market value of open positions."""
    if not KalshiClient:
        return None, None, None
    try:
        kalshi = KalshiClient()
        balance_data = kalshi.get_balance()
        cash_cents = balance_data.get('balance', 0)
        cash_dollars = cash_cents / 100

        positions_data = kalshi.get_positions(settlement_status='unsettled')
        positions = positions_data.get('market_positions', [])

        positions_value_cents = 0
        for pos in positions:
            market_exposure = pos.get('market_exposure', 0)
            positions_value_cents += abs(market_exposure)

        positions_value_dollars = positions_value_cents / 100
        total_value = cash_dollars + positions_value_dollars

        print(f"Account: Cash=${cash_dollars:.2f}, Positions=${positions_value_dollars:.2f}, Total=${total_value:.2f}")
        return total_value, cash_dollars, positions_value_dollars
    except Exception as e:
        print(f"Error getting total account value: {e}")
        return None, None, None


def get_volatility_from_dynamo():
    """Get latest volatility metrics from DynamoDB."""
    try:
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(VOL_TABLE)
        response = table.get_item(Key={'pk': 'VOL', 'sk': 'LATEST'})
        item = response.get('Item')
        if not item:
            print("No volatility data found in DynamoDB")
            return None

        vol_data = {
            'updated_at': item.get('updated_at'),
            '15m_std': float(item.get('vol_15m_std', 0)),
            '30m_std': float(item.get('vol_30m_std', 0)),
            '60m_std': float(item.get('vol_60m_std', 0)),
            '15m_samples': int(item.get('vol_15m_samples', 0)),
        }
        print(f"Volatility: 15m={vol_data['15m_std']:.4f}%, 30m={vol_data['30m_std']:.4f}%")
        return vol_data
    except Exception as e:
        print(f"Error getting volatility: {e}")
        return None


def get_next_range_contract():
    """
    Find the next available BTC hourly range contract from Kalshi.

    KXBTC contracts settle every hour (9pm, 10pm, 11pm, etc. EST).
    We look for contracts with at least 15 minutes to settlement.

    Returns (event_ticker, strike_date, minutes_to_settlement) or (None, None, None)
    """
    try:
        url = "https://api.elections.kalshi.com/trade-api/v2/events"
        # Fetch more contracts to find hourly ones
        params = {"series_ticker": BTC_RANGE_SERIES, "status": "open", "limit": 30}
        response = requests.get(url, params=params, headers={'Accept': 'application/json'}, timeout=10)

        if response.status_code != 200:
            print(f"Error fetching range events: {response.status_code}")
            return None, None, None

        data = response.json()
        events = data.get("events", [])

        now = datetime.utcnow()
        future_events = []

        for event in events:
            strike_date_str = event.get("strike_date", "")
            if strike_date_str:
                strike_date = datetime.fromisoformat(strike_date_str.replace("Z", "+00:00"))
                strike_date_naive = strike_date.replace(tzinfo=None)
                if strike_date_naive > now:
                    mins_to_settle = int((strike_date_naive - now).total_seconds() / 60)
                    future_events.append({
                        'ticker': event.get("event_ticker"),
                        'strike_date': strike_date_naive,
                        'title': event.get("title"),
                        'sub_title': event.get("sub_title", ""),
                        'minutes_to_settlement': mins_to_settle
                    })

        if future_events:
            future_events.sort(key=lambda x: x['strike_date'])
            soonest = future_events[0]
            print(f"Next range contract: {soonest['ticker']}")
            print(f"  Title: {soonest['sub_title']}")
            print(f"  Settles at: {soonest['strike_date']} UTC")
            print(f"  Minutes to settlement: {soonest['minutes_to_settlement']}")
            return soonest['ticker'], soonest['strike_date'], soonest['minutes_to_settlement']

        print("No available range contracts found")
        return None, None, None
    except Exception as e:
        print(f"Error fetching range contracts: {e}")
        return None, None, None


def get_range_markets(event_ticker):
    """
    Fetch all range markets for a BTC range event.

    Returns list of markets with floor_strike, cap_strike, and pricing.
    """
    try:
        url = f"https://api.elections.kalshi.com/trade-api/v2/events/{event_ticker}"
        response = requests.get(url, headers={'Accept': 'application/json'}, timeout=10)

        if response.status_code != 200:
            print(f"Error fetching markets: {response.status_code}")
            return []

        data = response.json()
        markets = data.get('markets', [])
        print(f"Retrieved {len(markets)} range markets")

        parsed = []
        for market in markets:
            floor = market.get('floor_strike')
            cap = market.get('cap_strike')

            # Skip the boundary markets (only cap or only floor)
            if floor is None or cap is None:
                continue

            parsed.append({
                'ticker': market.get('ticker'),
                'floor_strike': floor,
                'cap_strike': cap,
                'range_midpoint': (floor + cap) / 2,
                'range_width': cap - floor,
                'yes_bid': market.get('yes_bid', 0),
                'yes_ask': market.get('yes_ask', 0),
                'no_bid': market.get('no_bid', 0),
                'no_ask': market.get('no_ask', 0),
                'status': market.get('status'),
                'subtitle': market.get('subtitle', ''),
                'strike_type': market.get('strike_type', ''),
            })

        # Sort by range midpoint
        parsed.sort(key=lambda x: x['range_midpoint'])
        return parsed
    except Exception as e:
        print(f"Error fetching range markets: {e}")
        return []


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


def calculate_range_probability(btc_price, floor_strike, cap_strike, vol_std_pct, minutes_to_settlement):
    """
    Calculate probability that BTC lands WITHIN the range at settlement.

    P(floor <= BTC <= cap) = CDF(z_cap) - CDF(z_floor)

    Where z = (strike - current_price) / (price * scaled_vol)

    Returns probability (0-1) that YES wins (BTC lands in range)
    """
    if vol_std_pct <= 0 or minutes_to_settlement <= 0:
        return None

    # Apply volatility floor
    vol_with_floor = max(vol_std_pct, MIN_VOLATILITY_PCT)

    # Scale 15-minute volatility to the time remaining
    # Use sqrt scaling for random walk
    vol_scaled = vol_with_floor * math.sqrt(minutes_to_settlement / 15)

    # Calculate z-scores for both boundaries
    # z = (strike - current) / (current * vol_scaled / 100)
    # But our vol is already in %, so:
    z_floor = (floor_strike - btc_price) / btc_price * 100 / vol_scaled
    z_cap = (cap_strike - btc_price) / btc_price * 100 / vol_scaled

    # P(BTC in range) = P(floor < BTC < cap) = CDF(z_cap) - CDF(z_floor)
    prob_in_range = norm_cdf(z_cap) - norm_cdf(z_floor)

    # P(NO wins) = 1 - P(in range)
    prob_no_wins = 1 - prob_in_range

    # Cap probability
    prob_no_capped = min(prob_no_wins, MAX_MODEL_PROB)

    return prob_in_range, prob_no_capped, vol_scaled, z_floor, z_cap


def calculate_kelly_bet(win_prob, market_no_price, bankroll, max_kelly=MAX_KELLY_FRACTION):
    """Calculate optimal bet size using Kelly Criterion."""
    if market_no_price <= 0 or market_no_price >= 100:
        return None

    profit_cents = 100 - market_no_price
    risk_cents = market_no_price
    b = profit_cents / risk_cents

    p = win_prob
    q = 1 - win_prob

    kelly_fraction = (b * p - q) / b if b > 0 else 0
    kelly_fraction = max(0, min(kelly_fraction, max_kelly))

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


def log_trade(trade_data):
    """Log a trade to DynamoDB."""
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
            'floor_strike': Decimal(str(trade_data.get('floor_strike', 0))),
            'cap_strike': Decimal(str(trade_data.get('cap_strike', 0))),
            'model_prob': Decimal(str(trade_data.get('model_prob', 0))),
            'market_prob': Decimal(str(trade_data.get('market_prob', 0))),
            'edge': Decimal(str(trade_data.get('edge', 0))),
            'kelly_fraction': Decimal(str(trade_data.get('kelly_fraction', 0))),
            'balance_before': Decimal(str(trade_data.get('balance_before', 0))),
            'order_id': trade_data.get('order_id'),
            'status': trade_data.get('status', 'unknown'),
            'potential_profit': Decimal(str(trade_data.get('potential_profit', 0))),
            'minutes_to_settlement': trade_data.get('minutes_to_settlement', 0),
            'volatility': Decimal(str(trade_data.get('volatility', 0))),
        }

        table.put_item(Item=item)
        print(f"Trade logged: {timestamp}")
    except Exception as e:
        print(f"Error logging trade: {e}")


def execute_no_trade(ticker, count, price, trade_context=None):
    """Execute a NO buy order on Kalshi."""
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
        }

        if trade_context:
            trade_log_data = {**result, **trade_context}
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
                **trade_context
            }
            log_trade(trade_log_data)
        return None


def print_buy_signal(market, btc_price, opportunity, kelly, bankroll, vol_15m, mins_to_settle, vol_samples):
    """Print a detailed buy signal in a nice format."""
    no_ask = market['no_ask']
    fair_value = int(opportunity['prob_no_wins'] * 100)
    edge_cents = fair_value - no_ask
    edge_pct = opportunity['edge']
    win_rate = opportunity['prob_no_wins'] * 100
    ev_per_contract = (opportunity['prob_no_wins'] * (100 - no_ask) - (1 - opportunity['prob_no_wins']) * no_ask) / 100

    # Calculate distance from range
    range_mid = market['range_midpoint']
    distance = btc_price - range_mid
    if distance > 0:
        distance_str = f"BTC is ${distance:,.0f} ABOVE range midpoint"
    else:
        distance_str = f"BTC is ${abs(distance):,.0f} BELOW range midpoint"

    print("")
    print("=" * 60)
    print("            BUY SIGNAL! (RANGE CONTRACT)")
    print("=" * 60)
    print("")
    print(f"{'Field':<20} {'Value':<40}")
    print("-" * 60)
    print(f"{'Contract':<20} {market['ticker']}")
    print(f"{'Range':<20} ${market['floor_strike']:,.0f} - ${market['cap_strike']:,.0f}")
    print(f"{'Side':<20} NO")
    print(f"{'Price':<20} {no_ask}c")
    print(f"{'Fair Value':<20} {fair_value}c")
    print(f"{'Edge':<20} +{edge_cents}c ({edge_pct:.1f}%)")
    print(f"{'Win Rate':<20} {win_rate:.1f}% ({vol_samples} vol samples)")
    print(f"{'EV/contract':<20} {ev_per_contract:.1f}c")
    print(f"{'Time to Settle':<20} {mins_to_settle} minutes")
    print(f"{'Volatility (15m)':<20} {vol_15m:.4f}%")
    print("-" * 60)
    print(f"{'Kelly Fraction':<20} {kelly['kelly_fraction']*100:.1f}%")
    print(f"{'Bankroll':<20} ${bankroll:.2f}")
    print(f"{'Bet Size':<20} ${kelly['risk_dollars']:.2f}")
    print(f"{'Contracts':<20} {kelly['num_contracts']}")
    print(f"{'Potential Profit':<20} ${kelly['potential_profit']:.2f}")
    print("-" * 60)
    print(f"{distance_str}")
    print(f"NO is underpriced at {no_ask}c vs {fair_value}c fair value")
    print("=" * 60)
    print("")


def print_no_opportunity_summary(markets, btc_price, vol_std, mins_to_settle):
    """Print a summary when no opportunities are found."""
    print("")
    print("=" * 60)
    print("              NO TRADE (Range Contracts)")
    print("=" * 60)
    print(f"BTC Price: ${btc_price:,.2f}")
    print(f"Volatility: {vol_std:.4f}%")
    print(f"Minutes to settlement: {mins_to_settle}")
    print(f"Markets evaluated: {len(markets)}")
    print(f"Minimum edge required: {MIN_EDGE_PCT}%")
    print("")

    # Show top 3 closest opportunities
    near_misses = []
    for market in markets:
        no_ask = market.get('no_ask', 0)
        if not no_ask or no_ask < MIN_NO_PRICE or no_ask > MAX_NO_PRICE:
            continue

        result = calculate_range_probability(
            btc_price, market['floor_strike'], market['cap_strike'], vol_std, mins_to_settle
        )
        if result:
            prob_in_range, prob_no_wins, vol_scaled, z_floor, z_cap = result
            market_prob = no_ask / 100
            edge = (prob_no_wins - market_prob) * 100
            near_misses.append({
                'range': f"${market['floor_strike']:,.0f}-${market['cap_strike']:,.0f}",
                'no_ask': no_ask,
                'fair': int(prob_no_wins * 100),
                'edge': edge
            })

    near_misses.sort(key=lambda x: x['edge'], reverse=True)
    if near_misses:
        print("Top opportunities (below threshold):")
        for nm in near_misses[:3]:
            print(f"  {nm['range']}: {nm['no_ask']}c vs {nm['fair']}c fair, edge={nm['edge']:.1f}%")
    print("=" * 60)
    print("")


def find_best_range_opportunity(markets, btc_price, vol_std, mins_to_settle):
    """
    Find the best NO opportunity among all range markets.

    Returns the market with the highest edge that meets our criteria.
    """
    opportunities = []

    for market in markets:
        no_ask = market.get('no_ask', 0)

        # Skip if no ask price or out of bounds
        if not no_ask or no_ask < MIN_NO_PRICE or no_ask > MAX_NO_PRICE:
            continue

        floor = market['floor_strike']
        cap = market['cap_strike']

        # Calculate probability
        result = calculate_range_probability(
            btc_price, floor, cap, vol_std, mins_to_settle
        )

        if result is None:
            continue

        prob_in_range, prob_no_wins, vol_scaled, z_floor, z_cap = result

        # Market's implied probability (NO price = implied prob of NO winning)
        market_prob = no_ask / 100

        # Edge
        edge = (prob_no_wins - market_prob) * 100

        if edge >= MIN_EDGE_PCT:
            opportunities.append({
                'market': market,
                'prob_no_wins': prob_no_wins,
                'market_prob': market_prob,
                'edge': edge,
                'vol_scaled': vol_scaled,
                'z_floor': z_floor,
                'z_cap': z_cap,
                'distance_pct': abs(btc_price - market['range_midpoint']) / btc_price * 100
            })

    if not opportunities:
        return None

    # Sort by edge (highest first)
    opportunities.sort(key=lambda x: x['edge'], reverse=True)

    return opportunities[0]


def lambda_handler(event, context):
    """
    Main Lambda handler - BTC Range Contract NO Strategy

    Finds range contracts where the price range is far from current BTC price
    and buys NO when our model shows favorable odds.
    """
    try:
        print(f"Event: {json.dumps(event)}")

        et_time = get_et_time()
        print(f"Current time ET: {et_time.strftime('%Y-%m-%d %H:%M:%S')}")

        # =========================================================================
        # Step 1: Get account balance
        # =========================================================================
        print("\n=== Step 1: Account Balance ===")
        bankroll, cash_balance, positions_value = get_total_account_value()
        if bankroll is None:
            return {
                'statusCode': 500,
                'body': json.dumps({'status': 'balance_error'})
            }

        MIN_BALANCE = 5.00
        if bankroll < MIN_BALANCE:
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'insufficient_funds',
                    'balance': bankroll
                })
            }

        # =========================================================================
        # Step 2: Get volatility data
        # =========================================================================
        print("\n=== Step 2: Volatility Data ===")
        vol_data = get_volatility_from_dynamo()
        if not vol_data:
            return {
                'statusCode': 200,
                'body': json.dumps({'status': 'no_volatility_data'})
            }

        vol_15m = vol_data.get('15m_std', 0)
        if vol_15m >= MAX_VOLATILITY_PCT:
            print(f"⚠️ VOLATILITY STOP: {vol_15m:.2f}% >= {MAX_VOLATILITY_PCT}%")
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'volatility_stop',
                    'volatility': vol_15m
                })
            }

        # =========================================================================
        # Step 3: Get BTC price and range contracts
        # =========================================================================
        print("\n=== Step 3: BTC Price & Range Contracts ===")
        btc_price = get_coinbase_btc_price()
        if not btc_price:
            return {
                'statusCode': 500,
                'body': json.dumps({'error': 'Could not fetch BTC price'})
            }

        event_ticker, strike_date, mins_to_settle = get_next_range_contract()
        if not event_ticker:
            return {
                'statusCode': 200,
                'body': json.dumps({'status': 'no_contracts_available'})
            }

        # Check if we have enough time (at least 15 minutes to settlement)
        # This matches the hourly bot timing - we can trade up to 15 min before settlement
        MIN_MINUTES = 15
        if mins_to_settle < MIN_MINUTES:
            print(f"Too close to settlement: {mins_to_settle} min < {MIN_MINUTES} min")
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'too_close_to_settlement',
                    'minutes_to_settlement': mins_to_settle,
                    'event_ticker': event_ticker
                })
            }

        markets = get_range_markets(event_ticker)
        if not markets:
            return {
                'statusCode': 200,
                'body': json.dumps({'status': 'no_markets'})
            }

        # Get volatility samples for display
        vol_samples = vol_data.get('15m_samples', 0)

        # =========================================================================
        # Step 4: Find best opportunity
        # =========================================================================
        print("\n=== Step 4: Find Best Opportunity ===")
        opportunity = find_best_range_opportunity(
            markets, btc_price, vol_15m, mins_to_settle
        )

        if not opportunity:
            print_no_opportunity_summary(markets, btc_price, vol_15m, mins_to_settle)
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'no_opportunity',
                    'btc_price': btc_price,
                    'num_markets': len(markets),
                    'min_edge_required': MIN_EDGE_PCT
                })
            }

        market = opportunity['market']
        no_ask = market['no_ask']

        # =========================================================================
        # Step 5: Calculate bet size
        # =========================================================================
        print("\n=== Step 5: Position Sizing ===")
        kelly = calculate_kelly_bet(
            opportunity['prob_no_wins'],
            no_ask,
            bankroll
        )

        if kelly is None or kelly['num_contracts'] < 1:
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'bet_too_small',
                    'kelly_fraction': kelly['kelly_fraction'] if kelly else 0
                })
            }

        # Check profit percentage
        profit_pct = (100 - no_ask) / no_ask * 100
        if profit_pct < MIN_PROFIT_PCT:
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'insufficient_profit',
                    'profit_pct': profit_pct
                })
            }

        # =========================================================================
        # Step 6: Execute trade (or log alert in DRY_RUN mode)
        # =========================================================================
        # Print the buy signal!
        print_buy_signal(market, btc_price, opportunity, kelly, bankroll, vol_15m, mins_to_settle, vol_samples)

        # In DRY_RUN mode, just log the opportunity and return
        if DRY_RUN:
            print("\n" + "!" * 60)
            print("  DRY RUN MODE - Trade NOT executed (data collection only)")
            print("!" * 60)

            # Log to DynamoDB for tracking even in dry run
            dry_run_log = {
                'ticker': market['ticker'],
                'side': 'NO',
                'count': kelly['num_contracts'],
                'price_cents': no_ask,
                'btc_price': btc_price,
                'floor_strike': market['floor_strike'],
                'cap_strike': market['cap_strike'],
                'model_prob': round(opportunity['prob_no_wins'] * 100, 2),
                'market_prob': round(opportunity['market_prob'] * 100, 2),
                'edge': round(opportunity['edge'], 2),
                'kelly_fraction': round(kelly['kelly_fraction'], 4),
                'balance_before': bankroll,
                'potential_profit': kelly['potential_profit'],
                'minutes_to_settlement': mins_to_settle,
                'volatility': vol_15m,
                'status': 'dry_run',
                'order_id': 'DRY_RUN'
            }
            log_trade(dry_run_log)

            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'dry_run_alert',
                    'btc_price': btc_price,
                    'range': f"${market['floor_strike']:,.0f} - ${market['cap_strike']:,.0f}",
                    'ticker': market['ticker'],
                    'no_price': no_ask,
                    'fair_value': int(opportunity['prob_no_wins'] * 100),
                    'model_prob': round(opportunity['prob_no_wins'] * 100, 1),
                    'edge': round(opportunity['edge'], 1),
                    'kelly': kelly,
                    'minutes_to_settlement': mins_to_settle,
                }, cls=DecimalEncoder)
            }

        print("\n=== Step 6: Execute Trade ===")
        trade_context = {
            'btc_price': btc_price,
            'floor_strike': market['floor_strike'],
            'cap_strike': market['cap_strike'],
            'model_prob': round(opportunity['prob_no_wins'] * 100, 2),
            'market_prob': round(opportunity['market_prob'] * 100, 2),
            'edge': round(opportunity['edge'], 2),
            'kelly_fraction': round(kelly['kelly_fraction'], 4),
            'balance_before': bankroll,
            'potential_profit': kelly['potential_profit'],
            'minutes_to_settlement': mins_to_settle,
            'volatility': vol_15m,
        }

        order_result = execute_no_trade(
            ticker=market['ticker'],
            count=kelly['num_contracts'],
            price=no_ask,
            trade_context=trade_context
        )

        if order_result:
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'success',
                    'btc_price': btc_price,
                    'range': f"${market['floor_strike']:,.0f} - ${market['cap_strike']:,.0f}",
                    'no_price': no_ask,
                    'model_prob': round(opportunity['prob_no_wins'] * 100, 1),
                    'edge': round(opportunity['edge'], 1),
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
            'body': json.dumps({'error': str(e)})
        }


# For local testing
if __name__ == "__main__":
    result = lambda_handler({'force': True}, None)
    print(json.dumps(json.loads(result['body']), indent=2))
