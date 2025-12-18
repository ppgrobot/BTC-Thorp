#!/bin/bash
# Create DynamoDB table for BTC trade logs

set -e

REGION="us-east-2"
TABLE_NAME="BTCTradeLog"

echo "Creating DynamoDB table: $TABLE_NAME"

aws dynamodb create-table \
    --table-name $TABLE_NAME \
    --attribute-definitions \
        AttributeName=pk,AttributeType=S \
        AttributeName=sk,AttributeType=S \
    --key-schema \
        AttributeName=pk,KeyType=HASH \
        AttributeName=sk,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST \
    --region $REGION

echo "Waiting for table to be active..."
aws dynamodb wait table-exists --table-name $TABLE_NAME --region $REGION

echo "Done! Table $TABLE_NAME created successfully."
echo ""
echo "Schema:"
echo "  pk: TRADE (for all trades) or DATE#YYYY-MM-DD (for daily summary)"
echo "  sk: timestamp (ISO format)"
echo ""
echo "Trade record fields:"
echo "  - contract_ticker"
echo "  - side (yes/no)"
echo "  - quantity"
echo "  - price_cents"
echo "  - total_cost"
echo "  - btc_price"
echo "  - target_bucket"
echo "  - confidence"
echo "  - balance_before"
echo "  - balance_after"
echo "  - order_id"
echo "  - status (executed/failed/rejected)"
