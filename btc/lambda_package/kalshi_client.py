#!/usr/bin/env python3
"""
Simplified Kalshi client for Lambda - no external dependencies except requests
"""

import os
import time
import base64
import json
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding

class KalshiClient:
    """Minimal Kalshi API client for Lambda"""

    def __init__(self):
        self.key_id = os.environ.get('KALSHI_KEY_ID')
        self.base_url = "https://api.elections.kalshi.com/trade-api/v2"

        # Load private key from environment
        private_key_str = os.environ.get('KALSHI_PRIVATE_KEY')
        if not private_key_str:
            raise ValueError("KALSHI_PRIVATE_KEY not found in environment")

        # Format key if needed (add newlines every 64 chars)
        if "\n" not in private_key_str or private_key_str.count("\n") < 3:
            if private_key_str.startswith("-----BEGIN"):
                key_content = private_key_str.replace("-----BEGIN RSA PRIVATE KEY-----", "")
                key_content = key_content.replace("-----END RSA PRIVATE KEY-----", "")
                key_content = key_content.strip()
            else:
                key_content = private_key_str

            formatted_key = "-----BEGIN RSA PRIVATE KEY-----\n"
            for i in range(0, len(key_content), 64):
                formatted_key += key_content[i:i+64] + "\n"
            formatted_key += "-----END RSA PRIVATE KEY-----\n"
        else:
            formatted_key = private_key_str

        # Load the private key
        self.private_key = serialization.load_pem_private_key(
            formatted_key.encode('utf-8'),
            password=None
        )

    def _sign_request(self, method: str, path: str) -> dict:
        """Generate authentication headers"""
        import requests  # Import here to use Lambda's bundled requests

        timestamp = int(time.time() * 1000)
        timestamp_str = str(timestamp)

        # Remove query params
        path_parts = path.split('?')
        clean_path = path_parts[0]

        # Create message to sign
        msg_string = timestamp_str + method + clean_path

        # Sign with RSA-PSS
        signature = self.private_key.sign(
            msg_string.encode('utf-8'),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH
            ),
            hashes.SHA256()
        )

        return {
            "Content-Type": "application/json",
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode('utf-8'),
            "KALSHI-ACCESS-TIMESTAMP": timestamp_str,
        }

    def get_balance(self):
        """Get account balance"""
        import requests
        path = "/trade-api/v2/portfolio/balance"
        headers = self._sign_request("GET", path)
        response = requests.get(self.base_url + "/portfolio/balance", headers=headers)
        response.raise_for_status()
        return response.json()

    def create_order(self, ticker: str, side: str, count: int, price: int):
        """Create a limit order"""
        import requests
        path = "/trade-api/v2/portfolio/orders"
        headers = self._sign_request("POST", path)

        order_data = {
            "ticker": ticker,
            "action": "buy",
            "side": side,
            "count": count,
            "type": "limit"
        }

        # Add price based on side
        if side == "yes":
            order_data["yes_price"] = price
        else:
            order_data["no_price"] = price

        response = requests.post(
            self.base_url + "/portfolio/orders",
            headers=headers,
            json=order_data
        )
        response.raise_for_status()
        return response.json()

    def get_order(self, order_id: str):
        """Get order status"""
        import requests
        path = f"/trade-api/v2/portfolio/orders/{order_id}"
        headers = self._sign_request("GET", path)
        response = requests.get(self.base_url + f"/portfolio/orders/{order_id}", headers=headers)
        response.raise_for_status()
        return response.json()

    def cancel_order(self, order_id: str):
        """Cancel an order"""
        import requests
        path = f"/trade-api/v2/portfolio/orders/{order_id}"
        headers = self._sign_request("DELETE", path)
        response = requests.delete(self.base_url + f"/portfolio/orders/{order_id}", headers=headers)
        response.raise_for_status()
        return response.json()

    def get_orders(self, ticker: str = None, status: str = None):
        """Get orders, optionally filtered by ticker and/or status"""
        import requests
        path = "/trade-api/v2/portfolio/orders"
        headers = self._sign_request("GET", path)

        params = {}
        if ticker:
            params['ticker'] = ticker
        if status:
            params['status'] = status

        response = requests.get(
            self.base_url + "/portfolio/orders",
            headers=headers,
            params=params
        )
        response.raise_for_status()
        return response.json()
