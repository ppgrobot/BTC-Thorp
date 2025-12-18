# Kalshi Trading Bots

Automated trading bots for Kalshi prediction markets, covering **weather**, **Bitcoin**, and **Ethereum** hourly contracts.

## Bots Overview

| Bot | Market | Strategy | Schedule |
|-----|--------|----------|----------|
| Weather | High Temperature | Liquidity provider on verified winners | 6pm/8pm ET daily |
| BTC | Bitcoin Hourly | Volatility-based NO contracts | XX:45 (24/7) |
| ETH | Ethereum Hourly | Volatility-based (infrastructure ready) | Not yet scheduled |

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed system design.

---

## BTC Hourly Trading Bot

Automated trading bot for Kalshi's **Bitcoin hourly price markets** (KXBTCD). Uses realized volatility to identify mispriced NO contracts and sizes positions using Kelly criterion.

### Strategy

At minute 45 of each hour (15 minutes before settlement):

1. **Collect Volatility**: Price collector runs every minute, calculates 15/30/60/90/120 min volatility
2. **Find Target Strike**: Identify strike 20+ basis points above current BTC price
3. **Model vs Market**: Calculate model probability using volatility, compare to market price
4. **Edge Detection**: Only trade if model shows 3%+ edge over market
5. **Kelly Sizing**: Size position using quarter-Kelly for safety (max 5 contracts)
6. **Execute Trade**: Buy NO contracts, log trade to DynamoDB

### How It Works

```
EventBridge: BTCPriceCollector-EveryMinute (rate: 1 minute)
    ↓
Lambda: BTCPriceCollector
    ├─→ Fetch BTC price from Coinbase
    ├─→ Store in DynamoDB (BTCPriceHistory)
    └─→ Calculate rolling volatility metrics

EventBridge: BTCTradingBot-Minute45 (cron: 45 * * * ? *)
    ↓
Lambda: BTCTradingBot
    ├─→ Check account balance
    ├─→ Get 15m realized volatility
    ├─→ Find strike 20bps+ above current price
    ├─→ Calculate model probability (normal CDF)
    ├─→ Compare to market → calculate edge
    ├─→ Size bet with Kelly criterion
    ├─→ Execute trade on Kalshi
    └─→ Log trade to BTCTradeLog table
```

### Key Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| MIN_BPS_ABOVE | 20 | Minimum basis points above current price |
| MIN_EDGE_PCT | 3% | Minimum edge required to trade |
| MAX_KELLY_FRACTION | 0.25 | Quarter Kelly for safety |
| MAX_CONTRACTS | 5 | Maximum contracts per trade |
| MIN_NO_PRICE | 50¢ | Don't buy NO below this (too risky) |
| MAX_NO_PRICE | 99¢ | Don't buy NO above this (no profit) |
| Trading Window | XX:45 | Trade at minute 45 (15 min before settlement) |

### Deployment

```bash
# 1. Create DynamoDB tables
./btc/scripts/setup_btc_dynamodb.sh
./btc/scripts/setup_trade_log.sh

# 2. Deploy Lambdas
./btc/scripts/deploy_btc_lambdas.sh

# 3. Set Kalshi credentials
aws lambda update-function-configuration \
  --function-name BTCTradingBot \
  --environment "Variables={KALSHI_KEY_ID=your-key,KALSHI_PRIVATE_KEY=your-key}"
```

### Monitoring

```bash
# View price collector logs
aws logs tail /aws/lambda/BTCPriceCollector --follow

# View trading bot logs
aws logs tail /aws/lambda/BTCTradingBot --follow

# Query trade history
aws dynamodb scan --table-name BTCTradeLog --region us-east-1
```

---

## ETH Volatility Infrastructure

Infrastructure for **Ethereum hourly price markets** (KXETHD). Price collection and volatility API are deployed; trading bot can be added.

### Components

| Component | Lambda | Status |
|-----------|--------|--------|
| Price Collector | ETHPriceCollector | Running (every minute) |
| Volatility API | ETHVolatilityAPI | Running (API Gateway) |
| Trading Bot | - | Not yet implemented |

