"""
BTC Range/Hourly Arbitrage Scanner

Scans for riskless arbitrage opportunities between:
- KXBTC (Range contracts): "BTC price in range $X-$Y"
- KXBTCD (Hourly contracts): "BTC price >= $X"

Arbitrage relationship:
  P(in range $X-$Y) = P(>= $X) - P(>= $Y)

If market prices don't match this relationship, there's arbitrage.

Trade setup when Range YES is underpriced:
  BUY Range YES + BUY Hourly(cap) YES + SELL Hourly(floor) YES
  = Riskless profit = Implied Range - Actual Range price

Trade setup when Range YES is overpriced:
  SELL Range YES + SELL Hourly(cap) YES + BUY Hourly(floor) YES
  = Riskless profit = Actual Range - Implied Range price
"""

import json
import os
import requests
from datetime import datetime, timedelta

# Try to import boto3 for SES (Lambda has it, local might not)
try:
    import boto3
    ses_client = boto3.client('ses', region_name='us-east-2')
except ImportError:
    ses_client = None
    print("Warning: boto3 not available, email notifications disabled")

# Email for alerts
ALERT_EMAIL = os.environ.get('ALERT_EMAIL', 'ppglaw@gmail.com')
FROM_EMAIL = os.environ.get('FROM_EMAIL', 'ppglaw@gmail.com')  # Must be verified in SES


def send_email_alert(subject, body_text, body_html=None):
    """Send an email alert via AWS SES."""
    if not ses_client:
        print("SES client not available, skipping email")
        return False

    if not ALERT_EMAIL:
        print("No email configured, skipping")
        return False

    try:
        # Build email body
        body = {'Text': {'Data': body_text, 'Charset': 'UTF-8'}}
        if body_html:
            body['Html'] = {'Data': body_html, 'Charset': 'UTF-8'}

        response = ses_client.send_email(
            Source=FROM_EMAIL,
            Destination={'ToAddresses': [ALERT_EMAIL]},
            Message={
                'Subject': {'Data': subject, 'Charset': 'UTF-8'},
                'Body': body
            }
        )
        print(f"Email sent! MessageId: {response['MessageId']}")
        return True
    except Exception as e:
        print(f"Failed to send email: {e}")
        return False


def get_hourly_markets():
    """Fetch all hourly BTC contracts (KXBTCD series)."""
    try:
        # Get next hourly event
        url = "https://api.elections.kalshi.com/trade-api/v2/events"
        params = {"series_ticker": "KXBTCD", "status": "open", "limit": 10}
        response = requests.get(url, params=params, headers={'Accept': 'application/json'}, timeout=10)

        if response.status_code != 200:
            print(f"Error fetching hourly events: {response.status_code}")
            return None, []

        data = response.json()
        events = data.get("events", [])

        # Find the soonest future event
        now = datetime.utcnow()
        for event in sorted(events, key=lambda e: e.get('strike_date', '')):
            strike_date_str = event.get("strike_date", "")
            if strike_date_str:
                strike_date = datetime.fromisoformat(strike_date_str.replace("Z", "+00:00"))
                strike_date_naive = strike_date.replace(tzinfo=None)
                if strike_date_naive > now:
                    event_ticker = event.get("event_ticker")
                    mins_to_settle = int((strike_date_naive - now).total_seconds() / 60)

                    # Fetch markets for this event
                    markets_url = f"https://api.elections.kalshi.com/trade-api/v2/events/{event_ticker}"
                    markets_response = requests.get(markets_url, headers={'Accept': 'application/json'}, timeout=10)

                    if markets_response.status_code == 200:
                        markets_data = markets_response.json()
                        markets = markets_data.get('markets', [])

                        # Parse into strike -> prices lookup
                        hourly_strikes = {}
                        for m in markets:
                            floor = m.get('floor_strike')
                            if floor:
                                hourly_strikes[floor] = {
                                    'ticker': m.get('ticker'),
                                    'yes_bid': m.get('yes_bid', 0) or 0,
                                    'yes_ask': m.get('yes_ask', 0) or 0,
                                    'no_bid': m.get('no_bid', 0) or 0,
                                    'no_ask': m.get('no_ask', 0) or 0,
                                }

                        return {
                            'event_ticker': event_ticker,
                            'strike_date': strike_date_naive,
                            'mins_to_settle': mins_to_settle,
                            'title': event.get('sub_title', '')
                        }, hourly_strikes

        return None, {}
    except Exception as e:
        print(f"Error: {e}")
        return None, {}


