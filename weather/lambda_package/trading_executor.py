"""
Trading execution module for Kalshi Liquidity Provider Strategy

Places resting bids at 99¢ on winning contracts to:
1. Earn liquidity incentive rewards for having orders on the book
2. Profit 1¢ per contract when filled (redeems for $1.00)
"""

import os
import boto3
from datetime import datetime, timedelta
from decimal import Decimal
import json
import time

from kalshi_client import KalshiClient


# Bid price for resting orders (99¢ = 1¢ profit per contract)
BID_PRICE_CENTS = 99


def get_et_time():
    """Get current Eastern Time"""
    utc_now = datetime.utcnow()
    month = utc_now.month
    if 3 <= month <= 11:
        return utc_now - timedelta(hours=4)  # EDT
    else:
        return utc_now - timedelta(hours=5)  # EST


def get_daily_trades(dynamodb, date_str):
    """Get all trades executed today from DynamoDB"""
    table = dynamodb.Table('KalshiTrades')

    try:
        response = table.query(
            IndexName='DateIndex',
            KeyConditionExpression=boto3.dynamodb.conditions.Key('trade_date').eq(date_str)
        )
        return response.get('Items', [])
    except Exception as e:
        print(f"Error querying trades: {e}")
        return []


def calculate_daily_spend_for_ticker(trades, ticker):
    """Calculate total amount spent today on a specific ticker"""
    total = Decimal('0')
    for trade in trades:
        if trade.get('ticker') == ticker:
            cost = trade.get('cost_cents', 0)
            fees = trade.get('fees_cents', 0)
            total += Decimal(str(cost)) + Decimal(str(fees))

    return float(total) / 100  # Convert cents to dollars


def record_trade(dynamodb, trade_details):
    """Store trade in DynamoDB"""
    table = dynamodb.Table('KalshiTrades')

    et_time = get_et_time()

    item = {
        'trade_id': trade_details['order_id'],
        'trade_date': et_time.strftime('%Y-%m-%d'),
        'timestamp': datetime.utcnow().isoformat(),
        'ticker': trade_details['ticker'],
        'side': trade_details['side'],
        'count': trade_details['count'],
        'price_cents': trade_details['price_cents'],
        'cost_cents': trade_details['cost_cents'],
        'fees_cents': trade_details['fees_cents'],
        'total_cost_cents': trade_details['cost_cents'] + trade_details['fees_cents'],
        'profit_cents': trade_details.get('profit_cents', 0),
        'roi_percent': Decimal(str(trade_details.get('roi_percent', 0))),
        'strategy': 'liquidity_provider'
    }

    try:
        table.put_item(Item=item)
        print(f"Recorded trade: {trade_details['ticker']}")
        return True
    except Exception as e:
        print(f"Error recording trade: {e}")
        return False


def execute_liquidity_trades(opportunities, max_daily_budget_per_contract=5.00, bid_price=BID_PRICE_CENTS):
    """
    Place resting bids at 99¢ on winning contracts.

    The orders will sit on the book to:
    1. Earn liquidity incentive rewards
    2. Get filled if someone sells at 99¢ (we profit 1¢ per contract)

    Args:
        opportunities: List of opportunities from find_liquidity_opportunities()
        max_daily_budget_per_contract: Maximum to spend per contract per day (default $5)
        bid_price: Price to bid in cents (default 99¢)

    Returns:
        List of placed orders (resting or filled)
    """
    dynamodb = boto3.resource('dynamodb')
    kalshi = KalshiClient()

    et_time = get_et_time()
    date_str = et_time.strftime('%Y-%m-%d')

    # Get today's trades
    todays_trades = get_daily_trades(dynamodb, date_str)

    placed_orders = []

    for opp in opportunities:
        ticker = opp['ticker']

        # Check if we already have a resting order for this ticker
        try:
            existing_orders = kalshi.get_orders(ticker=ticker, status='resting')
            resting_orders = existing_orders.get('orders', [])
            if resting_orders:
                total_resting = sum(o.get('remaining_count', 0) for o in resting_orders)
                print(f"⏭️ Skipping {ticker} - already have {len(resting_orders)} resting order(s) ({total_resting} contracts)")
                continue
        except Exception as e:
            print(f"Warning: Could not check existing orders for {ticker}: {e}")
            # Continue anyway - better to potentially duplicate than miss an opportunity

        # Check daily spend for this specific ticker
        daily_spend = calculate_daily_spend_for_ticker(todays_trades, ticker)

        if daily_spend >= max_daily_budget_per_contract:
            print(f"Daily budget for {ticker} reached (${daily_spend:.2f} / ${max_daily_budget_per_contract:.2f})")
            continue

        remaining_budget = max_daily_budget_per_contract - daily_spend

        # Calculate how many contracts we can bid for at bid_price
        # Including ~3% fees: count * bid_price * 1.03 <= remaining_budget * 100
        max_contracts = int((remaining_budget * 100) / (bid_price * 1.03))

        if max_contracts < 1:
            print(f"Not enough budget for {ticker} (need ~${bid_price * 1.03 / 100:.2f}, have ${remaining_budget:.2f})")
            continue

        count = max_contracts
        estimated_cost = (count * bid_price) / 100
        estimated_fees = estimated_cost * 0.03
        total_estimated = estimated_cost + estimated_fees
        potential_profit = (100 - bid_price) * count / 100

        print(f"Placing resting bid: BUY {count} YES on {ticker} at {bid_price}¢")
        print(f"  If filled: ${total_estimated:.2f} cost → ${potential_profit:.2f} profit")

        try:
            # Place the resting order at bid_price
            order_result = kalshi.create_order(
                ticker=ticker,
                side="yes",
                count=count,
                price=bid_price
            )

            order = order_result.get('order', {})
            order_id = order.get('order_id')

            if not order_id:
                print(f"No order_id returned for {ticker}")
                continue

            status = order.get('status', 'unknown')
            print(f"✅ Order {order_id} placed! Status: {status}")

            # Check if it filled immediately (unlikely at 99¢ when ask is 100¢)
            if status == 'executed':
                print(f"🎉 Order filled immediately!")
                # Record the filled trade
                trade_details = {
                    'order_id': order_id,
                    'ticker': ticker,
                    'side': 'YES',
                    'count': count,
                    'price_cents': bid_price,
                    'cost_cents': order.get('taker_fill_cost', count * bid_price),
                    'fees_cents': order.get('taker_fees', 0),
                    'profit_cents': (100 - bid_price) * count,
                    'roi_percent': round((100 - bid_price) / bid_price * 100, 2),
                }
                record_trade(dynamodb, trade_details)
            else:
                # Order is resting - this is expected and good!
                # It will earn liquidity rewards and may fill later
                print(f"📊 Order resting on book - earning liquidity rewards")

            placed_orders.append({
                'order_id': order_id,
                'ticker': ticker,
                'side': 'YES',
                'count': count,
                'price_cents': bid_price,
                'status': status,
                'potential_profit_cents': (100 - bid_price) * count,
            })

        except Exception as e:
            print(f"Error placing order for {ticker}: {e}")
            continue

    return placed_orders
