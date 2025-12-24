"""
Dashboard API Lambda

Provides endpoints for the BTC/ETH Trading Dashboard:
- GET /price - Current BTC/ETH prices and history
- GET /volatility - Volatility metrics from DynamoDB
- GET /trades - Recent trade log with P&L
- GET /strikes - Available strikes with edge calculations
"""

import json
import boto3
import requests
from datetime import datetime, timedelta
from decimal import Decimal
import math


# DynamoDB tables
BTC_PRICE_TABLE = "BTCPriceHistory"
ETH_PRICE_TABLE = "ETHPriceHistory"
BTC_TRADE_TABLE = "BTCTradeLog"
ETH_TRADE_TABLE = "ETHTradeLog"

# Starting balance for IRR calculation (as of 12/18/2025)
IRR_START_DATE = "2025-12-18"
IRR_START_BALANCE = 1000.00

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


def get_coinbase_price(asset="BTC"):
    """Fetch current price from Coinbase."""
    try:
        url = f"https://api.coinbase.com/v2/prices/{asset}-USD/spot"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return float(data['data']['amount'])
    except Exception as e:
        print(f"Error fetching {asset} price: {e}")
    return None


def get_next_hour_event_ticker(asset="BTC"):
    """Generate the Kalshi event ticker for the next hour's contract."""
    from datetime import timezone

    # Get current time in ET
    utc_now = datetime.now(timezone.utc)
    # Check if DST (March - November)
    month = utc_now.month
    if 3 <= month <= 11:
        et_offset = timedelta(hours=-4)  # EDT
    else:
        et_offset = timedelta(hours=-5)  # EST
    et_time = utc_now + et_offset

    # Get the NEXT hour's contract
    next_hour_time = et_time + timedelta(hours=1)

    year = next_hour_time.strftime('%y')
    month_str = next_hour_time.strftime('%b').upper()
    day = next_hour_time.strftime('%d')
    hour = next_hour_time.strftime('%H')

    # Series names: KXBTCD for BTC, KXETHD for ETH
    series = "KXBTCD" if asset == "BTC" else "KXETHD"
    return f"{series}-{year}{month_str}{day}{hour}"


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


def get_volatility_data(dynamodb, asset="BTC"):
    """Get latest volatility metrics from DynamoDB."""
    table_name = BTC_PRICE_TABLE if asset == "BTC" else ETH_PRICE_TABLE
    table = dynamodb.Table(table_name)

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


def get_price_history(dynamodb, asset="BTC", minutes=60):
    """Get price history from the last N minutes."""
    table_name = BTC_PRICE_TABLE if asset == "BTC" else ETH_PRICE_TABLE
    table = dynamodb.Table(table_name)

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


def get_recent_trades(dynamodb, asset="BTC", limit=50):
    """Get recent trades from the trade log with settlement/P&L data."""
    table_name = BTC_TRADE_TABLE if asset == "BTC" else ETH_TRADE_TABLE
    table = dynamodb.Table(table_name)

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

            # Calculate risk and potential return
            risk_dollars = total_cost  # Amount we could lose
            potential_win = potential_profit  # Amount we could win

            # Check if trade has settled (look for settlement_price or settled flag)
            settlement_price = item.get('settlement_price')
            settled = item.get('settled', False)
            pnl = None
            pnl_pct = None
            won = None

            # Determine settlement based on ticker timestamp
            # Ticker format: KXBTCD-24DEC2110-T97500 -> settles at 10:00 on Dec 21
            # If current time is past settlement hour, we can infer outcome
            if settlement_price is not None:
                # Explicit settlement data
                if item.get('side', 'NO') == 'NO':
                    won = float(settlement_price) < strike if strike else None
                else:
                    won = float(settlement_price) >= strike if strike else None

                if won is not None:
                    if won:
                        pnl = potential_win
                        pnl_pct = (potential_win / risk_dollars * 100) if risk_dollars > 0 else 0
                    else:
                        pnl = -risk_dollars
                        pnl_pct = -100.0
            elif settled:
                # Trade marked as settled but no price - use won field if present
                won = item.get('won')
                if won is not None:
                    if won:
                        pnl = potential_win
                        pnl_pct = (potential_win / risk_dollars * 100) if risk_dollars > 0 else 0
                    else:
                        pnl = -risk_dollars
                        pnl_pct = -100.0

            trades.append({
                'timestamp': item.get('sk', ''),  # sk is the timestamp
                'ticker': ticker,
                'strike': strike,
                'side': item.get('side', 'NO'),
                'contracts': quantity,
                'price': price_cents,
                'total_cost': total_cost,
                'risk': risk_dollars,
                'potential_profit': potential_profit,
                'edge': float(item.get('edge', 0)),
                'kelly_fraction': float(item.get('kelly_fraction', 0)),
                'status': item.get('status', 'unknown'),
                'order_id': item.get('order_id'),
                'asset_price': float(item.get('btc_price', item.get('eth_price', 0))),
                'asset': asset,
                'settled': settled or settlement_price is not None,
                'won': won,
                'pnl': pnl,
                'pnl_pct': pnl_pct
            })

        # Sort by timestamp descending
        trades.sort(key=lambda x: x.get('timestamp', ''), reverse=True)

        return trades[:limit]

    except Exception as e:
        print(f"Error fetching {asset} trades: {e}")
        import traceback
        traceback.print_exc()

    return []