def get_range_markets():
    """Fetch all range BTC contracts (KXBTC series)."""
    try:
        # Get next range event
        url = "https://api.elections.kalshi.com/trade-api/v2/events"
        params = {"series_ticker": "KXBTC", "status": "open", "limit": 30}
        response = requests.get(url, params=params, headers={'Accept': 'application/json'}, timeout=10)

        if response.status_code != 200:
            print(f"Error fetching range events: {response.status_code}")
            return None, []

        data = response.json()
        events = data.get("events", [])

        # Find the soonest future event
        now = datetime.utcnow()
        for event in sorted(events, key=lambda e: e.get('strike_date', '')):
            strike_date_str = event.get("strike_date", "")
            if strike_date_str:
                strike_date = datetime.fromisoformat(strike_date_str.replace("Z", "+00:00"))
                strike_date_naive = strike_date.replace(tzinfo=None)
                if strike_date_naive > now:
                    event_ticker = event.get("event_ticker")
                    mins_to_settle = int((strike_date_naive - now).total_seconds() / 60)

                    # Fetch markets for this event
                    markets_url = f"https://api.elections.kalshi.com/trade-api/v2/events/{event_ticker}"
                    markets_response = requests.get(markets_url, headers={'Accept': 'application/json'}, timeout=10)

                    if markets_response.status_code == 200:
                        markets_data = markets_response.json()
                        markets = markets_data.get('markets', [])

                        # Parse into (floor, cap) -> prices lookup
                        range_markets = {}
                        for m in markets:
                            floor = m.get('floor_strike')
                            cap = m.get('cap_strike')
                            strike_type = m.get('strike_type', 'between')

                            if strike_type == 'between' and floor and cap:
                                range_markets[(floor, cap)] = {
                                    'ticker': m.get('ticker'),
                                    'yes_bid': m.get('yes_bid', 0) or 0,
                                    'yes_ask': m.get('yes_ask', 0) or 0,
                                    'no_bid': m.get('no_bid', 0) or 0,
                                    'no_ask': m.get('no_ask', 0) or 0,
                                }

                        return {
                            'event_ticker': event_ticker,
                            'strike_date': strike_date_naive,
                            'mins_to_settle': mins_to_settle,
                            'title': event.get('sub_title', '')
                        }, range_markets

        return None, {}
    except Exception as e:
        print(f"Error: {e}")
        return None, {}


def get_coinbase_btc_price():
    """Fetch current BTC price."""
    try:
        url = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return float(data['data']['amount'])
    except:
        pass
    return None


def find_matching_hourly(hourly_strikes, target_strike):
    """
    Find the hourly contract that matches a range strike.

    Range floors are round numbers (e.g., 81000, 81250)
    Hourly strikes are X.99 format (e.g., 80999.99 means >= $81000)

    So range floor 81000 matches hourly 80999.99
    And range cap 81249.99 matches hourly 81249.99
    """
    # Direct match (for caps which are already .99)
    if target_strike in hourly_strikes:
        return hourly_strikes[target_strike]

    # For round number floors, look for strike - 0.01
    adjusted = target_strike - 0.01
    if adjusted in hourly_strikes:
        return hourly_strikes[adjusted]

    # Try finding closest match within 1
    for strike in hourly_strikes:
        if abs(strike - target_strike) < 1 or abs(strike - (target_strike - 0.01)) < 1:
            return hourly_strikes[strike]

    return None


