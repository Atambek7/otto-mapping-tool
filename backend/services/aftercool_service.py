import os
import json
import time
import asyncio
import logging
import datetime
from decimal import Decimal
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Tuple, AsyncGenerator, Union

import httpx
import redis
from sqlalchemy.orm import Session

from database import SessionLocal
import models

logger = logging.getLogger("aftercool_service")

AFTERCOOL_LOGIN_URL = os.getenv("AFTERCOOL_LOGIN_URL", "https://aftercool.de/auth/login")
AFTERCOOL_BASE_URL = os.getenv("AFTERCOOL_BASE_URL", "https://aftercool.de")
REDIS_URL = os.getenv("CELERY_BROKER_URL", os.getenv("REDIS_URL", "redis://redis:6379/0"))

try:
    redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
except Exception as e:
    logger.warning("Failed to initialize Redis client: %s", e)
    redis_client = None

_cached_session: Optional[str] = None
_session_lock = asyncio.Lock()


# ==========================================
# 1. DTO & Parsing Layer
# ==========================================

@dataclass
class AftercoolProduct:
    ean: str
    name: str
    price: Optional[Decimal]
    price_af: Optional[Decimal]
    image_url: Optional[str]
    factory_id: str
    raw_item: Dict[str, Any]


def apply_aftercool_prices(mapping: Any, price: Optional[Decimal], price_af: Optional[Decimal]) -> None:
    """Refresh automatic prices while preserving the user's explicit override."""
    if not getattr(mapping, "has_manual_price", False) and price is not None:
        mapping.price = str(price)
    if price_af is not None:
        mapping.price_af = str(price_af)


def parse_price(val: Any) -> Optional[Decimal]:
    """
    Robust price parser supporting formats:
      '1.299,99 €', '1299,99', '1299.99', '1299', Decimal, float, int.
    Returns Decimal > 0 or None.
    """
    if val is None:
        return None
    if isinstance(val, (int, float, Decimal)):
        try:
            d = Decimal(str(val))
            return d if d > Decimal("0.0001") else None
        except Exception:
            return None

    s = str(val).replace("€", "").replace("EUR", "").replace("eur", "").strip()
    if not s:
        return None

    # Handle standard German thousand separator '1.299,99' -> '1299.99'
    if "." in s and "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")

    try:
        clean = Decimal(s)
        return clean if clean > Decimal("0.0001") else None
    except Exception:
        return None


def normalize_image_url(val: Any) -> Optional[str]:
    """
    Extracts the primary image URL from list, pipe-separated, or comma-separated strings.
    """
    if not val:
        return None
    if isinstance(val, list):
        val = val[0] if val else ""
    val_str = str(val).strip()
    if "|" in val_str:
        val_str = val_str.split("|")[0].strip()
    elif "," in val_str:
        val_str = val_str.split(",")[0].strip()
    return val_str if val_str else None


def parse_aftercool_product(item: Dict[str, Any], default_factory_id: str = "") -> Optional[AftercoolProduct]:
    """
    Single source of truth for parsing an Aftercool item dict into a normalized AftercoolProduct DTO.
    """
    if not isinstance(item, dict):
        return None

    row = item.get("row") or {}
    ean = str(item.get("ean") or row.get("EAN") or "").strip()
    if not ean or ean in ("—", "0", "-", "None"):
        return None

    name = str(row.get("Artikelbeschreibung") or item.get("name") or "").strip()
    price_af = parse_price(row.get("Startpreis"))
    sofort = parse_price(row.get("SofortkaufenPreis"))
    price_otto = sofort if sofort is not None else price_af

    image_url = normalize_image_url(row.get("PictureURLs") or row.get("PictureURL") or row.get("GalleryURL"))
    item_fid = str(item.get("factory_id") or default_factory_id or "").strip()

    return AftercoolProduct(
        ean=ean,
        name=name,
        price=price_otto,
        price_af=price_af,
        image_url=image_url,
        factory_id=item_fid,
        raw_item=item
    )


# ==========================================
# 2. Session & HTTP Client Layer
# ==========================================

async def invalidate_aftercool_session() -> None:
    global _cached_session
    async with _session_lock:
        _cached_session = None


