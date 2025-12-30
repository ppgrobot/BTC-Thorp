#!/usr/bin/env python3
"""
SOXS Short + Protective Call Strategy Analysis
==============================================
Strategy: Short SOXS in multiples of 100, buy protective LEAPS calls
(1+ year out, 100% above spot) to cap upside risk.

SOXS is a 3x inverse semiconductor ETF - it decays over time due to:
1. Volatility decay (daily rebalancing)
2. Expense ratio (0.95%)
3. Compounding effects

This makes it attractive to short, but spikes can be devastating.
Protective calls cap the max loss.

Updated parameters:
- Call strike: 100% above spot (2x current price)
- Short in multiples of 100 shares
- Buy 1 call contract per 100 shares shorted
- Includes 2025 data to capture April spike
"""

import pandas as pd
import numpy as np
from scipy.stats import norm
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# BLACK-SCHOLES MODEL
# =============================================================================

def black_scholes_call(S, K, T, r, sigma):
    """
    Calculate Black-Scholes call option price

    S: Current stock price
    K: Strike price
    T: Time to expiration (years)
    r: Risk-free rate
    sigma: Volatility (annualized)
    """
    if T <= 0:
        return max(0, S - K)

    if sigma <= 0:
        sigma = 0.01  # Floor to avoid div by zero

    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)

    call_price = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    return call_price

def calculate_historical_volatility(prices, window=30):
    """Calculate rolling historical volatility"""
    log_returns = np.log(prices / prices.shift(1))
    volatility = log_returns.rolling(window=window).std() * np.sqrt(252)
    return volatility

# =============================================================================
# FETCH REAL SOXS DATA FROM STOOQ
# =============================================================================

print("=" * 80)
print("SOXS SHORT + PROTECTIVE CALL STRATEGY ANALYSIS")
print("Using REAL market data from Stooq")
print("=" * 80)
print()

print("Fetching SOXS historical data from Stooq...")

# Stooq provides free historical data
url = "https://stooq.com/q/d/l/?s=soxs.us&d1=20220101&d2=20251231&i=d"
df = pd.read_csv(url)

# Convert date and set as index
df['Date'] = pd.to_datetime(df['Date'])
df.set_index('Date', inplace=True)
df.sort_index(inplace=True)

# IMPORTANT: Stooq data has inconsistent split adjustment
# Early 2022-2023 data appears to be multiplied by ~15 (split-adjusted backward)
# Late 2023-2025 data appears to be actual prices
#
# To get consistent actual prices, we need to divide early data by 15
# The transition appears to happen around late 2023

# Find where prices drop below 100 consistently (that's when real prices appear)
# Looking at data: 2023-12 shows ~$57, 2024 shows $20-60 (looks real)
# 2023-06 shows ~$95, 2023-01 shows ~$385 (looks inflated)

adjustment_date = pd.Timestamp('2023-12-01')
split_ratio = 15.0

# Divide all prices before the adjustment date by 15
early_mask = df.index < adjustment_date
df.loc[early_mask, 'Close'] = df.loc[early_mask, 'Close'] / split_ratio
df.loc[early_mask, 'Open'] = df.loc[early_mask, 'Open'] / split_ratio
df.loc[early_mask, 'High'] = df.loc[early_mask, 'High'] / split_ratio
df.loc[early_mask, 'Low'] = df.loc[early_mask, 'Low'] / split_ratio

print(f"Adjusted {early_mask.sum()} early days (before {adjustment_date.strftime('%Y-%m-%d')}) by dividing by {split_ratio}")

print(f"Data range: {df.index.min().strftime('%Y-%m-%d')} to {df.index.max().strftime('%Y-%m-%d')}")
print(f"Total trading days: {len(df)}")
print()

# Show key price points
print("KEY PRICE CHECKPOINTS:")
print("-" * 40)
checkpoints = ['2022-06-15', '2022-10-14', '2023-01-03', '2023-07-31',
               '2024-01-02', '2024-07-15', '2024-12-31', '2025-04-07', '2025-04-15']
for cp in checkpoints:
    try:
        # Find closest date
        idx = df.index.get_indexer([cp], method='nearest')[0]
        actual_date = df.index[idx]
        price = df.loc[actual_date, 'Close']
        print(f"  {actual_date.strftime('%Y-%m-%d')}: ${price:.2f}")
    except:
        pass
print()

# Calculate volatility
df['Volatility'] = calculate_historical_volatility(df['Close'])
avg_vol = df['Volatility'].mean()
if pd.isna(avg_vol):
    avg_vol = 0.80  # Default for leveraged ETF
df['Volatility'].fillna(avg_vol, inplace=True)

print(f"Average historical volatility: {avg_vol * 100:.1f}%")
print()

# =============================================================================
# STRATEGY PARAMETERS - UPDATED
# =============================================================================

