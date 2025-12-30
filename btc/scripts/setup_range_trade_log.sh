#!/bin/bash
# Create DynamoDB table for BTC Range trade logs

set -e

REGION="us-east-2"
TABLE_NAME="BTCRangeTradeLog"

echo "Creating DynamoDB table: $TABLE_NAME"

# Check if table exists
if aws dynamodb describe-table --table-name $TABLE_NAME --region $REGION 2>/dev/null; then
    echo "Table $TABLE_NAME already exists"
    exit 0
fi

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
echo "  pk: TRADE (for all trades)"
echo "  sk: timestamp (ISO format)"
echo ""
echo "Trade record fields:"
echo "  - contract_ticker"
echo "  - side (NO)"
echo "  - quantity"
echo "  - price_cents"
echo "  - total_cost"
echo "  - btc_price"
echo "  - floor_strike"
echo "  - cap_strike"
echo "  - model_prob"
echo "  - market_prob"
echo "  - edge"
echo "  - kelly_fraction"
echo "  - balance_before"
echo "  - order_id"
echo "  - status"
echo "  - potential_profit"
echo "  - minutes_to_settlement"
echo "  - volatility"
