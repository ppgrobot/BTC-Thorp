"""
Kalshi Weather Liquidity Provider Bot

Strategy: After 6pm ET when the daily high is locked in, buy YES contracts
on the winning outcome at 99¢ to redeem for $1.00.

Supports multiple cities: Philadelphia, Miami (more can be added)
"""

import json
import os
import boto3
import traceback
import requests
import re
from datetime import datetime, timedelta
from decimal import Decimal

# Import trading executor
try:
    from trading_executor import execute_liquidity_trades
except ImportError as e:
    print(f"Warning: trading_executor import failed: {e}")
    traceback.print_exc()
    execute_liquidity_trades = None


# =============================================================================
# CITY CONFIGURATION
# =============================================================================
# Each city has:
#   - kalshi_code: The Kalshi event prefix (e.g., KXHIGHPHIL)
#   - nws_url: The NWS Climatological Report URL
#   - name: Display name
#   - utc_offset: Hours behind UTC (EST=5, MST=7, etc.)
#   - settlement_hour: Local hour when daily high is locked in (typically 18 = 6pm)

CITIES = {
    'PHIL': {
        'kalshi_code': 'KXHIGHPHIL',
        'nws_url': 'https://forecast.weather.gov/product.php?site=PHI&product=CLI&issuedby=PHL',
        'name': 'Philadelphia',
        'utc_offset': 5,  # EST (winter)
        'settlement_hour': 18,  # 6pm ET
    },
    'MIA': {
        'kalshi_code': 'KXHIGHMIA',
        'nws_url': 'https://forecast.weather.gov/product.php?site=MFL&product=CLI&issuedby=MIA',
        'name': 'Miami',
        'utc_offset': 5,  # EST (winter)
        'settlement_hour': 18,  # 6pm ET
    },
    'DEN': {
        'kalshi_code': 'KXHIGHDEN',
        'nws_url': 'https://forecast.weather.gov/product.php?site=BOU&product=CLI&issuedby=DEN',
        'name': 'Denver',
        'utc_offset': 7,  # MST (winter)
        'settlement_hour': 18,  # 6pm MT
    },
    'NYC': {
        'kalshi_code': 'KXHIGHNY',
        'nws_url': 'https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC',
        'name': 'New York City',
        'utc_offset': 5,  # EST (winter)
        'settlement_hour': 18,  # 6pm ET
    },
    'AUS': {
        'kalshi_code': 'KXHIGHAUS',
        'nws_url': 'https://forecast.weather.gov/product.php?site=EWX&product=CLI&issuedby=AUS',
        'name': 'Austin',
        'utc_offset': 6,  # CST (winter)
        'settlement_hour': 18,  # 6pm CT
    },
    'CHI': {
        'kalshi_code': 'KXHIGHCHI',
        'nws_url': 'https://forecast.weather.gov/product.php?site=LOT&product=CLI&issuedby=MDW',
        'name': 'Chicago',
        'utc_offset': 6,  # CST (winter)
        'settlement_hour': 19,  # 7pm CT
    },
}


class DecimalEncoder(json.JSONEncoder):
    """Helper class to convert Decimal and datetime to JSON serializable formats"""
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        elif isinstance(o, datetime):
            return o.isoformat()
        return super(DecimalEncoder, self).default(o)


def get_et_time():
    """Get current Eastern Time (accounting for DST roughly)"""
    utc_now = datetime.utcnow()
    month = utc_now.month
    if 3 <= month <= 11:
        return utc_now - timedelta(hours=4)  # EDT
    else:
        return utc_now - timedelta(hours=5)  # EST


def is_after_settlement_time():
    """Check if it's after 6pm ET (when daily high is typically locked in)"""
    et_time = get_et_time()
    return et_time.hour >= 18


def get_city_local_time(city_code):
    """Get the local time for a specific city"""
    city = CITIES.get(city_code)
    if not city:
        return None

    utc_now = datetime.utcnow()
    utc_offset = city.get('utc_offset', 5)  # Default to EST
    return utc_now - timedelta(hours=utc_offset)


def is_city_ready_for_settlement(city_code):
    """
    Check if a city's daily high is locked in (after local settlement hour).
    Each city has its own timezone and settlement time.
    """
    city = CITIES.get(city_code)
    if not city:
        return False

    local_time = get_city_local_time(city_code)
    settlement_hour = city.get('settlement_hour', 18)

    is_ready = local_time.hour >= settlement_hour
    local_time_str = local_time.strftime('%H:%M')

    if not is_ready:
        print(f"  {city['name']}: {local_time_str} local - waiting until {settlement_hour}:00")
    else:
        print(f"  {city['name']}: {local_time_str} local - ready for settlement")

    return is_ready


def get_kalshi_event_id(city_code):
    """Generate today's Kalshi event ID for a given city"""
    city = CITIES.get(city_code)
    if not city:
        return None

    et_time = get_et_time()
    year = et_time.strftime('%y')
    month = et_time.strftime('%b').upper()
    day = et_time.strftime('%d')
    return f"{city['kalshi_code']}-{year}{month}{day}"