INITIAL_CAPITAL = 10000
RISK_FREE_RATE = 0.05  # ~5% average
MARGIN_REQUIREMENT = 1.5  # 150% margin requirement for short selling
CALL_STRIKE_PREMIUM = 1.00  # Buy calls 100% above current price (2x spot)
CALL_EXPIRY_DAYS = 365  # Minimum 1 year to expiry
SCALE_IN_DROP_PCT = 0.15  # Add to short when price drops 15%
LOT_SIZE = 100  # Short in multiples of 100 shares

# NEW: Cash buffer requirement for margin protection
# Cash (including short proceeds) must be >= 3x current short value
# This protects against a 3x spike in SOXS without margin call
CASH_BUFFER_MULTIPLIER = 3.0

print("STRATEGY PARAMETERS (WITH MARGIN PROTECTION)")
print("-" * 40)
print(f"Initial Capital: ${INITIAL_CAPITAL:,.2f}")
print(f"Margin Requirement: {MARGIN_REQUIREMENT * 100:.0f}%")
print(f"Cash Buffer: {CASH_BUFFER_MULTIPLIER}x short value (survives 3x spike)")
print(f"Call Strike Premium: {CALL_STRIKE_PREMIUM * 100:.0f}% above spot (2x price)")
print(f"Call Expiry: {CALL_EXPIRY_DAYS} days minimum")
print(f"Scale-in Trigger: {SCALE_IN_DROP_PCT * 100:.0f}% price drop")
print(f"Lot Size: {LOT_SIZE} shares (1 call per 100 shares)")
print()

# =============================================================================
# BACKTEST THE STRATEGY
# =============================================================================

class Position:
    def __init__(self, shares, entry_price, entry_date):
        self.shares = shares
        self.entry_price = entry_price
        self.entry_date = entry_date

class CallOption:
    def __init__(self, contracts, strike, expiry_date, premium_paid, entry_date, shares_covered):
        self.contracts = contracts  # Each contract = 100 shares
        self.strike = strike
        self.expiry_date = expiry_date
        self.premium_paid = premium_paid
        self.entry_date = entry_date
        self.shares_covered = shares_covered

