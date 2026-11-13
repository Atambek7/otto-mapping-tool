import json
import logging
import os
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))
_CANDIDATE_DATA_DIRS = [
    "/app/data",
    os.path.join(_DIR, "..", "data"),
    os.path.join(_DIR, "..", "..", "backend", "data"),
]

DATA_DIR = next((d for d in _CANDIDATE_DATA_DIRS if os.path.isdir(d)), _CANDIDATE_DATA_DIRS[1])
GROUPS_PATH = os.path.join(DATA_DIR, "category_groups.json")
CATEGORIES_PATH = os.path.join(DATA_DIR, "categories.json")
DEFAULTS_PATH = os.path.join(DATA_DIR, "default_attributes.json")

GROUPS: Dict[str, Dict[str, Any]] = {}
CATEGORIES: List[Dict[str, Any]] = []
CATEGORIES_BY_ID: Dict[str, Dict[str, Any]] = {}
CATEGORIES_BY_NAME: Dict[str, Dict[str, Any]] = {}

try:
    if os.path.exists(GROUPS_PATH):
        with open(GROUPS_PATH, "r", encoding="utf-8") as f:
            GROUPS = json.load(f)

    if os.path.exists(CATEGORIES_PATH):
        with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
            CATEGORIES = json.load(f)
            CATEGORIES_BY_ID = {str(c.get("categoryId")): c for c in CATEGORIES if c.get("categoryId")}
            CATEGORIES_BY_NAME = {str(c.get("name", "")).strip().lower(): c for c in CATEGORIES if c.get("name")}
except Exception as e:
    logger.warning("Could not load category reference data in categories_repo: %s", e)


def get_category(cat_id_or_name: Any) -> Optional[Dict[str, Any]]:
    if not cat_id_or_name:
        return None
    key = str(cat_id_or_name).strip()
    # Try ID first
    cat = CATEGORIES_BY_ID.get(key)
    if cat:
        return cat
    # Try name (case-insensitive)
    return CATEGORIES_BY_NAME.get(key.lower())


def get_category_by_id(cat_id: Any) -> Optional[Dict[str, Any]]:
    return get_category(cat_id)


def get_category_by_name(name: str) -> Optional[Dict[str, Any]]:
    return get_category(name)


def get_category_group(group_id: Any) -> Optional[Dict[str, Any]]:
    if not group_id:
        return None
    return GROUPS.get(str(group_id).strip())


def get_category_group_for_category(cat_id_or_name: Any) -> Optional[Dict[str, Any]]:
    cat = get_category(cat_id_or_name)
    if not cat:
        return None
    group_id = cat.get("category_group_id")
    return get_category_group(group_id)


