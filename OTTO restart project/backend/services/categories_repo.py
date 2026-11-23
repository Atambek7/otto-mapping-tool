import json
import logging
import os
import re
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
    "Marke",
}

_LIGHTING_CAPABLE_GROUP_TOKENS = (
    "leucht",
    "lampe",
    "licht",
    "schwibb",
    "kamin",
    "projektor",
    "beamer",
)

_LIGHTING_CAPABLE_CATEGORY_TOKENS = (
    "leucht",
    "licht",
    "lampe",
    "vitrine",
    "spiegelschrank",
    "badezimmerspiegelschrank",
    "wohnlandschaft",
    "multimediabett",
    "multimediasessel",
    "schwibb",
)

_ELECTRICAL_CAPABLE_CATEGORY_TOKENS = (
    "leucht",
    "licht",
    "lampe",
    "elektro",
    "akku",
    "solar",
    "gaming",
    "multimedia",
    "massage",
    "relax",
    "tv-",
    "schreibtisch",
    "beamer",
    "kühlschrank",
    "ofen",
    "herd",
    "spiegelschrank",
    "badezimmerspiegelschrank",
)

_LED_WORD_RE = re.compile(r"(?:^|[^a-zäöüß])led(?:[^a-zäöüß]|$)")

_ELECTRICAL_ONLY_GROUPS = {
    "Sofas",
    "Sessel",
    "Stühle",
    "Tische",
    "Betten",
    "Komplettbetten",
    "Schränke",
    "Regale",
    "Spiegel",
    "Bilder",
    "Kerzen",
    "Dekokränze",
    "Dekofiguren",
    "Sitzmöbel-Sets",
}

_ELECTRICAL_ONLY_ATTR_NAMES = {
    "Typ Netzstecker",
    "Art Stromversorgung",
    "Batterie-/Akku-Technologie",
    "Spannung",
    "Spannung Akku",
    "Kabellänge",
    "Nennspannung",
    "Leistungsaufnahme",
}

_LIGHTING_ONLY_ATTR_NAMES = {
    "Art Leuchtmittel",
    "Farbtemperatur Leuchtmittel",
    "Fassung Leuchtmittel",
    "Gewichteter Energieverbrauch Leuchtmittel",
    "Hinweis Beleuchtung",
    "Lebensdauer Leuchtmittel",
    "Leistung Leuchtmittel",
    "Leuchtmittel",
    "Lichtfarbe",
}

_ALLOWED_ATTRS_CACHE: Dict[str, Set[str]] = {}


def _is_lighting_capable_category(category_name: Optional[str], group_name: Optional[str]) -> bool:
    g_lower = str(group_name or "").strip().lower()
    c_lower = str(category_name or "").strip().lower()
    if any(token in g_lower for token in _LIGHTING_CAPABLE_GROUP_TOKENS):
        return True
    if _LED_WORD_RE.search(c_lower):
        return True
    return any(token in c_lower for token in _LIGHTING_CAPABLE_CATEGORY_TOKENS)


def _is_electrical_capable_category(category_name: Optional[str], group_name: Optional[str]) -> bool:
    g_name = str(group_name or "").strip()
    if g_name not in _ELECTRICAL_ONLY_GROUPS:
        return True
    c_lower = str(category_name or "").strip().lower()
    if _LED_WORD_RE.search(c_lower):
        return True
    return any(token in c_lower for token in _ELECTRICAL_CAPABLE_CATEGORY_TOKENS)


def is_legal_attribute(attribute: Dict[str, Any]) -> bool:
    """Return True only when OTTO taxonomy marks the attribute as legally mandatory."""
    from services.brand_guard import is_forbidden_warranty_attribute
    name = str(attribute.get("name") or "").strip()
    if not name or is_forbidden_warranty_attribute(name):
        return False
    feature_relevance = attribute.get("featureRelevance") or []
    return isinstance(feature_relevance, (list, tuple, set)) and "LEGAL" in feature_relevance


def is_inapplicable_group_attribute(
    attribute: Dict[str, Any],
    *,
    category_name: Optional[str] = None,
    group_name: Optional[str] = None,
) -> bool:
    """Exclude sub-variant attributes leaked across a shared OTTO categoryGroup."""
    from services.brand_guard import is_forbidden_warranty_attribute
    name = str(attribute.get("name") or "").strip()
    if not name or is_forbidden_warranty_attribute(name):
        return True
    if is_legal_attribute(attribute):
        return False
    attr_group = str(attribute.get("attributeGroup") or "").strip()
    if (
        (attr_group == "Beleuchtung" and name != "Modellbezeichnung")
        or name in _LIGHTING_ONLY_ATTR_NAMES
    ) and not _is_lighting_capable_category(category_name, group_name):
        return True
    if name in _ELECTRICAL_ONLY_ATTR_NAMES and not _is_electrical_capable_category(category_name, group_name):
        return True
    return False


