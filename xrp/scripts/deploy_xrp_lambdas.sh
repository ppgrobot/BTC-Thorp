#!/bin/bash
# Deploy XRP Price Collector and Trading Bot Lambdas

set -e

REGION="us-east-2"
LAMBDA_ROLE="arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):role/lambda-execution-role"
PACKAGE_DIR="lambda_package"
ZIP_FILE="xrp_lambda.zip"

echo "=========================================="
echo "Deploying XRP Lambdas"
echo "=========================================="

# Navigate to project root
cd "$(dirname "$0")/.."

# Create deployment package
echo "Creating deployment package..."

# Remove old zip if exists
rm -f $ZIP_FILE

# Create a temporary directory for the package
TEMP_DIR=$(mktemp -d)
echo "Building in $TEMP_DIR..."

# Copy Python files
cp $PACKAGE_DIR/*.py $TEMP_DIR/

# Install dependencies for Lambda (Linux x86_64, Python 3.12)
echo "Installing dependencies for Lambda..."
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

# Create zip file
cd $TEMP_DIR
zip -r $OLDPWD/$ZIP_FILE . -x "*.pyc" -x "__pycache__/*" -x "*.dist-info/*"
cd $OLDPWD

# Cleanup
rm -rf $TEMP_DIR

echo "Created $ZIP_FILE ($(du -h $ZIP_FILE | cut -f1))"

# ==========================================
# Deploy XRP Price Collector Lambda
# ==========================================
COLLECTOR_NAME="XRPPriceCollector"

echo ""
echo "Deploying $COLLECTOR_NAME..."

# Check if function exists
if aws lambda get-function --function-name $COLLECTOR_NAME --region $REGION 2>/dev/null; then
    echo "Updating existing function..."
    aws lambda update-function-code \
        --function-name $COLLECTOR_NAME \
        --zip-file fileb://$ZIP_FILE \
        --region $REGION
else
    echo "Creating new function..."
    aws lambda create-function \
        --function-name $COLLECTOR_NAME \
        --runtime python3.12 \
        --role $LAMBDA_ROLE \
        --handler xrp_price_collector.lambda_handler \
        --zip-file fileb://$ZIP_FILE \
        --timeout 30 \
        --memory-size 128 \
        --region $REGION
fi

# Wait for update to complete
echo "Waiting for function to be ready..."
aws lambda wait function-updated --function-name $COLLECTOR_NAME --region $REGION 2>/dev/null || true

# ==========================================
# Set up EventBridge trigger - every minute
# ==========================================
echo ""
echo "Setting up EventBridge trigger..."

COLLECTOR_RULE="XRPPriceCollector-EveryMinute"
echo "Creating rule: $COLLECTOR_RULE (every minute)"

aws events put-rule \
    --name $COLLECTOR_RULE \
    --schedule-expression "rate(1 minute)" \
    --state ENABLED \
    --region $REGION

# Add permission for EventBridge to invoke Lambda
aws lambda add-permission \
    --function-name $COLLECTOR_NAME \
    --statement-id "EventBridge-$COLLECTOR_RULE" \
    --action "lambda:InvokeFunction" \
    --principal events.amazonaws.com \
    --source-arn "arn:aws:events:$REGION:$(aws sts get-caller-identity --query Account --output text):rule/$COLLECTOR_RULE" \
    --region $REGION 2>/dev/null || echo "Permission already exists"

# Add target
aws events put-targets \
    --rule $COLLECTOR_RULE \
    --targets "Id"="1","Arn"="arn:aws:lambda:$REGION:$(aws sts get-caller-identity --query Account --output text):function:$COLLECTOR_NAME" \
    --region $REGION

# ==========================================
# Deploy XRP Trading Bot Lambda
# ==========================================
TRADER_NAME="XRPTradingBot"

echo ""
echo "Deploying $TRADER_NAME..."

if aws lambda get-function --function-name $TRADER_NAME --region $REGION 2>/dev/null; then
    echo "Updating existing function..."
    aws lambda update-function-code \
        --function-name $TRADER_NAME \
        --zip-file fileb://$ZIP_FILE \
        --region $REGION

    # Wait for code update before config update
    aws lambda wait function-updated --function-name $TRADER_NAME --region $REGION 2>/dev/null || true

    # Ensure runtime is Python 3.12
    aws lambda update-function-configuration \
        --function-name $TRADER_NAME \
        --runtime python3.12 \
        --region $REGION
else
    echo "Creating new function..."
    aws lambda create-function \
        --function-name $TRADER_NAME \
        --runtime python3.12 \
        --role $LAMBDA_ROLE \
        --handler xrp_lambda_function.lambda_handler \
        --zip-file fileb://$ZIP_FILE \
        --timeout 60 \
        --memory-size 256 \
        --region $REGION \
        --environment "Variables={KALSHI_KEY_ID=placeholder,KALSHI_PRIVATE_KEY=placeholder}"
fi

echo "Waiting for function to be ready..."
aws lambda wait function-updated --function-name $TRADER_NAME --region $REGION 2>/dev/null || true

# ==========================================
# Set up EventBridge triggers for Trading Bot
# ==========================================
echo ""
echo "Setting up EventBridge triggers for trading bot..."

# Trading bot - at minute 30 of every hour (early window, 12% edge)
TRADER_RULE_EARLY="XRPTradingBot-Minute30"
echo "Creating rule: $TRADER_RULE_EARLY (at minute 30 every hour - early window)"

aws events put-rule \
    --name $TRADER_RULE_EARLY \
    --schedule-expression "cron(30 * * * ? *)" \
    --state ENABLED \
    --region $REGION

aws lambda add-permission \
    --function-name $TRADER_NAME \
    --statement-id "EventBridge-$TRADER_RULE_EARLY" \
    --action "lambda:InvokeFunction" \
    --principal events.amazonaws.com \
    --source-arn "arn:aws:events:$REGION:$(aws sts get-caller-identity --query Account --output text):rule/$TRADER_RULE_EARLY" \
    --region $REGION 2>/dev/null || echo "Permission already exists"

aws events put-targets \
    --rule $TRADER_RULE_EARLY \
    --targets "Id"="1","Arn"="arn:aws:lambda:$REGION:$(aws sts get-caller-identity --query Account --output text):function:$TRADER_NAME" \
    --region $REGION

# Trading bot - at minute 45 of every hour (late window, 4% edge)
TRADER_RULE_LATE="XRPTradingBot-Minute45"
echo "Creating rule: $TRADER_RULE_LATE (at minute 45 every hour - late window)"

aws events put-rule \
    --name $TRADER_RULE_LATE \
    --schedule-expression "cron(45 * * * ? *)" \
    --state ENABLED \
    --region $REGION

aws lambda add-permission \
    --function-name $TRADER_NAME \
    --statement-id "EventBridge-$TRADER_RULE_LATE" \
    --action "lambda:InvokeFunction" \
    --principal events.amazonaws.com \
    --source-arn "arn:aws:events:$REGION:$(aws sts get-caller-identity --query Account --output text):rule/$TRADER_RULE_LATE" \
    --region $REGION 2>/dev/null || echo "Permission already exists"

aws events put-targets \
    --rule $TRADER_RULE_LATE \
    --targets "Id"="1","Arn"="arn:aws:lambda:$REGION:$(aws sts get-caller-identity --query Account --output text):function:$TRADER_NAME" \
    --region $REGION

echo ""
echo "=========================================="
echo "Deployment complete!"
echo "=========================================="
echo ""
echo "Lambdas deployed:"
echo "  - $COLLECTOR_NAME (runs every minute)"
echo "  - $TRADER_NAME (runs at :30 and :45 every hour)"
echo ""
echo "Next steps:"
echo "  1. Set Kalshi credentials on $TRADER_NAME:"
echo "     aws lambda update-function-configuration \\"
echo "       --function-name $TRADER_NAME \\"
echo "       --environment \"Variables={KALSHI_KEY_ID=your-key,KALSHI_PRIVATE_KEY=your-key}\" \\"
echo "       --region $REGION"
echo ""
echo "  2. Monitor price collection:"
echo "     aws logs tail /aws/lambda/$COLLECTOR_NAME --follow --region $REGION"
echo ""
echo "  3. Test trading bot:"
echo "     aws lambda invoke --function-name $TRADER_NAME --payload '{\"force\": true}' output.json --region $REGION"
