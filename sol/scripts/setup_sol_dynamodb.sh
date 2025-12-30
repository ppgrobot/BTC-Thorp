#!/bin/bash
# Set up SOL DynamoDB tables

set -e

REGION="us-east-2"

echo "=========================================="
echo "Setting up SOL DynamoDB Tables"
echo "=========================================="

# Create SOLPriceHistory table
echo "Creating SOLPriceHistory table..."
aws dynamodb create-table \
    --table-name SOLPriceHistory \
    --attribute-definitions \
        AttributeName=pk,AttributeType=S \
        AttributeName=sk,AttributeType=S \
    --key-schema \
        AttributeName=pk,KeyType=HASH \
        AttributeName=sk,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST \
    --region $REGION 2>/dev/null || echo "Table may already exist"

# Wait for table to be active
echo "Waiting for SOLPriceHistory table..."
aws dynamodb wait table-exists --table-name SOLPriceHistory --region $REGION 2>/dev/null || true

# Enable TTL on SOLPriceHistory
echo "Enabling TTL on SOLPriceHistory..."
aws dynamodb update-time-to-live \
    --table-name SOLPriceHistory \
    --time-to-live-specification Enabled=true,AttributeName=ttl \
    --region $REGION 2>/dev/null || echo "TTL may already be enabled"

# Create SOLTradeLog table
echo "Creating SOLTradeLog table..."
aws dynamodb create-table \
    --table-name SOLTradeLog \
    --attribute-definitions \
        AttributeName=pk,AttributeType=S \
        AttributeName=sk,AttributeType=S \
    --key-schema \
        AttributeName=pk,KeyType=HASH \
        AttributeName=sk,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST \
    --region $REGION 2>/dev/null || echo "Table may already exist"

echo "Waiting for SOLTradeLog table..."
aws dynamodb wait table-exists --table-name SOLTradeLog --region $REGION 2>/dev/null || true

echo ""
echo "=========================================="
echo "SOL DynamoDB Setup Complete!"
echo "=========================================="
echo ""
echo "Tables created:"
echo "  - SOLPriceHistory (for price/volatility data)"
echo "  - SOLTradeLog (for trade history)"