async def get_aftercool_session(force_refresh: bool = False) -> str:
    """
    Thread-safe session getter with credentials protection and automatic re-authentication.
    """
    global _cached_session
    if _cached_session and not force_refresh:
        return _cached_session

    async with _session_lock:
        if _cached_session and not force_refresh:
            return _cached_session

        username = os.getenv("AFTERCOOL_USERNAME")
        password = os.getenv("AFTERCOOL_PASSWORD")
        if not username or not password:
            raise RuntimeError(
                "Aftercool credentials (AFTERCOOL_USERNAME, AFTERCOOL_PASSWORD) are not configured in environment"
            )

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                AFTERCOOL_LOGIN_URL,
                headers={"Content-Type": "application/json"},
                json={"username": username, "password": password}
            )
            if response.status_code != 200:
                logger.error("Aftercool auth failed with status code: %s", response.status_code)
                raise RuntimeError(f"Failed to auth with Aftercool: HTTP {response.status_code}")

            session = dict(response.cookies).get("session")
            if not session:
                raise RuntimeError("No session cookie returned by Aftercool auth endpoint")

            _cached_session = session
            return session


_aftercool_sems: Dict[Any, asyncio.Semaphore] = {}


def get_aftercool_semaphore() -> asyncio.Semaphore:
    """
    Limits concurrent outbound requests to Aftercool (max 3) per event loop,
    guaranteeing that Aftercool's server/database is never overwhelmed or crashed.
    """
    try:
        loop = asyncio.get_running_loop()
        if loop not in _aftercool_sems:
            _aftercool_sems[loop] = asyncio.Semaphore(3)
        return _aftercool_sems[loop]
    except RuntimeError:
        return asyncio.Semaphore(3)


async def request_aftercool(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    timeout: float = 30.0,
    max_retries: int = 3,
) -> httpx.Response:
    """
    Centralized HTTP helper for all Aftercool requests:
      - Strictly limited to max 3 concurrent requests to protect Aftercool
      - Automatically injects session cookie
      - On 401/403: refreshes session once and retries immediately
      - On 429: respects Retry-After header or applies exponential backoff
      - On 500/502/503/504: applies exponential backoff with jitter
      - On timeout/network error: bounded retry
    """
    sem = get_aftercool_semaphore()
    async with sem:
        url = f"{AFTERCOOL_BASE_URL}{path}" if path.startswith("/") else f"{AFTERCOOL_BASE_URL}/{path}"
        session = await get_aftercool_session()

        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(cookies={"session": session}, timeout=timeout) as client:
                    res = await client.request(method, url, params=params, json=json_body)

                    # Auth expired or invalid
                    if res.status_code in (401, 403):
                        logger.info("Aftercool session expired (HTTP %s). Refreshing session (attempt %s/%s)...",
                                    res.status_code, attempt, max_retries)
                        session = await get_aftercool_session(force_refresh=True)
                        # Retry immediately with fresh session
                        async with httpx.AsyncClient(cookies={"session": session}, timeout=timeout) as client_retry:
                            res = await client_retry.request(method, url, params=params, json=json_body)

                    # Rate limited (429)
                    if res.status_code == 429:
                        retry_after = res.headers.get("Retry-After")
                        wait_time = float(retry_after) if retry_after and retry_after.isdigit() else (2.0 * attempt)
                        logger.warning("Aftercool rate limit 429. Sleeping %ss before retry %s/%s...",
                                       wait_time, attempt, max_retries)
                        await asyncio.sleep(wait_time)
                        continue

                    # Server error (5xx)
                    if res.status_code in (500, 502, 503, 504):
                        if attempt < max_retries:
                            wait_time = 1.0 * (2 ** (attempt - 1))
                            logger.warning("Aftercool server error HTTP %s. Retrying in %ss (attempt %s/%s)...",
                                           res.status_code, wait_time, attempt, max_retries)
                            await asyncio.sleep(wait_time)
                            continue

                    return res

            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt < max_retries:
                    wait_time = 1.0 * attempt
                    logger.warning("Network/Timeout error communicating with Aftercool (%s). Retrying in %ss (attempt %s/%s)...",
                                   exc, wait_time, attempt, max_retries)
                    await asyncio.sleep(wait_time)
                    continue
                logger.error("Exhausted retries connecting to Aftercool: %s", exc)
                raise

        return res


# ==========================================
# 3. Cache Abstraction Layer
# ==========================================

async def get_cached_json(key: str) -> Optional[Any]:
    if not redis_client:
        return None
    try:
        return await asyncio.to_thread(_sync_redis_get, key)
    except Exception as e:
        logger.debug("Redis cache read error for key '%s': %s", key, e)
        return None


def _sync_redis_get(key: str) -> Optional[Any]:
    if not redis_client:
        return None
    data = redis_client.get(key)
    return json.loads(data) if data else None


async def set_cached_json(key: str, data: Any, ex: int = 1800) -> None:
    if not redis_client or data is None:
        return
    try:
        payload = json.dumps(data)
        await asyncio.to_thread(_sync_redis_set, key, payload, ex)
    except Exception as e:
        logger.debug("Redis cache write error for key '%s': %s", key, e)


def _sync_redis_set(key: str, payload: str, ex: int) -> None:
    if redis_client:
        redis_client.set(key, payload, ex=ex)


