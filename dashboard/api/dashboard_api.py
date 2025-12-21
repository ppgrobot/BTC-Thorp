"""
Dashboard API Lambda

Provides endpoints for the BTC Trading Dashboard:
- GET /price - Current BTC price and history
- GET /volatility - Volatility metrics from DynamoDB
- GET /trades - Recent trade log
- GET /strikes - Available strikes with edge calculations
"""

import json
import boto3
import requests
from datetime import datetime, timedelta
from decimal import Decimal
import math


# DynamoDB tables
PRICE_TABLE = "BTCPriceHistory"
TRADE_TABLE = "BTCTradeLog"

# CORS headers
CORS_HEADERS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type,Authorization',
    'Access-Control-Allow-Methods': 'GET,OPTIONS'
}


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


def get_coinbase_btc_price():
    """Fetch current BTC price from Coinbase."""
    try:
        url = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return float(data['data']['amount'])
    except Exception as e:
        print(f"Error fetching BTC price: {e}")
    return None


def get_next_hour_event_ticker():
    """Generate the Kalshi event ticker for the next hour's BTC contract."""
    from datetime import timezone

    # Get current time in ET
    utc_now = datetime.now(timezone.utc)
    et_offset = timedelta(hours=-5)  # EST (use -4 for EDT)
    et_time = utc_now + et_offset

    # Get the NEXT hour's contract
    next_hour_time = et_time + timedelta(hours=1)

    year = next_hour_time.strftime('%y')
    month = next_hour_time.strftime('%b').upper()
    day = next_hour_time.strftime('%d')
    hour = next_hour_time.strftime('%H')

    return f"KXBTCD-{year}{month}{day}{hour}"


def get_kalshi_markets(event_ticker):
    """Fetch all markets for a BTC hourly event from Kalshi."""
    try:
        url = f"https://api.elections.kalshi.com/trade-api/v2/events/{event_ticker}"
        response = requests.get(url, headers={'Accept': 'application/json'}, timeout=10)

        if response.status_code != 200:
            print(f"Error fetching Kalshi markets: {response.status_code}")
            return []

        data = response.json()
        markets = data.get('markets', [])

        # Parse and return relevant data
        parsed = []
        for market in markets:
            parsed.append({
                'ticker': market.get('ticker'),
                'floor_strike': market.get('floor_strike'),
                'yes_bid': market.get('yes_bid', 0),
                'yes_ask': market.get('yes_ask', 0),
                'no_bid': market.get('no_bid', 0),
                'no_ask': market.get('no_ask', 0),
            })

        # Sort by strike price
        parsed.sort(key=lambda x: x['floor_strike'] if x['floor_strike'] else 0)
        return parsed

    except Exception as e:
        print(f"Error fetching Kalshi markets: {e}")
        return []


def get_volatility_data(dynamodb):
    """Get latest volatility metrics from DynamoDB."""
    table = dynamodb.Table(PRICE_TABLE)

    try:
        response = table.get_item(
            Key={'pk': 'VOL', 'sk': 'LATEST'}
        )

        if 'Item' in response:
            item = response['Item']
            return {
                'updated_at': item.get('updated_at'),
                '15m': {
                    'std': float(item.get('vol_15m_std', 0)),
                    'range': float(item.get('vol_15m_range', 0)),
                    'max_move': float(item.get('vol_15m_max_move', 0)),
                    'samples': int(item.get('vol_15m_samples', 0))
                },
                '30m': {
                    'std': float(item.get('vol_30m_std', 0)),
                    'range': float(item.get('vol_30m_range', 0)),
                    'max_move': float(item.get('vol_30m_max_move', 0)),
                    'samples': int(item.get('vol_30m_samples', 0))
                },
                '60m': {
                    'std': float(item.get('vol_60m_std', 0)),
                    'range': float(item.get('vol_60m_range', 0)),
                    'max_move': float(item.get('vol_60m_max_move', 0)),
                    'samples': int(item.get('vol_60m_samples', 0))
                },
                '90m': {
                    'std': float(item.get('vol_90m_std', 0)),
                    'range': float(item.get('vol_90m_range', 0)),
                    'max_move': float(item.get('vol_90m_max_move', 0)),
                    'samples': int(item.get('vol_90m_samples', 0))
                },
                '120m': {
                    'std': float(item.get('vol_120m_std', 0)),
                    'range': float(item.get('vol_120m_range', 0)),
                    'max_move': float(item.get('vol_120m_max_move', 0)),
                    'samples': int(item.get('vol_120m_samples', 0))
                }
            }
    except Exception as e:
        print(f"Error fetching volatility: {e}")

    return None


