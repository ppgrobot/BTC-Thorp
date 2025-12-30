#!/bin/bash
# Deploy BTC Range Contract Bot Lambda

set -e

REGION="us-east-2"
LAMBDA_ROLE="arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):role/lambda-execution-role"
PACKAGE_DIR="lambda_package"
ZIP_FILE="btc_range_lambda.zip"

echo "=========================================="
echo "Deploying BTC Range Bot Lambda"
echo "=========================================="

# Navigate to project root
cd "$(dirname "$0")/.."

# Create deployment package
echo "Creating deployment package..."

rm -f $ZIP_FILE

TEMP_DIR=$(mktemp -d)
echo "Building in $TEMP_DIR..."

# Copy Python files
cp $PACKAGE_DIR/btc_range_bot.py $TEMP_DIR/
cp $PACKAGE_DIR/kalshi_client.py $TEMP_DIR/

# Install dependencies
echo "Installing dependencies..."
pip3 install --target $TEMP_DIR \
    --platform manylinux2014_x86_64 \
    --implementation cp \
    --python-version 3.12 \
    --only-binary=:all: \
    requests==2.31.0 \
    cryptography==42.0.0 \
    2>/dev/null || {
    echo "Warning: Could not install binary packages, trying without platform constraint..."
    pip3 install --target $TEMP_DIR \
        requests==2.31.0 \
        cryptography==42.0.0
}

# Create zip
cd $TEMP_DIR
zip -r $OLDPWD/$ZIP_FILE . -x "*.pyc" -x "__pycache__/*" -x "*.dist-info/*"
cd $OLDPWD

rm -rf $TEMP_DIR

echo "Created $ZIP_FILE ($(du -h $ZIP_FILE | cut -f1))"

# ==========================================
# Deploy Lambda
# ==========================================
LAMBDA_NAME="BTCRangeBot"

echo ""
echo "Deploying $LAMBDA_NAME..."

if aws lambda get-function --function-name $LAMBDA_NAME --region $REGION 2>/dev/null; then
    echo "Updating existing function..."
    aws lambda update-function-code \
        --function-name $LAMBDA_NAME \
        --zip-file fileb://$ZIP_FILE \
        --region $REGION

    aws lambda wait function-updated --function-name $LAMBDA_NAME --region $REGION 2>/dev/null || true

    aws lambda update-function-configuration \
        --function-name $LAMBDA_NAME \
        --runtime python3.12 \
        --timeout 60 \
        --region $REGION
else
    echo "Creating new function..."

    # Get Kalshi credentials from the existing trading bot
    KALSHI_KEY_ID=$(aws lambda get-function-configuration --function-name BTCTradingBot --region $REGION --query 'Environment.Variables.KALSHI_KEY_ID' --output text 2>/dev/null || echo "placeholder")
    KALSHI_PRIVATE_KEY=$(aws lambda get-function-configuration --function-name BTCTradingBot --region $REGION --query 'Environment.Variables.KALSHI_PRIVATE_KEY' --output text 2>/dev/null || echo "placeholder")

    aws lambda create-function \
        --function-name $LAMBDA_NAME \
        --runtime python3.12 \
        --role $LAMBDA_ROLE \
        --handler btc_range_bot.lambda_handler \
        --zip-file fileb://$ZIP_FILE \
        --timeout 60 \
        --memory-size 256 \
        --region $REGION \
        --environment "Variables={KALSHI_KEY_ID=$KALSHI_KEY_ID,KALSHI_PRIVATE_KEY=$KALSHI_PRIVATE_KEY}"
fi

echo "Waiting for function to be ready..."
aws lambda wait function-updated --function-name $LAMBDA_NAME --region $REGION 2>/dev/null || true

# ==========================================
# Copy credentials from BTCTradingBot if new
# ==========================================
echo ""
echo "Syncing credentials from BTCTradingBot..."

# Get current env vars from BTCTradingBot
EXISTING_ENV=$(aws lambda get-function-configuration \
    --function-name BTCTradingBot \
    --region $REGION \
    --query 'Environment.Variables' \
    --output json 2>/dev/null || echo '{}')

if [ "$EXISTING_ENV" != "{}" ] && [ "$EXISTING_ENV" != "null" ]; then
    aws lambda update-function-configuration \
        --function-name $LAMBDA_NAME \
        --environment "{\"Variables\": $EXISTING_ENV}" \
        --region $REGION >/dev/null 2>&1 || echo "Could not sync credentials"
    echo "Credentials synced from BTCTradingBot"
fi

aws lambda wait function-updated --function-name $LAMBDA_NAME --region $REGION 2>/dev/null || true

# ==========================================
# Set up EventBridge trigger
# ==========================================
echo ""
echo "Setting up EventBridge trigger..."

# Range contracts settle HOURLY (9pm, 10pm, 11pm, etc. EST)
# Run at minute 30 of every hour to find opportunities (like the hourly bot)
RULE_NAME="BTCRangeBot-Minute30"
echo "Creating rule: $RULE_NAME (at :30 every hour)"

aws events put-rule \
    --name $RULE_NAME \
    --schedule-expression "cron(30 * * * ? *)" \
    --state ENABLED \
    --region $REGION

# Add permission for EventBridge to invoke Lambda
aws lambda add-permission \
    --function-name $LAMBDA_NAME \
    --statement-id "EventBridge-$RULE_NAME" \
    --action "lambda:InvokeFunction" \
    --principal events.amazonaws.com \
    --source-arn "arn:aws:events:$REGION:$(aws sts get-caller-identity --query Account --output text):rule/$RULE_NAME" \
    --region $REGION 2>/dev/null || echo "Permission already exists"

# Add target
aws events put-targets \
    --rule $RULE_NAME \
    --targets "Id"="1","Arn"="arn:aws:lambda:$REGION:$(aws sts get-caller-identity --query Account --output text):function:$LAMBDA_NAME" \
    --region $REGION

echo ""
echo "=========================================="
echo "Deployment complete!"
echo "=========================================="
echo ""
echo "Lambda deployed: $LAMBDA_NAME"
echo "  - Runs at :30 every hour to find hourly range contract opportunities"
echo "  - KXBTC contracts settle every hour (9pm, 10pm, 11pm, etc. EST)"
echo ""
echo "Test the bot:"
echo "  aws lambda invoke --function-name $LAMBDA_NAME --payload '{}' output.json --region $REGION && cat output.json | jq ."
echo ""
echo "View logs:"
echo "  aws logs tail /aws/lambda/$LAMBDA_NAME --follow --region $REGION"
