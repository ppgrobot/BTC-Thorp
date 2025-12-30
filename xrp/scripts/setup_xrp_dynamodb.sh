#!/bin/bash
# Set up XRP DynamoDB tables

set -e

REGION="us-east-2"

echo "=========================================="
echo "Setting up XRP DynamoDB Tables"
echo "=========================================="

# Create XRPPriceHistory table
echo "Creating XRPPriceHistory table..."
aws dynamodb create-table \
    --table-name XRPPriceHistory \
    --attribute-definitions \
        AttributeName=pk,AttributeType=S \
        AttributeName=sk,AttributeType=S \
    --key-schema \
        AttributeName=pk,KeyType=HASH \
        AttributeName=sk,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST \
    --region $REGION 2>/dev/null || echo "Table may already exist"

# Wait for table to be active
echo "Waiting for XRPPriceHistory table..."
aws dynamodb wait table-exists --table-name XRPPriceHistory --region $REGION 2>/dev/null || true

# Enable TTL on XRPPriceHistory
echo "Enabling TTL on XRPPriceHistory..."
aws dynamodb update-time-to-live \
    --table-name XRPPriceHistory \
    --time-to-live-specification Enabled=true,AttributeName=ttl \
    --region $REGION 2>/dev/null || echo "TTL may already be enabled"

# Create XRPTradeLog table
echo "Creating XRPTradeLog table..."
aws dynamodb create-table \
    --table-name XRPTradeLog \
    --attribute-definitions \
        AttributeName=pk,AttributeType=S \
        AttributeName=sk,AttributeType=S \
    --key-schema \
        AttributeName=pk,KeyType=HASH \
        AttributeName=sk,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST \
    --region $REGION 2>/dev/null || echo "Table may already exist"

echo "Waiting for XRPTradeLog table..."
aws dynamodb wait table-exists --table-name XRPTradeLog --region $REGION 2>/dev/null || true

echo ""
echo "=========================================="
echo "XRP DynamoDB Setup Complete!"
echo "=========================================="
echo ""
echo "Tables created:"
echo "  - XRPPriceHistory (for price/volatility data)"
echo "  - XRPTradeLog (for trade history)"
