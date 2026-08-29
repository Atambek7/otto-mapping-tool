import os
import json

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
GROUPS_PATH = os.path.join(DATA_DIR, "category_groups.json")
CATEGORIES_PATH = os.path.join(DATA_DIR, "categories.json")

# Load data into memory once
GROUPS = {}
CATEGORIES = []
CATEGORIES_BY_ID = {}
CATEGORIES_BY_NAME = {}

try:
    if os.path.exists(GROUPS_PATH):
        with open(GROUPS_PATH, "r", encoding="utf-8") as f:
            GROUPS = json.load(f)
            
    if os.path.exists(CATEGORIES_PATH):
        with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
            CATEGORIES = json.load(f)
            # Create O(1) lookup dictionary
            CATEGORIES_BY_ID = {str(c.get("categoryId")): c for c in CATEGORIES if c.get("categoryId")}
            # Keep the last encountered category by name for fallback (not perfect for duplicates but O(1))
            CATEGORIES_BY_NAME = {c.get("name"): c for c in CATEGORIES if c.get("name")}
except Exception as e:
    print("Warning: Could not load OKB data in categories_repo:", e)

def get_category_by_id(cat_id: str):
    if not cat_id:
        return None
    cat = CATEGORIES_BY_ID.get(str(cat_id))
    if cat:
        return cat
    # Fallback to search by name if frontend passes name instead of ID
    return CATEGORIES_BY_NAME.get(cat_id)

def get_categories():
    return CATEGORIES

def get_groups():
    return GROUPS
