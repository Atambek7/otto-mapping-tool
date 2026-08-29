import httpx
from sqlalchemy.orm import Session
import asyncio
from database import SessionLocal
import models
import json
import os

from services.categories_repo import get_category_by_id

def find_category_id_by_name(category_name: str) -> str:
    if not category_name:
        return None
    cat = get_category_by_id(category_name)
    if cat:
        return str(cat.get("categoryId"))
    return None

async def fetch_product_from_okb(ean: str) -> dict:
    url = "https://okb.automatonsoft.de/extermal/get_products"
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            # OKB API often uses SKU and EAN interchangeably for this endpoint
            res = await client.get(url, params={"sku": ean})
            if res.status_code == 200:
                data = res.json()
                if data and "productVariations" in data and len(data["productVariations"]) > 0:
                    return data["productVariations"][0]
        except Exception as e:
            print(f"Error fetching {ean} from OKB:", e)
    return None

async def sync_product_from_okb(ean: str, factory_id: str, account: str = "JV"):
    product_data = await fetch_product_from_okb(ean)
    if not product_data:
        return {"ean": ean, "status": "not_found", "message": "Product not found in OKB"}

    desc = product_data.get("productDescription", {})
    category_name = desc.get("category")
    attributes_raw = desc.get("attributes", [])

    category_id = find_category_id_by_name(category_name)
    if not category_id:
        return {"ean": ean, "status": "error", "message": f"Category '{category_name}' not found locally"}

    # Format attributes
    # OKB format: {"name": "Farbe", "values": ["Grau", "Schwarz"]}
    formatted_attributes = {}
    for attr in attributes_raw:
        name = attr.get("name")
        values = attr.get("values", [])
        if name and values:
            formatted_attributes[name] = ", ".join(str(v) for v in values)

    return {
        "ean": ean,
        "status": "success",
        "category": category_name,
        "category_id": category_id,
        "attributes": formatted_attributes
    }
