"""
Price History Cleanup Lambda
Removes price data older than 180 minutes from BTC and ETH DynamoDB tables.
Runs hourly to keep tables lean.
"""

import boto3
from datetime import datetime, timedelta
from decimal import Decimal

# Tables to clean
TABLES = ["BTCPriceHistory", "ETHPriceHistory"]
RETENTION_MINUTES = 180


def lambda_handler(event, context):
    """Clean up old price history records from DynamoDB tables."""
    dynamodb = boto3.resource('dynamodb', region_name='us-east-1')

    cutoff_time = datetime.utcnow() - timedelta(minutes=RETENTION_MINUTES)
    cutoff_ts = cutoff_time.strftime('%Y-%m-%dT%H:%M:%S')

    total_deleted = 0
    results = {}

    for table_name in TABLES:
        try:
            table = dynamodb.Table(table_name)
            deleted_count = cleanup_table(table, cutoff_ts)
            results[table_name] = {"deleted": deleted_count, "status": "success"}
            total_deleted += deleted_count
            print(f"{table_name}: Deleted {deleted_count} old records")
        except Exception as e:
            results[table_name] = {"deleted": 0, "status": "error", "error": str(e)}
            print(f"{table_name}: Error - {e}")

    return {
        'statusCode': 200,
        'body': {
            'cutoff_time': cutoff_ts,
            'retention_minutes': RETENTION_MINUTES,
            'total_deleted': total_deleted,
            'tables': results
        }
    }


def cleanup_table(table, cutoff_ts):
    """Delete all records older than cutoff timestamp from a table."""
    deleted_count = 0

    # Scan for old PRICE records (pk = "PRICE", sk = timestamp)
    response = table.scan(
        FilterExpression='pk = :pk AND sk < :cutoff',
        ExpressionAttributeValues={
            ':pk': 'PRICE',
            ':cutoff': cutoff_ts
        },
        ProjectionExpression='pk, sk'
    )

    items_to_delete = response.get('Items', [])

    # Handle pagination
    while 'LastEvaluatedKey' in response:
        response = table.scan(
            FilterExpression='pk = :pk AND sk < :cutoff',
            ExpressionAttributeValues={
                ':pk': 'PRICE',
                ':cutoff': cutoff_ts
            },
            ProjectionExpression='pk, sk',
            ExclusiveStartKey=response['LastEvaluatedKey']
        )
        items_to_delete.extend(response.get('Items', []))

    # Batch delete old items
    with table.batch_writer() as batch:
        for item in items_to_delete:
            batch.delete_item(Key={'pk': item['pk'], 'sk': item['sk']})
            deleted_count += 1

    return deleted_count


if __name__ == "__main__":
    # Local testing
    result = lambda_handler({}, None)
    print(f"\nResult: {result}")