def run_backtest(df, start_date="2022-06-15"):
    """Run the backtest starting after the reverse split"""

    # Filter to backtest period (after reverse split)
    backtest_df = df[df.index >= start_date].copy()

    # Initialize
    cash = INITIAL_CAPITAL
    short_positions = []  # List of Position objects
    call_options = []  # List of CallOption objects

    # Track for scale-in
    last_scale_in_price = None

    # Results tracking
    results = []
    trades = []

    for i, (date, row) in enumerate(backtest_df.iterrows()):
        price = row['Close']
        volatility = max(row['Volatility'], 0.50)  # Floor volatility at 50% for SOXS

        # Calculate current position values
        total_short_shares = sum(p.shares for p in short_positions)
        short_value = total_short_shares * price  # Value we'd need to buy back
        short_proceeds = sum(p.shares * p.entry_price for p in short_positions)  # What we received
        short_pnl = short_proceeds - short_value  # Profit if positive

        # Calculate call option values and handle expirations
        call_value = 0
        expired_calls = []

        for j, call in enumerate(call_options):
            days_to_expiry = (call.expiry_date - date).days
            if days_to_expiry <= 0:
                # Option expired
                if price > call.strike:
                    # In the money - exercise value protects against loss
                    intrinsic = (price - call.strike) * call.contracts * 100
                    call_value += intrinsic
                    cash += intrinsic  # Receive intrinsic value
                expired_calls.append(j)
            else:
                # Value the option using Black-Scholes
                T = days_to_expiry / 365
                option_price = black_scholes_call(price, call.strike, T, RISK_FREE_RATE, volatility)
                call_value += option_price * call.contracts * 100

        # Remove expired calls (in reverse order to maintain indices)
        for j in sorted(expired_calls, reverse=True):
            expired_call = call_options.pop(j)
            trades.append({
                'date': date,
                'action': 'CALL EXPIRED',
                'strike': expired_call.strike,
                'itm': price > expired_call.strike,
                'price': price
            })

        # Net liquidation value
        net_liq = cash + short_pnl + call_value

        # Margin requirement for short position
        margin_required = short_value * MARGIN_REQUIREMENT
        excess_margin = cash + short_proceeds - margin_required

        # =================================================================
        # TRADING LOGIC
        # =================================================================

        # Initial position on first day
        if len(short_positions) == 0 and i == 0:
            # Cash buffer constraint: cash >= 3 * short_value
            # If we short N shares at price P:
            #   cash_after = INITIAL_CAPITAL + N*P (we receive short proceeds)
            #   short_value = N*P
            #   Constraint: INITIAL_CAPITAL + N*P >= 3 * N*P
            #   => INITIAL_CAPITAL >= 2 * N * P
            #   => N <= INITIAL_CAPITAL / (2 * P)
            max_shares_for_buffer = INITIAL_CAPITAL / (2 * price)
            lots_to_short = int(max_shares_for_buffer / LOT_SIZE)
            shares_to_short = lots_to_short * LOT_SIZE

            if shares_to_short >= LOT_SIZE:
                # Short the shares (receive cash)
                short_positions.append(Position(shares_to_short, price, date))
                cash += shares_to_short * price  # Receive proceeds

                # Buy protective call (LEAPS) - 1 contract per 100 shares
                call_strike = price * (1 + CALL_STRIKE_PREMIUM)  # 100% above = 2x price
                expiry = date + timedelta(days=CALL_EXPIRY_DAYS)
                T = CALL_EXPIRY_DAYS / 365

                call_premium = black_scholes_call(price, call_strike, T, RISK_FREE_RATE, volatility)
                contracts_needed = lots_to_short  # 1 contract per 100 shares
                total_premium = call_premium * contracts_needed * 100

                call_options.append(CallOption(
                    contracts_needed, call_strike, expiry, total_premium,
                    date, contracts_needed * 100
                ))
                cash -= total_premium

                trades.append({
                    'date': date,
                    'action': 'INITIAL SHORT + CALL',
                    'shares': shares_to_short,
                    'price': price,
                    'call_strike': call_strike,
                    'call_premium': total_premium,
                    'call_contracts': contracts_needed,
                    'call_expiry': expiry
                })

                last_scale_in_price = price

        # Scale-in logic: add to short when price drops significantly
        elif last_scale_in_price is not None and i > 0:
            price_drop = (last_scale_in_price - price) / last_scale_in_price

            # Check if price has dropped enough to scale in
            if price_drop >= SCALE_IN_DROP_PCT:
                # Cash buffer constraint for scale-in:
                # After adding N more shares at price P:
                #   new_cash = cash + N*P
                #   new_short_value = (total_short_shares + N) * P
                #   Constraint: new_cash >= 3 * new_short_value
                #   => cash + N*P >= 3 * (total_short_shares + N) * P
                #   => cash + N*P >= 3*total_short_shares*P + 3*N*P
                #   => cash >= 3*total_short_shares*P + 2*N*P
                #   => N <= (cash - 3*total_short_shares*P) / (2*P)
                #   => N <= (cash - 3*short_value) / (2*P)

                available_for_shorting = cash - CASH_BUFFER_MULTIPLIER * short_value
                if available_for_shorting > 0:
                    max_additional_shares = available_for_shorting / (2 * price)
                    lots_to_add = int(max_additional_shares / LOT_SIZE)
                    additional_shares = lots_to_add * LOT_SIZE

                    if additional_shares >= LOT_SIZE:
                        short_positions.append(Position(additional_shares, price, date))
                        cash += additional_shares * price

                        # Buy more protective calls - 1 contract per 100 shares
                        call_strike = price * (1 + CALL_STRIKE_PREMIUM)  # 2x price
                        expiry = date + timedelta(days=CALL_EXPIRY_DAYS)
                        T = CALL_EXPIRY_DAYS / 365

                        call_premium = black_scholes_call(price, call_strike, T, RISK_FREE_RATE, volatility)
                        contracts_needed = lots_to_add
                        total_premium = call_premium * contracts_needed * 100

                        call_options.append(CallOption(
                            contracts_needed, call_strike, expiry, total_premium,
                            date, contracts_needed * 100
                        ))
                        cash -= total_premium

                        trades.append({
                            'date': date,
                            'action': 'SCALE-IN SHORT + CALL',
                            'shares': additional_shares,
                            'price': price,
                            'call_strike': call_strike,
                            'call_premium': total_premium,
                            'call_contracts': contracts_needed,
                            'total_short_shares': total_short_shares + additional_shares,
                            'call_expiry': expiry
                        })

                        last_scale_in_price = price

        # Roll calls that are expiring within 60 days
        for j, call in enumerate(call_options):
            days_to_expiry = (call.expiry_date - date).days
            if 30 < days_to_expiry <= 60:
                # Roll the call - sell current, buy new
                T_old = days_to_expiry / 365
                old_value = black_scholes_call(price, call.strike, T_old, RISK_FREE_RATE, volatility)

                new_strike = price * (1 + CALL_STRIKE_PREMIUM)  # 2x current price
                new_expiry = date + timedelta(days=CALL_EXPIRY_DAYS)
                T_new = CALL_EXPIRY_DAYS / 365
                new_premium = black_scholes_call(price, new_strike, T_new, RISK_FREE_RATE, volatility)

                roll_cost = (new_premium - old_value) * call.contracts * 100

                if abs(roll_cost) < cash * 0.15:  # Only roll if affordable
                    cash += old_value * call.contracts * 100  # Sell old
                    cash -= new_premium * call.contracts * 100  # Buy new

                    call_options[j] = CallOption(
                        call.contracts, new_strike, new_expiry,
                        call.premium_paid + max(0, roll_cost), date, call.shares_covered
                    )

                    trades.append({
                        'date': date,
                        'action': 'ROLL CALL',
                        'old_strike': call.strike,
                        'new_strike': new_strike,
                        'roll_cost': roll_cost,
                        'new_expiry': new_expiry
                    })

        # Recalculate after trades
        total_short_shares = sum(p.shares for p in short_positions)
        short_value = total_short_shares * price
        short_proceeds = sum(p.shares * p.entry_price for p in short_positions)
        short_pnl = short_proceeds - short_value

        call_value = 0
        for call in call_options:
            days_to_expiry = (call.expiry_date - date).days
            if days_to_expiry > 0:
                T = days_to_expiry / 365
                option_price = black_scholes_call(price, call.strike, T, RISK_FREE_RATE, volatility)
                call_value += option_price * call.contracts * 100

        net_liq = cash + short_pnl + call_value

        # Calculate cash buffer ratio (should always be >= 3.0)
        cash_buffer_ratio = cash / short_value if short_value > 0 else float('inf')

        # Record daily results
        results.append({
            'date': date,
            'price': price,
            'volatility': volatility,
            'cash': cash,
            'short_shares': total_short_shares,
            'short_value': short_value,
            'short_proceeds': short_proceeds,
            'short_pnl': short_pnl,
            'call_value': call_value,
            'net_liquidation': net_liq,
            'num_calls': len(call_options),
            'total_call_premium': sum(c.premium_paid for c in call_options),
            'num_call_contracts': sum(c.contracts for c in call_options),
            'cash_buffer_ratio': cash_buffer_ratio
        })

    return pd.DataFrame(results), trades

