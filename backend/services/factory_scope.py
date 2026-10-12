import os
import json
import logging
from dataclasses import dataclass, field
from typing import Set, List, Dict, Any, Optional
import httpx

from routers.aftercool import (
    get_aftercool_session,
    AFTERCOOL_BASE_URL,
    redis_client
)
from services.aftercool_service import resolve_aftercool_account

logger = logging.getLogger("factory_scope")

@dataclass
class AuthoritativeResult:
    status: str  # "success", "empty", "error"
    eans: Set[str] = field(default_factory=set)
    error_message: Optional[str] = None

    def __iter__(self):
        return iter(self.eans)

    def __len__(self):
        return len(self.eans)

    def __contains__(self, item):
        return item in self.eans

    def __bool__(self):
        return bool(self.eans)

async def get_authoritative_factory_eans(
    factory_id: str,
    account: str = "JV",
    force_refresh: bool = False
) -> AuthoritativeResult:
    """
    Fetches the authoritative set of EANs that currently belong to a given factory in Aftercool.
    This serves as the single source of truth to prevent foreign or orphaned mappings
    from entering the Drip Feed queue.

    Returns AuthoritativeResult with status "success", "empty", or "error".
    On network or API error, returns status="error" without returning or caching partial results.
    """
    fid_str = str(factory_id).strip()
    acc_str = str(account or "JV").strip().upper()
    aftercool_acc = resolve_aftercool_account(fid_str, acc_str)
    cache_key = f"authoritative_eans:{acc_str}:{fid_str}"

    if not force_refresh and redis_client:
        try:
            cached = redis_client.get(cache_key)
            if cached:
                data = json.loads(cached)
                if isinstance(data, dict) and "status" in data:
                    st = data.get("status")
                    cached_eans = set(data.get("eans", []))
                    if st in ("success", "empty"):
                        return AuthoritativeResult(status=st, eans=cached_eans)
                elif isinstance(data, list):
                    st = "success" if data else "empty"
                    return AuthoritativeResult(status=st, eans=set(data))
        except Exception as e:
            logger.warning(f"Error reading Redis cache for authoritative EANs ({fid_str}): {e}")

    try:
        session = await get_aftercool_session()
    except Exception as e:
        err_msg = f"Failed to obtain Aftercool session for factory {fid_str}: {e}"
        logger.error(err_msg)
        return AuthoritativeResult(status="error", eans=set(), error_message=err_msg)

    eans = set()
    offset = 0
    limit = 5000

    async with httpx.AsyncClient(cookies={"session": session}, timeout=30.0) as client:
        while True:
            params = {
                "factory_id": fid_str,
                "account": aftercool_acc,
                "include_row": 0,
                "limit": limit,
                "offset": offset
            }
            try:
                res = await client.get(f"{AFTERCOOL_BASE_URL}/api/products", params=params)
                if res.status_code in (401, 403):
                    session = await get_aftercool_session(force_refresh=True)
                    res = await client.get(f"{AFTERCOOL_BASE_URL}/api/products", params=params, cookies={"session": session})
            except Exception as e:
                err_msg = f"Network error fetching products for factory {fid_str} at offset {offset}: {e}"
                logger.error(err_msg)
                return AuthoritativeResult(status="error", eans=set(), error_message=err_msg)

            if res.status_code != 200:
                err_msg = f"Aftercool API error {res.status_code} fetching products for factory {fid_str} at offset {offset}"
                logger.error(err_msg)
                # CRITICAL: Never return or cache partial results on error!
                return AuthoritativeResult(status="error", eans=set(), error_message=err_msg)

            data = res.json()
            items = data.get("items", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            if not items:
                break

            for item in items:
                raw_ean = item.get("ean") or (item.get("row") or {}).get("EAN") or ""
                ean = str(raw_ean).strip()
                if ean and ean not in ("—", "0", "None", "-"):
                    eans.add(ean)

            if len(items) < limit:
                break
            offset += limit

    if not eans:
        # Cache successful empty result with short TTL (2 minutes) to prevent hammering
        if redis_client:
            try:
                redis_client.set(cache_key, json.dumps({"status": "empty", "eans": []}), ex=120)
            except Exception as e:
                logger.warning(f"Error saving empty Redis cache for authoritative EANs ({fid_str}): {e}")
        return AuthoritativeResult(status="empty", eans=set())

    # Cache successful non-empty result for 15 minutes
    if redis_client:
        try:
            redis_client.set(cache_key, json.dumps({"status": "success", "eans": list(eans)}), ex=900)
        except Exception as e:
            logger.warning(f"Error saving Redis cache for authoritative EANs ({fid_str}): {e}")

    return AuthoritativeResult(status="success", eans=eans)

