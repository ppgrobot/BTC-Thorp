#!/bin/bash
# =============================================================================
# BTC-Thorp Unified Docker Deployment Script
# Deploys all Lambda functions using Docker containers to AWS
# Compatible with bash 3.2+ (macOS default)
# =============================================================================

set -e

# Configuration
AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
LAMBDA_ROLE_NAME="lambda-execution-role"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# =============================================================================
# REPOSITORY MAPPINGS (bash 3.2 compatible)
# =============================================================================

get_repo_name() {
    local service=$1
    case $service in
        btc) echo "btc-thorp/btc" ;;
        eth) echo "btc-thorp/eth" ;;
        weather) echo "btc-thorp/weather" ;;
        cleanup) echo "btc-thorp/cleanup" ;;
    esac
}

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

create_ecr_repo() {
    local repo_name=$1
    log_info "Creating ECR repository: ${repo_name}"

    if aws ecr describe-repositories --repository-names "${repo_name}" --region "${AWS_REGION}" > /dev/null 2>&1; then
        log_warn "Repository ${repo_name} already exists"
    else
        aws ecr create-repository \
            --repository-name "${repo_name}" \
            --region "${AWS_REGION}" \
            --image-scanning-configuration scanOnPush=true \
            --image-tag-mutability MUTABLE > /dev/null
        log_success "Created repository: ${repo_name}"
    fi
}

ecr_login() {
    log_info "Logging in to ECR..."
    aws ecr get-login-password --region "${AWS_REGION}" | \
        docker login --username AWS --password-stdin "${ECR_REGISTRY}"
    log_success "ECR login successful"
}

build_and_push_image() {
    local service=$1
    local context=$2
    local repo_name=$(get_repo_name "$service")
    local image_uri="${ECR_REGISTRY}/${repo_name}:latest"

    log_info "Building Docker image for ${service}..."
    docker build --platform linux/amd64 -t "${repo_name}:latest" "${context}"

    log_info "Tagging image..."
    docker tag "${repo_name}:latest" "${image_uri}"

    log_info "Pushing image to ECR..."
    docker push "${image_uri}"

    log_success "Pushed ${image_uri}"
    echo "${image_uri}"
}

get_or_create_lambda_role() {
    log_info "Checking Lambda execution role..."

    local role_arn
    if role_arn=$(aws iam get-role --role-name "${LAMBDA_ROLE_NAME}" --query 'Role.Arn' --output text 2>/dev/null); then
        log_info "Using existing role: ${role_arn}"
        echo "${role_arn}"
        return
    fi

    log_info "Creating Lambda execution role..."

    # Create trust policy
    cat > /tmp/trust-policy.json << 'EOF'
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {
                "Service": "lambda.amazonaws.com"
            },
            "Action": "sts:AssumeRole"
        }
    ]
}
EOF

    # Create role
    role_arn=$(aws iam create-role \
        --role-name "${LAMBDA_ROLE_NAME}" \
        --assume-role-policy-document file:///tmp/trust-policy.json \
        --query 'Role.Arn' --output text)

    # Attach policies
    aws iam attach-role-policy \
        --role-name "${LAMBDA_ROLE_NAME}" \
        --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

    aws iam attach-role-policy \
        --role-name "${LAMBDA_ROLE_NAME}" \
        --policy-arn arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess

    # Wait for role to propagate
    log_info "Waiting for role to propagate..."
    sleep 10

    log_success "Created role: ${role_arn}"
    echo "${role_arn}"
}