### Volatility API

```bash
# Get current ETH volatility
curl -X POST https://[api-id].execute-api.us-east-1.amazonaws.com/volatility \
  -H 'Authorization: Bearer [token]'
```

Returns:
```json
{
  "updated_at": "2025-12-11T01:25:17",
  "volatility": {
    "15m": {"std_dev": 0.073, "range_pct": 0.103, "samples": 15},
    "30m": {"std_dev": 0.089, "range_pct": 0.142, "samples": 30},
    "60m": {"std_dev": 0.112, "range_pct": 0.198, "samples": 60}
  }
}
```

### Deployment

```bash
# 1. Create DynamoDB table
./eth/scripts/setup_eth_dynamodb.sh

# 2. Deploy price collector
./eth/scripts/deploy_eth_lambdas.sh

# 3. Setup API Gateway (saves token to .eth_api_token)
./eth/scripts/setup_volatility_api.sh
```

---

## Weather Liquidity Provider Bot

Automated trading bot for Kalshi's **high temperature markets** across multiple cities. Places resting bids at 99¢ on verified winning contracts to earn liquidity incentive rewards.

### Strategy

After 6pm local time when daily high temperatures are locked in:

1. **Identify Winners**: Find contracts where YES bid ≥ 95¢
2. **Verify with NWS**: Cross-check against official NWS Climatological Reports
3. **Place Resting Bids**: Bid 99¢ on verified winners
4. **Earn Rewards**: Orders on the book earn Kalshi liquidity rewards
5. **Profit on Fill**: When filled, redeem for $1.00 (1¢ profit per contract)

### Supported Cities

| City | Kalshi Code | Timezone | Settlement Hour |
|------|-------------|----------|-----------------|
| Philadelphia | KXHIGHPHIL | EST | 6pm ET |
| Miami | KXHIGHMIA | EST | 6pm ET |
| New York City | KXHIGHNY | EST | 6pm ET |
| Denver | KXHIGHDEN | MST | 6pm MT |
| Austin | KXHIGHAUS | CST | 6pm CT |
| Chicago | KXHIGHCHI | CST | 7pm CT |

### Deployment

```bash
# 1. Deploy Lambda
./weather/scripts/deploy_lambda.sh

# 2. Set credentials
aws lambda update-function-configuration \
  --function-name KalshiWeatherTradingBot \
  --environment "Variables={KALSHI_KEY_ID=your-key,KALSHI_PRIVATE_KEY=your-key}"

# 3. Create DynamoDB table
./weather/scripts/setup_dynamodb.sh

# 4. Set up EventBridge triggers
./weather/scripts/setup_eventbridge.sh
```

---

## Price History Cleanup

Lambda that runs hourly to clean up price data older than 180 minutes from both BTC and ETH tables.

```bash
# Deploy cleanup Lambda
./scripts/deploy_cleanup_lambda.sh

# Test manually
aws lambda invoke --function-name PriceHistoryCleanup output.json
```

---

## Docker Deployment (Recommended)

Deploy all services using Docker containers to AWS Lambda:

### Prerequisites

- Docker installed and running
- AWS CLI configured with appropriate permissions
- AWS account with ECR access

### Quick Deploy (All Services)

```bash
# Deploy everything - creates ECR repos, builds images, deploys Lambdas, sets up schedules
./deploy.sh

# After deployment, set Kalshi credentials
aws lambda update-function-configuration \
  --function-name BTCTradingBot \
  --environment "Variables={KALSHI_KEY_ID=xxx,KALSHI_PRIVATE_KEY=xxx}"

aws lambda update-function-configuration \
  --function-name KalshiWeatherTradingBot \
  --environment "Variables={KALSHI_KEY_ID=xxx,KALSHI_PRIVATE_KEY=xxx}"
```

### Deploy Individual Services

```bash
./deploy.sh btc       # Deploy BTC trading bot only
./deploy.sh eth       # Deploy ETH infrastructure only
./deploy.sh weather   # Deploy Weather bot only
./deploy.sh cleanup   # Deploy cleanup Lambda only
./deploy.sh tables    # Create DynamoDB tables only
./deploy.sh schedules # Setup EventBridge schedules only
./deploy.sh images    # Build and push Docker images only
```