def find_arbitrage_opportunities(hourly_strikes, range_markets, min_profit_cents=2):
    """
    Find arbitrage opportunities between hourly and range contracts.

    For each range (floor, cap):
      Implied Range YES = Hourly(floor) YES - Hourly(cap) YES

    If Range YES ask < Implied - spread cost: BUY Range, SELL spread
    If Range YES bid > Implied + spread cost: SELL Range, BUY spread
    """
    opportunities = []

    for (floor, cap), range_data in range_markets.items():
        # Find matching hourly contracts
        hourly_floor = find_matching_hourly(hourly_strikes, floor)
        hourly_cap = find_matching_hourly(hourly_strikes, cap)

        if not hourly_floor or not hourly_cap:
            continue

        # Calculate implied range YES value from hourly spread
        # P(in range) = P(>= floor) - P(>= cap)
        # To BUY the implied range: BUY floor YES, SELL cap YES
        # Cost = floor_yes_ask - cap_yes_bid
        implied_range_buy_cost = hourly_floor['yes_ask'] - hourly_cap['yes_bid']

        # To SELL the implied range: SELL floor YES, BUY cap YES
        # Revenue = floor_yes_bid - cap_yes_ask
        implied_range_sell_revenue = hourly_floor['yes_bid'] - hourly_cap['yes_ask']

        # Actual range prices
        range_yes_ask = range_data['yes_ask']
        range_yes_bid = range_data['yes_bid']

        # Opportunity 1: Range YES is cheap relative to implied
        # Trade: BUY Range YES, SELL Hourly(floor) YES, BUY Hourly(cap) YES
        # Profit = Implied sell revenue - Range buy cost
        if range_yes_ask > 0 and implied_range_sell_revenue > 0:
            profit_buy_range = implied_range_sell_revenue - range_yes_ask
            if profit_buy_range >= min_profit_cents:
                opportunities.append({
                    'type': 'BUY_RANGE',
                    'range': f"${floor:,.0f}-${cap:,.0f}",
                    'floor': floor,
                    'cap': cap,
                    'range_ticker': range_data['ticker'],
                    'range_yes_ask': range_yes_ask,
                    'implied_value': implied_range_sell_revenue,
                    'profit_cents': profit_buy_range,
                    'trades': [
                        f"BUY Range YES @ {range_yes_ask}c",
                        f"SELL Hourly ${floor:,.0f} YES @ {hourly_floor['yes_bid']}c",
                        f"BUY Hourly ${cap:,.0f} YES @ {hourly_cap['yes_ask']}c",
                    ],
                    'hourly_floor_ticker': hourly_floor['ticker'],
                    'hourly_cap_ticker': hourly_cap['ticker'],
                    'hourly_floor_yes_bid': hourly_floor['yes_bid'],
                    'hourly_cap_yes_ask': hourly_cap['yes_ask'],
                })

        # Opportunity 2: Range YES is expensive relative to implied
        # Trade: SELL Range YES, BUY Hourly(floor) YES, SELL Hourly(cap) YES
        # Profit = Range sell revenue - Implied buy cost
        if range_yes_bid > 0 and implied_range_buy_cost > 0:
            profit_sell_range = range_yes_bid - implied_range_buy_cost
            if profit_sell_range >= min_profit_cents:
                opportunities.append({
                    'type': 'SELL_RANGE',
                    'range': f"${floor:,.0f}-${cap:,.0f}",
                    'floor': floor,
                    'cap': cap,
                    'range_ticker': range_data['ticker'],
                    'range_yes_bid': range_yes_bid,
                    'implied_value': implied_range_buy_cost,
                    'profit_cents': profit_sell_range,
                    'trades': [
                        f"SELL Range YES @ {range_yes_bid}c",
                        f"BUY Hourly ${floor:,.0f} YES @ {hourly_floor['yes_ask']}c",
                        f"SELL Hourly ${cap:,.0f} YES @ {hourly_cap['yes_bid']}c",
                    ],
                    'hourly_floor_ticker': hourly_floor['ticker'],
                    'hourly_cap_ticker': hourly_cap['ticker'],
                    'hourly_floor_yes_ask': hourly_floor['yes_ask'],
                    'hourly_cap_yes_bid': hourly_cap['yes_bid'],
                })

    # Sort by profit (highest first)
    opportunities.sort(key=lambda x: x['profit_cents'], reverse=True)
    return opportunities


def print_arbitrage_report(btc_price, hourly_info, range_info, opportunities):
    """Print a detailed arbitrage scan report."""
    print("")
    print("=" * 70)
    print("        BTC RANGE/HOURLY ARBITRAGE SCANNER")
    print("=" * 70)
    print(f"BTC Price: ${btc_price:,.2f}")
    print(f"Hourly Contract: {hourly_info['event_ticker']} ({hourly_info['mins_to_settle']} min)")
    print(f"Range Contract: {range_info['event_ticker']} ({range_info['mins_to_settle']} min)")
    print("")

    if not opportunities:
        print("No arbitrage opportunities found (minimum 2c profit required)")
        print("")
        print("Markets are efficiently priced - range prices match implied values")
        print("=" * 70)
        return

    print(f"Found {len(opportunities)} arbitrage opportunities!")
    print("-" * 70)

    for i, opp in enumerate(opportunities[:5], 1):
        print(f"\n#{i} - {opp['type']} - {opp['range']}")
        print(f"    Profit: {opp['profit_cents']}c per contract (RISKLESS)")
        print(f"    Trades:")
        for trade in opp['trades']:
            print(f"      - {trade}")

        if opp['type'] == 'BUY_RANGE':
            print(f"    Range YES ask: {opp['range_yes_ask']}c")
            print(f"    Implied value: {opp['implied_value']}c")
        else:
            print(f"    Range YES bid: {opp['range_yes_bid']}c")
            print(f"    Implied value: {opp['implied_value']}c")

    print("")
    print("=" * 70)

    # Calculate total potential profit for top opportunity
    if opportunities:
        best = opportunities[0]
        print(f"\nBest opportunity: {best['profit_cents']}c/contract riskless profit")
        print(f"With 100 contracts: ${best['profit_cents']}")
        print("=" * 70)


