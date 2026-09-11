import asyncio
import httpx
from routers.aftercool import get_aftercool_session, AFTERCOOL_BASE_URL

async def test():
    session = await get_aftercool_session()
    async with httpx.AsyncClient(cookies={"session": session}, timeout=20.0) as client:
        # 1. Search by name fragment "Couchtisch Moderner"
        res = await client.get(f"{AFTERCOOL_BASE_URL}/api/products", params={"q": "Couchtisch Moderner", "include_row": 1})
        items = res.json().get("items", [])
        print("Search by title fragment 'Couchtisch Moderner':", len(items))
        for it in items[:3]:
            print(" - EAN:", it.get("ean"), "Name:", it.get("name"), "Account:", it.get("account"))
            row = it.get("row", {})
            print("   Pic:", row.get("PictureURLs") or row.get("PictureURL") or row.get("GalleryURL"))

        # 2. Search by article number S03H002U
        res2 = await client.get(f"{AFTERCOOL_BASE_URL}/api/products", params={"q": "S03H002U", "include_row": 1})
        items2 = res2.json().get("items", [])
        print("Search by art S03H002U:", len(items2))

        # 3. Search for other order items
        # Let's get 10 order items from DB and check their EANs
        from database import SessionLocal
        import models
        db = SessionLocal()
        order_items = db.query(models.OttoOrderItem).limit(10).all()
        print(f"Checking {len(order_items)} DB order items in Aftercool...")
        for oi in order_items:
            ean = oi.ean
            r = await client.get(f"{AFTERCOOL_BASE_URL}/api/products", params={"q": ean, "include_row": 1})
            its = r.json().get("items", [])
            print(f"EAN {ean} ({oi.product_title[:30]}): found={len(its)}")
            if its:
                row = its[0].get("row", {})
                img = row.get("PictureURLs") or row.get("PictureURL") or row.get("GalleryURL") or ""
                print(f"   Image: {img}")

if __name__ == "__main__":
    asyncio.run(test())