### Deploy to Different Region

```bash
./deploy.sh -r us-east-2 all
```

### Local Development

Use Docker Compose for local testing with a local DynamoDB:

```bash
# Start local environment
docker-compose up -d dynamodb-local

# Run a specific Lambda locally
docker-compose run btc-price-collector

# Run all services
docker-compose up
```

### Docker Images

Each service has its own Dockerfile optimized for AWS Lambda:

| Service | Dockerfile | ECR Repository |
|---------|------------|----------------|
| BTC | `btc/Dockerfile` | `btc-thorp/btc` |
| ETH | `eth/Dockerfile` | `btc-thorp/eth` |
| Weather | `weather/Dockerfile` | `btc-thorp/weather` |
| Cleanup | `scripts/Dockerfile` | `btc-thorp/cleanup` |

---

## Project Structure

```
btc-thorp/
├── deploy.sh                         # Unified Docker deployment script
├── docker-compose.yml                # Local development environment
├── btc/
│   ├── Dockerfile                    # BTC Lambda Docker image
│   ├── requirements.txt              # Python dependencies
│   ├── lambda_package/
│   │   ├── btc_lambda_function.py    # Trading bot (volatility-based NO strategy)
│   │   ├── btc_price_collector.py    # Price collector (every minute)
│   │   ├── btc_volatility_api.py     # Volatility API endpoint
│   │   └── kalshi_client.py          # Kalshi API client (RSA-PSS auth)
│   └── scripts/                      # Legacy zip deployment scripts
│
├── eth/
│   ├── Dockerfile                    # ETH Lambda Docker image
│   ├── requirements.txt              # Python dependencies
│   ├── lambda_package/
│   │   ├── eth_price_collector.py    # ETH price collector
│   │   └── eth_volatility_api.py     # ETH volatility API
│   └── scripts/                      # Legacy zip deployment scripts
│
├── weather/
│   ├── Dockerfile                    # Weather Lambda Docker image
│   ├── requirements.txt              # Python dependencies
│   ├── lambda_package/
│   │   ├── lambda_function.py        # Weather bot main logic
│   │   ├── kalshi_client.py          # Kalshi API client
│   │   ├── trading_executor.py       # Order placement
│   │   └── cancel_all_open_orders.py # Utility to cancel orders
│   └── scripts/                      # Legacy zip deployment scripts
│
├── scripts/
│   ├── Dockerfile                    # Cleanup Lambda Docker image
│   ├── requirements.txt              # Python dependencies
│   └── price_history_cleanup.py      # Cleanup Lambda (180 min retention)
│
├── .env.example                      # Environment variables template
└── README.md
```

## AWS Resources

### Lambdas

| Lambda | Trigger | Description |
|--------|---------|-------------|
| BTCPriceCollector | Every minute | Collects BTC price, calculates volatility |
| BTCTradingBot | XX:45 (hourly) | Trades BTC hourly NO contracts |
| ETHPriceCollector | Every minute | Collects ETH price, calculates volatility |
| ETHVolatilityAPI | API Gateway | Returns ETH volatility metrics |
| PriceHistoryCleanup | Every hour | Removes data older than 180 min |
| KalshiWeatherTradingBot | 6pm/8pm ET | Weather liquidity provider |

### DynamoDB Tables

| Table | Purpose |
|-------|---------|
| BTCPriceHistory | BTC prices and volatility metrics |
| BTCTradeLog | BTC trade history and analytics |
| ETHPriceHistory | ETH prices and volatility metrics |
| KalshiTradingBudget | Weather bot daily budget tracking |

### API Gateway

| API | Endpoint | Auth |
|-----|----------|------|
| ETHVolatilityAPI | POST /volatility | Bearer token |

## Cost

- **Lambda**: Free tier (low invocation count)
- **DynamoDB**: Free tier (on-demand billing)
- **API Gateway**: Free tier (1M requests/month)
- **Total AWS**: ~$0/month

## License

MIT
