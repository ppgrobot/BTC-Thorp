# BTC Hourly Trading Strategy

## The Blackjack Analogy

This trading bot operates on the same mathematical principles as card counting in blackjack. Both strategies share key characteristics that make them profitable over time:

| Concept | Blackjack | BTC Hourly Bot |
|---------|-----------|----------------|
| Edge | 1-2% from card counting | 10-20% from volatility mispricing |
| Bounded Risk | Lose your bet, nothing more | Lose your premium, nothing more |
| Position Sizing | Kelly criterion | Kelly criterion (quarter Kelly) |
| Sample Size | Hundreds of hands | 24 trades/day, 720/month |
| House Advantage | Overcome by counting | Overcome by better volatility model |

The critical insight is that **binary options have bounded downside**. Unlike futures or margin trading where a black swan can wipe you out, the worst case here is losing your premium. This makes Kelly criterion sizing safe and effective.

---

## The Math

### Random Walk Model

We assume BTC price follows a random walk over short time horizons:

```
P(t+dt) = P(t) * (1 + e)
where e ~ N(0, sigma^2)
```

Given 15-minute realized volatility (sigma_15m), we scale to the time remaining:

```
sigma_scaled = sigma_15m * sqrt(minutes_remaining / 15)
```

### Probability Calculation

For a NO contract (betting price stays BELOW strike):

```
distance = (strike - current_price) / current_price * 100  (as %)
z_score = distance / sigma_scaled
P(NO wins) = Phi(z)  (standard normal CDF)
```

**Example** (from actual trade):
- BTC Price: $90,255
- Strike: $90,500 (27 bps above)
- 15m Volatility: 0.08%
- Minutes remaining: 15
- Scaled volatility: 0.08%
- Z-score: 0.27 / 0.08 = 3.4
- P(NO wins) = Phi(3.4) = 99.97%

### Edge Calculation

```
Model probability:  P_model = 99.97%
Market NO price:    81 cents (implies 81% probability)
Edge = P_model - P_market = 99.97% - 81% = +18.97%
```

### Kelly Criterion

The Kelly formula for optimal bet sizing with asymmetric payoffs:

```
f* = (b * p - q) / b

where:
  p = probability of winning (model estimate)
  q = 1 - p (probability of losing)
  b = profit/loss ratio = (100 - NO_price) / NO_price
```

**Example** at 81 cents NO:
- Win: +19 cents
- Lose: -81 cents
- b = 19/81 = 0.235
- p = 0.9997, q = 0.0003
- f* = (0.235 * 0.9997 - 0.0003) / 0.235 = 99.8%

Kelly says bet almost everything! But we use **quarter Kelly (25%)** for safety:
- Reduces variance
- Accounts for model uncertainty
- Still compounds effectively

---

## Why This Works

### 1. Market Inefficiency

These hourly binary markets are likely inefficient because:
- They're relatively new
- Low liquidity means wide spreads
- Market makers use crude models
- Short time horizon seems "too short" for sophisticated analysis

### 2. Volatility Edge

The market prices NO contracts as if large moves are more likely than they are. Our realized volatility shows BTC typically moves 0.05-0.15% in 15 minutes, yet the market prices 20+ bps moves as having 15-20% probability.

### 3. Bounded Downside

Unlike traditional options or futures:
- Maximum loss = premium paid (e.g., 81 cents/contract)
- No margin calls
- No gap risk beyond the binary outcome
- Perfect for Kelly sizing

### 4. Law of Large Numbers

With 24 trades per day:
- ~720 trades per month
- ~8,640 trades per year
- Variance averages out quickly
- Even a small edge becomes highly profitable

---

## Expected Value Per Trade

At typical conditions (81 cent NO, 99% model probability):

```
EV = P(win) * profit - P(lose) * loss
EV = 0.99 * $0.19 - 0.01 * $0.81
EV = $0.188 - $0.008
EV = +$0.18 per contract
```

With 5 contracts per trade:
- EV per trade: +$0.90
- 24 trades/day: +$21.60/day
- 30 days: +$648/month

And this assumes the 5-contract cap. With uncapped quarter Kelly, positions grow with bankroll.

---

## Risk Factors

### Model Risk
The random walk assumption may not hold during:
- Major news events
- Exchange outages
- Flash crashes
- Coordinated market movements

**Mitigation**: Quarter Kelly sizing, minimum edge threshold (3%)

### Execution Risk
- API failures
- Order rejection
- Price slippage
- Lambda timeouts

**Mitigation**: Logging, error handling, market orders

### Liquidity Risk
- Wide bid-ask spreads
- Thin order books
- Unable to fill at desired price

**Mitigation**: MAX_CONTRACTS cap, only trade liquid strikes

---

## Parameter Tuning

| Parameter | Current | Conservative | Aggressive |
|-----------|---------|--------------|------------|
| MIN_BPS_ABOVE | 20 | 30 | 15 |
| MIN_EDGE_PCT | 3% | 5% | 2% |
| MAX_KELLY_FRACTION | 0.25 | 0.10 | 0.50 |
| MAX_CONTRACTS | 5 | 3 | 10 |
| MIN_NO_PRICE | 50 | 60 | 40 |
| MAX_NO_PRICE | 99 | 95 | 99 |

---

## Comparison to Other Strategies

| Strategy | Edge | Risk | Frequency | Complexity |
|----------|------|------|-----------|------------|
| **This Bot** | 10-20% | Bounded | 24/day | Low |
| Blackjack counting | 1-2% | Bounded | Variable | Medium |
| Sports betting | 2-5% | Bounded | Variable | High |
| Options selling | 5-10% | Unbounded | Weekly | High |
| HFT | <0.1% | Low | 1000s/sec | Extreme |

The combination of high edge, bounded risk, and high frequency makes this strategy unusually attractive.

---

## Conclusion

This is not a "get rich quick" scheme - it's applied probability theory. The strategy works because:

1. **The model is simple but accurate** for short time horizons
2. **The market is inefficient** at pricing these contracts
3. **Risk is bounded** enabling aggressive Kelly sizing
4. **Frequency is high** allowing the law of large numbers to work

Like card counting, the edge is real but requires discipline:
- Trust the math
- Don't override the model
- Let it run
- Review performance over hundreds of trades, not individual outcomes