def get_nws_high_temperature(city_code):
    """
    Fetch the official high temperature from NWS Climatological Report.
    This is the same source Kalshi uses for settlement.

    The report format is:
    TEMPERATURE (F)
     TODAY
      MAXIMUM         43   3:30 PM  72    2001  49     -6       37
    """
    city = CITIES.get(city_code)
    if not city:
        return None

    try:
        url = city['nws_url']
        response = requests.get(url, timeout=10)

        if response.status_code != 200:
            print(f"Error fetching NWS data for {city['name']}: {response.status_code}")
            return None

        text = response.text

        # The format is: "MAXIMUM" followed by spaces, then the observed value
        # Example: "  MAXIMUM         43   3:30 PM"
        pattern = r'MAXIMUM\s+(\d+)\s+\d+:\d+\s*[AP]M'
        match = re.search(pattern, text, re.IGNORECASE)

        if match:
            high_temp = int(match.group(1))
            print(f"NWS {city['name']} high temperature: {high_temp}°F")
            return high_temp

        # Fallback: try simpler pattern
        pattern2 = r'MAXIMUM\s+(\d+)'
        match2 = re.search(pattern2, text, re.IGNORECASE)
        if match2:
            high_temp = int(match2.group(1))
            print(f"NWS {city['name']} high temperature (fallback): {high_temp}°F")
            return high_temp

        print(f"Could not parse high temperature from NWS report for {city['name']}")
        return None

    except Exception as e:
        print(f"Error fetching NWS high temperature for {city['name']}: {e}")
        return None


def get_winning_contract_for_temp(temp_f):
    """
    Given a temperature, return which B-contract it falls into.

    Kalshi B-contracts cover 2-degree ranges starting from odd numbers:
    B41.5 = 41° to 42°
    B43.5 = 43° to 44°
    B45.5 = 45° to 46°
    etc.
    """
    if temp_f is None:
        return None

    temp = int(round(temp_f))

    # B-contracts cover 2-degree ranges starting from odd numbers
    # Odd temp (41) -> B41.5 (41-42°)
    # Even temp (42) -> B41.5 (42 is in 41-42° range)
    # Odd temp (43) -> B43.5 (43-44°)
    # Even temp (44) -> B43.5 (44 is in 43-44° range)
    if temp % 2 == 0:
        bucket = temp - 0.5  # Even: subtract 0.5
    else:
        bucket = temp + 0.5  # Odd: add 0.5

    return f"B{bucket}"


def get_kalshi_market_data(city_code):
    """Get market data from Kalshi API for a given city's high temperature"""
    try:
        event_id = get_kalshi_event_id(city_code)
        if not event_id:
            return []

        url = f"https://api.elections.kalshi.com/trade-api/v2/events/{event_id}"
        print(f"Fetching market data for: {event_id}")

        response = requests.get(url, headers={'Accept': 'application/json'})

        if response.status_code == 200:
            data = response.json()
            markets = data.get('markets', [])
            print(f"Retrieved {len(markets)} markets for {CITIES[city_code]['name']}")

            contracts = []
            for market in markets:
                ticker = market.get('ticker')
                if not ticker:
                    continue

                parts = ticker.split('-')
                contract_code = parts[2] if len(parts) >= 3 else ticker

                contracts.append({
                    'ticker': ticker,
                    'contract_code': contract_code,
                    'title': market.get('title', ''),
                    'yes_bid': market.get('yes_bid', 0),
                    'yes_ask': market.get('yes_ask', 0),
                    'no_bid': market.get('no_bid', 0),
                    'no_ask': market.get('no_ask', 0),
                    'volume': market.get('volume', 0),
                    'status': market.get('status', ''),
                    'city': city_code,
                    'floor_strike': market.get('floor_strike'),
                    'cap_strike': market.get('cap_strike'),
                    'strike_type': market.get('strike_type', ''),
                })

            return contracts

        print(f"Error getting market data: {response.status_code} - {response.text}")
        return []
    except Exception as e:
        print(f"Exception getting market data: {e}")
        traceback.print_exc()
        return []


