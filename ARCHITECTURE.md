# Architecture

System architecture for the Kalshi Trading Bots.

## High-Level Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              AWS Cloud                                       │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                         EventBridge Rules                             │  │
│  │                                                                       │  │
│  │  BTCPriceCollector-EveryMinute ──→ rate(1 minute)                    │  │
│  │  BTCTradingBot-Minute45 ─────────→ cron(45 * * * ? *)                │  │
│  │  ETHPriceCollector-EveryMinute ──→ rate(1 minute)                    │  │
│  │  PriceHistoryCleanup-Hourly ────→ rate(1 hour)                       │  │
│  │  Weather-6pm-ET ────────────────→ cron(0 23 * * ? *)                 │  │
│  │  Weather-8pm-ET ────────────────→ cron(0 1 * * ? *)                  │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                    │                                        │
│                                    ▼                                        │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                           Lambda Functions                            │  │
│  │                                                                       │  │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐       │  │
│  │  │BTCPriceCollector│  │  BTCTradingBot  │  │ETHPriceCollector│       │  │
│  │  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘       │  │
│  │           │                    │                    │                │  │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐       │  │
│  │  │ETHVolatilityAPI │  │PriceHistoryClean│  │KalshiWeatherBot │       │  │
│  │  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘       │  │
│  └───────────┼────────────────────┼────────────────────┼────────────────┘  │
│              │                    │                    │                   │
│              ▼                    ▼                    ▼                   │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                           DynamoDB Tables                             │  │
│  │                                                                       │  │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐       │  │
│  │  │ BTCPriceHistory │  │   BTCTradeLog   │  │ ETHPriceHistory │       │  │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────┘       │  │
│  │                                                                       │  │
│  │  ┌─────────────────┐                                                 │  │
│  │  │KalshiTradingBdgt│                                                 │  │
│  │  └─────────────────┘                                                 │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           External Services                                  │
│                                                                             │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐             │
│  │  Coinbase API   │  │   Kalshi API    │  │   NWS API       │             │
│  │  (BTC/ETH Price)│  │   (Trading)     │  │   (Weather)     │             │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘             │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## BTC Trading Bot Data Flow

```
                              Every Minute
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          BTCPriceCollector                                   │
│                                                                             │
│   1. Fetch BTC price from Coinbase API                                      │
│   2. Store price record in DynamoDB (pk=PRICE, sk=timestamp)                │
│   3. Query last 15/30/60/90/120 minutes of prices                           │
│   4. Calculate volatility metrics (std dev, range, max move)                │
│   5. Update VOL/LATEST record in DynamoDB                                   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           BTCPriceHistory Table                              │
│                                                                             │
│   pk          │ sk                    │ price    │ vol_15m_std │ ...        │
│   ────────────┼───────────────────────┼──────────┼─────────────┼────────    │
│   PRICE       │ 2025-12-11T01:45:00   │ 97234.50 │             │            │
│   PRICE       │ 2025-12-11T01:46:00   │ 97256.12 │             │            │
│   VOL         │ LATEST                │          │ 0.0823      │ ...        │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                   │
                              At Minute 45
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            BTCTradingBot                                     │
│                                                                             │
│   1. Check if minute == 45 (else exit)                                      │
│   2. Get account balance from Kalshi                                        │
│   3. Read VOL/LATEST from DynamoDB                                          │
│   4. Fetch current BTC price from Coinbase                                  │
│   5. Get next hour's markets from Kalshi (KXBTCD-YYMONDDHH)                 │
│   6. Find first strike ≥ 20bps above current price                          │
│   7. Calculate model probability using normal CDF:                          │
│      - Scale 15m vol to time remaining: vol × √(minutes/15)                 │
│      - Z-score = (strike - current) / scaled_vol                            │
│      - P(NO wins) = Φ(z)                                                    │
│   8. Compare to market NO price → calculate edge                            │
│   9. If edge ≥ 3%, calculate Kelly bet size (quarter Kelly, max 5)          │
│  10. Execute trade on Kalshi API                                            │
│  11. Log trade to BTCTradeLog table                                         │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            BTCTradeLog Table                                 │
│                                                                             │
│   pk     │ sk                    │ ticker           │ qty │ edge  │ status  │
│   ───────┼───────────────────────┼──────────────────┼─────┼───────┼─────────│
│   TRADE  │ 2025-12-11T01:45:03   │ KXBTCD-25DEC1102 │ 5   │ 4.2%  │ executed│
│   TRADE  │ 2025-12-11T02:45:01   │ KXBTCD-25DEC1103 │ 3   │ 3.1%  │ executed│
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Pricing Model

The bot uses a **random walk model** to price NO contracts on Bitcoin hourly markets.

### Assumption

Bitcoin price movement over short time horizons (15-60 minutes) follows a random walk:

```
P(t+Δt) = P(t) × (1 + ε)
where ε ~ N(0, σ²)
```

### Model Calculation

```
Given:
  - Current BTC price: P
  - Strike price: K
  - Minutes to settlement: T
  - 15-minute realized volatility: σ_15m (as percentage)

