import time
import requests
import base64
from typing import List, Dict, Any

class OttoMarketClient:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = None
        self.token_expires_at = 0
        self.base_url = "https://api.otto.market"

    def _get_auth_header(self) -> str:
        credentials = f"{self.client_id}:{self.client_secret}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return f"Basic {encoded}"

    def get_access_token(self) -> str:
        if self.access_token and time.time() < self.token_expires_at:
            return self.access_token

        url = f"{self.base_url}/v1/token"
        headers = {
            "Authorization": self._get_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded"
        }
        data = {
            "grant_type": "client_credentials",
            "scope": "products orders availability shipping-profiles"
        }

        response = requests.post(url, headers=headers, data=data)
        response.raise_for_status()

        token_data = response.json()
        self.access_token = token_data["access_token"]
        expires_in = int(token_data.get("expires_in", 3600))
        self.token_expires_at = time.time() + expires_in - 60

        return self.access_token

    def publish_products(self, payload: List[Dict[str, Any]]) -> Dict[str, Any]:
        token = self.get_access_token()
        url = f"{self.base_url}/v5/products"
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        max_retries = 3
        backoff = 2

        for attempt in range(max_retries):
            response = requests.post(url, headers=headers, json=payload)
            if response.status_code == 429:
                if attempt < max_retries - 1:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
            
            try:
                result = response.json()
            except ValueError:
                result = {"status_code": response.status_code, "text": response.text}
            
            return {
                "status_code": response.status_code,
                "response": result
            }
        
        return {"status_code": 429, "error": "Max retries reached due to rate limits."}