async def invalidate_products_cache(account: str = "", factory_id: str = "") -> None:
    """Invalidates Redis cached product entries for a given account and factory."""
    if not redis_client:
        return
    try:
        pattern = f"aftercool:products:{account}:{factory_id}:*" if factory_id else f"aftercool:products:{account}:*"
        await asyncio.to_thread(_sync_redis_delete_pattern, pattern)
    except Exception as e:
        logger.debug("Error invalidating cache pattern '%s': %s", pattern, e)


def _sync_redis_delete_pattern(pattern: str) -> None:
    if not redis_client:
        return
    keys = redis_client.keys(pattern)
    if keys:
        redis_client.delete(*keys)


# ==========================================
# 4. Product Retrieval & Pagination Layer
# ==========================================

async def iter_products(
    factory_id: str,
    account: str = "JV",
    dataset: str = "lister",
    page_size: int = 500,
    max_pages: int = 100,
    dedup: bool = False
) -> AsyncGenerator[List[AftercoolProduct], None]:
    """
    Deterministic async generator that yields parsed AftercoolProduct batches page by page.
    Guarantees global deduplication across pages if dedup=True.
    """
    offset = 0
    seen_eans = set()
    pages_count = 0

    while pages_count < max_pages:
        params = {
            "factory_id": factory_id,
            "account": account,
            "dataset": dataset,
            "include_row": 1,
            "limit": page_size,
            "offset": offset
        }
        res = await request_aftercool("GET", "/api/products", params=params, timeout=45.0)
        if res.status_code != 200:
            logger.error("iter_products failed on offset %s with HTTP %s: %s", offset, res.status_code, res.text[:200])
            break

        data = res.json()
        raw_items = data.get("items", data) if isinstance(data, dict) else data
        if not isinstance(raw_items, list) or len(raw_items) == 0:
            break

        batch: List[AftercoolProduct] = []
        for raw in raw_items:
            prod = parse_aftercool_product(raw, default_factory_id=factory_id)
            if not prod:
                continue
            if dedup:
                if prod.ean in seen_eans:
                    continue
                seen_eans.add(prod.ean)
            batch.append(prod)

        if batch:
            yield batch

        if len(raw_items) < page_size:
            break

        offset += page_size
        pages_count += 1


# ==========================================
# 5. Database Synchronization Layer
# ==========================================

def resolve_aftercool_account(factory_id: str, account: str, db: Optional[Session] = None) -> str:
    """
    Determines which Aftercool account must be queried for a factory.
    If products for this factory in `account` were transferred from another account
    (source_account is not None and source_account != account), returns source_account (e.g., 'JV').
    Otherwise returns `account` as-is.
    """
    acc_clean = str(account or "JV").upper().strip()
    fid_clean = str(factory_id or "").strip()
    if not fid_clean:
        return acc_clean

    # Native ID protection: factory IDs in native range belong strictly to that account
    if acc_clean == "XL" and fid_clean.startswith("48"):
        return "XL"
    if acc_clean == "JV" and fid_clean.startswith("50"):
        return "JV"

    def _query(session: Session) -> Optional[str]:
        sample = session.query(models.ProductMapping.source_account).filter(
            models.ProductMapping.factory_id == fid_clean,
            models.ProductMapping.account_name == acc_clean,
            models.ProductMapping.source_account.isnot(None),
            models.ProductMapping.source_account != acc_clean
        ).first()
        return sample[0] if sample and sample[0] else None

    if db is not None:
        src = _query(db)
        return src if src else acc_clean

    temp_db: Session = SessionLocal()
    try:
        src = _query(temp_db)
        return src if src else acc_clean
    except Exception as e:
        logger.warning("Error resolving aftercool account for factory %s: %s", fid_clean, e)
        return acc_clean
    finally:
        temp_db.close()


