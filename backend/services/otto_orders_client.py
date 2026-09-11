"""
Otto Orders API client with MOCK mode.

HOW TO SWITCH TO REAL DATA:
1. Make sure AccountSettings for the account has otto_client_id and otto_client_secret filled in.
2. Change USE_MOCK_DATA = True  →  USE_MOCK_DATA = False  (one line below).
3. That is all — the rest of the code is already wired up.

Otto Orders API reference: https://api.otto.market/docs#tag/Orders-V4
"""
import random
from datetime import date, timedelta, datetime, timezone
from typing import List, Dict, Any, Optional

# ─────────────────────────────────────────────────────────────────────────────
# SWITCH: set to False when real client_id / client_secret are configured
USE_MOCK_DATA = False
# ─────────────────────────────────────────────────────────────────────────────


class OttoOrdersClient:
    """
    Client for Otto Market Orders API v4.
    Returns a normalised list of daily order records.
    """

    OTTO_API_BASE = "https://api.otto.market"

    def __init__(self, client_id: Optional[str], client_secret: Optional[str]):
        self.client_id = client_id
        self.client_secret = client_secret
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    # ── Authentication ────────────────────────────────────────────────────────

    def _get_access_token(self) -> str:
        import time, requests, base64
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token
        credentials = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        resp = requests.post(
            f"{self.OTTO_API_BASE}/v1/token",
            headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials", "scope": "products orders availability shipping-profiles"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        self._token_expires_at = time.time() + int(data.get("expires_in", 3600)) - 60
        return self._access_token

    # ── Real API ──────────────────────────────────────────────────────────────

    def _fetch_real_orders(self, from_date: date, to_date: date) -> List[Dict[str, Any]]:
        """
        Fetches orders from Otto API v4 for the given date range.
        Returns normalised records: {order_date: date, quantity: int, revenue: float}
        """
        import requests
        token = self._get_access_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

        records: List[Dict[str, Any]] = []
        from_str = f"{from_date.isoformat()}T00:00:00.000Z"
        to_str = f"{to_date.isoformat()}T23:59:59.999Z"
        url = (
            f"{self.OTTO_API_BASE}/v4/orders"
            f"?fromOrderDate={from_str}"
            f"&toOrderDate={to_str}"
            f"&limit=128"
        )

        while url:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            payload = resp.json()

            for order in payload.get("resources", []):
                sales_order_id = order.get("salesOrderId")
                order_number = order.get("orderNumber")
                raw_date_str = order.get("orderDate", "")
                try:
                    order_dt = datetime.fromisoformat(raw_date_str)
                    order_calendar_date = order_dt.date()
                except Exception:
                    try:
                        order_calendar_date = date.fromisoformat(raw_date_str[:10])
                        order_dt = datetime.combine(order_calendar_date, datetime.min.time(), tzinfo=timezone.utc)
                    except Exception:
                        continue

                positions = order.get("positionItems", [])
                valid_positions = [
                    p for p in positions
                    if not p.get("fulfillmentStatus", "").startswith("CANCELLED")
                ]

                if valid_positions:
                    items_list = []
                    order_rev = 0.0
                    for p in valid_positions:
                        price_amt = float(p.get("itemValueGrossPrice", {}).get("amount", 0) or 0)
                        curr = p.get("itemValueGrossPrice", {}).get("currency", "EUR") or "EUR"
                        order_rev += price_amt
                        prod = p.get("product", {}) or {}
                        items_list.append({
                            "position_item_id": p.get("positionItemId") or f"pos_{sales_order_id}_{len(items_list)}",
                            "sku": prod.get("sku"),
                            "ean": prod.get("ean"),
                            "article_number": prod.get("articleNumber"),
                            "product_title": prod.get("productTitle") or "Otto Product",
                            "price": price_amt,
                            "currency": curr,
                            "fulfillment_status": p.get("fulfillmentStatus"),
                            "shop_url": prod.get("shopUrl"),
                        })

                    records.append({
                        "sales_order_id": sales_order_id or f"ord_{order_number}",
                        "order_number": order_number or "N/A",
                        "order_date": order_dt,
                        "order_calendar_date": order_calendar_date,
                        "weekday": order_calendar_date.weekday(),
                        "quantity": len(valid_positions),
                        "revenue": round(order_rev, 2),
                        "items": items_list,
                    })

            # Pagination
            links = payload.get("links", [])
            next_link = next((lnk["href"] for lnk in links if lnk.get("rel") == "next"), None)
            if next_link and next_link.startswith("/"):
                url = f"{self.OTTO_API_BASE}{next_link}"
            else:
                url = next_link

        return records

    # ── Mock data ─────────────────────────────────────────────────────────────

    def _fetch_mock_orders(self, from_date: date, to_date: date) -> List[Dict[str, Any]]:
        """
        Generates realistic-looking mock order data with position items.
        """
        multipliers = [0.7, 0.75, 0.9, 0.95, 1.4, 1.3, 0.8]
        base_orders = 12

        dummy_catalog = [
            ("Chesterfield Sofa 3-Sitzer Garnitur", 1299.0, "4062292036996", "https://www.otto.de"),
            ("Modernes Ecksofa Samt Velours", 1549.0, "4062292036997", "https://www.otto.de"),
            ("Esstisch Massivholz Eiche 200cm", 649.0, "4062292036998", "https://www.otto.de"),
            ("Polsterbett 180x200 Beige Stoff", 799.0, "4062292036999", "https://www.otto.de"),
            ("Wohnlandschaft U-Form Leder", 1899.0, "4062292037000", "https://www.otto.de"),
            ("Relaxsesssel Leder mit Hocker", 599.0, "4062292037001", "https://www.otto.de"),
        ]

        records: List[Dict[str, Any]] = []
        current = from_date
        rng = random.Random(42)
        counter = 1000

        while current <= to_date:
            wd = current.weekday()
            count = max(1, int(rng.gauss(base_orders * multipliers[wd], 2)))
            for i in range(count):
                counter += 1
                hour = rng.randint(8, 22)
                minute = rng.randint(0, 59)
                order_dt = datetime.combine(current, datetime.min.time(), tzinfo=timezone.utc).replace(hour=hour, minute=minute)
                order_num = f"cbn{counter}mock"
                sales_id = f"mock-order-{counter}"

                num_items = rng.choices([1, 2, 3], weights=[0.7, 0.25, 0.05])[0]
                order_items = []
                order_rev = 0.0
                for item_idx in range(num_items):
                    prod_title, prod_price, prod_ean, prod_url = rng.choice(dummy_catalog)
                    order_rev += prod_price
                    order_items.append({
                        "position_item_id": f"pos-{counter}-{item_idx}",
                        "sku": prod_ean,
                        "ean": prod_ean,
                        "article_number": f"ART-{prod_ean[-6:]}",
                        "product_title": prod_title,
                        "price": prod_price,
                        "currency": "EUR",
                        "fulfillment_status": "SENT" if wd != 6 else "PROCESSABLE",
                        "shop_url": prod_url,
                    })

                records.append({
                    "sales_order_id": sales_id,
                    "order_number": order_num,
                    "order_date": order_dt,
                    "order_calendar_date": current,
                    "weekday": wd,
                    "quantity": num_items,
                    "revenue": round(order_rev, 2),
                    "items": order_items,
                })
            current += timedelta(days=1)

        return records

    # ── Public API ────────────────────────────────────────────────────────────

    def fetch_orders(self, from_date: date, to_date: date) -> List[Dict[str, Any]]:
        """
        Returns raw order records for the given period.
        Uses mock data if USE_MOCK_DATA is True or credentials are missing.
        """
        if USE_MOCK_DATA or not self.client_id or not self.client_secret:
            return self._fetch_mock_orders(from_date, to_date)
        return self._fetch_real_orders(from_date, to_date)
