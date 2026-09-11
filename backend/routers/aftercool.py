import os
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
import httpx
import auth
import json
import redis
from database import get_db, SessionLocal
from sqlalchemy.orm import Session
import models
import datetime

router = APIRouter(prefix="/aftercool", tags=["aftercool"])

AFTERCOOL_LOGIN_URL = os.getenv("AFTERCOOL_LOGIN_URL", "https://aftercool.de/auth/login")
AFTERCOOL_BASE_URL = os.getenv("AFTERCOOL_BASE_URL", "https://aftercool.de")
REDIS_URL = os.getenv("CELERY_BROKER_URL", os.getenv("REDIS_URL", "redis://redis:6379/0"))

try:
    redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
except Exception:
    redis_client = None

_cached_session = None

async def get_aftercool_session(force_refresh: bool = False) -> str:
    global _cached_session
    if _cached_session and not force_refresh:
        return _cached_session
        
    username = os.getenv("AFTERCOOL_USERNAME")
    password = os.getenv("AFTERCOOL_PASSWORD")
    if not username or not password:
        raise HTTPException(
            status_code=500,
            detail="Aftercool credentials (AFTERCOOL_USERNAME, AFTERCOOL_PASSWORD) are not configured in environment"
        )
        
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            AFTERCOOL_LOGIN_URL,
            headers={"Content-Type": "application/json"},
            json={"username": username, "password": password}
        )
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail="Failed to auth with Aftercool")
        
        session = dict(response.cookies).get("session")
        if not session:
            raise HTTPException(status_code=502, detail="No session cookie returned")
            
        _cached_session = session
        return session

@router.get("/factories")
async def get_factories(refresh: bool = False, current_user=Depends(auth.get_current_user)):
    cache_key = "aftercool_factories_cache"
    if not refresh and redis_client:
        try:
            cached_data = redis_client.get(cache_key)
            if cached_data:
                return json.loads(cached_data)
        except Exception as e:
            print(f"[Redis Cache] Read error: {e}")

    session = await get_aftercool_session()
    async with httpx.AsyncClient(cookies={"session": session}, timeout=20.0) as client:
        res = await client.get(f"{AFTERCOOL_BASE_URL}/api/factories", params={"refresh": "", "fast": ""})
        if res.status_code in (401, 403):
            session = await get_aftercool_session(force_refresh=True)
            res = await client.get(f"{AFTERCOOL_BASE_URL}/api/factories", params={"refresh": "", "fast": ""}, cookies={"session": session})
            
        if res.status_code != 200:
            raise HTTPException(status_code=res.status_code, detail="Error fetching factories")
        
        data = res.json()
        if redis_client and data:
            try:
                redis_client.set(cache_key, json.dumps(data), ex=600)
            except Exception as e:
                print(f"[Redis Cache] Write error: {e}")
        return data