def sync_products_to_db(
    factory_id: str,
    account: str,
    data: Union[dict, list, List[AftercoolProduct]],
    chunk_size: int = 500,
    source_account: Optional[str] = None
) -> Dict[str, Any]:
    """
    Synchronizes product metadata from Aftercool to PostgreSQL with:
      - Strict JV / XL account isolation
      - Aftercool as primary source of truth, PROTECTING manual user prices (has_manual_price)
      - price_af always updated from Startpreis
      - Cross-account source_account tracking for transferred factories
      - Chunked batch processing preventing memory bloat
      - Structured logging instead of print
    """
    target_fid = str(factory_id or "").strip()
    acc_name = str(account or "JV").upper().strip()

    # 1. Parse items into normalized DTOs
    parsed_products: List[AftercoolProduct] = []
    if isinstance(data, list):
        if data and isinstance(data[0], AftercoolProduct):
            parsed_products = data
        else:
            for item in data:
                p = parse_aftercool_product(item, default_factory_id=target_fid)
                if p:
                    parsed_products.append(p)
    elif isinstance(data, dict):
        items = data.get("items", [])
        for item in items:
            p = parse_aftercool_product(item, default_factory_id=target_fid)
            if p:
                parsed_products.append(p)

    if not parsed_products:
        return {"synced": 0, "created": 0, "updated": 0}

    db: Session = SessionLocal()
    now = datetime.datetime.utcnow()
    total_created = 0
    total_updated = 0

    try:
        # Process in chunks of 500
        for i in range(0, len(parsed_products), chunk_size):
            chunk = parsed_products[i : i + chunk_size]
            chunk_eans = [p.ean for p in chunk]

            # Query existing records STRICTLY matching account_name
            query = db.query(models.ProductMapping).filter(
                models.ProductMapping.product_ean.in_(chunk_eans),
                models.ProductMapping.account_name == acc_name
            )
            existing_map = {m.product_ean: m for m in query.all()}

            new_mappings = []
            seen_chunk_eans = set()

            for p in chunk:
                item_fid = p.factory_id or target_fid
                m = existing_map.get(p.ean)

                if m:
                    # Reassignment within same account
                    if item_fid and m.factory_id != item_fid:
                        logger.info("[REASSIGNMENT] EAN %s factory changed from %s to %s (%s)",
                                    p.ean, m.factory_id, item_fid, acc_name)
                        m.factory_id = item_fid

                    if source_account and not m.source_account:
                        m.source_account = source_account

                    # Base metadata update
                    if p.name and p.name not in ("", "Без названия"):
                        m.product_name = p.name

                    # PRICE POLICY:
                    # Aftercool is authoritative source of truth, UNLESS user set a manual price
                    apply_aftercool_prices(m, p.price, p.price_af)

                    if p.image_url:
                        m.image_url = p.image_url

                    m.last_synced_at = now
                    total_updated += 1
                else:
                    if p.ean not in seen_chunk_eans:
                        new_m = models.ProductMapping(
                            product_ean=p.ean,
                            factory_id=item_fid,
                            account_name=acc_name,
                            source_account=source_account,
                            product_name=p.name or f"Product {p.ean}",
                            price=str(p.price) if p.price is not None else None,
                            has_manual_price=False,
                            price_af=str(p.price_af) if p.price_af is not None else None,
                            image_url=p.image_url,
                            last_synced_at=now
                        )
                        new_mappings.append(new_m)
                        seen_chunk_eans.add(p.ean)
                        total_created += 1

            if new_mappings:
                db.add_all(new_mappings)

            db.commit()

        return {"synced": total_created + total_updated, "created": total_created, "updated": total_updated}

    except Exception as exc:
        logger.exception("Failed to sync Aftercool products to DB for factory %s (%s): %s", target_fid, acc_name, exc)
        db.rollback()
        raise
    finally:
        db.close()


async def force_sync_factory_metadata(
    factory_id: str,
    account: str = "JV",
    dataset: str = "lister"
) -> Dict[str, Any]:
    """
    Streams all pages of a factory from Aftercool, parses and syncs them in memory-bounded batches.
    Detects cross-account transferred factories (e.g. JV -> XL) and queries the source Aftercool account.
    Returns status: 'ok' or 'partial_error' and invalidates Redis cache.
    """
    total_loaded = 0
    total_synced = 0
    last_error = None

    fid_clean = str(factory_id or "").strip()
    acc_clean = str(account or "JV").upper().strip()

    aftercool_account = resolve_aftercool_account(fid_clean, acc_clean)
    src_account = aftercool_account if aftercool_account != acc_clean else None
    if src_account:
        logger.info(
            "Factory %s is transferred from %s -> %s. Routing Aftercool queries to %s.",
            fid_clean, src_account, acc_clean, aftercool_account
        )

    try:
        async for product_page in iter_products(factory_id=fid_clean, account=aftercool_account, dataset=dataset, page_size=500):
            total_loaded += len(product_page)
            # Run DB sync in thread pool to prevent blocking the async loop
            res = await asyncio.to_thread(
                sync_products_to_db,
                fid_clean,
                acc_clean,
                product_page,
                500,
                src_account
            )
            total_synced += (res.get("created", 0) + res.get("updated", 0))

        # Invalidate cache for this factory
        await invalidate_products_cache(account=acc_clean, factory_id=fid_clean)

    except Exception as exc:
        last_error = str(exc)
        logger.exception("Error in force_sync_factory_metadata for factory %s: %s", fid_clean, exc)

    status = "partial_error" if last_error else "ok"
    return {
        "status": status,
        "synced": total_synced,
        "total_loaded": total_loaded,
        "error": last_error
    }