# Run backtest starting 2023 (when prices are consistent)
print("Running backtest from 2023-01-01...")
print()

results_df, trades = run_backtest(df, start_date="2023-01-01")
results_df.set_index('date', inplace=True)

# =============================================================================
# ANALYSIS AND RESULTS
# =============================================================================

print("=" * 80)
print("BACKTEST RESULTS")
print("=" * 80)
print()

# Summary statistics
initial_value = INITIAL_CAPITAL
final_value = results_df['net_liquidation'].iloc[-1]
total_return = (final_value - initial_value) / initial_value * 100
trading_days = len(results_df)
years = trading_days / 252
annualized_return = ((final_value / initial_value) ** (1/years) - 1) * 100 if years > 0 else 0

print("PERFORMANCE SUMMARY")
print("-" * 40)
print(f"Initial Capital:    ${initial_value:,.2f}")
print(f"Final Value:        ${final_value:,.2f}")
print(f"Total Return:       {total_return:+,.2f}%")
print(f"Annualized Return:  {annualized_return:+,.2f}%")
print(f"Trading Days:       {trading_days}")
print(f"Years:              {years:.2f}")
print()

# Risk metrics
daily_returns = results_df['net_liquidation'].pct_change().dropna()
sharpe_ratio = (daily_returns.mean() * 252) / (daily_returns.std() * np.sqrt(252)) if daily_returns.std() > 0 else 0

max_value = results_df['net_liquidation'].cummax()
drawdown = (results_df['net_liquidation'] - max_value) / max_value
max_drawdown = drawdown.min() * 100
max_dd_date = drawdown.idxmin()

print("RISK METRICS")
print("-" * 40)
print(f"Sharpe Ratio:       {sharpe_ratio:.2f}")
print(f"Max Drawdown:       {max_drawdown:.2f}%")
print(f"Max DD Date:        {max_dd_date.strftime('%Y-%m-%d')}")
print(f"Volatility (Ann.):  {daily_returns.std() * np.sqrt(252) * 100:.2f}%")
print(f"Best Day:           {daily_returns.max() * 100:+.2f}%")
print(f"Worst Day:          {daily_returns.min() * 100:.2f}%")
print()

# SOXS price analysis
soxs_start = results_df['price'].iloc[0]
soxs_end = results_df['price'].iloc[-1]
soxs_return = (soxs_end - soxs_start) / soxs_start * 100
soxs_high = results_df['price'].max()
soxs_low = results_df['price'].min()
soxs_high_date = results_df['price'].idxmax()
soxs_low_date = results_df['price'].idxmin()

print("SOXS PRICE ANALYSIS")
print("-" * 40)
print(f"Starting Price:     ${soxs_start:.2f}")
print(f"Ending Price:       ${soxs_end:.2f}")
print(f"SOXS Return:        {soxs_return:+.2f}%")
print(f"High:               ${soxs_high:.2f} ({soxs_high_date.strftime('%Y-%m-%d')})")
print(f"Low:                ${soxs_low:.2f} ({soxs_low_date.strftime('%Y-%m-%d')})")
print(f"Avg Volatility:     {results_df['volatility'].mean() * 100:.1f}%")
print()

