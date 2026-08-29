import os
import json
import asyncio
import httpx
from openai import AsyncOpenAI
from sqlalchemy.orm import Session
from database import SessionLocal
import models
from services.validator import validate_product

from dotenv import load_dotenv

load_dotenv()

# ----------------- CONFIGURATION -----------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
MODEL_NAME = os.environ.get("MODEL_NAME", "gpt-5.6-luna")
FACTORY_ID = "504135"

AFTERCOOL_LOGIN_URL = "https://aftercool.de/auth/login"
AFTERCOOL_BASE_URL = "https://aftercool.de"
USERNAME = "Miras"
PASSWORD = "Miras112112"

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CATEGORIES_PATH = os.path.join(DATA_DIR, "categories.json")
GROUPS_PATH = os.path.join(DATA_DIR, "category_groups.json")
# -------------------------------------------------

print("Loading categories and groups...")
with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
    CATEGORIES = json.load(f)
with open(GROUPS_PATH, "r", encoding="utf-8") as f:
    GROUPS = json.load(f)

# Build a compact list of categories for the LLM to choose from
# We only take categories that are relevant to Möbel / Furniture to save context window.
FURNITURE_CATEGORIES = []
for c in CATEGORIES:
    name = c.get("name", "")
    path = c.get("path", "")
    if "Möbel" in path or "Sofa" in name or "Sessel" in name or "Bett" in name or "Stuhl" in name or "Tisch" in name:
        FURNITURE_CATEGORIES.append({"id": str(c.get("categoryId")), "name": name, "path": path})

if not FURNITURE_CATEGORIES:
    FURNITURE_CATEGORIES = [{"id": str(c.get("categoryId")), "name": c.get("name")} for c in CATEGORIES[:100]]

client = AsyncOpenAI(api_key=OPENAI_API_KEY)

def get_category_schema(category_id: str):
    cat = next((c for c in CATEGORIES if str(c.get("categoryId")) == str(category_id)), None)
    if not cat: return []
    
    group_id = str(cat.get("category_group_id"))
    group_data = GROUPS.get(group_id)
    if not group_data: return []
    
    raw_attrs = group_data.get("attributes", {}).get("attributes", [])
    schema = []
    for a in raw_attrs:
        is_req = a.get("relevance") == "HIGH"
        if a.get("name") == "Marke":
            is_req = True
        schema.append({
            "name": a.get("name"),
            "required": is_req,
            "options": [v if isinstance(v, str) else v.get("value", str(v)) for v in a.get("allowedValues", [])] if a.get("allowedValues") else None
        })
    return schema

async def fetch_products():
    print("Authenticating with Aftercool...")
    async with httpx.AsyncClient(timeout=30.0) as http_client:
        res = await http_client.post(
            AFTERCOOL_LOGIN_URL,
            json={"username": USERNAME, "password": PASSWORD}
        )
        if res.status_code != 200:
            print("Failed to authenticate")
            return []
        
        session_cookie = dict(res.cookies).get("session")
        
        print(f"Fetching products for factory {FACTORY_ID}...")
        res = await http_client.get(
            f"{AFTERCOOL_BASE_URL}/api/products?factory_id={FACTORY_ID}&limit=1000&include_row=1",
            cookies={"session": session_cookie}
        )
        if res.status_code != 200:
            print("Failed to fetch products")
            return []
        
        data = res.json()
        return data.get("items", data.get("results", []))