def get_allowed_attribute_names(cat_id_or_name: Any) -> Optional[Set[str]]:
    """
    Returns the set of valid Otto attribute names for the given category ID or name.
    Includes category schema attributes plus universal Marke.
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
    group_name = group_data.get("group_name") or cat.get("category_group")
    category_name = cat.get("name")
    raw_attrs = group_data.get("attributes", {}).get("attributes", [])

    allowed = set(UNIVERSAL_ALLOWED_ATTRIBUTES)
    for a in raw_attrs:
        name = a.get("name")
        if name and str(name).strip() and not is_inapplicable_group_attribute(
            a, category_name=category_name, group_name=group_name
        ):
            allowed.add(str(name).strip())

    _ALLOWED_ATTRS_CACHE[cat_key] = allowed
    return allowed


def is_required_attribute(attribute: Dict[str, Any]) -> bool:
    """Strictly mandatory OTTO attributes: Marke and OTTO LEGAL requirements."""
    from services.brand_guard import is_forbidden_warranty_attribute
    name = str(attribute.get("name") or "").strip()
    if not name or name == "Ausführung" or is_forbidden_warranty_attribute(name):
        return False
    return name == "Marke" or is_legal_attribute(attribute)


def is_recommended_attribute(
    attribute: Dict[str, Any],
    *,
    category_name: Optional[str] = None,
    group_name: Optional[str] = None,
) -> bool:
    """OTTO HIGH relevance attributes that are helpful for search/filters, not mandatory."""
    from services.brand_guard import is_forbidden_warranty_attribute
    name = str(attribute.get("name") or "").strip()
    if not name or name == "Ausführung" or is_forbidden_warranty_attribute(name):
        return False
    if is_required_attribute(attribute):
        return False
    if is_inapplicable_group_attribute(attribute, category_name=category_name, group_name=group_name):
        return False
    attr_group = str(attribute.get("attributeGroup") or "").strip()
    if (
        (attr_group == "Beleuchtung" and name != "Modellbezeichnung")
        or name in _LIGHTING_ONLY_ATTR_NAMES
    ) and not any(t in str(group_name or "").lower() for t in _LIGHTING_CAPABLE_GROUP_TOKENS):
        return False
    if name in _ELECTRICAL_ONLY_ATTR_NAMES and str(group_name or "").strip() in _ELECTRICAL_ONLY_GROUPS:
        return False
    return attribute.get("relevance") == "HIGH"


def get_category_schema_attributes(cat_id_or_name: Any) -> List[Dict[str, Any]]:
    """
    Returns schema attribute definitions for constructing categorySchema in source_snapshot.
    Each item: {"name": str, "required": bool, "recommended": bool, "legal": bool, "allowedValues": list | None}
    """
    cat = get_category(cat_id_or_name)
    if not cat:
        return []
    group_data = get_category_group(cat.get("category_group_id"))
    if not group_data:
        return []

    category_name = cat.get("name")
    group_name = group_data.get("group_name") or cat.get("category_group")
    raw_attrs = group_data.get("attributes", {}).get("attributes", [])
    results = []
    seen = set()

    for a in raw_attrs:
        name = a.get("name")
        if not name or name in seen:
            continue
        if is_inapplicable_group_attribute(a, category_name=category_name, group_name=group_name):
            continue
        seen.add(name)
        allowed_vals = a.get("allowedValues")
        results.append({
            "name": name,
            "required": is_required_attribute(a),
            "recommended": is_recommended_attribute(a, category_name=category_name, group_name=group_name),
            "legal": is_legal_attribute(a),
            "relevance": a.get("relevance"),
            "attributeGroup": a.get("attributeGroup") or None,
            "multiValue": bool(a.get("multiValue", False)),
            "allowedValues": [v if isinstance(v, str) else str(v.get("value", "")) if isinstance(v, dict) else str(v) for v in allowed_vals] if isinstance(allowed_vals, list) and allowed_vals else None,
            "type": a.get("type"), "unit": a.get("unit"), "attributeId": a.get("attributeId", name),
        })

    # Add universal Marke if not yet present in the group schema
    for u in sorted(UNIVERSAL_ALLOWED_ATTRIBUTES):
        if u not in seen:
            seen.add(u)
            results.append({
                "name": u,
                "required": u == "Marke",
                "recommended": False,
                "legal": False,
                "relevance": "HIGH" if u == "Marke" else None,
                "attributeGroup": None,
                "type": "STRING",
                "allowedValues": None,
            })

    return results


def get_frontend_category_schema(cat_id_or_name: Any) -> List[Dict[str, Any]]:
    """
    Returns the attribute schema formatted for the ProductDrawer frontend,
    matching OttoFlow's /mapping/categories/{category_id}/attributes contract.
    """
    definitions = get_category_schema_attributes(cat_id_or_name)
    return [{
        "id": str(a.get("attributeId", a["name"])), "name": a["name"],
        "type": "number" if a.get("type") in ("FLOAT", "INTEGER") else "select" if a.get("allowedValues") else "text",
        "unit": a.get("unit"), "required": a["required"], "recommended": bool(a.get("recommended", False)),
        "legal": bool(a.get("legal", False)), "attributeGroup": a.get("attributeGroup"),
        "options": a.get("allowedValues"), "multiValue": bool(a.get("multiValue", False)),
    } for a in definitions]