def get_all_trades_for_irr(dynamodb):
    """Get all trades since IRR_START_DATE for IRR calculation."""
    all_trades = []

    for asset, table_name in [("BTC", BTC_TRADE_TABLE), ("ETH", ETH_TRADE_TABLE)]:
        try:
            table = dynamodb.Table(table_name)

            # Query all trades since start date
            response = table.query(
                KeyConditionExpression=boto3.dynamodb.conditions.Key('pk').eq('TRADE') &
                                      boto3.dynamodb.conditions.Key('sk').gte(IRR_START_DATE),
                ScanIndexForward=True  # Ascending order (oldest first)
            )

            for item in response.get('Items', []):
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

                # Check settlement
                settlement_price = item.get('settlement_price')
                settled = item.get('settled', False)
                won = item.get('won')
                pnl = None

                if won is not None:
                    if won:
                        pnl = potential_profit
                    else:
                        pnl = -total_cost
                elif settlement_price is not None and strike:
                    if item.get('side', 'NO') == 'NO':
                        won = float(settlement_price) < strike
                    else:
                        won = float(settlement_price) >= strike
                    if won:
                        pnl = potential_profit
                    else:
                        pnl = -total_cost

                all_trades.append({
                    'timestamp': item.get('sk', ''),
                    'asset': asset,
                    'risk': total_cost,
                    'potential_profit': potential_profit,
                    'settled': settled or settlement_price is not None or won is not None,
                    'won': won,
                    'pnl': pnl
                })

        except Exception as e:
            print(f"Error fetching {asset} trades for IRR: {e}")

    # Sort by timestamp
    all_trades.sort(key=lambda x: x.get('timestamp', ''))
    return all_trades


def calculate_irr_stats(trades):
    """Calculate running P&L and IRR from trades."""
    running_balance = IRR_START_BALANCE
    total_pnl = 0
    wins = 0
    losses = 0
    pending = 0

    trade_history = []

    for trade in trades:
        if trade['settled'] and trade['pnl'] is not None:
            total_pnl += trade['pnl']
            running_balance += trade['pnl']
            if trade['won']:
                wins += 1
            else:
                losses += 1
        else:
            pending += 1

        trade_history.append({
            'timestamp': trade['timestamp'],
            'asset': trade['asset'],
            'pnl': trade['pnl'],
            'running_balance': running_balance
        })

    # Calculate simple return
    total_return_pct = ((running_balance - IRR_START_BALANCE) / IRR_START_BALANCE) * 100

    # Calculate days since start for annualized return
    try:
        start_date = datetime.strptime(IRR_START_DATE, '%Y-%m-%d')
        days_elapsed = (datetime.utcnow() - start_date).days
        if days_elapsed > 0:
            # Annualized return = (1 + total_return)^(365/days) - 1
            annualized_return = ((1 + total_return_pct/100) ** (365/days_elapsed) - 1) * 100
        else:
            annualized_return = 0
    except:
        annualized_return = 0
        days_elapsed = 0

    return {
        'start_balance': IRR_START_BALANCE,
        'current_balance': round(running_balance, 2),
        'total_pnl': round(total_pnl, 2),
        'total_return_pct': round(total_return_pct, 2),
        'annualized_return_pct': round(annualized_return, 2),
        'days_elapsed': days_elapsed,
        'wins': wins,
        'losses': losses,
        'pending': pending,
        'win_rate': round(wins / (wins + losses) * 100, 1) if (wins + losses) > 0 else 0,
        'trade_history': trade_history[-20:]  # Last 20 for chart
    }


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