def get_price_history(dynamodb, minutes=60):
    """Get price history from the last N minutes."""
    table = dynamodb.Table(PRICE_TABLE)

    now = datetime.utcnow()
    start_time = now - timedelta(minutes=minutes)

    prices = []

    # Query today's prices
    today_pk = f"PRICE#{now.strftime('%Y%m%d')}"
    today_start_sk = start_time.strftime('%H:%M:%S') if start_time.date() == now.date() else "00:00:00"

    try:
        response = table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key('pk').eq(today_pk) &
                                  boto3.dynamodb.conditions.Key('sk').gte(today_start_sk)
        )

        for item in response.get('Items', []):
            prices.append({
                'timestamp': item['timestamp_utc'],
                'price': float(item['price'])
            })

        # If we need data from yesterday
        if start_time.date() < now.date():
            yesterday_pk = f"PRICE#{start_time.strftime('%Y%m%d')}"
            yesterday_start_sk = start_time.strftime('%H:%M:%S')

            response = table.query(
                KeyConditionExpression=boto3.dynamodb.conditions.Key('pk').eq(yesterday_pk) &
                                      boto3.dynamodb.conditions.Key('sk').gte(yesterday_start_sk)
            )

            for item in response.get('Items', []):
                prices.append({
                    'timestamp': item['timestamp_utc'],
                    'price': float(item['price'])
                })

        # Sort by timestamp
        prices.sort(key=lambda x: x['timestamp'])

    except Exception as e:
        print(f"Error fetching price history: {e}")

    return prices


def get_recent_trades(dynamodb, limit=20):
    """Get recent trades from the trade log."""
    table = dynamodb.Table(TRADE_TABLE)

    try:
        # Query trades using the pk='TRADE' partition key
        # This gets all trades, sorted by sk (timestamp) descending
        response = table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key('pk').eq('TRADE'),
            ScanIndexForward=False,  # Descending order (newest first)
            Limit=limit
        )

        trades = []
        for item in response.get('Items', []):
            # Parse the ticker to get strike price
            ticker = item.get('contract_ticker', '')
            strike = None
            if '-T' in ticker:
                try:
                    strike = float(ticker.split('-T')[1])
                except:
                    pass

            quantity = int(item.get('quantity', 0))
            price_cents = int(item.get('price_cents', 0))
            total_cost = float(item.get('total_cost', 0))
            potential_profit = float(item.get('potential_profit', 0))

            trades.append({
                'timestamp': item.get('sk', ''),  # sk is the timestamp
                'ticker': ticker,
                'strike': strike,
                'side': item.get('side', 'NO'),
                'contracts': quantity,
                'price': price_cents,
                'total_cost': total_cost,
                'potential_profit': potential_profit,
                'edge': float(item.get('edge', 0)),
                'status': item.get('status', 'unknown'),
                'order_id': item.get('order_id'),
                'btc_price': float(item.get('btc_price', 0)),
                'pnl': None  # Will be calculated after settlement
            })

        # Sort by timestamp descending
        trades.sort(key=lambda x: x.get('timestamp', ''), reverse=True)

        return trades[:limit]

    except Exception as e:
        print(f"Error fetching trades: {e}")

    return []


