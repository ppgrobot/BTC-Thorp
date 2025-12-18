#!/bin/bash

# Create DynamoDB table for tracking Kalshi trades

echo "Creating KalshiTrades DynamoDB table..."

aws dynamodb create-table \
    --table-name KalshiTrades \
    --attribute-definitions \
        AttributeName=trade_id,AttributeType=S \
        AttributeName=trade_date,AttributeType=S \
    --key-schema \
        AttributeName=trade_id,KeyType=HASH \
    --global-secondary-indexes \
        "[{
            \"IndexName\": \"DateIndex\",
            \"KeySchema\": [{\"AttributeName\":\"trade_date\",\"KeyType\":\"HASH\"}],
            \"Projection\":{\"ProjectionType\":\"ALL\"},
            \"ProvisionedThroughput\": {\"ReadCapacityUnits\": 1, \"WriteCapacityUnits\": 1}
        }]" \
    --provisioned-throughput \
        ReadCapacityUnits=1,WriteCapacityUnits=1 \
    --region us-east-1

echo "Waiting for table to be created..."
aws dynamodb wait table-exists --table-name KalshiTrades --region us-east-1

echo "✅ KalshiTrades table created successfully!"
echo ""
echo "Table details:"
aws dynamodb describe-table --table-name KalshiTrades --region us-east-1 --query 'Table.[TableName,TableStatus,ItemCount]' --output table
