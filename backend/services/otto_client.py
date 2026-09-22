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

    def get_update_task(self, task_href_or_id: str) -> Dict[str, Any]:
        try:
            token = self.get_access_token()
        except Exception as e:
            return {"status_code": 401, "error": f"Failed to acquire token: {e}"}

        if not task_href_or_id.startswith("http"):
            clean_path = task_href_or_id if task_href_or_id.startswith("/") else f"/v5/products/update-tasks/{task_href_or_id}"
            url = f"{self.base_url}{clean_path}"
        else:
            url = task_href_or_id

        headers = {"Authorization": f"Bearer {token}"}
        try:
            resp = requests.get(url, headers=headers, timeout=min(15.0, self.timeout))
            if resp.status_code == 200:
                return resp.json()
            return {"status_code": resp.status_code, "text": resp.text}
        except Exception as e:
            return {"status_code": 500, "error": str(e)}

    def get_task_failed_details(self, task_href_or_id: str) -> List[Dict[str, Any]]:
        try:
            token = self.get_access_token()
        except Exception:
            return []

        clean = task_href_or_id.rstrip("/")
        if not clean.endswith("/failed"):
            clean = f"{clean}/failed"

        if not clean.startswith("http"):
            clean_path = clean if clean.startswith("/") else f"/v5/products/update-tasks/{clean}"
            url = f"{self.base_url}{clean_path}"
        else:
            url = clean

        headers = {"Authorization": f"Bearer {token}"}
        try:
            resp = requests.get(url, headers=headers, timeout=min(15.0, self.timeout))
            if resp.status_code == 200:
                return resp.json().get("results", [])
            return []
        except Exception:
            return []

    def update_quantities(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Updates available stock quantity for SKUs.
        items format: [{"sku": "...", "quantity": 20}]
        """
        try:
            token = self.get_access_token()
        except Exception as e:
            return {"status_code": 401, "error": f"Failed to acquire token: {e}"}

        url = f"{self.base_url}/v1/availability/quantities"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        last_status = 429
        req_timeout = min(10.0, self.timeout)
        for attempt in range(2):
            try:
                resp = requests.post(url, headers=headers, json=items, timeout=req_timeout)
                last_status = resp.status_code
                if resp.status_code in (429, 502, 503, 504) and attempt < 1:
                    time.sleep(1.0)
                    continue
                try:
                    result = resp.json()
                except ValueError:
                    result = {"status_code": resp.status_code, "text": resp.text}
                return {"status_code": resp.status_code, "response": result}
            except requests.exceptions.RequestException as e:
                if attempt < 1:
                    time.sleep(1.0)
                    continue
                logging.error(f"[OttoMarketClient] Error updating quantities: {e}")
                return {"status_code": 500, "error": str(e)}

        return {"status_code": last_status, "error": f"Max retries reached ({last_status})"}

    def update_product_delivery_information(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Updates shipping profile and delivery information for SKUs.
        items format: [{"sku": "...", "shippingProfileId": "uuid", "processingTime": "DEFAULT"}]
        """
        try:
            token = self.get_access_token()
        except Exception as e:
            return {"status_code": 401, "error": f"Failed to acquire token: {e}"}

        url = f"{self.base_url}/v1/availability/product-delivery-information"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        last_status = 429
        req_timeout = min(10.0, self.timeout)
        for attempt in range(2):
            try:
                resp = requests.post(url, headers=headers, json=items, timeout=req_timeout)
                last_status = resp.status_code
                if resp.status_code in (429, 502, 503, 504) and attempt < 1:
                    time.sleep(1.0)
                    continue
                try:
                    result = resp.json()
                except ValueError:
                    result = {"status_code": resp.status_code, "text": resp.text}
                return {"status_code": resp.status_code, "response": result}
            except requests.exceptions.RequestException as e:
                if attempt < 1:
                    time.sleep(1.0)
                    continue
                logging.error(f"[OttoMarketClient] Error updating delivery information: {e}")
                return {"status_code": last_status, "error": f"Max retries reached ({last_status})"}

    def get_category_definition(self, category_id: str) -> Dict[str, Any]:
        """
        Fetches category metadata / attributes from OTTO Categories API.
        Tries /v4/categories/{category_id} and /v5/categories/{category_id}.
        """
        try:
            token = self.get_access_token()
        except Exception as e:
            return {"status_code": 401, "error": f"Failed to acquire token: {e}"}

        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        for ver in ("v4", "v5", "v1"):
            url = f"{self.base_url}/{ver}/categories/{category_id}"
            try:
                resp = requests.get(url, headers=headers, timeout=min(15.0, self.timeout))
                if resp.status_code == 200:
                    return {"status_code": 200, "data": resp.json()}
                elif resp.status_code == 404:
                    continue
                else:
                    return {"status_code": resp.status_code, "text": resp.text}
            except Exception as e:
                logging.debug(f"[OttoMarketClient] Error checking category at {url}: {e}")

        return {"status_code": 404, "error": f"Category {category_id} not found in Otto API"}