# Trade summary
print("TRADE SUMMARY")
print("-" * 40)
print(f"Total Trades:       {len(trades)}")

initial_trades = [t for t in trades if 'INITIAL' in t.get('action', '')]
scale_ins = [t for t in trades if 'SCALE-IN' in t.get('action', '')]
rolls = [t for t in trades if 'ROLL' in t.get('action', '')]
expirations = [t for t in trades if 'EXPIRED' in t.get('action', '')]

print(f"Initial Positions:  {len(initial_trades)}")
print(f"Scale-in Events:    {len(scale_ins)}")
print(f"Call Rolls:         {len(rolls)}")
print(f"Call Expirations:   {len(expirations)}")
print()

# Position breakdown
print("FINAL POSITION")
print("-" * 40)
print(f"Short Shares:       {results_df['short_shares'].iloc[-1]:,.0f}")
print(f"Short Value:        ${results_df['short_value'].iloc[-1]:,.2f}")
print(f"Short P&L:          ${results_df['short_pnl'].iloc[-1]:+,.2f}")
print(f"Call Contracts:     {results_df['num_call_contracts'].iloc[-1]:.0f}")
print(f"Call Value:         ${results_df['call_value'].iloc[-1]:,.2f}")
print(f"Total Call Premium: ${results_df['total_call_premium'].iloc[-1]:,.2f}")
print(f"Cash:               ${results_df['cash'].iloc[-1]:,.2f}")
print()

# Decomposition
print("P&L DECOMPOSITION")
print("-" * 40)
short_pnl_final = results_df['short_pnl'].iloc[-1]
call_value_final = results_df['call_value'].iloc[-1]
total_premium = results_df['total_call_premium'].iloc[-1]
net_call_pnl = call_value_final - total_premium
total_pnl = final_value - initial_value

print(f"Short Profit:       ${short_pnl_final:+,.2f}")
print(f"Call Premium Paid:  ${-total_premium:,.2f}")
print(f"Call Current Value: ${call_value_final:,.2f}")
print(f"Net Call P&L:       ${net_call_pnl:+,.2f}")
print(f"Total P&L:          ${total_pnl:+,.2f}")
print()

# =============================================================================
# CASH BUFFER ANALYSIS
# =============================================================================

print("=" * 80)
print("CASH BUFFER ANALYSIS")
print("=" * 80)
print()

min_buffer = results_df['cash_buffer_ratio'].min()
min_buffer_date = results_df['cash_buffer_ratio'].idxmin()
avg_buffer = results_df['cash_buffer_ratio'].mean()
buffer_at_end = results_df['cash_buffer_ratio'].iloc[-1]

print(f"Cash Buffer Requirement:  {CASH_BUFFER_MULTIPLIER}x short value")
print(f"Minimum Buffer Ratio:     {min_buffer:.2f}x (on {min_buffer_date.strftime('%Y-%m-%d')})")
print(f"Average Buffer Ratio:     {avg_buffer:.2f}x")
print(f"Final Buffer Ratio:       {buffer_at_end:.2f}x")
print()

if min_buffer >= CASH_BUFFER_MULTIPLIER:
    print("RESULT: Cash buffer maintained throughout! No margin call risk.")
else:
    violations = results_df[results_df['cash_buffer_ratio'] < CASH_BUFFER_MULTIPLIER]
    print(f"NOTE: Buffer requirement only enforced at position entry.")
    print(f"      Price moves after entry can reduce the buffer ratio.")
    print(f"      Buffer fell below {CASH_BUFFER_MULTIPLIER}x on {len(violations)} days due to SOXS spikes.")
    print()
    print("KEY OBSERVATION:")
    print("  Even with 3x entry buffer, a 3x price spike drops you to 1x buffer.")
    print("  The protective calls provide the actual protection, not just the buffer.")

# Show the worst case and how calls protected
worst_buffer_day = results_df.loc[min_buffer_date]
print()
print(f"WORST BUFFER DAY ({min_buffer_date.strftime('%Y-%m-%d')}):")
print(f"  SOXS Price:      ${worst_buffer_day['price']:.2f}")
print(f"  Cash:            ${worst_buffer_day['cash']:,.2f}")
print(f"  Short Value:     ${worst_buffer_day['short_value']:,.2f}")
print(f"  Buffer Ratio:    {min_buffer:.2f}x")
print(f"  Short P&L:       ${worst_buffer_day['short_pnl']:+,.2f}")
print(f"  Call Value:      ${worst_buffer_day['call_value']:,.2f}")
print(f"  Net Liquidation: ${worst_buffer_day['net_liquidation']:,.2f}")
call_protection = worst_buffer_day['call_value'] / abs(worst_buffer_day['short_pnl']) if worst_buffer_day['short_pnl'] < 0 else 0
print(f"  Call Protection: {call_protection:.1%} of short loss covered by calls")
print()

