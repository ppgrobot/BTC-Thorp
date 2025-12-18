#!/usr/bin/env python3
"""
Script to cancel ALL open orders on Kalshi
"""
import json
import requests
from kalshi_client import KalshiClient

def cancel_all_open_orders():
    """Cancel all open/resting orders"""
    kalshi = KalshiClient()

    print("Fetching all open orders...\n")

    try:
        # Get all orders from portfolio
        path = "/trade-api/v2/portfolio/orders"
        headers = kalshi._sign_request("GET", path)
        response = requests.get(kalshi.base_url + "/portfolio/orders?status=resting", headers=headers)

        if response.status_code != 200:
            print(f"Error fetching orders: {response.status_code} - {response.text}")
            return

        data = response.json()
        orders = data.get('orders', [])

        if not orders:
            print("No open orders found!")
            return

        print(f"Found {len(orders)} open order(s):\n")

        for order in orders:
            order_id = order.get('order_id')
            ticker = order.get('ticker', 'unknown')
            status = order.get('status', 'unknown')
            remaining = order.get('remaining_count', 0)
            side = order.get('side', 'unknown')

            print(f"Order {order_id}")
            print(f"  Ticker: {ticker}")
            print(f"  Side: {side}")
            print(f"  Status: {status}")
            print(f"  Remaining: {remaining}")
            print(f"  Cancelling...")

            try:
                kalshi.cancel_order(order_id)
                print(f"  ✅ Cancelled successfully!\n")
            except Exception as e:
                print(f"  ❌ Error cancelling: {e}\n")

        print("Done!")

    except Exception as e:
        print(f"Error: {e}")

def lambda_handler(event, context):
    """Lambda handler wrapper"""
    cancel_all_open_orders()
    return {
        'statusCode': 200,
        'body': json.dumps('All open orders cancelled')
    }

if __name__ == "__main__":
    cancel_all_open_orders()