Step 1: Scale volatility to time remaining
  σ_scaled = σ_15m × √(T / 15)

Step 2: Calculate distance to strike
  distance = (K - P) / P × 100  (as percentage)

Step 3: Calculate Z-score
  z = distance / σ_scaled

Step 4: Calculate probability NO wins (BTC stays below K)
  P(NO) = Φ(z)  (standard normal CDF)
```

### Edge Calculation

```
Model probability:  P_model = Φ(z)
Market probability: P_market = NO_price / 100

Edge = P_model - P_market

Trade if: Edge ≥ 3%
```

### Kelly Criterion Sizing

```
Odds ratio: b = (100 - NO_price) / NO_price
Kelly fraction: f* = (b × P_model - (1 - P_model)) / b

Applied fraction: min(f*, 0.25)  # Quarter Kelly
Contracts: min(floor(bankroll × f / NO_price), 5)  # Max 5 contracts
```

---

## EventBridge Rules

| Rule Name | Schedule | Target Lambda | Purpose |
|-----------|----------|---------------|---------|
| BTCPriceCollector-EveryMinute | `rate(1 minute)` | BTCPriceCollector | Collect BTC prices |
| BTCTradingBot-Minute45 | `cron(45 * * * ? *)` | BTCTradingBot | Execute trades at :45 |
| ETHPriceCollector-EveryMinute | `rate(1 minute)` | ETHPriceCollector | Collect ETH prices |
| PriceHistoryCleanup-Hourly | `rate(1 hour)` | PriceHistoryCleanup | Delete old price data |

---

## DynamoDB Schema

### BTCPriceHistory / ETHPriceHistory

| Attribute | Type | Description |
|-----------|------|-------------|
| pk | String (PK) | `PRICE` or `VOL` |
| sk | String (SK) | ISO timestamp or `LATEST` |
| price | Number | BTC/ETH price in USD |
| vol_15m_std | Number | 15-min volatility (std dev %) |
| vol_15m_range | Number | 15-min price range % |
| vol_15m_max_move | Number | Max single-minute move % |
| vol_15m_samples | Number | Number of samples |
| vol_30m_std | Number | 30-min volatility |
| ... | ... | Additional timeframes |

### BTCTradeLog

| Attribute | Type | Description |
|-----------|------|-------------|
| pk | String (PK) | `TRADE` |
| sk | String (SK) | ISO timestamp |
| contract_ticker | String | e.g., `KXBTCD-25DEC1102` |
| side | String | `NO` |
| quantity | Number | Contracts purchased |
| price_cents | Number | Price per contract |
| btc_price | Number | BTC price at trade time |
| strike_price | Number | Contract strike price |
| model_prob | Number | Model's probability |
| market_prob | Number | Market's implied probability |
| edge | Number | Edge in percentage points |
| kelly_fraction | Number | Kelly fraction used |
| status | String | `executed`, `failed`, `rejected` |

---

## Security

### Kalshi API Authentication

The bot uses RSA-PSS signatures for Kalshi API authentication:

1. Generate RSA key pair (2048-bit)
2. Store private key in Lambda environment variable
3. For each request:
   - Create signature payload: `timestamp + method + path`
   - Sign with RSA-PSS (SHA-256)
   - Include in headers: `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-SIGNATURE`, `KALSHI-ACCESS-TIMESTAMP`

### AWS IAM

Lambda execution role requires:
- `dynamodb:GetItem`, `dynamodb:PutItem`, `dynamodb:Query`, `dynamodb:Scan`, `dynamodb:DeleteItem`
- `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents`

---

## Monitoring

### CloudWatch Logs

```bash
# BTC Price Collector
aws logs tail /aws/lambda/BTCPriceCollector --follow

# BTC Trading Bot
aws logs tail /aws/lambda/BTCTradingBot --follow

# ETH Price Collector
aws logs tail /aws/lambda/ETHPriceCollector --follow
```

### Trade History

```bash
# Query all trades
aws dynamodb scan --table-name BTCTradeLog --region us-east-1

# Query trades from specific day
aws dynamodb query \
  --table-name BTCTradeLog \
  --key-condition-expression "pk = :pk AND begins_with(sk, :date)" \
  --expression-attribute-values '{":pk":{"S":"TRADE"},":date":{"S":"2025-12-11"}}' \
  --region us-east-1
```

---

## Cost Analysis

| Service | Usage | Monthly Cost |
|---------|-------|--------------|
| Lambda | ~45K invocations/month | Free tier |
| DynamoDB | ~1M reads, 50K writes | Free tier |
| EventBridge | ~45K invocations/month | Free tier |
| CloudWatch Logs | ~100MB/month | ~$0.50 |
| **Total** | | **~$0.50/month** |