def scan_for_arbitrage():
    """Main function to scan for arbitrage opportunities."""
    print("Fetching BTC price...")
    btc_price = get_coinbase_btc_price()
    if not btc_price:
        print("Could not fetch BTC price")
        return None

    print("Fetching hourly contracts...")
    hourly_info, hourly_strikes = get_hourly_markets()
    if not hourly_info:
        print("Could not fetch hourly contracts")
        return None
    print(f"  Found {len(hourly_strikes)} hourly strikes")

    print("Fetching range contracts...")
    range_info, range_markets = get_range_markets()
    if not range_info:
        print("Could not fetch range contracts")
        return None
    print(f"  Found {len(range_markets)} range markets")

    # Check if contracts settle at the same time
    hourly_settle = hourly_info['strike_date']
    range_settle = range_info['strike_date']

    if hourly_settle != range_settle:
        time_diff = abs((hourly_settle - range_settle).total_seconds() / 60)
        print(f"\nWARNING: Settlement times differ by {time_diff:.0f} minutes!")
        print(f"  Hourly: {hourly_settle}")
        print(f"  Range:  {range_settle}")
        if time_diff > 5:
            print("  Arbitrage may not be riskless due to time mismatch")

    print("\nScanning for arbitrage opportunities...")
    opportunities = find_arbitrage_opportunities(hourly_strikes, range_markets)

    print_arbitrage_report(btc_price, hourly_info, range_info, opportunities)

    return {
        'btc_price': btc_price,
        'hourly_info': hourly_info,
        'range_info': range_info,
        'opportunities': opportunities
    }


def lambda_handler(event, context):
    """Lambda handler for arbitrage scanner."""
    result = scan_for_arbitrage()

    if result and result['opportunities']:
        best = result['opportunities'][0]
        mins = result['hourly_info']['mins_to_settle']
        btc_price = result['btc_price']

        # Build email content
        subject = f"BTC Arbitrage: {best['profit_cents']}c profit - {best['range']}"

        body_text = f"""BTC ARBITRAGE OPPORTUNITY FOUND!

Profit: {best['profit_cents']}c per contract (RISKLESS)
Range: {best['range']}
Type: {best['type']}
BTC Price: ${btc_price:,.2f}
Settles in: {mins} minutes

TRADES TO EXECUTE:
"""
        for trade in best['trades']:
            body_text += f"  - {trade}\n"

        body_text += f"""
With 100 contracts: ${best['profit_cents']:.2f} profit

Act fast - settles in {mins} minutes!
"""

        # HTML version for better formatting
        body_html = f"""
<html>
<body style="font-family: Arial, sans-serif;">
<h2 style="color: #228B22;">BTC ARBITRAGE OPPORTUNITY!</h2>

<table style="border-collapse: collapse; margin: 20px 0;">
<tr><td style="padding: 8px; font-weight: bold;">Profit:</td><td style="padding: 8px; color: #228B22; font-size: 18px;">{best['profit_cents']}c per contract</td></tr>
<tr><td style="padding: 8px; font-weight: bold;">Range:</td><td style="padding: 8px;">{best['range']}</td></tr>
<tr><td style="padding: 8px; font-weight: bold;">Type:</td><td style="padding: 8px;">{best['type']}</td></tr>
<tr><td style="padding: 8px; font-weight: bold;">BTC Price:</td><td style="padding: 8px;">${btc_price:,.2f}</td></tr>
<tr><td style="padding: 8px; font-weight: bold;">Settles in:</td><td style="padding: 8px; color: #FF6600;">{mins} minutes</td></tr>
</table>

<h3>Trades to Execute:</h3>
<ul style="background: #f5f5f5; padding: 15px 30px; border-radius: 5px;">
"""
        for trade in best['trades']:
            body_html += f"<li style='padding: 5px 0;'>{trade}</li>\n"

        body_html += f"""
</ul>

<p style="margin-top: 20px;"><strong>With 100 contracts: ${best['profit_cents']:.2f} profit</strong></p>

<p style="color: #FF6600; font-weight: bold;">Act fast - settles in {mins} minutes!</p>

</body>
</html>
"""

        send_email_alert(subject, body_text, body_html)

        return {
            'statusCode': 200,
            'body': json.dumps({
                'status': 'opportunities_found',
                'btc_price': result['btc_price'],
                'num_opportunities': len(result['opportunities']),
                'best_profit_cents': result['opportunities'][0]['profit_cents'],
                'opportunities': result['opportunities'][:5]
            })
        }
    else:
        return {
            'statusCode': 200,
            'body': json.dumps({
                'status': 'no_opportunities',
                'btc_price': result['btc_price'] if result else None
            })
        }


if __name__ == "__main__":
    scan_for_arbitrage()
