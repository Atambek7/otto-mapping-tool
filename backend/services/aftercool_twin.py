import logging
import asyncio
from typing import Dict, Optional, Tuple, Any, List
import time
import re
import httpx

from routers.aftercool import get_aftercool_session, AFTERCOOL_BASE_URL

logger = logging.getLogger("aftercool_twin")

_FACTORY_MAP_CACHE: Dict[str, Any] = {}
_FACTORY_MAP_TIMESTAMP: float = 0.0
FACTORY_MAP_TTL: float = 120.0  # 2 minutes cache TTL

_FACTORY_ITEMS_CACHE: Dict[str, Tuple[float, Dict[str, str]]] = {}
FACTORY_ITEMS_TTL: float = 180.0  # 3 minutes items cache TTL


def _normalize_name(name: str) -> str:
    if not name:
        return ""
    return re.sub(r"\s+", " ", name.strip().lower())


def _normalize_title(title: str) -> str:
    if not title:
        return ""
    # Lowercase, trim extra whitespace and common punctuation
    t = title.strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


async def get_factory_pairs(force_refresh: bool = False) -> Tuple[Dict[Any, str], Dict[str, Tuple[str, str, str]]]:
    """
    Returns:
      pair_map: ((factory_name, kind, target_acc) -> target_fid) and normalized fallbacks
      fid_info: (fid -> (factory_name, kind, account))
    """
    global _FACTORY_MAP_CACHE, _FACTORY_MAP_TIMESTAMP
    now = time.time()
    if not force_refresh and (now - _FACTORY_MAP_TIMESTAMP < FACTORY_MAP_TTL):
        if _FACTORY_MAP_CACHE.get("pair_map") and _FACTORY_MAP_CACHE.get("fid_info"):
            return _FACTORY_MAP_CACHE["pair_map"], _FACTORY_MAP_CACHE["fid_info"]

    session = await get_aftercool_session(force_refresh=force_refresh)
    async with httpx.AsyncClient(cookies={"session": session}, timeout=20.0) as client:
        res = await client.get(f"{AFTERCOOL_BASE_URL}/api/factories")
        if res.status_code != 200:
            logger.error("Failed to load factories from Aftercool: HTTP %s", res.status_code)
            if _FACTORY_MAP_CACHE.get("pair_map") and _FACTORY_MAP_CACHE.get("fid_info"):
                return _FACTORY_MAP_CACHE["pair_map"], _FACTORY_MAP_CACHE["fid_info"]
            return {}, {}

        data = res.json()
        factories = data.get("items", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])

    pair_map = {}
    fid_info = {}

    for f in factories:
        fname = f.get("name")
        kind = f.get("kind")
        acc = str(f.get("account") or "").upper().strip()
        fid = str(f.get("id") or "").strip()
        if not fname or not fid:
            continue
        fname_str = str(fname).strip()
        norm_name = _normalize_name(fname_str)
        norm_kind = str(kind).strip().lower() if kind else None

        fid_info[fid] = (fname_str, kind, acc)

        # 1. Exact key: (original_name, original_kind, ACC)
        pair_map[(fname_str, kind, acc)] = fid
        # 2. Normalized key: (norm_name, norm_kind, ACC)
        pair_map[(norm_name, norm_kind, acc)] = fid
        # 3. Account + name fallback (prefer lister if multiple kinds exist)
        fallback_key = (norm_name, acc)
        if fallback_key not in pair_map or norm_kind == "lister":
            pair_map[fallback_key] = fid

    _FACTORY_MAP_CACHE["pair_map"] = pair_map
    _FACTORY_MAP_CACHE["fid_info"] = fid_info
    _FACTORY_MAP_TIMESTAMP = now
    return pair_map, fid_info


