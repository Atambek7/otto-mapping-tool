import sys
import os
import asyncio
import httpx

# Add parent to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from database import SessionLocal
import models
from routers.aftercool import get_aftercool_session, AFTERCOOL_BASE_URL, sync_products_to_db

async def backfill():
    db = SessionLocal()
    
    # Get unique factory IDs
    factories = db.query(models.ProductMapping.factory_id).distinct().all()
    factory_ids = [f[0] for f in factories if f[0]]
    
    print(f"Found {len(factory_ids)} unique factories to backfill.")
    
    session = await get_aftercool_session()
    async with httpx.AsyncClient(cookies={"session": session}, timeout=60.0) as client:
        for fid in factory_ids:
            print(f"Fetching data for factory {fid}...")
            # We assume maximum 5000 is enough, as per original fetch_product_info_for_export logic
            res = await client.get(f"{AFTERCOOL_BASE_URL}/api/products", params={
                "factory_id": fid,
                "limit": 5000,
                "include_row": 1
            })
            
            if res.status_code == 200:
                data = res.json()
                items = data.get("items", [])
                print(f" - Found {len(items)} items from API.")
                
                # First product mapping to figure out account
                sample_map = db.query(models.ProductMapping).filter(models.ProductMapping.factory_id == fid).first()
                account = sample_map.account_name if sample_map else "JV"
                
                sync_products_to_db(fid, account, data, db)
                print(f" - Synced factory {fid}.")
            else:
                print(f" - Failed to fetch for {fid} (HTTP {res.status_code})")
                
    db.close()
    print("Backfill complete.")

if __name__ == "__main__":
    asyncio.run(backfill())