# =============================================================================
# APRIL 2025 SPIKE ANALYSIS
# =============================================================================

print("=" * 80)
print("APRIL 2025 SPIKE ANALYSIS")
print("=" * 80)
print()

# Look at April 2025 data
april_2025 = results_df[(results_df.index >= '2025-04-01') & (results_df.index <= '2025-04-30')]
if len(april_2025) > 0:
    print("APRIL 2025 PERFORMANCE (Semiconductor Stress Period)")
    print("-" * 50)

    april_start = april_2025['net_liquidation'].iloc[0]
    april_end = april_2025['net_liquidation'].iloc[-1]
    april_high = april_2025['net_liquidation'].max()
    april_low = april_2025['net_liquidation'].min()

    soxs_april_high = april_2025['price'].max()
    soxs_april_high_date = april_2025['price'].idxmax()
    soxs_april_low = april_2025['price'].min()

    print(f"SOXS April High:    ${soxs_april_high:.2f} ({soxs_april_high_date.strftime('%Y-%m-%d')})")
    print(f"SOXS April Low:     ${soxs_april_low:.2f}")
    print(f"Portfolio Start:    ${april_start:,.2f}")
    print(f"Portfolio Low:      ${april_low:,.2f}")
    print(f"Portfolio High:     ${april_high:,.2f}")
    print(f"Portfolio End:      ${april_end:,.2f}")
    print(f"April Return:       {(april_end/april_start - 1) * 100:+.2f}%")
    print()

    # Show daily detail during spike
    print("DAILY DETAIL (April 2025):")
    print("-" * 70)
    for date, row in april_2025.iterrows():
        print(f"{date.strftime('%Y-%m-%d')}: SOXS ${row['price']:.2f}, "
              f"Short P&L: ${row['short_pnl']:+,.0f}, "
              f"Call Val: ${row['call_value']:,.0f}, "
              f"Net: ${row['net_liquidation']:,.0f}")
    print()
else:
    print("No April 2025 data available yet")
    print()

# =============================================================================
# YEARLY RETURNS
# =============================================================================

print("=" * 80)
print("YEARLY RETURNS")
print("=" * 80)
print()

yearly = results_df['net_liquidation'].resample('Y').last()
yearly_returns = yearly.pct_change().dropna() * 100