async def resolve_target_factory_id(
    source_factory_id: str,
    source_account: str,
    target_account: str,
    force_refresh: bool = False
) -> Optional[str]:
    """
    Finds the twin factory ID in target_account with the same name and kind.
    Automatically re-fetches from Aftercool if not found in current cache.
    """
    sfid = str(source_factory_id).strip()
    tgt_acc = str(target_account).upper().strip()

    pair_map, fid_info = await get_factory_pairs(force_refresh=force_refresh)
    info = fid_info.get(sfid)

    # Auto-refresh on cache miss if source factory unknown
    if not info and not force_refresh:
        logger.info(f"Source factory {sfid} not found in cache, refreshing from Aftercool...")
        pair_map, fid_info = await get_factory_pairs(force_refresh=True)
        info = fid_info.get(sfid)

    if not info:
        logger.warning(f"Source factory {sfid} not found in Aftercool factories list")
        return None

    fname, kind, acc = info
    norm_name = _normalize_name(fname)
    norm_kind = str(kind).strip().lower() if kind else None

    # Priority 1: Exact match (fname, kind, tgt_acc)
    tgt_fid = pair_map.get((fname, kind, tgt_acc))

    # Priority 2: Normalized match (norm_name, norm_kind, tgt_acc)
    if not tgt_fid:
        tgt_fid = pair_map.get((norm_name, norm_kind, tgt_acc))

    # Priority 3: Name match in target account (ignoring kind differences)
    if not tgt_fid:
        tgt_fid = pair_map.get((norm_name, tgt_acc))

    # Auto-refresh on cache miss if target twin not found in cache
    if not tgt_fid and not force_refresh:
        logger.info(f"Target twin for {fname} ({sfid}) in {tgt_acc} not found in cache, refreshing from Aftercool...")
        return await resolve_target_factory_id(source_factory_id, source_account, target_account, force_refresh=True)

    return tgt_fid


async def get_factory_product_map(factory_id: str, account: str, force_refresh: bool = False) -> Dict[str, str]:
    """
    Returns mapping: normalized_name -> ean for all products in the given factory.
    """
    fid = str(factory_id).strip()
    acc = str(account).upper().strip()
    cache_key = f"{acc}:{fid}"
    now = time.time()

    if not force_refresh and cache_key in _FACTORY_ITEMS_CACHE:
        cached_time, cached_data = _FACTORY_ITEMS_CACHE[cache_key]
        if now - cached_time < FACTORY_ITEMS_TTL:
            return cached_data

    from services.aftercool_service import iter_products

    name_to_ean = {}
    # Do not cache a partial name map when a later page fails.
    async for page in iter_products(fid, account=acc, dataset="lister", page_size=500):
        for product in page:
            if product.name:
                name_to_ean[_normalize_title(product.name)] = product.ean

    _FACTORY_ITEMS_CACHE[cache_key] = (now, name_to_ean)
    return name_to_ean


async def resolve_twin_product(
    source_account: str,
    target_account: str,
    source_factory_id: Optional[str],
    source_ean: str,
    source_product_name: Optional[str] = None
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Resolves the twin product in the target account.
    Returns: (target_factory_id, target_ean, target_product_name)
    If twin cannot be safely resolved, returns target_ean = None (no dangerous fallback to source_ean).
    """
    src_acc = str(source_account).upper()
    tgt_acc = str(target_account).upper()
    
    if not source_factory_id:
        return None, None, None

    target_fid = await resolve_target_factory_id(source_factory_id, src_acc, tgt_acc)
    if not target_fid:
        return None, None, None

    # Fetch target factory products
    target_name_map = await get_factory_product_map(target_fid, tgt_acc)

    # Determine source product name
    prod_name = source_product_name
    if not prod_name:
        # Fetch from source factory map
        src_name_map = await get_factory_product_map(source_factory_id, src_acc)
        # Invert to find name by ean
        for n, e in src_name_map.items():
            if e == source_ean:
                prod_name = n
                break

    if prod_name:
        norm_name = _normalize_title(prod_name)
        target_ean = target_name_map.get(norm_name)
        if not target_ean:
            # Force refresh target factory product map once in case newly added
            target_name_map = await get_factory_product_map(target_fid, tgt_acc, force_refresh=True)
            target_ean = target_name_map.get(norm_name)
        if target_ean:
            return target_fid, target_ean, prod_name

    # If no title match found, DO NOT fallback to source_ean
    return target_fid, None, prod_name
