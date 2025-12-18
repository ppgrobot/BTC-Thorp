"""
BTC Volatility API Lambda

Returns the latest 15, 30, 60, 90, and 120 minute realized volatility metrics.
Called via API Gateway with Bearer token authorization.
"""

import json
import os
import boto3
from decimal import Decimal

TABLE_NAME = "BTCPriceHistory"


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


def get_volatility():
    """Fetch latest volatility metrics from DynamoDB."""
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(TABLE_NAME)

    response = table.get_item(
        Key={
            'pk': 'VOL',
            'sk': 'LATEST'
        }
    )

    item = response.get('Item')
    if not item:
        return None

    return {
        'updated_at': item.get('updated_at'),
        'volatility': {
            '15m': {
                'std_dev': float(item.get('vol_15m_std', 0)),
                'range_pct': float(item.get('vol_15m_range', 0)),
                'max_move': float(item.get('vol_15m_max_move', 0)),
                'samples': int(item.get('vol_15m_samples', 0)),
            },
            '30m': {
                'std_dev': float(item.get('vol_30m_std', 0)),
                'range_pct': float(item.get('vol_30m_range', 0)),
                'max_move': float(item.get('vol_30m_max_move', 0)),
                'samples': int(item.get('vol_30m_samples', 0)),
            },
            '60m': {
                'std_dev': float(item.get('vol_60m_std', 0)),
                'range_pct': float(item.get('vol_60m_range', 0)),
                'max_move': float(item.get('vol_60m_max_move', 0)),
                'samples': int(item.get('vol_60m_samples', 0)),
            },
            '90m': {
                'std_dev': float(item.get('vol_90m_std', 0)),
                'range_pct': float(item.get('vol_90m_range', 0)),
                'max_move': float(item.get('vol_90m_max_move', 0)),
                'samples': int(item.get('vol_90m_samples', 0)),
            },
            '120m': {
                'std_dev': float(item.get('vol_120m_std', 0)),
                'range_pct': float(item.get('vol_120m_range', 0)),
                'max_move': float(item.get('vol_120m_max_move', 0)),
                'samples': int(item.get('vol_120m_samples', 0)),
            },
        }
    }


def lambda_handler(event, context):
    """
    API Gateway Lambda handler.

    Expects Bearer token in Authorization header.
    Returns volatility data as JSON.
    """
    # Get valid tokens from environment
    valid_tokens = []
    for key in os.environ:
        if key.startswith('API_BEARER_TOKEN'):
            valid_tokens.append(os.environ[key])

    # Extract authorization header
    headers = event.get('headers', {}) or {}
    auth_header = headers.get('Authorization') or headers.get('authorization', '')

    # Validate bearer token
    if valid_tokens:
        if not auth_header.startswith('Bearer '):
            return {
                'statusCode': 401,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'error': 'Missing Bearer token'})
            }

        provided_token = auth_header[7:]  # Remove 'Bearer ' prefix
        if provided_token not in valid_tokens:
            return {
                'statusCode': 403,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'error': 'Invalid token'})
            }

    # Fetch volatility data
    try:
        data = get_volatility()

        if not data:
            return {
                'statusCode': 404,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'error': 'No volatility data available yet'})
            }

        return {
            'statusCode': 200,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps(data, cls=DecimalEncoder)
        }

    except Exception as e:
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'error': str(e)})
        }
