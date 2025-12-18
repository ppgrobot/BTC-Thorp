#!/bin/bash
# Create DynamoDB table for ETH price history and volatility

set -e

REGION="us-east-2"
TABLE_NAME="ETHPriceHistory"

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

# Enable TTL for automatic cleanup
echo "Enabling TTL on 'ttl' attribute..."
aws dynamodb update-time-to-live \
    --table-name $TABLE_NAME \
    --time-to-live-specification "Enabled=true, AttributeName=ttl" \
    --region $REGION

echo "Done! Table $TABLE_NAME created successfully."