UNIVERSAL_ALLOWED_ATTRIBUTES: Set[str] = {
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

# Dynamically add all configured default attributes from default_attributes.json
try:
    if os.path.exists(DEFAULTS_PATH):
        with open(DEFAULTS_PATH, "r", encoding="utf-8") as df:
            def_data = json.load(df)
            for k in def_data.get("global", {}).keys():
                UNIVERSAL_ALLOWED_ATTRIBUTES.add(str(k).strip())
            for acc_data in def_data.get("accounts", {}).values():
                for k in acc_data.keys():
                    UNIVERSAL_ALLOWED_ATTRIBUTES.add(str(k).strip())
except Exception as e:
    logger.warning("Could not load default_attributes.json in categories_repo: %s", e)

_ALLOWED_ATTRS_CACHE: Dict[str, Set[str]] = {}


def get_allowed_attribute_names(cat_id_or_name: Any) -> Optional[Set[str]]:
    """
    Returns the set of valid Otto attribute names for the given category ID or name.
    Includes category schema attributes plus universal attributes.
    Returns None if category is not found in reference data.
    """
    if not cat_id_or_name:
        return None

    cat_key = str(cat_id_or_name).strip().lower()
    if cat_key in _ALLOWED_ATTRS_CACHE:
        return _ALLOWED_ATTRS_CACHE[cat_key]

    cat = get_category(cat_id_or_name)
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


def get_category_schema_attributes(cat_id_or_name: Any) -> List[Dict[str, Any]]:
    """
    Returns schema attribute definitions for constructing categorySchema in source_snapshot.
    Each item: {"name": str, "required": bool, "legal": bool, "allowedValues": list | None}
    """
    group_data = get_category_group_for_category(cat_id_or_name)
    if not group_data:
        return []

    raw_attrs = group_data.get("attributes", {}).get("attributes", [])
    results = []
    seen = set()

    LEGAL_NAMES = {
        "WEEE-Reg.-Nr. DE",
        "Marke laut BattVO",
        "Warnhinweise",
        "Informationen zur Datennutzung (nach EU Data Act)",
        "Materialhinweis",
        "CLP-Gefahrenpiktogramm",
        "Geräteart laut ElektroG",
    }

    for a in raw_attrs:
        name = a.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        allowed_vals = a.get("allowedValues")
        results.append({
            "name": name,
            "required": bool(a.get("mandatory", False)),
            "legal": bool(name in LEGAL_NAMES or a.get("legal", False)),
            "multiValue": bool(a.get("multiValue", False)),
            "allowedValues": allowed_vals if isinstance(allowed_vals, list) and len(allowed_vals) > 0 else None,
        })

    # Add universal attributes not yet present
    for u in sorted(UNIVERSAL_ALLOWED_ATTRIBUTES):
        if u not in seen:
            seen.add(u)
            results.append({
                "name": u,
                "required": False,
                "legal": bool(u in LEGAL_NAMES),
                "allowedValues": None,
            })

    return results


def get_frontend_category_schema(cat_id_or_name: Any) -> List[Dict[str, Any]]:
    """
    Returns the attribute schema formatted for the ProductDrawer frontend,
    matching OttoFlow's /mapping/categories/{category_id}/attributes contract.
    """
    group_data = get_category_group_for_category(cat_id_or_name)
    if not group_data:
        return [
            {"id": "marke", "name": "Marke", "type": "text", "unit": None, "required": True, "options": None},
            {"id": "farbe", "name": "Farbe", "type": "select", "unit": None, "required": True, "options": ["Schwarz", "Weiß", "Eiche", "Natur", "Grau", "Braun", "Beige"]},
            {"id": "material", "name": "Material", "type": "select", "unit": None, "required": True, "options": ["Massivholz", "Holzwerkstoff", "Metall", "Textil", "Glas"]},
            {"id": "breite", "name": "Breite", "type": "number", "unit": "cm", "required": True, "options": None},
            {"id": "hoehe", "name": "Höhe", "type": "number", "unit": "cm", "required": True, "options": None},
            {"id": "tiefe", "name": "Tiefe", "type": "number", "unit": "cm", "required": True, "options": None},
        ]

    from services.brand_guard import is_forbidden_warranty_attribute

    raw_attrs = group_data.get("attributes", {}).get("attributes", [])
    schema: List[Dict[str, Any]] = []
    seen = set()

    for a in raw_attrs:
        attr_name = a.get("name")
        if not attr_name or attr_name in seen:
            continue
        if is_forbidden_warranty_attribute(attr_name):
            continue
        seen.add(attr_name)

        is_req = a.get("relevance") == "HIGH" or attr_name == "Marke"
        if attr_name == "Ausführung":
            is_req = False

        allowed_values = a.get("allowedValues")
        options = None
        if isinstance(allowed_values, list) and len(allowed_values) > 0:
            options = [v if isinstance(v, str) else v.get("value", str(v)) for v in allowed_values]

        if attr_name in {"Holzart", "Holzart Bettgestell"} and not options:
            options = ["Buche", "Kiefer", "Eiche", "Nussbaum"]

        attr_type = "text"
        if a.get("type") in ("FLOAT", "INTEGER"):
            attr_type = "number"
        elif options:
            attr_type = "select"

        schema.append({
            "id": str(a.get("attributeId", attr_name)),
            "name": attr_name,
            "type": attr_type,
            "unit": a.get("unit"),
            "required": is_req,
            "options": options,
            "multiValue": bool(a.get("multiValue", False)),
        })

    # Always ensure Marke is in schema and required
    if "Marke" not in seen:
        seen.add("Marke")
        schema.insert(0, {
            "id": "marke",
            "name": "Marke",
            "type": "text",
            "unit": None,
            "required": True,
            "options": None,
            "multiValue": False,
        })

    return schema