def normal_cdf(x):
    """Approximate normal CDF."""
    a1 =  0.254829592
    a2 = -0.284496736
    a3 =  1.421413741
    a4 = -1.453152027
    a5 =  1.061405429
    p  =  0.3275911

    sign = -1 if x < 0 else 1
    x = abs(x) / math.sqrt(2)

    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x)

    return 0.5 * (1.0 + sign * y)


def calculate_strikes(btc_price, volatility, minutes_to_settlement=15):
    """Calculate available strikes with edge calculations using real Kalshi data."""
    strikes = []

    # Get real market data from Kalshi
    event_ticker = get_next_hour_event_ticker()
    kalshi_markets = get_kalshi_markets(event_ticker)

    # Create a lookup by strike price
    market_lookup = {}
    for m in kalshi_markets:
        if m['floor_strike']:
            market_lookup[m['floor_strike']] = m

    # Show strikes starting just above current price (not filtered by 20 bps)
    # This allows dashboard to show all tradeable opportunities
    for market in kalshi_markets:
        strike_price = market.get('floor_strike')
        if not strike_price or strike_price < btc_price:
            continue

        # Calculate distance as percentage from current price
        distance_pct = (strike_price / btc_price - 1) * 100  # e.g., 0.30% for 30 bps
        distance_bps = round(distance_pct * 100)  # e.g., 30 bps

        # Calculate fair NO price using normal distribution
        # Volatility is already in % (e.g., 0.0438 = 0.0438%)
        # Scale volatility to remaining time
        vol_scaled = volatility * math.sqrt(minutes_to_settlement / 15) if volatility > 0 else 0.1

        # Z-score: how many standard deviations the strike is above current price
        z_score = distance_pct / vol_scaled if vol_scaled > 0 else 0

        # P(BTC ends above strike) = normal_cdf(z_score), so P(NO wins) = normal_cdf(z_score)
        # (NO wins when BTC stays BELOW the strike)
        fair_no = normal_cdf(z_score)  # P(price stays below strike)
        fair_no_cents = round(fair_no * 100)

        # Get real market ask price
        no_ask = market.get('no_ask', 0) or 0

        # Calculate edge: (fair - ask) as percentage points
        edge = fair_no_cents - no_ask if no_ask > 0 else 0

        # Kelly bet calculation - returns bet amount in dollars for $100 bankroll
        kelly_fraction = 0
        kelly_bet_dollars = 0
        if edge > 0 and no_ask > 0 and no_ask < 100:
            win_prob = fair_no
            profit_if_win = 100 - no_ask
            loss_if_lose = no_ask
            odds = profit_if_win / loss_if_lose if loss_if_lose > 0 else 0
            kelly_fraction = (odds * win_prob - (1 - win_prob)) / odds if odds > 0 else 0
            kelly_fraction = max(0, min(kelly_fraction, 0.20))  # Cap at 20%
            # Calculate bet amount for $100 bankroll
            kelly_bet_dollars = round(100 * kelly_fraction, 2)

        strikes.append({
            'strike': strike_price,
            'ticker': market.get('ticker'),
            'distance_bps': distance_bps,
            'fair_no_price': fair_no_cents,
            'no_ask': no_ask,
            'edge': edge,
            'kelly_fraction': round(kelly_fraction * 100, 1),  # As percentage
            'kelly_bet': kelly_bet_dollars,  # Bet amount in dollars
            'z_score': round(z_score, 2)
        })

    # Sort by strike and limit to first 10
    strikes.sort(key=lambda x: x['strike'])
    return strikes[:10]


