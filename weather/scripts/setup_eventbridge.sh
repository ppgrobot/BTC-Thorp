#!/bin/bash

# Set up EventBridge schedule for Kalshi Trading Bot
# Runs every 10 minutes from 12pm-8pm Denver time (6pm-2am UTC)

FUNCTION_NAME="KalshiWeatherTradingBot"
RULE_NAME="KalshiTradingSchedule"
REGION="us-east-1"

echo "================================"
echo "EventBridge Schedule Setup"
echo "================================"

# Get Lambda function ARN
FUNCTION_ARN=$(aws lambda get-function --function-name $FUNCTION_NAME --region $REGION --query 'Configuration.FunctionArn' --output text)

if [ -z "$FUNCTION_ARN" ]; then
    echo "❌ Error: Lambda function $FUNCTION_NAME not found"
    exit 1
fi

echo "Lambda Function ARN: $FUNCTION_ARN"

# Create EventBridge rule
# Denver time 12pm-8pm = UTC 18:00-02:00 (next day)
# We need two rules to handle the day boundary
echo ""
echo "Creating EventBridge rule..."

# Rule 1: 6pm-11:50pm UTC (12pm-5:50pm Denver)
aws events put-rule \
    --name "${RULE_NAME}-Afternoon" \
    --schedule-expression "cron(0/10 18-23 * * ? *)" \
    --state ENABLED \
    --description "Kalshi trading bot - every 10 min, 12pm-5:50pm Denver time" \
    --region $REGION

# Rule 2: 12am-2am UTC (6pm-8pm previous day Denver)
aws events put-rule \
    --name "${RULE_NAME}-Evening" \
    --schedule-expression "cron(0/10 0-2 * * ? *)" \
    --state ENABLED \
    --description "Kalshi trading bot - every 10 min, 6pm-8pm Denver time" \
    --region $REGION

echo "✅ EventBridge rules created"

# Add Lambda permission for EventBridge to invoke
echo ""
echo "Adding Lambda permissions..."

aws lambda add-permission \
    --function-name $FUNCTION_NAME \
    --statement-id "${RULE_NAME}-Afternoon" \
    --action 'lambda:InvokeFunction' \
    --principal events.amazonaws.com \
    --source-arn "arn:aws:events:${REGION}:$(aws sts get-caller-identity --query Account --output text):rule/${RULE_NAME}-Afternoon" \
    --region $REGION 2>/dev/null || echo "Permission already exists"

aws lambda add-permission \
    --function-name $FUNCTION_NAME \
    --statement-id "${RULE_NAME}-Evening" \
    --action 'lambda:InvokeFunction' \
    --principal events.amazonaws.com \
    --source-arn "arn:aws:events:${REGION}:$(aws sts get-caller-identity --query Account --output text):rule/${RULE_NAME}-Evening" \
    --region $REGION 2>/dev/null || echo "Permission already exists"

echo "✅ Lambda permissions added"

# Add Lambda as target for EventBridge rules
echo ""
echo "Adding Lambda as target..."

aws events put-targets \
    --rule "${RULE_NAME}-Afternoon" \
    --targets "Id"="1","Arn"="$FUNCTION_ARN" \
    --region $REGION

aws events put-targets \
    --rule "${RULE_NAME}-Evening" \
    --targets "Id"="1","Arn"="$FUNCTION_ARN" \
    --region $REGION

echo "✅ Lambda added as target"

echo ""
echo "================================"
echo "Schedule Configuration Complete"
echo "================================"
echo "Rule 1 (Afternoon): Every 10 min, 12pm-5:50pm Denver (6pm-11:50pm UTC)"
echo "Rule 2 (Evening): Every 10 min, 6pm-8pm Denver (12am-2am UTC next day)"
echo ""
echo "Lambda will run every 10 minutes between 12pm-8pm Denver time"
echo ""
echo "To disable:"
echo "  aws events disable-rule --name ${RULE_NAME}-Afternoon --region $REGION"
echo "  aws events disable-rule --name ${RULE_NAME}-Evening --region $REGION"
echo ""
echo "To enable:"
echo "  aws events enable-rule --name ${RULE_NAME}-Afternoon --region $REGION"
echo "  aws events enable-rule --name ${RULE_NAME}-Evening --region $REGION"