@router.get("/products")
async def get_products(
    factory_id: Optional[str] = None, 
    account: str = "",
    dataset: str = "",
    q: str = "",
    include_row: int = 0,
    limit: int = 100, 
    offset: int = 0, 
    refresh: bool = False,
    sync_db: int = 1,
    dedup: int = 0,
    background_tasks: BackgroundTasks = None,
    current_user=Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    fid_str = str(factory_id).strip() if factory_id else ""
    cache_key = f"aftercool:products:{account}:{fid_str}:{dataset}:{q}:{limit}:{offset}:{include_row}:{dedup}"
    if not refresh and redis_client:
        try:
            cached_data = redis_client.get(cache_key)
            if cached_data:
                return json.loads(cached_data)
        except Exception as e:
            print(f"[Redis Products Cache] Read error: {e}")

    session = await get_aftercool_session()
    async with httpx.AsyncClient(cookies={"session": session}, timeout=30.0) as client:
        params = {
            "account": account,
            "dataset": dataset,
            "q": q,
            "include_row": include_row,
            "limit": limit,
            "offset": offset
        }
        if fid_str:
            params["factory_id"] = fid_str
        res = await client.get(f"{AFTERCOOL_BASE_URL}/api/products", params=params)
        
        if res.status_code in (401, 403):
            session = await get_aftercool_session(force_refresh=True)
            res = await client.get(f"{AFTERCOOL_BASE_URL}/api/products", params=params, cookies={"session": session})
            
        if res.status_code != 200:
            raise HTTPException(status_code=res.status_code, detail="Error fetching products")
        
        data = res.json()

        if dedup == 1 and isinstance(data, dict) and "items" in data:
            seen_eans = set()
            deduped = []
            for item in data["items"]:
                ean = str(item.get("ean") or (item.get("row") or {}).get("EAN") or "").strip()
                if not ean or ean in ("—", "0"):
                    deduped.append(item)
                elif ean not in seen_eans:
                    seen_eans.add(ean)
                    deduped.append(item)
            data["items"] = deduped
            data["total"] = len(deduped)
        
        if redis_client and data:
            try:
                # Cache for 30 minutes (1800s)
                redis_client.set(cache_key, json.dumps(data), ex=1800)
            except Exception as e:
                print(f"[Redis Products Cache] Write error: {e}")

        if background_tasks and include_row == 1 and sync_db == 1:
            background_tasks.add_task(sync_products_to_db, fid_str, account, data)
            
        return data

@router.post("/sync-metadata/{factory_id}")
async def force_sync_metadata(
    factory_id: str,
    account: str = "JV",
    dataset: str = "lister",
    current_user=Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """Force-sync product_name, price, image_url for all products of a factory from Aftercool into our DB."""
    session = await get_aftercool_session()
    all_items = []
    offset = 0
    limit = 500
    async with httpx.AsyncClient(cookies={"session": session}, timeout=60.0) as client:
        while True:
            params = {"factory_id": factory_id, "account": account, "dataset": dataset,
                      "include_row": 1, "limit": limit, "offset": offset}
            res = await client.get(f"{AFTERCOOL_BASE_URL}/api/products", params=params)
            if res.status_code in (401, 403):
                session = await get_aftercool_session(force_refresh=True)
                res = await client.get(f"{AFTERCOOL_BASE_URL}/api/products", params=params,
                                       cookies={"session": session})
            if res.status_code != 200:
                break
            data = res.json()
            items = data.get("items", data) if isinstance(data, dict) else data
            if not isinstance(items, list) or len(items) == 0:
                break
            all_items.extend(items)
            if len(items) < limit:
                break
            offset += limit

    if not all_items:
        return {"status": "ok", "synced": 0}

    # Parse
    now = datetime.datetime.utcnow()
    parsed = []
    for item in all_items:
        row = item.get("row", {})
        ean = item.get("ean") or row.get("EAN")
        if not ean:
            continue
        name = row.get("Artikelbeschreibung") or item.get("name") or ""
        price_af = row.get("Startpreis") or ""
        price_otto = row.get("SofortkaufenPreis") or row.get("Startpreis") or ""
        image_url = row.get("PictureURLs") or row.get("PictureURL") or row.get("GalleryURL") or ""
        if isinstance(image_url, list):
            image_url = image_url[0] if image_url else ""
        elif isinstance(image_url, str) and "|" in image_url:
            image_url = image_url.split("|")[0]
        parsed.append({
            "ean": str(ean), 
            "name": str(name).strip(), 
            "price": str(price_otto).strip(), 
            "price_af": str(price_af).strip(),
            "image_url": str(image_url).strip()
        })

    all_eans = [p["ean"] for p in parsed]
    existing = {m.product_ean: m for m in db.query(models.ProductMapping).filter(
        models.ProductMapping.product_ean.in_(all_eans)).all()}

    synced = 0
    for p in parsed:
        m = existing.get(p["ean"])
        if m:
            if account:
                m.account_name = account
            m.product_name = p["name"] or m.product_name
            m.price = p["price"] or m.price
            m.price_af = p["price_af"] or m.price_af
            m.image_url = p["image_url"] or m.image_url
            m.last_synced_at = now
            synced += 1

    db.commit()
    return {"status": "ok", "synced": synced}

def sync_products_to_db(factory_id: str, account: str, data: dict):
    db: Session = SessionLocal()
    try:
        items = data.get("items", data) if isinstance(data, dict) else data
        if not isinstance(items, list):
            return
            
        now = datetime.datetime.utcnow()
        updates = []
        
        # Existing eans
        all_eans = []
        parsed_items = []
        for item in items:
            row = item.get("row", {})
            ean = item.get("ean") or row.get("EAN")
            if not ean:
                continue
                
            name = row.get("Artikelbeschreibung") or item.get("name") or ""
            price_af = row.get("Startpreis") or ""
            price_otto = row.get("SofortkaufenPreis") or row.get("Startpreis") or "0"
            
            image_url = row.get("PictureURLs") or row.get("PictureURL") or row.get("GalleryURL") or ""
            if isinstance(image_url, list):
                image_url = image_url[0] if image_url else ""
            elif isinstance(image_url, str) and "|" in image_url:
                image_url = image_url.split("|")[0]
            elif isinstance(image_url, str) and "," in image_url:
                image_url = image_url.split(",")[0]
                
            item_fid = str(item.get("factory_id") or factory_id or "").strip()
            all_eans.append(str(ean))
            parsed_items.append({
                "ean": str(ean),
                "factory_id": item_fid,
                "name": str(name).strip(),
                "price": str(price_otto).strip(),
                "price_af": str(price_af).strip(),
                "image_url": str(image_url).strip()
            })
            
        # Bulk query existing strictly per account
        acc_name = account or "JV"
        existing_mappings = {m.product_ean: m for m in db.query(models.ProductMapping).filter(
            models.ProductMapping.product_ean.in_(all_eans),
            models.ProductMapping.account_name == acc_name
        ).all()}
        
        new_mappings = []
        seen_new_eans = set()
        
        for p in parsed_items:
            ean = p["ean"]
            item_fid = p.get("factory_id") or factory_id or ""
            m = existing_mappings.get(ean)
            if m:
                if item_fid and m.factory_id != item_fid:
                    print(f"[REASSIGNMENT] EAN {p['ean']} moved from factory {m.factory_id} to {item_fid}")
                    m.factory_id = item_fid
                if account:
                    m.account_name = account
                m.product_name = p["name"]
                m.price = p["price"]
                m.price_af = p["price_af"]
                m.image_url = p["image_url"]
                m.last_synced_at = now
            else:
                if ean not in seen_new_eans:
                    new_m = models.ProductMapping(
                        product_ean=ean,
                        factory_id=item_fid,
                        account_name=acc_name,
                        product_name=p["name"],
                        price=p["price"],
                        price_af=p["price_af"],
                        image_url=p["image_url"],
                        last_synced_at=now
                    )
                    new_mappings.append(new_m)
                    seen_new_eans.add(ean)
                
        if new_mappings:
            db.add_all(new_mappings)
            
        db.commit()
    except Exception as e:
        print(f"Error syncing products to DB: {e}")
        db.rollback()
    finally:
        db.close()
