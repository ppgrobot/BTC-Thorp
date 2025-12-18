#!/bin/bash
# Deploy Dashboard API Lambda and API Gateway

set -e

REGION="us-east-2"
FUNCTION_NAME="BTCDashboardAPI"
API_NAME="BTCDashboardAPI"
LAMBDA_ROLE="arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):role/lambda-execution-role"

echo "=========================================="
echo "Deploying BTC Dashboard API"
echo "=========================================="

# Navigate to project root
cd "$(dirname "$0")/../.."

# Step 1: Create deployment package
echo ""
echo "Step 1: Creating deployment package..."

TEMP_DIR=$(mktemp -d)
echo "Building in $TEMP_DIR..."

# Copy Python files
cp dashboard/api/dashboard_api.py $TEMP_DIR/

# Install dependencies for Lambda (Linux x86_64, Python 3.12)
echo "Installing dependencies..."
pip3 install --target $TEMP_DIR \
    --platform manylinux2014_x86_64 \
    --implementation cp \
    --python-version 3.12 \
    --only-binary=:all: \
    requests==2.31.0 \
    2>/dev/null || pip3 install --target $TEMP_DIR requests==2.31.0

# Create zip file
cd $TEMP_DIR
zip -r $OLDPWD/dashboard_api.zip . -x "*.pyc" -x "__pycache__/*" -x "*.dist-info/*"
cd $OLDPWD

# Cleanup
rm -rf $TEMP_DIR

echo "Created dashboard_api.zip ($(du -h dashboard_api.zip | cut -f1))"

# Step 2: Deploy Lambda function
echo ""
echo "Step 2: Deploying Lambda function..."

if aws lambda get-function --function-name $FUNCTION_NAME --region $REGION 2>/dev/null; then
    echo "Updating existing function..."
    aws lambda update-function-code \
        --function-name $FUNCTION_NAME \
        --zip-file fileb://dashboard_api.zip \
        --region $REGION
else
    echo "Creating new function..."
    aws lambda create-function \
        --function-name $FUNCTION_NAME \
        --runtime python3.12 \
        --role $LAMBDA_ROLE \
        --handler dashboard_api.lambda_handler \
        --zip-file fileb://dashboard_api.zip \
        --timeout 30 \
        --memory-size 256 \
        --region $REGION
fi

# Wait for update to complete
echo "Waiting for function to be ready..."
aws lambda wait function-updated --function-name $FUNCTION_NAME --region $REGION 2>/dev/null || true

# Step 3: Create/Update API Gateway
echo ""
echo "Step 3: Setting up API Gateway..."

# Check if API exists
API_ID=$(aws apigatewayv2 get-apis --region $REGION \
    --query "Items[?Name=='$API_NAME'].ApiId" --output text 2>/dev/null || echo "")

if [ -z "$API_ID" ] || [ "$API_ID" == "None" ]; then
    echo "Creating new HTTP API..."
    API_ID=$(aws apigatewayv2 create-api \
        --name $API_NAME \
        --protocol-type HTTP \
        --cors-configuration AllowOrigins='*',AllowMethods='GET,OPTIONS',AllowHeaders='Content-Type,Authorization' \
        --region $REGION \
        --query 'ApiId' --output text)
    echo "Created API: $API_ID"
else
    echo "Using existing API: $API_ID"
fi

# Create Lambda integration
LAMBDA_ARN="arn:aws:lambda:$REGION:$(aws sts get-caller-identity --query Account --output text):function:$FUNCTION_NAME"

INTEGRATION_ID=$(aws apigatewayv2 get-integrations --api-id $API_ID --region $REGION \
    --query "Items[0].IntegrationId" --output text 2>/dev/null || echo "")

if [ -z "$INTEGRATION_ID" ] || [ "$INTEGRATION_ID" == "None" ]; then
    echo "Creating Lambda integration..."
    INTEGRATION_ID=$(aws apigatewayv2 create-integration \
        --api-id $API_ID \
        --integration-type AWS_PROXY \
        --integration-uri $LAMBDA_ARN \
        --payload-format-version 2.0 \
        --region $REGION \
        --query 'IntegrationId' --output text)
fi

# Create routes
for ROUTE in "/dashboard/all" "/dashboard/price" "/dashboard/volatility" "/dashboard/trades" "/dashboard/strikes"; do
    echo "Creating route: GET $ROUTE"
    aws apigatewayv2 create-route \
        --api-id $API_ID \
        --route-key "GET $ROUTE" \
        --target "integrations/$INTEGRATION_ID" \
        --region $REGION 2>/dev/null || echo "  Route may already exist"
done

# Create default stage
aws apigatewayv2 create-stage \
    --api-id $API_ID \
    --stage-name '$default' \
    --auto-deploy \
    --region $REGION 2>/dev/null || echo "Stage may already exist"

# Add Lambda permission for API Gateway
aws lambda add-permission \
    --function-name $FUNCTION_NAME \
    --statement-id "APIGateway-$API_ID" \
    --action lambda:InvokeFunction \
    --principal apigateway.amazonaws.com \
    --source-arn "arn:aws:execute-api:$REGION:$(aws sts get-caller-identity --query Account --output text):$API_ID/*" \
    --region $REGION 2>/dev/null || echo "Permission may already exist"

# Get API endpoint
API_ENDPOINT=$(aws apigatewayv2 get-api --api-id $API_ID --region $REGION --query 'ApiEndpoint' --output text)

echo ""
echo "=========================================="
echo "Deployment Complete!"
echo "=========================================="
echo ""
echo "API Endpoint: $API_ENDPOINT"
echo ""
echo "Available endpoints:"
echo "  GET $API_ENDPOINT/dashboard/all"
echo "  GET $API_ENDPOINT/dashboard/price"
echo "  GET $API_ENDPOINT/dashboard/volatility"
echo "  GET $API_ENDPOINT/dashboard/trades"
echo "  GET $API_ENDPOINT/dashboard/strikes"
echo ""
echo "Update your dashboard/index.html with:"
echo "  const API_BASE = '$API_ENDPOINT';"
echo ""

# Save API endpoint to file for reference
echo "$API_ENDPOINT" > dashboard/.api_endpoint
echo "API endpoint saved to dashboard/.api_endpoint"

# Cleanup
rm -f dashboard_api.zip
