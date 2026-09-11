import os
from typing import Dict, Any, List

def format_otto_product(ean: str, aftercool_info: Dict[str, Any], mapping: Dict[str, Any], category_name: str) -> Dict[str, Any]:
    # Basic Info
    name = aftercool_info.get("name", "")
    desc = aftercool_info.get("description", name)
    price_str = aftercool_info.get("price", "0")
    try:
        price_float = float(str(price_str).replace(",", "."))
    except ValueError:
        price_float = 0.0

    # Images: Otto API strictly requires full absolute URLs (e.g. https://domain.com/static/...)
    images = aftercool_info.get("images", [])
    public_base_url = os.getenv("PUBLIC_BASE_URL", "https://omt.automatonsoft.de").rstrip("/")
    media_assets = []
    for img in images:
        if not img or not isinstance(img, str):
            continue
        img_str = img.strip()
        if not img_str:
            continue
        if img_str.startswith("/"):
            location = f"{public_base_url}{img_str}"
        elif not (img_str.startswith("http://") or img_str.startswith("https://")):
            location = f"{public_base_url}/{img_str.lstrip('/')}"
        else:
            location = img_str
        media_assets.append({"type": "IMAGE", "location": location})

    # Attributes
    attributes = []
    # Some attributes might need to be hoisted if Otto requires them at top level.
    # We rely on user mapping or fallback.
    raw_attrs = mapping.get("attributes", {})
    brand_id = raw_attrs.pop("brandId", None) or raw_attrs.pop("BrandId", None) or raw_attrs.pop("Marke", None) or "UNKNOWN"

    for k, v in raw_attrs.items():
        if v:
            attributes.append({
                "name": str(k),
                "values": [str(v)],
                "additional": False
            })

    payload = {
        "productReference": str(ean),
        "sku": str(ean),
        "ean": str(ean),
        "productDescription": {
            "category": category_name,
            "brandId": brand_id,
            "description": desc,
            "attributes": attributes
        },
        "mediaAssets": media_assets,
        "pricing": {
            "standardPrice": {
                "amount": price_float,
                "currency": "EUR"
            },
            "vat": "FULL"  # Defaulting to FULL as per typical requirements
        },
        "logistics": {
            "packingUnitCount": 1,
            "packingUnits": [{
                "weight": 1,
                "width": 10,
                "height": 10,
                "length": 10
            }]
        }
    }
    return payload
