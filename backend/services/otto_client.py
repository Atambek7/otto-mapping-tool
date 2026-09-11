import time
import logging
import requests
import base64
from typing import List, Dict, Any

class OttoMarketClient:
    def __init__(self, client_id: str, client_secret: str, timeout: float = 30.0):
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout
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

        try:
            response = requests.post(url, headers=headers, data=data, timeout=min(15.0, self.timeout))
            response.raise_for_status()
            token_data = response.json()
            self.access_token = token_data["access_token"]
            expires_in = int(token_data.get("expires_in", 3600))
            self.token_expires_at = time.time() + expires_in - 60
            return self.access_token
        except requests.exceptions.Timeout as e:
            logging.error(f"[OttoMarketClient] Timeout requesting access token: {e}")
            raise
        except requests.exceptions.RequestException as e:
            logging.error(f"[OttoMarketClient] Network error requesting access token: {e}")
            raise

    def publish_products(self, payload: List[Dict[str, Any]]) -> Dict[str, Any]:
        try:
            token = self.get_access_token()
        except Exception as e:
            return {"status_code": 401, "error": f"Failed to acquire Otto access token: {e}"}

        url = f"{self.base_url}/v5/products"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        max_retries = 3
        backoff = 2

        for attempt in range(max_retries):
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
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
            except requests.exceptions.Timeout as e:
                logging.warning(f"[OttoMarketClient] Timeout publishing products (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                return {"status_code": 504, "error": f"Otto API request timed out after {self.timeout}s: {e}"}
            except requests.exceptions.RequestException as e:
                logging.warning(f"[OttoMarketClient] Network error publishing products (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                return {"status_code": 502, "error": f"Otto API network connection error: {e}"}

        return {"status_code": 429, "error": "Max retries reached due to rate limits."}