create_or_update_lambda() {
    local function_name=$1
    local image_uri=$2
    local handler=$3
    local timeout=$4
    local memory=$5
    local description=$6
    local role_arn=$7

    log_info "Deploying Lambda function: ${function_name}"

    if aws lambda get-function --function-name "${function_name}" --region "${AWS_REGION}" > /dev/null 2>&1; then
        # Update existing function
        log_info "Updating existing function..."
        aws lambda update-function-code \
            --function-name "${function_name}" \
            --image-uri "${image_uri}" \
            --region "${AWS_REGION}" > /dev/null

        # Wait for update to complete
        aws lambda wait function-updated --function-name "${function_name}" --region "${AWS_REGION}"

        # Update configuration
        aws lambda update-function-configuration \
            --function-name "${function_name}" \
            --timeout "${timeout}" \
            --memory-size "${memory}" \
            --region "${AWS_REGION}" > /dev/null

        # Wait again for config update
        aws lambda wait function-updated --function-name "${function_name}" --region "${AWS_REGION}"

        # Override CMD for the specific handler
        aws lambda update-function-configuration \
            --function-name "${function_name}" \
            --image-config "Command=[${handler}]" \
            --region "${AWS_REGION}" > /dev/null 2>&1 || true

        log_success "Updated: ${function_name}"
    else
        # Create new function
        log_info "Creating new function..."
        aws lambda create-function \
            --function-name "${function_name}" \
            --package-type Image \
            --code ImageUri="${image_uri}" \
            --role "${role_arn}" \
            --timeout "${timeout}" \
            --memory-size "${memory}" \
            --description "${description}" \
            --region "${AWS_REGION}" > /dev/null

        # Wait for function to be active
        aws lambda wait function-active --function-name "${function_name}" --region "${AWS_REGION}"

        # Set the handler
        aws lambda update-function-configuration \
            --function-name "${function_name}" \
            --image-config "Command=[${handler}]" \
            --region "${AWS_REGION}" > /dev/null 2>&1 || true

        log_success "Created: ${function_name}"
    fi
}

setup_eventbridge_schedule() {
    local function_name=$1
    local schedule_expression=$2
    local rule_name="${function_name}-trigger"

    log_info "Setting up EventBridge schedule for ${function_name}: ${schedule_expression}"

    # Create or update rule
    aws events put-rule \
        --name "${rule_name}" \
        --schedule-expression "${schedule_expression}" \
        --region "${AWS_REGION}" > /dev/null

    # Get Lambda ARN
    local lambda_arn=$(aws lambda get-function \
        --function-name "${function_name}" \
        --region "${AWS_REGION}" \
        --query 'Configuration.FunctionArn' --output text)

    # Add permission for EventBridge to invoke Lambda
    aws lambda add-permission \
        --function-name "${function_name}" \
        --statement-id "${rule_name}-permission" \
        --action lambda:InvokeFunction \
        --principal events.amazonaws.com \
        --source-arn "arn:aws:events:${AWS_REGION}:${AWS_ACCOUNT_ID}:rule/${rule_name}" \
        --region "${AWS_REGION}" 2>/dev/null || true

    # Add target
    aws events put-targets \
        --rule "${rule_name}" \
        --targets "Id=1,Arn=${lambda_arn}" \
        --region "${AWS_REGION}" > /dev/null

    log_success "EventBridge schedule configured: ${rule_name}"
}

# =============================================================================
# DYNAMODB SETUP
# =============================================================================

setup_dynamodb_tables() {
    log_info "Setting up DynamoDB tables..."

    local tables="BTCPriceHistory BTCTradeLog ETHPriceHistory KalshiTradingBudget"

    for table in $tables; do
        if aws dynamodb describe-table --table-name "${table}" --region "${AWS_REGION}" > /dev/null 2>&1; then
            log_warn "Table ${table} already exists"
        else
            log_info "Creating table: ${table}"

            aws dynamodb create-table \
                --table-name "${table}" \
                --attribute-definitions \
                    AttributeName=pk,AttributeType=S \
                    AttributeName=sk,AttributeType=S \
                --key-schema \
                    AttributeName=pk,KeyType=HASH \
                    AttributeName=sk,KeyType=RANGE \
                --billing-mode PAY_PER_REQUEST \
                --region "${AWS_REGION}" > /dev/null

            # Wait for table to be active
            aws dynamodb wait table-exists --table-name "${table}" --region "${AWS_REGION}"

            # Enable TTL for price history tables
            if [[ "${table}" == *"PriceHistory"* ]]; then
                aws dynamodb update-time-to-live \
                    --table-name "${table}" \
                    --time-to-live-specification Enabled=true,AttributeName=ttl \
                    --region "${AWS_REGION}" > /dev/null 2>&1 || true
            fi

            log_success "Created table: ${table}"
        fi
    done
}

