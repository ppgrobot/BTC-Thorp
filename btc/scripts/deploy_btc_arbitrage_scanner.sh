#!/bin/bash
# Deploy BTC Arbitrage Scanner Lambda

set -e

REGION="us-east-2"
LAMBDA_ROLE="arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):role/lambda-execution-role"
PACKAGE_DIR="lambda_package"
ZIP_FILE="btc_arbitrage_lambda.zip"

# Email for alerts (default to ppglaw@gmail.com)
ALERT_EMAIL="${ALERT_EMAIL:-ppglaw@gmail.com}"
echo "Email alerts will be sent to: $ALERT_EMAIL"

echo "=========================================="
echo "Deploying BTC Arbitrage Scanner Lambda"
echo "=========================================="

# Navigate to project root
cd "$(dirname "$0")/.."

# Create deployment package
echo "Creating deployment package..."

rm -f $ZIP_FILE

TEMP_DIR=$(mktemp -d)
echo "Building in $TEMP_DIR..."

# Copy Python files
cp $PACKAGE_DIR/btc_arbitrage_scanner.py $TEMP_DIR/

# Install dependencies
echo "Installing dependencies..."
pip3 install --target $TEMP_DIR \
    --platform manylinux2014_x86_64 \
    --implementation cp \
    --python-version 3.12 \
    --only-binary=:all: \
    requests==2.31.0 \
    2>/dev/null || {
    echo "Warning: Could not install binary packages, trying without platform constraint..."
    pip3 install --target $TEMP_DIR requests==2.31.0
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
LAMBDA_NAME="BTCArbitrageScanner"

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
        --environment "Variables={ALERT_EMAIL=$ALERT_EMAIL}" \
        --region $REGION
else
    echo "Creating new function..."

    aws lambda create-function \
        --function-name $LAMBDA_NAME \
        --runtime python3.12 \
        --role $LAMBDA_ROLE \
        --handler btc_arbitrage_scanner.lambda_handler \
        --environment "Variables={ALERT_EMAIL=$ALERT_EMAIL}" \
        --zip-file fileb://$ZIP_FILE \
        --timeout 60 \
        --memory-size 256 \
        --region $REGION
fi

echo "Waiting for function to be ready..."
aws lambda wait function-updated --function-name $LAMBDA_NAME --region $REGION 2>/dev/null || true

# ==========================================
# Set up EventBridge trigger
# ==========================================
echo ""
echo "Setting up EventBridge trigger..."

# Run every 5 minutes to catch arbitrage opportunities quickly
RULE_NAME="BTCArbitrageScanner-Every5Min"
echo "Creating rule: $RULE_NAME (every 5 minutes)"

aws events put-rule \
    --name $RULE_NAME \
    --schedule-expression "rate(5 minutes)" \
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
echo "  - Runs every 5 minutes to scan for arbitrage"
echo "  - Looks for mispricing between KXBTC (range) and KXBTCD (hourly) contracts"
echo "  - Email alerts: $ALERT_EMAIL"
echo ""
echo "Test the scanner:"
echo "  aws lambda invoke --function-name $LAMBDA_NAME --payload '{}' output.json --region $REGION && cat output.json | jq ."
echo ""
echo "View logs:"
echo "  aws logs tail /aws/lambda/$LAMBDA_NAME --follow --region $REGION"
