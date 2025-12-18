#!/bin/bash
# Deploy Price History Cleanup Lambda

set -e

REGION="us-east-2"
LAMBDA_NAME="PriceHistoryCleanup"
LAMBDA_ROLE="arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):role/lambda-execution-role"
SCRIPT_DIR="$(dirname "$0")"

echo "=========================================="
echo "Deploying Price History Cleanup Lambda"
echo "=========================================="

# Create deployment package
echo "Creating deployment package..."
cd "$SCRIPT_DIR"
rm -f cleanup_lambda.zip
zip cleanup_lambda.zip price_history_cleanup.py

# Deploy Lambda
echo ""
echo "Deploying Lambda function..."

if aws lambda get-function --function-name $LAMBDA_NAME --region $REGION 2>/dev/null; then
    echo "Updating existing function..."
    aws lambda update-function-code \
        --function-name $LAMBDA_NAME \
        --zip-file fileb://cleanup_lambda.zip \
        --region $REGION > /dev/null
else
    echo "Creating new function..."
    aws lambda create-function \
        --function-name $LAMBDA_NAME \
        --runtime python3.12 \
        --role $LAMBDA_ROLE \
        --handler price_history_cleanup.lambda_handler \
        --zip-file fileb://cleanup_lambda.zip \
        --timeout 60 \
        --memory-size 128 \
        --region $REGION > /dev/null
fi

echo "Waiting for function to be ready..."
aws lambda wait function-active --function-name $LAMBDA_NAME --region $REGION 2>/dev/null || true
sleep 3

# Set up hourly EventBridge trigger
echo ""
echo "Setting up hourly EventBridge trigger..."

RULE_NAME="PriceHistoryCleanup-Hourly"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

aws events put-rule \
    --name $RULE_NAME \
    --schedule-expression "rate(1 hour)" \
    --state ENABLED \
    --region $REGION > /dev/null

# Add permission for EventBridge to invoke Lambda
aws lambda add-permission \
    --function-name $LAMBDA_NAME \
    --statement-id "EventBridge-$RULE_NAME" \
    --action "lambda:InvokeFunction" \
    --principal events.amazonaws.com \
    --source-arn "arn:aws:events:$REGION:$ACCOUNT_ID:rule/$RULE_NAME" \
    --region $REGION 2>/dev/null || echo "Permission already exists"

# Add target
aws events put-targets \
    --rule $RULE_NAME \
    --targets "Id"="1","Arn"="arn:aws:lambda:$REGION:$ACCOUNT_ID:function:$LAMBDA_NAME" \
    --region $REGION > /dev/null

# Clean up
rm -f cleanup_lambda.zip

echo ""
echo "=========================================="
echo "Deployment complete!"
echo "=========================================="
echo ""
echo "Lambda: $LAMBDA_NAME"
echo "Schedule: Every hour"
echo "Retention: 180 minutes"
echo "Tables: BTCPriceHistory, ETHPriceHistory"
echo ""
echo "Test with:"
echo "  aws lambda invoke --function-name $LAMBDA_NAME output.json && cat output.json"