# =============================================================================
# SERVICE DEPLOYMENT FUNCTIONS
# =============================================================================

deploy_btc() {
    local role_arn=$1
    local repo_name=$(get_repo_name "btc")

    log_info "Deploying BTC service..."

    # Create ECR repository
    create_ecr_repo "${repo_name}"

    # Build and push image
    local image_uri=$(build_and_push_image "btc" "./btc")

    # Deploy BTCPriceCollector
    create_or_update_lambda "BTCPriceCollector" "${image_uri}" \
        "btc_price_collector.lambda_handler" 30 128 \
        "Collects BTC prices every minute" "${role_arn}"

    # Deploy BTCTradingBot
    create_or_update_lambda "BTCTradingBot" "${image_uri}" \
        "btc_lambda_function.lambda_handler" 60 256 \
        "BTC hourly trading bot" "${role_arn}"
}

deploy_eth() {
    local role_arn=$1
    local repo_name=$(get_repo_name "eth")

    log_info "Deploying ETH service..."

    # Create ECR repository
    create_ecr_repo "${repo_name}"

    # Build and push image
    local image_uri=$(build_and_push_image "eth" "./eth")

    # Deploy ETHPriceCollector
    create_or_update_lambda "ETHPriceCollector" "${image_uri}" \
        "eth_price_collector.lambda_handler" 30 128 \
        "Collects ETH prices every minute" "${role_arn}"

    # Deploy ETHVolatilityAPI
    create_or_update_lambda "ETHVolatilityAPI" "${image_uri}" \
        "eth_volatility_api.lambda_handler" 30 128 \
        "ETH volatility API endpoint" "${role_arn}"
}

deploy_weather() {
    local role_arn=$1
    local repo_name=$(get_repo_name "weather")

    log_info "Deploying Weather service..."

    # Create ECR repository
    create_ecr_repo "${repo_name}"

    # Build and push image
    local image_uri=$(build_and_push_image "weather" "./weather")

    # Deploy KalshiWeatherTradingBot
    create_or_update_lambda "KalshiWeatherTradingBot" "${image_uri}" \
        "lambda_function.lambda_handler" 300 256 \
        "Weather liquidity provider bot" "${role_arn}"
}

deploy_cleanup() {
    local role_arn=$1
    local repo_name=$(get_repo_name "cleanup")

    log_info "Deploying Cleanup service..."

    # Create ECR repository
    create_ecr_repo "${repo_name}"

    # Build and push image
    local image_uri=$(build_and_push_image "cleanup" "./scripts")

    # Deploy PriceHistoryCleanup
    create_or_update_lambda "PriceHistoryCleanup" "${image_uri}" \
        "price_history_cleanup.lambda_handler" 60 128 \
        "Cleans up old price history" "${role_arn}"
}

setup_schedules() {
    log_info "Setting up EventBridge schedules..."

    # BTC Price Collector - every minute
    setup_eventbridge_schedule "BTCPriceCollector" "rate(1 minute)"

    # BTC Trading Bot - at minute 45 of every hour
    setup_eventbridge_schedule "BTCTradingBot" "cron(45 * * * ? *)"

    # ETH Price Collector - every minute
    setup_eventbridge_schedule "ETHPriceCollector" "rate(1 minute)"

    # Price History Cleanup - every hour
    setup_eventbridge_schedule "PriceHistoryCleanup" "rate(1 hour)"

    # Weather Bot - 6pm and 8pm ET (11pm and 1am UTC)
    setup_eventbridge_schedule "KalshiWeatherTradingBot" "cron(0 23,1 * * ? *)"

    log_success "All schedules configured"
}

# =============================================================================
# MAIN DEPLOYMENT
# =============================================================================