def find_winning_contract_for_city(city_code, force=False):
    """
    Find the winning contract for a specific city using BOTH market data AND NWS verification.
    Only processes cities that are past their local settlement time.
    """
    city = CITIES.get(city_code)
    if not city:
        print(f"Unknown city code: {city_code}")
        return None

    city_name = city['name']
    print(f"\n{'='*50}")
    print(f"Processing {city_name}")
    print(f"{'='*50}")

    # Check if this city is ready for settlement
    if not force and not is_city_ready_for_settlement(city_code):
        print(f"Skipping {city_name} - not yet at settlement time")
        return None

    contracts = get_kalshi_market_data(city_code)

    if not contracts:
        print(f"No market data for {city_name}")
        return None

    # Method A: Find contract with highest YES bid (must be >= 95¢)
    market_winner = None
    highest_yes_bid = 0

    for contract in contracts:
        yes_bid = contract.get('yes_bid', 0)
        if yes_bid > highest_yes_bid and yes_bid >= 95:
            highest_yes_bid = yes_bid
            market_winner = contract

    if not market_winner:
        print(f"No winning contract from market data for {city_name} (no YES bid >= 95¢)")
        return None

    market_contract_code = market_winner['contract_code']
    print(f"Market winner: {market_contract_code} (YES bid: {highest_yes_bid}¢)")

    # Method B: Get NWS official high temperature and verify it falls in contract range
    nws_high = get_nws_high_temperature(city_code)

    if nws_high is not None:
        floor_strike = market_winner.get('floor_strike')
        cap_strike = market_winner.get('cap_strike')
        strike_type = market_winner.get('strike_type', '')

        # Verify NWS temp falls within contract's range
        temp_in_range = False
        range_desc = ""

        if strike_type == 'between' and floor_strike is not None and cap_strike is not None:
            # B-contracts: between floor and cap (inclusive)
            temp_in_range = floor_strike <= nws_high <= cap_strike
            range_desc = f"{floor_strike}-{cap_strike}°"
        elif strike_type == 'less' and cap_strike is not None:
            # T-contracts (less than): temp < cap
            temp_in_range = nws_high < cap_strike
            range_desc = f"<{cap_strike}°"
        elif strike_type == 'greater' and floor_strike is not None:
            # T-contracts (greater than): temp > floor
            temp_in_range = nws_high > floor_strike
            range_desc = f">{floor_strike}°"
        else:
            # Fallback to contract code comparison
            nws_contract_code = get_winning_contract_for_temp(nws_high)
            temp_in_range = market_contract_code == nws_contract_code
            range_desc = f"calculated as {nws_contract_code}"

        print(f"NWS high: {nws_high}°F | Contract range: {range_desc}")

        if not temp_in_range:
            print(f"⚠️ MISMATCH for {city_name}! NWS temp {nws_high}°F not in {market_contract_code} range ({range_desc})")
            print("Skipping trade for safety - sources disagree")
            return None

        print(f"✅ VERIFIED: NWS {nws_high}°F is within {market_contract_code} range ({range_desc})")
    else:
        print(f"⚠️ Could not verify {city_name} with NWS - proceeding with market data only")

    winning_ticker = market_winner['ticker']
    yes_ask = market_winner.get('yes_ask', 0)

    print(f"Winner: {winning_ticker}")
    print(f"  YES bid: {highest_yes_bid}¢ | YES ask: {yes_ask}¢")

    return {
        'ticker': winning_ticker,
        'contract_code': market_contract_code,
        'title': market_winner['title'],
        'side': 'YES',
        'yes_bid': highest_yes_bid,
        'yes_ask': yes_ask,
        'nws_high': nws_high,
        'verified': nws_high is not None,
        'city': city_code,
        'city_name': city_name,
    }


def find_all_winning_contracts(force=False):
    """Find winning contracts for all configured cities that are ready"""
    opportunities = []

    print("\nChecking settlement times for all cities:")
    for city_code in CITIES:
        winner = find_winning_contract_for_city(city_code, force=force)
        if winner:
            opportunities.append(winner)

    return opportunities


def lambda_handler(event, context):
    """
    Main Lambda handler - Liquidity Provider Strategy

    Runs after 6pm ET to find and execute liquidity opportunities
    on settled weather contracts for all configured cities.
    """
    try:
        print(f"Event: {json.dumps(event)}")
        et_time = get_et_time()
        print(f"Current ET time: {et_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Configured cities: {', '.join([CITIES[c]['name'] for c in CITIES])}")

        # Check if this is a cancel all orders request
        if event.get('action') == 'cancel_all_orders':
            print("Cancel all orders action requested")
            from cancel_all_open_orders import cancel_all_open_orders
            cancel_all_open_orders()
            return {
                'statusCode': 200,
                'body': json.dumps('All open orders cancelled')
            }

        # Force flag bypasses settlement time checks
        force = event.get('force', False)
        if force:
            print("Force flag set - bypassing settlement time checks")

        # Find winning contracts for all cities (each city checks its own settlement time)
        opportunities = find_all_winning_contracts(force=force)
        print(f"\nFound {len(opportunities)} winning contract(s) across all cities")

        # Place resting bids at 99¢
        placed_orders = []
        if opportunities and execute_liquidity_trades:
            try:
                print("\nPlacing resting bids at 99¢...")
                placed_orders = execute_liquidity_trades(
                    opportunities=opportunities,
                    max_daily_budget_per_contract=10.50,  # $10.50 per contract per day (~10 contracts)
                    bid_price=99,  # Bid at 99¢
                )
                print(f"Placed {len(placed_orders)} orders")
            except Exception as e:
                print(f"Error placing orders: {e}")
                traceback.print_exc()

        return {
            'statusCode': 200,
            'body': json.dumps({
                'status': 'success',
                'et_time': et_time.isoformat(),
                'cities_processed': list(CITIES.keys()),
                'winning_contracts': opportunities,
                'placed_orders': placed_orders,
                'orders_count': len(placed_orders),
            }, cls=DecimalEncoder)
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
