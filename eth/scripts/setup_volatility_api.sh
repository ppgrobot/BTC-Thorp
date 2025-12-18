#!/bin/bash
# Setup API Gateway for ETH Volatility API with Bearer token auth

set -e

REGION="us-east-1"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
LAMBDA_NAME="ETHVolatilityAPI"
API_NAME="ETHVolatilityAPI"
LAMBDA_ROLE="arn:aws:iam::${ACCOUNT_ID}:role/lambda-execution-role"

# Generate a random bearer token
BEARER_TOKEN=$(openssl rand -hex 32)

echo "=========================================="
echo "Setting up ETH Volatility API"
echo "=========================================="

# 1. Update Lambda package and deploy API Lambda
echo ""
echo "Step 1: Deploying Lambda function..."
cd "$(dirname "$0")/.."

# Create new zip with the API handler
cd lambda_package
rm -f ../eth_lambda.zip
zip -r ../eth_lambda.zip . -x "*.pyc" -x "__pycache__/*" > /dev/null
cd ..

# Check if function exists
if aws lambda get-function --function-name $LAMBDA_NAME --region $REGION 2>/dev/null; then
    echo "Updating existing function..."
    aws lambda update-function-code \
        --function-name $LAMBDA_NAME \
        --zip-file fileb://eth_lambda.zip \
        --region $REGION > /dev/null
else
    echo "Creating new function..."
    aws lambda create-function \
        --function-name $LAMBDA_NAME \
        --runtime python3.12 \
        --role $LAMBDA_ROLE \
        --handler eth_volatility_api.lambda_handler \
        --zip-file fileb://eth_lambda.zip \
        --timeout 30 \
        --memory-size 128 \
        --environment "Variables={API_BEARER_TOKEN=${BEARER_TOKEN}}" \
        --region $REGION > /dev/null
fi

echo "Waiting for Lambda to be ready..."
aws lambda wait function-active --function-name $LAMBDA_NAME --region $REGION 2>/dev/null || true
sleep 5

# Set the bearer token
aws lambda update-function-configuration \
    --function-name $LAMBDA_NAME \
    --environment "Variables={API_BEARER_TOKEN=${BEARER_TOKEN}}" \
    --region $REGION > /dev/null

# 2. Create API Gateway (HTTP API - simpler and cheaper)
echo ""
echo "Step 2: Creating API Gateway..."

# Check if API already exists
EXISTING_API_ID=$(aws apigatewayv2 get-apis --region $REGION \
    --query "Items[?Name=='${API_NAME}'].ApiId" --output text 2>/dev/null || echo "")

if [ -n "$EXISTING_API_ID" ] && [ "$EXISTING_API_ID" != "None" ]; then
    echo "API already exists: $EXISTING_API_ID"
    API_ID=$EXISTING_API_ID
else
    echo "Creating new HTTP API..."
    API_ID=$(aws apigatewayv2 create-api \
        --name $API_NAME \
        --protocol-type HTTP \
        --region $REGION \
        --query 'ApiId' --output text)
    echo "Created API: $API_ID"
fi

# 3. Create Lambda integration
echo ""
echo "Step 3: Creating Lambda integration..."

LAMBDA_ARN="arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:${LAMBDA_NAME}"

INTEGRATION_ID=$(aws apigatewayv2 create-integration \
    --api-id $API_ID \
    --integration-type AWS_PROXY \
    --integration-uri $LAMBDA_ARN \
    --payload-format-version "2.0" \
    --region $REGION \
    --query 'IntegrationId' --output text 2>/dev/null || echo "")

if [ -z "$INTEGRATION_ID" ]; then
    # Get existing integration
    INTEGRATION_ID=$(aws apigatewayv2 get-integrations \
        --api-id $API_ID \
        --region $REGION \
        --query 'Items[0].IntegrationId' --output text)
fi
echo "Integration ID: $INTEGRATION_ID"

# 4. Create route for POST /volatility
echo ""
echo "Step 4: Creating route..."

aws apigatewayv2 create-route \
    --api-id $API_ID \
    --route-key "POST /volatility" \
    --target "integrations/${INTEGRATION_ID}" \
    --region $REGION > /dev/null 2>&1 || echo "Route may already exist"

# 5. Create default stage with auto-deploy
echo ""
echo "Step 5: Creating stage..."

aws apigatewayv2 create-stage \
    --api-id $API_ID \
    --stage-name '$default' \
    --auto-deploy \
    --region $REGION > /dev/null 2>&1 || echo "Stage may already exist"

# 6. Add Lambda permission for API Gateway
echo ""
echo "Step 6: Adding Lambda permissions..."

aws lambda add-permission \
    --function-name $LAMBDA_NAME \
    --statement-id "APIGateway-${API_ID}" \
    --action "lambda:InvokeFunction" \
    --principal apigateway.amazonaws.com \
    --source-arn "arn:aws:execute-api:${REGION}:${ACCOUNT_ID}:${API_ID}/*/*" \
    --region $REGION 2>/dev/null || echo "Permission may already exist"

# Get the API endpoint
API_ENDPOINT=$(aws apigatewayv2 get-api \
    --api-id $API_ID \
    --region $REGION \
    --query 'ApiEndpoint' --output text)

echo ""
echo "=========================================="
echo "API Gateway Setup Complete!"
echo "=========================================="
echo ""
echo "API Endpoint: ${API_ENDPOINT}/volatility"
echo "Bearer Token: ${BEARER_TOKEN}"
echo ""
echo "Test with:"
echo "  curl -X POST ${API_ENDPOINT}/volatility \\"
echo "    -H 'Authorization: Bearer ${BEARER_TOKEN}'"
echo ""
echo "IMPORTANT: Save your bearer token! It won't be shown again."
echo ""

# Save token to a local file (gitignored)
echo "$BEARER_TOKEN" > .eth_api_token
echo "Token also saved to .eth_api_token (gitignored)"
