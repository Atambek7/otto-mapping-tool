import os
import sys
import json

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
GROUPS_PATH = os.path.join(DATA_DIR, "category_groups.json")
CATEGORIES_PATH = os.path.join(DATA_DIR, "categories.json")

# Load data into memory once (projected & interned to minimize multi-worker RAM footprint)
GROUPS = {}
CATEGORIES = []
CATEGORIES_BY_ID = {}
CATEGORIES_BY_NAME = {}

_KEEP_ATTR_KEYS = (
    "attributeId",
    "name",
    "type",
    "unit",
    "relevance",
    "featureRelevance",
    "maxLength",
    "allowedValues",
)


def _compact_group_entry(group_obj: dict) -> dict:
    raw_attrs = (group_obj.get("attributes") or {}).get("attributes") or []
    compact_attrs = []
    for a in raw_attrs:
        if not isinstance(a, dict):
            continue
        item = {}
        for k in _KEEP_ATTR_KEYS:
            val = a.get(k)
            if val is None:
                continue
            if isinstance(val, str):
                val = sys.intern(val)
            elif k in ("allowedValues", "featureRelevance") and isinstance(val, list):
                val = [sys.intern(v) if isinstance(v, str) else v for v in val]
            item[sys.intern(k)] = val
        compact_attrs.append(item)
    return {
        "group_name": group_obj.get("group_name"),
        "attributes": {"attributes": compact_attrs},
    }


try:
    if os.path.exists(GROUPS_PATH):
        with open(GROUPS_PATH, "r", encoding="utf-8") as f:
            _raw_groups = json.load(f)
            GROUPS = {
                sys.intern(str(gid)): _compact_group_entry(gdata)
                for gid, gdata in _raw_groups.items()
                if isinstance(gdata, dict)
            }
            del _raw_groups

    if os.path.exists(CATEGORIES_PATH):
        with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
            CATEGORIES = json.load(f)
            for _c in CATEGORIES:
                _c["_name_lower"] = (_c.get("name") or "").lower()
                _c["_group_lower"] = (_c.get("category_group") or "").lower()
            # Create O(1) lookup dictionary
            CATEGORIES_BY_ID = {str(c.get("categoryId")): c for c in CATEGORIES if c.get("categoryId")}
            # Keep the last encountered category by name for fallback (not perfect for duplicates but O(1))
            CATEGORIES_BY_NAME = {c.get("name"): c for c in CATEGORIES if c.get("name")}
except Exception as e:
    print("Warning: Could not load OKB data in categories_repo:", e)

CATEGORY_ALIASES = {
    "jungendzimmer-set": "24446",
    "jungedzimmer-set": "24446",
    "jugendzimmer set": "24446",
    "dreisitzer": "25919",
    "3-sitzer-sofa": "25919",
    "zweisitzer": "25907",
    "2-sitzer-sofa": "25907",
}


def get_category_by_id(cat_id: str):
    if not cat_id:
        return None
    raw_key = str(cat_id).strip()
    cat = CATEGORIES_BY_ID.get(raw_key)
    if cat:
        return cat
    # Fallback to search by name if frontend passes name instead of ID
    cat = CATEGORIES_BY_NAME.get(raw_key)
    if cat:
        return cat
    alias_id = CATEGORY_ALIASES.get(raw_key.lower())
    if alias_id:
        return CATEGORIES_BY_ID.get(alias_id)
    return None

# Precomputed sorted categories for fast A-Z lookup
CATEGORIES_SORTED = sorted(CATEGORIES, key=lambda c: (c.get("name") or "").lower())

def get_categories(sorted_alphabetical: bool = False):
    return CATEGORIES_SORTED if sorted_alphabetical else CATEGORIES

def get_groups():
    return GROUPS


UNIVERSAL_ALLOWED_ATTRIBUTES = {
    "Modellbezeichnung",
    "Marke",
    "BrandId",
    "brandId",
    "Bullet Point 1",
    "Bullet Point 2",
    "Bullet Point 3",
    "Bullet Point 4",
    "Bullet Point 5",
    "bullet_point_1",
    "bullet_point_2",
    "bullet_point_3",
    "bullet_point_4",
    "bullet_point_5",
}

# Dynamically add all configured default attributes to the whitelist so they are never stripped
try:
    _defaults_file = os.path.join(DATA_DIR, "default_attributes.json")
    if os.path.exists(_defaults_file):
        with open(_defaults_file, "r", encoding="utf-8") as _df:
            _def_data = json.load(_df)
            for _k in _def_data.get("global", {}).keys():
                UNIVERSAL_ALLOWED_ATTRIBUTES.add(str(_k).strip())
            for _acc_data in _def_data.get("accounts", {}).values():
                for _k in _acc_data.keys():
                    UNIVERSAL_ALLOWED_ATTRIBUTES.add(str(_k).strip())
except Exception as e:
    print("Warning: Could not load default_attributes.json in categories_repo:", e)


_ALLOWED_ATTRS_CACHE = {}
_NUMERIC_ATTRS_CACHE = {}


def get_allowed_attribute_names(cat_id: str) -> set[str] | None:
    """
    Returns the set of valid Otto attribute names for the given category ID or name.
    Includes category schema attributes plus universal attributes (Modellbezeichnung, Marke, Bullets).
    Returns None if category is not found in OKB (custom / unknown category).
    """
    if not cat_id:
        return None

    cat_key = str(cat_id).strip()
    if cat_key in _ALLOWED_ATTRS_CACHE:
        return _ALLOWED_ATTRS_CACHE[cat_key]

    cat = get_category_by_id(cat_key)
    if not cat:
        return None

    group_id = str(cat.get("category_group_id") or "")
    group_data = GROUPS.get(group_id) or {}
    raw_attrs = group_data.get("attributes", {}).get("attributes", [])

    allowed = set(UNIVERSAL_ALLOWED_ATTRIBUTES)
    for a in raw_attrs:
        name = a.get("name")
        if name and str(name).strip():
            allowed.add(str(name).strip())

    _ALLOWED_ATTRS_CACHE[cat_key] = allowed
    return allowed


def get_numeric_attributes_for_category(cat_id: str) -> dict[str, dict]:
    """
    Returns a dictionary of numeric attributes for the category:
    {attr_name: {"type": "FLOAT"|"INTEGER", "unit": str|None}}
    """
    if not cat_id:
        return {}

    cat_key = str(cat_id).strip()
    if cat_key in _NUMERIC_ATTRS_CACHE:
        return _NUMERIC_ATTRS_CACHE[cat_key]

    cat = get_category_by_id(cat_key)
    if not cat:
        return {}

    group_id = str(cat.get("category_group_id") or "")
    group_data = GROUPS.get(group_id) or {}
    raw_attrs = group_data.get("attributes", {}).get("attributes", [])

    numeric_map = {}
    for a in raw_attrs:
        name = a.get("name")
        attr_type = a.get("type")
        if name and attr_type in ("FLOAT", "INTEGER"):
            numeric_map[str(name).strip()] = {
                "type": attr_type,
                "unit": a.get("unit"),
            }

    _NUMERIC_ATTRS_CACHE[cat_key] = numeric_map
    return numeric_map