async def map_product(product: dict, db: Session):
    row = product.get("row", {})
    ean = product.get("ean") or row.get("EAN")
    title = row.get("Artikelbeschreibung") or product.get("name") or ""
    gallery_url = row.get("GalleryURL") or ""
    picture_urls = row.get("PictureURLs") or row.get("PictureURL") or ""
    # CustomItemSpecifics can be JSON string with structured attributes like color, size, material
    specifics_raw = row.get("CustomItemSpecifics") or row.get("ItemSpecifics") or ""
    specifics = ""
    if specifics_raw:
        try:
            parsed = json.loads(specifics_raw) if isinstance(specifics_raw, str) else specifics_raw
            if isinstance(parsed, list):
                specifics = ", ".join([f"{i.get('Name','')}: {i.get('Value','')}" for i in parsed if i.get('Name')])
            elif isinstance(parsed, dict):
                specifics = ", ".join([f"{k}: {v}" for k, v in parsed.items()])
        except:
            specifics = str(specifics_raw)
    
    if not ean or not title:
        return
        
    print(f"\nProcessing product: {ean} - {title[:50]}...")
    
    # 1. Ask LLM to determine the category ID
    product_info = f"Title: {title}"
    if specifics:
        product_info += f"\nItem Specifics: {specifics}"
    if gallery_url:
        product_info += f"\nGallery URL: {gallery_url}"
    if picture_urls:
        product_info += f"\nPicture URLs: {picture_urls}"

    prompt = f"""
    You are an expert product categorizer.
    {product_info}
    
    Here is a list of available categories (ID: Path):
    {chr(10).join([f"{c['id']}: {c['path']} > {c['name']}" for c in FURNITURE_CATEGORIES])}
    
    Based on the product information, choose the single most appropriate category ID for this product.
    Respond ONLY with the Category ID (numbers only).
    """
    
    try:
        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=10,
            temperature=0.0
        )
        category_id = response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Error calling OpenAI for category: {e}")
        return
        
    if not category_id.isdigit():
        print(f"Invalid category ID returned: {category_id}")
        return
        
    print(f"Assigned category: {category_id}")
    
    # 2. Get Schema
    schema = get_category_schema(category_id)
    if not schema:
        print("No schema found for this category.")
        return
        
    required_attrs = [a for a in schema if a["required"]]
    
    # 3. Ask LLM to extract attributes
    attr_prompt = f"""
    You are an expert product attribute extractor.
    {product_info}
    
    Extract the following required attributes for this product. 
    If options are provided, you MUST choose from the options.
    If you cannot find the information, make your best guess based on the product title and specifics.
    
    Attributes to extract:
    """
    for a in required_attrs:
        opts = f" (Options: {', '.join(a['options'])})" if a['options'] else ""
        attr_prompt += f"\n- {a['name']}{opts}"
        
    attr_prompt += "\n\nRespond with a JSON object where keys are the attribute names and values are the extracted values."
    
    try:
        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": attr_prompt}],
            response_format={ "type": "json_object" },
            temperature=0.0
        )
        extracted_attrs = json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"Error calling OpenAI for attributes: {e}")
        return
        
    print(f"Extracted attributes: {extracted_attrs}")
    
    # 4. Save to DB
    # Delete old
    db.query(models.ProductAttribute).filter(models.ProductAttribute.product_ean == ean).delete()
    
    for k, v in extracted_attrs.items():
        if v and str(v).strip():
            db.add(models.ProductAttribute(product_ean=ean, attribute_name=k, attribute_value=str(v).strip()))
            
    db_mapping = db.query(models.ProductMapping).filter(models.ProductMapping.product_ean == ean).first()
    if not db_mapping:
        db_mapping = models.ProductMapping(product_ean=ean, factory_id=FACTORY_ID, category_id=category_id)
        db.add(db_mapping)
    else:
        db_mapping.category_id = category_id
        
    # Re-validate
    db.commit() # commit first so we can read back if needed
    all_attrs = db.query(models.ProductAttribute).filter(models.ProductAttribute.product_ean == ean).all()
    attr_dict = {a.attribute_name: a.attribute_value for a in all_attrs}
    errors = validate_product(ean, category_id, attr_dict)
    
    db_mapping.status = "Error" if errors else "Ready"
    db.commit()
    print(f"Saved! Status: {db_mapping.status}")


async def run():
    products = await fetch_products()
    print(f"Loaded {len(products)} products.")
    
    db = SessionLocal()
    
    for p in products:
        await map_product(p, db)
        
    db.close()
    print("All products processed!")

if __name__ == "__main__":
    asyncio.run(run())
