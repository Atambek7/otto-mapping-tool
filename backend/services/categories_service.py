import os
import json
import logging
from typing import Dict, Iterable
from sqlalchemy.orm import Session
import models

_JSON_CAT_CACHE: Dict[str, str] | None = None

def get_json_categories() -> Dict[str, str]:
    global _JSON_CAT_CACHE
    if _JSON_CAT_CACHE is not None:
        return _JSON_CAT_CACHE

    _JSON_CAT_CACHE = {}
    json_path = os.path.join(os.path.dirname(__file__), "..", "data", "categories.json")
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data:
                if "categoryId" in item and "name" in item:
                    _JSON_CAT_CACHE[str(item["categoryId"])] = str(item["name"])
        except Exception as e:
            logging.warning(f"Failed to load categories.json fallback: {e}")
    return _JSON_CAT_CACHE

def resolve_category_names(category_ids: Iterable[str], db: Session) -> Dict[str, str]:
    """
    Resolves a collection of category IDs to their German category names.
    Queries database (CategoryCache) first, then falls back to backend/data/categories.json
    for any missing IDs to guarantee Otto API never receives an invalid or empty category.
    """
    clean_ids = list({str(cid).strip() for cid in category_ids if cid and str(cid).strip()})
    if not clean_ids:
        return {}

    name_map: Dict[str, str] = {}
    try:
        cats = db.query(models.CategoryCache).filter(models.CategoryCache.id.in_(clean_ids)).all()
        for c in cats:
            if c.name:
                name_map[str(c.id)] = str(c.name)
    except Exception as e:
        logging.warning(f"Error querying CategoryCache from DB: {e}")

    missing_ids = [cid for cid in clean_ids if cid not in name_map]
    if missing_ids:
        json_map = get_json_categories()
        for cid in missing_ids:
            if cid in json_map:
                name_map[cid] = json_map[cid]

    return name_map
