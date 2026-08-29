import httpx
import asyncio

async def test():
    # Login
    async with httpx.AsyncClient() as client:
        res = await client.post(
            "https://aftercool.de/auth/login",
            json={"username": "Miras", "password": "Miras112112"}
        )
        session = dict(res.cookies).get("session")
        print("Session:", session)
        
        # Get products
        res2 = await client.get(
            "https://aftercool.de/api/products",
            params={
                "factory_id": "504135",
                "account": "JV",
                "dataset": "lister",
                "limit": 10,
                "include_row": 1
            },
            cookies={"session": session}
        )
        data = res2.json()
        print("Products count:", len(data))
        if isinstance(data, list):
            pass
        elif "items" in data:
            print("Items count:", len(data["items"]))
        else:
            print("Data format:", list(data.keys()))

asyncio.run(test())