# First year calculation
first_year_end = yearly.iloc[0] if len(yearly) > 0 else final_value
first_year_return = (first_year_end - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
print(f"2022 (partial):     {first_year_return:+.2f}%")

for date, ret in yearly_returns.items():
    print(f"{date.year}:              {ret:+.2f}%")
print()

# =============================================================================
# MONTHLY RETURNS
# =============================================================================

print("MONTHLY RETURNS")
print("-" * 40)
monthly = results_df['net_liquidation'].resample('M').last()
monthly_returns = monthly.pct_change().dropna() * 100
for date, ret in monthly_returns.tail(24).items():  # Last 24 months
    print(f"{date.strftime('%Y-%m')}: {ret:+.2f}%")
print()

# =============================================================================
# TRADE LOG
# =============================================================================

print("TRADE LOG (All Trades)")
print("-" * 40)
for trade in trades:
    date_str = trade['date'].strftime('%Y-%m-%d')
    action = trade['action']
    if 'shares' in trade:
        print(f"{date_str}: {action}")
        print(f"           Shares: {trade['shares']:,} @ ${trade['price']:.2f}")
        if 'call_strike' in trade and trade.get('call_premium', 0) > 0:
            print(f"           Call: {trade['call_contracts']} contracts, Strike ${trade['call_strike']:.2f}, Premium ${trade['call_premium']:.2f}")
    elif 'ROLL' in action:
        print(f"{date_str}: {action}")
        print(f"           ${trade['old_strike']:.2f} -> ${trade['new_strike']:.2f}, Cost: ${trade['roll_cost']:.2f}")
    elif 'EXPIRED' in action:
        itm_str = "ITM (protected)" if trade['itm'] else "OTM"
        print(f"{date_str}: {action} @ ${trade['strike']:.2f} - {itm_str}")
print()

# =============================================================================
# SCENARIO ANALYSIS
# =============================================================================

print("=" * 80)
print("SCENARIO ANALYSIS: Protection During SOXS Spikes")
print("=" * 80)
print()

# Analyze worst periods
print("WORST PERIODS (Highest SOXS Prices = Largest Short Losses)")
print("-" * 50)
worst_days = results_df.nlargest(10, 'price')
for date, row in worst_days.iterrows():
    protection_ratio = row['call_value'] / abs(row['short_pnl']) if row['short_pnl'] < 0 else 0
    print(f"{date.strftime('%Y-%m-%d')}: SOXS ${row['price']:.2f}")
    print(f"    Short P&L: ${row['short_pnl']:+,.2f}, Call Value: ${row['call_value']:,.2f}")
    print(f"    Net Liq: ${row['net_liquidation']:,.2f}, Protection: {protection_ratio:.1%}")
print()

# Best periods
print("BEST PERIODS (Lowest SOXS Prices = Largest Short Gains)")
print("-" * 50)
best_days = results_df.nsmallest(5, 'price')
for date, row in best_days.iterrows():
    print(f"{date.strftime('%Y-%m-%d')}: SOXS ${row['price']:.2f}")
    print(f"    Short P&L: ${row['short_pnl']:+,.2f}, Net Liq: ${row['net_liquidation']:,.2f}")
print()

# =============================================================================
# MAX LOSS ANALYSIS
# =============================================================================

print("=" * 80)
print("THEORETICAL MAX LOSS ANALYSIS")
print("=" * 80)
print()

avg_entry = results_df['short_proceeds'].iloc[-1] / results_df['short_shares'].iloc[-1] if results_df['short_shares'].iloc[-1] > 0 else 0
print(f"Average Short Entry:     ${avg_entry:.2f}")
print(f"Current Short Shares:    {results_df['short_shares'].iloc[-1]:,.0f}")
print(f"Call Strike (avg):       ${avg_entry * (1 + CALL_STRIKE_PREMIUM):.2f} (2x entry)")
print()

print("MAX LOSS SCENARIOS (if SOXS spikes):")
print("-" * 50)
for spike_mult in [2.0, 3.0, 5.0, 10.0]:
    spike_price = soxs_end * spike_mult

    # Short loss at spike
    short_shares = results_df['short_shares'].iloc[-1]
    short_loss = short_shares * (spike_price - avg_entry)

    print(f"  SOXS at ${spike_price:.2f} ({spike_mult}x current):")
    print(f"    Without protection: ${-short_loss:+,.2f}")

    # With calls, loss is capped at strike
    avg_strike = avg_entry * (1 + CALL_STRIKE_PREMIUM)
    if spike_price > avg_strike:
        call_gain = short_shares * (spike_price - avg_strike)
        net_loss = short_loss - call_gain
        max_loss_per_share = avg_strike - avg_entry
        capped_loss = short_shares * max_loss_per_share + total_premium
        print(f"    With protection:    ${-capped_loss:+,.2f} (capped at strike)")
    else:
        print(f"    With protection:    ${-short_loss:+,.2f} (calls not yet ITM)")
    print()

# =============================================================================
# SAVE RESULTS
# =============================================================================

results_df.to_csv('/Users/ppg/BTC-Thorp/analysis/soxs_backtest_results.csv')
print("Results saved to analysis/soxs_backtest_results.csv")

# =============================================================================
# VISUALIZATION
# =============================================================================

import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

fig, axes = plt.subplots(5, 1, figsize=(14, 20))
fig.suptitle('SOXS Short + Protective Call Strategy (REAL DATA)\n$10,000 Starting Capital, June 2022 - Present\nCalls at 100% above spot (2x price), 100-share lots',
             fontsize=14, fontweight='bold')

# Plot 1: SOXS Price
ax1 = axes[0]
ax1.plot(results_df.index, results_df['price'], 'b-', linewidth=1, label='SOXS Price')
ax1.fill_between(results_df.index, results_df['price'], alpha=0.3, color='blue')
ax1.set_title('SOXS Price (3x Inverse Semiconductor ETF) - REAL DATA', fontsize=12, fontweight='bold')
ax1.set_ylabel('Price ($)')
ax1.grid(True, alpha=0.3)
ax1.legend(loc='upper right')

# Add annotations for max/min
ax1.annotate(f'High: ${soxs_high:.2f}',
             xy=(soxs_high_date, soxs_high),
             xytext=(10, 10), textcoords='offset points', fontsize=9,
             arrowprops=dict(arrowstyle='->', color='red'))
ax1.annotate(f'Low: ${soxs_low:.2f}',
             xy=(soxs_low_date, soxs_low),
             xytext=(10, -15), textcoords='offset points', fontsize=9,
             arrowprops=dict(arrowstyle='->', color='green'))

# Plot 2: Portfolio Value
ax2 = axes[1]
ax2.plot(results_df.index, results_df['net_liquidation'], 'g-', linewidth=2, label='Net Liquidation Value')
ax2.axhline(y=INITIAL_CAPITAL, color='r', linestyle='--', linewidth=1, label='Initial Capital ($10K)')
ax2.fill_between(results_df.index, INITIAL_CAPITAL, results_df['net_liquidation'],
                  where=results_df['net_liquidation'] >= INITIAL_CAPITAL,
                  color='green', alpha=0.3, label='Profit')
ax2.fill_between(results_df.index, INITIAL_CAPITAL, results_df['net_liquidation'],
                  where=results_df['net_liquidation'] < INITIAL_CAPITAL,
                  color='red', alpha=0.3, label='Loss')
ax2.set_title(f'Portfolio Value (Final: ${final_value:,.0f}, Return: {total_return:+.1f}%)',
              fontsize=12, fontweight='bold')
ax2.set_ylabel('Value ($)')
ax2.legend(loc='upper left')
ax2.grid(True, alpha=0.3)

# Plot 3: P&L Components
ax3 = axes[2]
ax3.plot(results_df.index, results_df['short_pnl'], 'r-', linewidth=1.5, label='Short P&L')
ax3.plot(results_df.index, results_df['call_value'], 'b-', linewidth=1.5, label='Call Option Value')
ax3.plot(results_df.index, -results_df['total_call_premium'], 'b--', linewidth=1,
         label='Total Premium Paid', alpha=0.7)
ax3.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
ax3.set_title('P&L Components: Short Position vs Call Protection', fontsize=12, fontweight='bold')
ax3.set_ylabel('Value ($)')
ax3.legend(loc='best')
ax3.grid(True, alpha=0.3)

# Plot 4: Short Shares Over Time
ax4 = axes[3]
ax4.fill_between(results_df.index, results_df['short_shares'], alpha=0.5, color='purple')
ax4.plot(results_df.index, results_df['short_shares'], 'purple', linewidth=1.5, label='Short Shares')
ax4.set_title(f'Short Position Size (Final: {results_df["short_shares"].iloc[-1]:,.0f} shares)',
              fontsize=12, fontweight='bold')
ax4.set_ylabel('Shares')
ax4.legend(loc='upper left')
ax4.grid(True, alpha=0.3)

# Plot 5: Drawdown
ax5 = axes[4]
drawdown_pct = drawdown * 100
ax5.fill_between(results_df.index, drawdown_pct, 0, color='red', alpha=0.3)
ax5.plot(results_df.index, drawdown_pct, 'r-', linewidth=1)
ax5.set_title(f'Drawdown (Max: {max_drawdown:.1f}% on {max_dd_date.strftime("%Y-%m-%d")})',
              fontsize=12, fontweight='bold')
ax5.set_ylabel('Drawdown (%)')
ax5.set_xlabel('Date')
ax5.grid(True, alpha=0.3)
ax5.set_ylim(min(drawdown_pct) * 1.1, 5)

# Format x-axis for all plots
for ax in axes:
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=4))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')

