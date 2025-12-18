#!/bin/bash
# Deploy ETH Price Collector Lambda

set -e

REGION="us-east-2"
LAMBDA_ROLE="arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):role/lambda-execution-role"
PACKAGE_DIR="lambda_package"
ZIP_FILE="eth_lambda.zip"

echo "=========================================="
echo "Deploying ETH Lambdas"
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

# Install dependencies for Lambda (Linux x86_64)
echo "Installing dependencies for Lambda..."
pip3 install --target $TEMP_DIR --platform manylinux2014_x86_64 --only-binary=:all: \
    requests==2.31.0 \
    2>/dev/null || {
    echo "Warning: Could not install binary packages, trying without platform constraint..."
    pip3 install --target $TEMP_DIR requests==2.31.0
}

# Create zip file
cd $TEMP_DIR
zip -r $OLDPWD/$ZIP_FILE . -x "*.pyc" -x "__pycache__/*" -x "*.dist-info/*"
cd $OLDPWD

# Cleanup
rm -rf $TEMP_DIR

echo "Created $ZIP_FILE ($(du -h $ZIP_FILE | cut -f1))"

# ==========================================
# Deploy ETH Price Collector Lambda
# ==========================================
COLLECTOR_NAME="ETHPriceCollector"

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
        --handler eth_price_collector.lambda_handler \
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

COLLECTOR_RULE="ETHPriceCollector-EveryMinute"
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

echo ""
echo "=========================================="
echo "Deployment complete!"
echo "=========================================="
echo ""
echo "Lambda deployed:"
echo "  - $COLLECTOR_NAME (runs every minute)"
echo ""
echo "Monitor price collection:"
echo "  aws logs tail /aws/lambda/$COLLECTOR_NAME --follow --region $REGION"
