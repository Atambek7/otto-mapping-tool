import os
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
import httpx
import auth
from database import get_db
from sqlalchemy.orm import Session
import models
import datetime

router = APIRouter(prefix="/aftercool", tags=["aftercool"])

AFTERCOOL_LOGIN_URL = "https://aftercool.de/auth/login"
AFTERCOOL_BASE_URL = "https://aftercool.de"

USERNAME = os.getenv("AFTERCOOL_USERNAME", "Atambek")
PASSWORD = os.getenv("AFTERCOOL_PASSWORD", "Atambek112")

_cached_session = None

async def get_aftercool_session(force_refresh: bool = False) -> str:
    global _cached_session
    if _cached_session and not force_refresh:
        return _cached_session
        
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            AFTERCOOL_LOGIN_URL,
            headers={"Content-Type": "application/json"},
            json={"username": USERNAME, "password": PASSWORD}
        )
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail="Failed to auth with Aftercool")
        
        session = dict(response.cookies).get("session")
        if not session:
            raise HTTPException(status_code=502, detail="No session cookie returned")
            
        _cached_session = session
        return session

@router.get("/factories")
async def get_factories(current_user=Depends(auth.get_current_user)):
    session = await get_aftercool_session()
    async with httpx.AsyncClient(cookies={"session": session}, timeout=20.0) as client:
        res = await client.get(f"{AFTERCOOL_BASE_URL}/api/factories", params={"refresh": "", "fast": ""})
        if res.status_code in (401, 403):
            # Try once more with fresh session
            session = await get_aftercool_session(force_refresh=True)
            res = await client.get(f"{AFTERCOOL_BASE_URL}/api/factories", params={"refresh": "", "fast": ""}, cookies={"session": session})
            
        if res.status_code != 200:
            raise HTTPException(status_code=res.status_code, detail="Error fetching factories")
        return res.json()

@router.get("/products")
async def get_products(
    factory_id: str, 
    account: str = "",
    dataset: str = "",
    q: str = "",
    include_row: int = 0,
    limit: int = 100, 
    offset: int = 0, 
    background_tasks: BackgroundTasks = None,
    current_user=Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    session = await get_aftercool_session()
    async with httpx.AsyncClient(cookies={"session": session}, timeout=30.0) as client:
        params = {
            "factory_id": factory_id,
            "account": account,
            "dataset": dataset,
            "q": q,
            "include_row": include_row,
            "limit": limit,
            "offset": offset
        }
        res = await client.get(f"{AFTERCOOL_BASE_URL}/api/products", params=params)
        
        if res.status_code in (401, 403):
            session = await get_aftercool_session(force_refresh=True)
            res = await client.get(f"{AFTERCOOL_BASE_URL}/api/products", params=params, cookies={"session": session})
            
        if res.status_code != 200:
            raise HTTPException(status_code=res.status_code, detail="Error fetching products")
        
        data = res.json()
        
        if background_tasks and include_row == 1:
            background_tasks.add_task(sync_products_to_db, factory_id, account, data, db)
            
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
        price = row.get("Startpreis") or row.get("SofortkaufenPreis") or ""
        image_url = row.get("PictureURLs") or row.get("PictureURL") or row.get("GalleryURL") or ""
        if isinstance(image_url, list):
            image_url = image_url[0] if image_url else ""
        elif isinstance(image_url, str) and "|" in image_url:
            image_url = image_url.split("|")[0]
        parsed.append({"ean": str(ean), "name": str(name).strip(), "price": str(price).strip(), "image_url": str(image_url).strip()})

    all_eans = [p["ean"] for p in parsed]
    existing = {m.product_ean: m for m in db.query(models.ProductMapping).filter(
        models.ProductMapping.product_ean.in_(all_eans)).all()}

    synced = 0
    for p in parsed:
        m = existing.get(p["ean"])
        if m:
            m.product_name = p["name"] or m.product_name
            m.price = p["price"] or m.price
            m.image_url = p["image_url"] or m.image_url
            m.last_synced_at = now
            synced += 1

    db.commit()
    return {"status": "ok", "synced": synced}

def sync_products_to_db(factory_id: str, account: str, data: dict, db: Session):
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
            price = row.get("Startpreis") or row.get("SofortkaufenPreis") or "0"
            
            image_url = row.get("PictureURLs") or row.get("PictureURL") or row.get("GalleryURL") or ""
            if isinstance(image_url, list):
                image_url = image_url[0] if image_url else ""
            elif isinstance(image_url, str) and "|" in image_url:
                image_url = image_url.split("|")[0]
            elif isinstance(image_url, str) and "," in image_url:
                image_url = image_url.split(",")[0]
                
            all_eans.append(str(ean))
            parsed_items.append({
                "ean": str(ean),
                "name": str(name).strip(),
                "price": str(price).strip(),
                "image_url": str(image_url).strip()
            })
            
        # Bulk query existing globally by EAN (since EAN is unique)
        existing_mappings = {m.product_ean: m for m in db.query(models.ProductMapping).filter(
            models.ProductMapping.product_ean.in_(all_eans)
        ).all()}
        
        new_mappings = []
        seen_new_eans = set()
        
        for p in parsed_items:
            ean = p["ean"]
            m = existing_mappings.get(ean)
            if m:
                if m.factory_id != factory_id:
                    print(f"[REASSIGNMENT] EAN {p['ean']} moved from factory {m.factory_id} to {factory_id}")
                    m.factory_id = factory_id
                m.product_name = p["name"]
                m.price = p["price"]
                m.image_url = p["image_url"]
                m.last_synced_at = now
            else:
                if ean not in seen_new_eans:
                    new_m = models.ProductMapping(
                        product_ean=ean,
                        factory_id=factory_id,
                        account_name=account or "JV",
                        product_name=p["name"],
                        price=p["price"],
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