def lambda_handler(event, context):
    """Main Lambda handler."""
    print(f"Event: {json.dumps(event)}")

    # Handle HTTP API v2 format (API Gateway HTTP API)
    # v2 uses requestContext.http.path and requestContext.http.method
    if 'requestContext' in event and 'http' in event.get('requestContext', {}):
        http_context = event['requestContext']['http']
        path = http_context.get('path', '/')
        method = http_context.get('method', 'GET')
    else:
        # v1 format (REST API or direct invoke)
        path = event.get('path', '/')
        method = event.get('httpMethod', 'GET')

    # Handle CORS preflight
    if method == 'OPTIONS':
        return {
            'statusCode': 200,
            'headers': CORS_HEADERS,
            'body': ''
        }

    print(f"Path: {path}, Method: {method}")
    dynamodb = boto3.resource('dynamodb')

    try:
        if path == '/price' or path == '/dashboard/price':
            # Get current price and history
            current_price = get_coinbase_btc_price()
            history = get_price_history(dynamodb, minutes=60)

            return {
                'statusCode': 200,
                'headers': CORS_HEADERS,
                'body': json.dumps({
                    'current_price': current_price,
                    'history': history,
                    'timestamp': datetime.utcnow().isoformat()
                }, cls=DecimalEncoder)
            }

        elif path == '/volatility' or path == '/dashboard/volatility':
            # Get volatility metrics
            vol_data = get_volatility_data(dynamodb)

            return {
                'statusCode': 200,
                'headers': CORS_HEADERS,
                'body': json.dumps({
                    'volatility': vol_data,
                    'timestamp': datetime.utcnow().isoformat()
                }, cls=DecimalEncoder)
            }

        elif path == '/trades' or path == '/dashboard/trades':
            # Get recent trades
            trades = get_recent_trades(dynamodb)

            return {
                'statusCode': 200,
                'headers': CORS_HEADERS,
                'body': json.dumps({
                    'trades': trades,
                    'timestamp': datetime.utcnow().isoformat()
                }, cls=DecimalEncoder)
            }

        elif path == '/strikes' or path == '/dashboard/strikes':
            # Get strikes with edge calculations
            current_price = get_coinbase_btc_price()
            vol_data = get_volatility_data(dynamodb)

            now = datetime.utcnow()
            mins_to_settle = 60 - now.minute

            vol_15m = vol_data['15m']['std'] if vol_data else 0.1
            strikes = calculate_strikes(current_price, vol_15m, mins_to_settle)

            return {
                'statusCode': 200,
                'headers': CORS_HEADERS,
                'body': json.dumps({
                    'btc_price': current_price,
                    'volatility_15m': vol_15m,
                    'minutes_to_settlement': mins_to_settle,
                    'strikes': strikes,
                    'timestamp': datetime.utcnow().isoformat()
                }, cls=DecimalEncoder)
            }

        elif path == '/all' or path == '/dashboard/all':
            # Get all data in one call
            current_price = get_coinbase_btc_price()
            vol_data = get_volatility_data(dynamodb)
            history = get_price_history(dynamodb, minutes=60)
            trades = get_recent_trades(dynamodb)

            # Calculate minutes to settlement
            now = datetime.utcnow()
            mins_to_settle = 60 - now.minute

            vol_15m = vol_data['15m']['std'] if vol_data else 0.1
            strikes = calculate_strikes(current_price, vol_15m, mins_to_settle)

            return {
                'statusCode': 200,
                'headers': CORS_HEADERS,
                'body': json.dumps({
                    'btc_price': current_price,
                    'price_history': history,
                    'volatility': vol_data,
                    'strikes': strikes,
                    'trades': trades,
                    'minutes_to_settlement': mins_to_settle,
                    'trading_active': vol_15m < 11.0,
                    'timestamp': datetime.utcnow().isoformat()
                }, cls=DecimalEncoder)
            }

        else:
            return {
                'statusCode': 404,
                'headers': CORS_HEADERS,
                'body': json.dumps({'error': 'Not found'})
            }

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

        return {
            'statusCode': 500,
            'headers': CORS_HEADERS,
            'body': json.dumps({'error': str(e)})
        }


# For local testing
if __name__ == "__main__":
    # Test the handler
    event = {'path': '/all', 'httpMethod': 'GET'}
    result = lambda_handler(event, None)
    print(json.dumps(json.loads(result['body']), indent=2))
