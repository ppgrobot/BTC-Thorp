"""
ETH Price Collector Lambda

Runs every minute to:
1. Fetch ETH price from Coinbase
2. Store in DynamoDB with timestamp
3. Calculate rolling volatility metrics (15m, 30m, 60m, 90m, 120m)
4. Store latest volatility for trading bot to check
"""

import json
import boto3
import requests
from datetime import datetime, timedelta
from decimal import Decimal
import statistics


# DynamoDB table name
TABLE_NAME = "ETHPriceHistory"

# Volatility windows (in minutes)
VOL_WINDOWS = [15, 30, 60, 90, 120]


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        elif isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)


def get_coinbase_eth_price():
    """Fetch current ETH price from Coinbase API."""
    try:
        url = "https://api.coinbase.com/v2/prices/ETH-USD/spot"
        response = requests.get(url, timeout=10)

        if response.status_code != 200:
            print(f"Error fetching Coinbase price: {response.status_code}")
            return None

        data = response.json()
        price = float(data['data']['amount'])
        return price

    except Exception as e:
        print(f"Error fetching ETH price: {e}")
        return None


def store_price(dynamodb, price, timestamp):
    """
    Store price in DynamoDB.

    Schema:
    - pk: "PRICE#YYYYMMDD" (partition by day for efficient queries)
    - sk: "HH:MM:SS" (sort by time)
    - price: decimal
    - timestamp_utc: ISO8601 string
    - ttl: Unix timestamp for auto-deletion (7 days)
    """
    table = dynamodb.Table(TABLE_NAME)

    date_str = timestamp.strftime('%Y%m%d')
    time_str = timestamp.strftime('%H:%M:%S')

    # TTL: delete after 7 days
    ttl = int((timestamp + timedelta(days=7)).timestamp())

    item = {
        'pk': f"PRICE#{date_str}",
        'sk': time_str,
        'price': Decimal(str(price)),
        'timestamp_utc': timestamp.isoformat(),
        'ttl': ttl
    }

    table.put_item(Item=item)
    print(f"Stored price: ${price:,.2f} at {timestamp.isoformat()}")


def get_recent_prices(dynamodb, minutes):
    """
    Get prices from the last N minutes.
    Returns list of (timestamp, price) tuples sorted by time.
    """
    table = dynamodb.Table(TABLE_NAME)

    now = datetime.utcnow()
    start_time = now - timedelta(minutes=minutes)

    prices = []

    # Query today's prices
    today_pk = f"PRICE#{now.strftime('%Y%m%d')}"
    today_start_sk = start_time.strftime('%H:%M:%S') if start_time.date() == now.date() else "00:00:00"

    response = table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key('pk').eq(today_pk) &
                              boto3.dynamodb.conditions.Key('sk').gte(today_start_sk)
    )

    for item in response.get('Items', []):
        prices.append({
            'timestamp': item['timestamp_utc'],
            'price': float(item['price'])
        })

    # If we need data from yesterday (e.g., it's 00:05 and we need 30 min of data)
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

    return prices


def calculate_volatility(prices):
    """
    Calculate volatility metrics from a list of prices.

    Returns dict with:
    - returns: list of minute-to-minute percent returns
    - std_dev: standard deviation of returns (volatility)
    - range_pct: (max - min) / avg as percentage
    - max_move: largest single-minute move
    """
    if len(prices) < 2:
        return None

    price_values = [p['price'] for p in prices]

    # Calculate minute-to-minute returns (percent)
    returns = []
    for i in range(1, len(price_values)):
        ret = (price_values[i] - price_values[i-1]) / price_values[i-1] * 100
        returns.append(ret)

    if not returns:
        return None

    avg_price = statistics.mean(price_values)

    return {
        'std_dev': statistics.stdev(returns) if len(returns) > 1 else 0,
        'range_pct': (max(price_values) - min(price_values)) / avg_price * 100,
        'max_move': max(abs(r) for r in returns),
        'sample_count': len(prices),
        'avg_price': avg_price,
        'min_price': min(price_values),
        'max_price': max(price_values),
    }


def store_volatility(dynamodb, vol_metrics):
    """
    Store latest volatility metrics in DynamoDB.

    Schema:
    - pk: "VOL"
    - sk: "LATEST"
    - vol_15m, vol_30m, etc.
    - updated_at: ISO8601
    """
    table = dynamodb.Table(TABLE_NAME)

    item = {
        'pk': 'VOL',
        'sk': 'LATEST',
        'updated_at': datetime.utcnow().isoformat(),
    }

    for window, metrics in vol_metrics.items():
        if metrics:
            item[f'vol_{window}m_std'] = Decimal(str(round(metrics['std_dev'], 4)))
            item[f'vol_{window}m_range'] = Decimal(str(round(metrics['range_pct'], 4)))
            item[f'vol_{window}m_max_move'] = Decimal(str(round(metrics['max_move'], 4)))
            item[f'vol_{window}m_samples'] = metrics['sample_count']

    table.put_item(Item=item)
    print(f"Stored volatility metrics")


def lambda_handler(event, context):
    """
    Main Lambda handler - runs every minute.
    """
    try:
        print(f"ETH Price Collector starting at {datetime.utcnow().isoformat()}")

        # Get current ETH price
        price = get_coinbase_eth_price()
        if not price:
            return {
                'statusCode': 500,
                'body': json.dumps({'error': 'Could not fetch ETH price'})
            }

        print(f"ETH Price: ${price:,.2f}")

        # Store in DynamoDB
        dynamodb = boto3.resource('dynamodb')
        timestamp = datetime.utcnow()
        store_price(dynamodb, price, timestamp)

        # Calculate volatility for each window
        vol_metrics = {}
        for window in VOL_WINDOWS:
            prices = get_recent_prices(dynamodb, window)
            vol = calculate_volatility(prices)
            vol_metrics[window] = vol

            if vol:
                print(f"  {window}m: std={vol['std_dev']:.4f}%, range={vol['range_pct']:.4f}%, samples={vol['sample_count']}")
            else:
                print(f"  {window}m: insufficient data")

        # Store volatility metrics
        store_volatility(dynamodb, vol_metrics)

        return {
            'statusCode': 200,
            'body': json.dumps({
                'price': price,
                'timestamp': timestamp.isoformat(),
                'volatility': {k: v for k, v in vol_metrics.items() if v},
            }, cls=DecimalEncoder)
        }

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }


# For local testing
if __name__ == "__main__":
    # Just test price fetch without DynamoDB
    price = get_coinbase_eth_price()
    print(f"Current ETH price: ${price:,.2f}")
