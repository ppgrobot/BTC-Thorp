#!/bin/bash
# Set up ETH DynamoDB tables

set -e

REGION="us-east-2"

echo "=========================================="
echo "Setting up ETH DynamoDB Tables"
echo "=========================================="

# Create ETHPriceHistory table
echo "Creating ETHPriceHistory table..."
aws dynamodb create-table \
    --table-name ETHPriceHistory \
    --attribute-definitions \
        AttributeName=pk,AttributeType=S \
        AttributeName=sk,AttributeType=S \
    --key-schema \
        AttributeName=pk,KeyType=HASH \
        AttributeName=sk,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST \
    --region $REGION 2>/dev/null || echo "Table may already exist"

# Wait for table to be active
echo "Waiting for ETHPriceHistory table..."
aws dynamodb wait table-exists --table-name ETHPriceHistory --region $REGION 2>/dev/null || true

# Enable TTL on ETHPriceHistory
echo "Enabling TTL on ETHPriceHistory..."
aws dynamodb update-time-to-live \
    --table-name ETHPriceHistory \
    --time-to-live-specification Enabled=true,AttributeName=ttl \
    --region $REGION 2>/dev/null || echo "TTL may already be enabled"

# Create ETHTradeLog table
echo "Creating ETHTradeLog table..."
aws dynamodb create-table \
    --table-name ETHTradeLog \
    --attribute-definitions \
        AttributeName=pk,AttributeType=S \
        AttributeName=sk,AttributeType=S \
    --key-schema \
        AttributeName=pk,KeyType=HASH \
        AttributeName=sk,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST \
    --region $REGION 2>/dev/null || echo "Table may already exist"

echo "Waiting for ETHTradeLog table..."
aws dynamodb wait table-exists --table-name ETHTradeLog --region $REGION 2>/dev/null || true

# Create CryptoPositions table (shared between BTC and ETH)
echo "Creating CryptoPositions table (shared position tracking)..."
aws dynamodb create-table \
    --table-name CryptoPositions \
    --attribute-definitions \
        AttributeName=pk,AttributeType=S \
        AttributeName=sk,AttributeType=S \
    --key-schema \
        AttributeName=pk,KeyType=HASH \
        AttributeName=sk,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST \
    --region $REGION 2>/dev/null || echo "Table may already exist"

echo "Waiting for CryptoPositions table..."
aws dynamodb wait table-exists --table-name CryptoPositions --region $REGION 2>/dev/null || true

# Enable TTL on CryptoPositions
echo "Enabling TTL on CryptoPositions..."
aws dynamodb update-time-to-live \
    --table-name CryptoPositions \
    --time-to-live-specification Enabled=true,AttributeName=ttl \
    --region $REGION 2>/dev/null || echo "TTL may already be enabled"

echo ""
echo "=========================================="
echo "DynamoDB Setup Complete!"
echo "=========================================="
echo ""
echo "Tables created:"
echo "  - ETHPriceHistory (for price/volatility data)"
echo "  - ETHTradeLog (for trade history)"
echo "  - CryptoPositions (shared BTC+ETH position tracking)"