def calculate_strikes(asset_price, volatility, minutes_to_settlement=15, asset="BTC"):
    """Calculate available strikes with edge calculations using real Kalshi data."""
    strikes = []

    # Get real market data from Kalshi
    event_ticker = get_next_hour_event_ticker(asset)
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
        if not strike_price or strike_price < asset_price:
            continue

        # Calculate distance as percentage from current price
        distance_pct = (strike_price / asset_price - 1) * 100  # e.g., 0.30% for 30 bps
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
            # Get current prices and history
            btc_price = get_coinbase_price("BTC")
            eth_price = get_coinbase_price("ETH")
            btc_history = get_price_history(dynamodb, "BTC", minutes=60)

            return {
                'statusCode': 200,
                'headers': CORS_HEADERS,
                'body': json.dumps({
                    'btc_price': btc_price,
                    'eth_price': eth_price,
                    'history': btc_history,
                    'timestamp': datetime.utcnow().isoformat()
                }, cls=DecimalEncoder)
            }

        elif path == '/volatility' or path == '/dashboard/volatility':
            # Get volatility metrics for both assets
            btc_vol = get_volatility_data(dynamodb, "BTC")
            eth_vol = get_volatility_data(dynamodb, "ETH")

            return {
                'statusCode': 200,
                'headers': CORS_HEADERS,
                'body': json.dumps({
                    'btc_volatility': btc_vol,
                    'eth_volatility': eth_vol,
                    'timestamp': datetime.utcnow().isoformat()
                }, cls=DecimalEncoder)
            }

        elif path == '/trades' or path == '/dashboard/trades':
            # Get recent trades for both assets
            btc_trades = get_recent_trades(dynamodb, "BTC")
            eth_trades = get_recent_trades(dynamodb, "ETH")

            # Combine and sort by timestamp
            all_trades = btc_trades + eth_trades
            all_trades.sort(key=lambda x: x.get('timestamp', ''), reverse=True)

            # Get IRR stats
            irr_trades = get_all_trades_for_irr(dynamodb)
            irr_stats = calculate_irr_stats(irr_trades)

            return {
                'statusCode': 200,
                'headers': CORS_HEADERS,
                'body': json.dumps({
                    'trades': all_trades[:30],
                    'irr_stats': irr_stats,
                    'timestamp': datetime.utcnow().isoformat()
                }, cls=DecimalEncoder)
            }

        elif path == '/strikes' or path == '/dashboard/strikes':
            # Get strikes with edge calculations for both assets
            btc_price = get_coinbase_price("BTC")
            eth_price = get_coinbase_price("ETH")
            btc_vol = get_volatility_data(dynamodb, "BTC")
            eth_vol = get_volatility_data(dynamodb, "ETH")

            now = datetime.utcnow()
            mins_to_settle = 60 - now.minute

            btc_vol_15m = btc_vol['15m']['std'] if btc_vol else 0.1
            eth_vol_15m = eth_vol['15m']['std'] if eth_vol else 0.1
            btc_strikes = calculate_strikes(btc_price, btc_vol_15m, mins_to_settle, "BTC")
            eth_strikes = calculate_strikes(eth_price, eth_vol_15m, mins_to_settle, "ETH")

            return {
                'statusCode': 200,
                'headers': CORS_HEADERS,
                'body': json.dumps({
                    'btc_price': btc_price,
                    'eth_price': eth_price,
                    'btc_volatility_15m': btc_vol_15m,
                    'eth_volatility_15m': eth_vol_15m,
                    'minutes_to_settlement': mins_to_settle,
                    'btc_strikes': btc_strikes,
                    'eth_strikes': eth_strikes,
                    'timestamp': datetime.utcnow().isoformat()
                }, cls=DecimalEncoder)
            }

        elif path == '/all' or path == '/dashboard/all':
            # Get all data in one call
            btc_price = get_coinbase_price("BTC")
            eth_price = get_coinbase_price("ETH")
            btc_vol = get_volatility_data(dynamodb, "BTC")
            eth_vol = get_volatility_data(dynamodb, "ETH")
            btc_history = get_price_history(dynamodb, "BTC", minutes=60)
            btc_trades = get_recent_trades(dynamodb, "BTC")
            eth_trades = get_recent_trades(dynamodb, "ETH")

            # Combine trades and sort by timestamp
            all_trades = btc_trades + eth_trades
            all_trades.sort(key=lambda x: x.get('timestamp', ''), reverse=True)

            # Get IRR stats
            irr_trades = get_all_trades_for_irr(dynamodb)
            irr_stats = calculate_irr_stats(irr_trades)

            # Calculate minutes to settlement
            now = datetime.utcnow()
            mins_to_settle = 60 - now.minute

            btc_vol_15m = btc_vol['15m']['std'] if btc_vol else 0.1
            eth_vol_15m = eth_vol['15m']['std'] if eth_vol else 0.1
            btc_strikes = calculate_strikes(btc_price, btc_vol_15m, mins_to_settle, "BTC")
            eth_strikes = calculate_strikes(eth_price, eth_vol_15m, mins_to_settle, "ETH")

            return {
                'statusCode': 200,
                'headers': CORS_HEADERS,
                'body': json.dumps({
                    'btc_price': btc_price,
                    'eth_price': eth_price,
                    'price_history': btc_history,
                    'volatility': btc_vol,  # Keep for backward compatibility
                    'btc_volatility': btc_vol,
                    'eth_volatility': eth_vol,
                    'strikes': btc_strikes,  # Keep for backward compatibility
                    'btc_strikes': btc_strikes,
                    'eth_strikes': eth_strikes,
                    'trades': all_trades[:30],
                    'irr_stats': irr_stats,
                    'minutes_to_settlement': mins_to_settle,
                    'trading_active': btc_vol_15m < 11.0,
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