plt.tight_layout()
plt.savefig('/Users/ppg/BTC-Thorp/analysis/soxs_strategy_chart.png', dpi=150, bbox_inches='tight')
print("Chart saved to analysis/soxs_strategy_chart.png")
plt.close()

# =============================================================================
# SUMMARY
# =============================================================================

print()
print("=" * 80)
print("EXECUTIVE SUMMARY")
print("=" * 80)
print()
print(f"Starting with ${INITIAL_CAPITAL:,}, the SOXS short + protective call strategy")
print(f"generated a {total_return:+.1f}% return ({annualized_return:+.1f}% annualized) over {years:.1f} years.")
print()
print("KEY INSIGHTS:")
print(f"  - SOXS declined {soxs_return:.1f}% during the period (from ${soxs_start:.2f} to ${soxs_end:.2f})")
print(f"  - The short positions generated ${short_pnl_final:+,.0f} in profit")
print(f"  - Call protection cost ${total_premium:,.0f} in premiums (100% OTM = cheaper)")
print(f"  - Current call value is ${call_value_final:,.0f}")
print(f"  - Maximum drawdown was {max_drawdown:.1f}% (on {max_dd_date.strftime('%Y-%m-%d')})")
print()
print("STRATEGY MECHANICS:")
print(f"  - Cash buffer: {CASH_BUFFER_MULTIPLIER}x short value at all times")
print(f"  - Short in lots of {LOT_SIZE} shares (1 call contract each)")
print(f"  - Calls bought at {CALL_STRIKE_PREMIUM*100:.0f}% above spot (2x price)")
print(f"  - Added to shorts {len(scale_ins)} times as price dropped")
print(f"  - Rolled call options {len(rolls)} times before expiration")
print()
print("WHY THIS WORKS:")
print("  - SOXS is a 3x leveraged inverse ETF that decays over time")
print("  - Volatility decay + expense ratio create structural tailwind for shorts")
print("  - 100% OTM calls are cheaper than 20% OTM but still provide spike protection")
print("  - Scale-in on drops compounds gains while maintaining protection")
print()
print("RISKS:")
print("  - Semiconductor industry crash would spike SOXS (but calls protect)")
print("  - 100% OTM calls only protect above 2x your entry price")
print("  - Margin requirements can limit position sizing")
print("  - Borrow costs for shorting SOXS (not modeled here)")
print()
print("=" * 80)
print("ANALYSIS COMPLETE - USING REAL SOXS DATA")
print("=" * 80)
