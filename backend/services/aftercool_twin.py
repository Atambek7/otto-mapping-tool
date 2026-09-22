import logging
import asyncio
from typing import Dict, Optional, Tuple, Any, List
import httpx

from routers.aftercool import get_aftercool_session, AFTERCOOL_BASE_URL

logger = logging.getLogger("aftercool_twin")

_FACTORY_MAP_CACHE: Dict[str, Any] = {}
_FACTORY_ITEMS_CACHE: Dict[str, Dict[str, str]] = {}


def _normalize_title(title: str) -> str:
    if not title:
        return ""
    import re
    # Lowercase, trim extra whitespace and common punctuation
    t = title.strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


async def get_factory_pairs() -> Tuple[Dict[Tuple[str, str], str], Dict[str, Tuple[str, str, str]]]:
    """
    Returns:
      pair_map: ((factory_name, kind, target_acc) -> target_fid)
      fid_info: (fid -> (factory_name, kind, account))
    """
    global _FACTORY_MAP_CACHE
    if _FACTORY_MAP_CACHE.get("pair_map") and _FACTORY_MAP_CACHE.get("fid_info"):
        return _FACTORY_MAP_CACHE["pair_map"], _FACTORY_MAP_CACHE["fid_info"]

    session = await get_aftercool_session()
    async with httpx.AsyncClient(cookies={"session": session}, timeout=20.0) as client:
        res = await client.get(f"{AFTERCOOL_BASE_URL}/api/factories")
        if res.status_code != 200:
            logger.error("Failed to load factories from Aftercool: HTTP %s", res.status_code)
            return {}, {}

        data = res.json()
        factories = data.get("items", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])

    pair_map = {}
    fid_info = {}

    for f in factories:
        fname = f.get("name")
        kind = f.get("kind")
        acc = str(f.get("account") or "").upper()
        fid = str(f.get("id") or "").strip()
        if not fname or not fid:
            continue
        fid_info[fid] = (fname, kind, acc)
        pair_map[(fname, kind, acc)] = fid

    _FACTORY_MAP_CACHE["pair_map"] = pair_map
    _FACTORY_MAP_CACHE["fid_info"] = fid_info
    return pair_map, fid_info


async def resolve_target_factory_id(
    source_factory_id: str,
    source_account: str,
    target_account: str
) -> Optional[str]:
    """
    Finds the twin factory ID in target_account with the same name and kind.
    """
    pair_map, fid_info = await get_factory_pairs()
    info = fid_info.get(str(source_factory_id).strip())
    if not info:
        return None

    fname, kind, acc = info
    tgt_acc = str(target_account).upper()
    return pair_map.get((fname, kind, tgt_acc))


async def get_factory_product_map(factory_id: str, account: str) -> Dict[str, str]:
    """
    Returns mapping: normalized_name -> ean for all products in the given factory.
    """
    fid = str(factory_id).strip()
    acc = str(account).upper()
    cache_key = f"{acc}:{fid}"

    if cache_key in _FACTORY_ITEMS_CACHE:
        return _FACTORY_ITEMS_CACHE[cache_key]

    session = await get_aftercool_session()
    name_to_ean = {}
    async with httpx.AsyncClient(cookies={"session": session}, timeout=30.0) as client:
        res = await client.get(
            f"{AFTERCOOL_BASE_URL}/api/products",
            params={"factory_id": fid, "account": acc, "limit": 5000}
        )
        if res.status_code == 200:
            data = res.json()
            items = data.get("items", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            for item in items:
                pname = item.get("name")
                ean = item.get("ean")
                if pname and ean:
                    norm = _normalize_title(pname)
                    name_to_ean[norm] = str(ean).strip()

    _FACTORY_ITEMS_CACHE[cache_key] = name_to_ean
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
        if target_ean:
            return target_fid, target_ean, prod_name

    # If no title match found, DO NOT fallback to source_ean
    return target_fid, None, prod_name
