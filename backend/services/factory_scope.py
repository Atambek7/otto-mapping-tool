import os
import json
import logging
from typing import Set, List, Dict, Any, Optional
import httpx

from routers.aftercool import (
    get_aftercool_session,
    AFTERCOOL_BASE_URL,
    redis_client
)

logger = logging.getLogger("factory_scope")

async def get_authoritative_factory_eans(
    factory_id: str,
    account: str = "JV",
    force_refresh: bool = False
) -> Set[str]:
    """
    Fetches the authoritative set of EANs that currently belong to a given factory in Aftercool.
    This serves as the single source of truth to prevent foreign or orphaned mappings
    from entering the Drip Feed queue.
    """
    fid_str = str(factory_id).strip()
    acc_str = str(account or "JV").strip().upper()
    cache_key = f"authoritative_eans:{acc_str}:{fid_str}"

    if not force_refresh and redis_client:
        try:
            cached = redis_client.get(cache_key)
            if cached:
                eans_list = json.loads(cached)
                if isinstance(eans_list, list) and len(eans_list) > 0:
                    return set(eans_list)
        except Exception as e:
            logger.warning(f"Error reading Redis cache for authoritative EANs ({fid_str}): {e}")

    try:
        session = await get_aftercool_session()
    except Exception as e:
        logger.error(f"Failed to obtain Aftercool session for factory {fid_str}: {e}")
        return set()

    eans = set()
    offset = 0
    limit = 5000

    async with httpx.AsyncClient(cookies={"session": session}, timeout=30.0) as client:
        while True:
            params = {
                "factory_id": fid_str,
                "account": acc_str,
                "include_row": 0,
                "limit": limit,
                "offset": offset
            }
            res = await client.get(f"{AFTERCOOL_BASE_URL}/api/products", params=params)
            if res.status_code in (401, 403):
                session = await get_aftercool_session(force_refresh=True)
                res = await client.get(f"{AFTERCOOL_BASE_URL}/api/products", params=params, cookies={"session": session})

            if res.status_code != 200:
                logger.error(f"Aftercool API error {res.status_code} fetching products for factory {fid_str}")
                break

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

    if eans and redis_client:
        try:
            # Cache for 15 minutes
            redis_client.set(cache_key, json.dumps(list(eans)), ex=900)
        except Exception as e:
            logger.warning(f"Error saving Redis cache for authoritative EANs ({fid_str}): {e}")

    return eans
