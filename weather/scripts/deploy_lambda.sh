#!/bin/bash

# Deploy Kalshi Weather Trading Bot to AWS Lambda

set -e

FUNCTION_NAME="KalshiWeatherTradingBot"
REGION="us-east-1"

echo "================================"
echo "Kalshi Trading Bot Deployment"
echo "================================"

# Get script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Step 1: Create deployment package
echo ""
echo "Step 1: Creating deployment package..."
cd "$PROJECT_ROOT"
rm -f lambda_function.zip

# Create a temporary directory for the package
TEMP_DIR=$(mktemp -d)
echo "Building in $TEMP_DIR..."

# Copy Python files
cp lambda_package/*.py $TEMP_DIR/

# Install dependencies for Lambda (Linux x86_64, Python 3.12)
echo "Installing dependencies for Lambda..."
pip3 install --target $TEMP_DIR \
    --platform manylinux2014_x86_64 \
    --implementation cp \
    --python-version 3.12 \
    --only-binary=:all: \
    requests==2.31.0 \
    cryptography==42.0.0 \
    beautifulsoup4==4.12.0 \
    lxml==5.1.0 \
    cffi \
    2>/dev/null || {
    echo "Warning: Could not install binary packages, trying without platform constraint..."
    pip3 install --target $TEMP_DIR \
        requests==2.31.0 \
        cryptography==42.0.0 \
        beautifulsoup4==4.12.0 \
        lxml==5.1.0 \
        cffi
}

# Create zip file
cd $TEMP_DIR
zip -r $OLDPWD/lambda_function.zip . -x "*.pyc" -x "__pycache__/*" -x "*.dist-info/*"
cd $OLDPWD

# Cleanup
rm -rf $TEMP_DIR

echo "✅ Deployment package created: lambda_function.zip ($(du -h lambda_function.zip | cut -f1))"

# Step 2: Deploy Lambda function
echo ""
echo "Step 2: Deploying Lambda function..."
LAMBDA_ROLE="arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):role/lambda-execution-role"

if aws lambda get-function --function-name $FUNCTION_NAME --region $REGION 2>/dev/null; then
    echo "Function exists, updating code..."
    aws lambda update-function-code \
        --function-name $FUNCTION_NAME \
        --zip-file fileb://lambda_function.zip \
        --region $REGION

    echo "✅ Lambda function code updated!"
else
    echo "Creating new function..."
    aws lambda create-function \
        --function-name $FUNCTION_NAME \
        --runtime python3.12 \
        --role $LAMBDA_ROLE \
        --handler lambda_function.lambda_handler \
        --zip-file fileb://lambda_function.zip \
        --timeout 300 \
        --memory-size 512 \
        --region $REGION \
        --environment "Variables={KALSHI_KEY_ID=placeholder,KALSHI_PRIVATE_KEY=placeholder}"

    echo "✅ Lambda function created!"
fi

# Wait for update to complete
echo "Waiting for function to be ready..."
aws lambda wait function-updated --function-name $FUNCTION_NAME --region $REGION 2>/dev/null || true

# Step 3: Update environment variables
echo ""
echo "Step 3: Updating environment variables..."
echo "Please set these manually in AWS Lambda Console:"
echo "  - KALSHI_KEY_ID: Your Kalshi API Key ID"
echo "  - KALSHI_PRIVATE_KEY: Your Kalshi Private Key (single line, no newlines)"
echo ""
echo "Or run:"
echo "aws lambda update-function-configuration \\"
echo "    --function-name $FUNCTION_NAME \\"
echo "    --environment Variables=\"{KALSHI_KEY_ID=your-key-id,KALSHI_PRIVATE_KEY=your-private-key}\" \\"
echo "    --region $REGION"

echo ""
echo "================================"
echo "Deployment Summary"
echo "================================"
echo "Function: $FUNCTION_NAME"
echo "Region: $REGION"
echo "Package: lambda_function.zip"
echo ""
echo "Next steps:"
echo "1. Set environment variables (KALSHI_KEY_ID, KALSHI_PRIVATE_KEY)"
echo "2. Create KalshiTrades DynamoDB table: ./scripts/setup_dynamodb.sh"
echo "3. Set up EventBridge triggers: ./scripts/setup_eventbridge.sh"
echo "4. Test the function: aws lambda invoke --function-name $FUNCTION_NAME output.json"
echo ""
echo "✅ Deployment complete!"