usage() {
    cat << EOF
Usage: $0 [OPTIONS] [COMMAND]

Commands:
  all         Deploy everything (default)
  btc         Deploy BTC service only
  eth         Deploy ETH service only
  weather     Deploy Weather service only
  cleanup     Deploy Cleanup service only
  tables      Create DynamoDB tables only
  schedules   Setup EventBridge schedules only
  images      Build and push Docker images only

Options:
  -r, --region REGION   AWS region (default: us-east-1)
  -h, --help            Show this help message

Environment Variables:
  AWS_REGION            AWS region (can also use -r flag)
  KALSHI_KEY_ID         Kalshi API key ID
  KALSHI_PRIVATE_KEY    Kalshi private key (base64 encoded)
  API_BEARER_TOKEN      Bearer token for volatility API

Examples:
  $0                    Deploy everything
  $0 btc                Deploy BTC service only
  $0 -r us-east-2 all   Deploy to us-east-2 region
EOF
}

main() {
    local command="all"

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            -r|--region)
                AWS_REGION="$2"
                shift 2
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            all|btc|eth|weather|cleanup|tables|schedules|images)
                command="$1"
                shift
                ;;
            *)
                log_error "Unknown option: $1"
                usage
                exit 1
                ;;
        esac
    done

    # Update registry with potentially new region
    ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

    echo "=============================================="
    echo "  BTC-Thorp Docker Deployment"
    echo "=============================================="
    echo "AWS Account: ${AWS_ACCOUNT_ID}"
    echo "AWS Region:  ${AWS_REGION}"
    echo "Command:     ${command}"
    echo "=============================================="
    echo ""

    # Get Lambda execution role
    local role_arn=$(get_or_create_lambda_role)

    case "${command}" in
        tables)
            setup_dynamodb_tables
            ;;
        schedules)
            setup_schedules
            ;;
        images)
            ecr_login
            for service in btc eth weather cleanup; do
                create_ecr_repo "$(get_repo_name $service)"
                case $service in
                    btc) build_and_push_image "btc" "./btc" ;;
                    eth) build_and_push_image "eth" "./eth" ;;
                    weather) build_and_push_image "weather" "./weather" ;;
                    cleanup) build_and_push_image "cleanup" "./scripts" ;;
                esac
            done
            ;;
        btc)
            ecr_login
            setup_dynamodb_tables
            deploy_btc "${role_arn}"
            setup_eventbridge_schedule "BTCPriceCollector" "rate(1 minute)"
            setup_eventbridge_schedule "BTCTradingBot" "cron(45 * * * ? *)"
            ;;
        eth)
            ecr_login
            setup_dynamodb_tables
            deploy_eth "${role_arn}"
            setup_eventbridge_schedule "ETHPriceCollector" "rate(1 minute)"
            ;;
        weather)
            ecr_login
            setup_dynamodb_tables
            deploy_weather "${role_arn}"
            setup_eventbridge_schedule "KalshiWeatherTradingBot" "cron(0 23,1 * * ? *)"
            ;;
        cleanup)
            ecr_login
            deploy_cleanup "${role_arn}"
            setup_eventbridge_schedule "PriceHistoryCleanup" "rate(1 hour)"
            ;;
        all)
            ecr_login
            setup_dynamodb_tables
            deploy_btc "${role_arn}"
            deploy_eth "${role_arn}"
            deploy_weather "${role_arn}"
            deploy_cleanup "${role_arn}"
            setup_schedules
            ;;
    esac

    echo ""
    log_success "Deployment complete!"
    echo ""
    echo "Next steps:"
    echo "  1. Set environment variables for Lambda functions:"
    echo "     aws lambda update-function-configuration --function-name BTCTradingBot \\"
    echo "       --environment 'Variables={KALSHI_KEY_ID=xxx,KALSHI_PRIVATE_KEY=xxx}'"
    echo ""
    echo "  2. Test functions:"
    echo "     aws lambda invoke --function-name BTCPriceCollector output.json"
    echo ""
    echo "  3. Monitor logs:"
    echo "     aws logs tail /aws/lambda/BTCTradingBot --follow"
}

# Run main
main "$@"
