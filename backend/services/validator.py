import os
import json
from services.categories_repo import get_category_by_id, get_groups
from services.brand_guard import is_forbidden_warranty_attribute

LEGAL_REQUIRED_ATTRIBUTE_NAMES = {
    "Benutzergewicht maximal",
    "Trainingsgewicht maximal",
}

TEXTILE_MATERIAL_COMPOSITION_GROUP_IDS = {
    "2957",  # Matratzen
    "2824",  # Kopfkissen
    "2136",  # Bettdecken
    "2142",  # Bettwaren-Sets
}

BEDDING_TEXTILE_GROUP_IDS = {"2824", "2136", "2142"}
_FURNITURE_CONFLICT_ATTRS = {
    "Holzart",
    "Holzart Bettgestell",
    "Material Korpus",
    "Benutzergewicht maximal",
    "Trainingsgewicht maximal",
}
_FURNITURE_CONFLICT_KEYWORDS = (
    "schlafzimmer set",
    "schlafzimmer-set",
    "nachttisch",
    "kleiderschrank",
    "schminktisch",
    "polsterbett",
    "boxspringbett",
    "lattenrost",
)
_NON_TEXTILE_MATERIAL_KEYWORDS = (
    "holz",
    "metall",
    "stahl",
    "eisen",
    "glas",
    "marmor",
    "stein",
    "buche",
    "kiefer",
    "eiche",
    "mdf",
    "spanplatte",
)
_BEDDING_TEXTILE_KEYWORDS = (
    "bettdecke",
    "kopfkissen",
    "bettwäsche",
    "bettwaesche",
    "daunen",
    "steppbett",
    "kissenbezug",
    "bettbezug",
)


def is_otto_attribute_required(attr_def: dict, group_id: str | None = None) -> bool:
    attr_name = attr_def.get("name")
    if not attr_name or attr_name == "Ausführung":
        return False
    if attr_def.get("relevance") == "HIGH" or attr_name == "Marke":
        return True
    if "LEGAL" in (attr_def.get("featureRelevance") or []):
        if attr_name in LEGAL_REQUIRED_ATTRIBUTE_NAMES:
            return True
        if attr_name == "Materialzusammensetzung" and str(group_id or "") in TEXTILE_MATERIAL_COMPOSITION_GROUP_IDS:
            return True
    return False


def is_otto_legal_attribute_required(attr_def: dict, group_id: str | None = None) -> bool:
    """Return True for attributes enforced by OTTO as hard LEGAL requirements."""
    attr_name = attr_def.get("name")
    if not attr_name or "LEGAL" not in (attr_def.get("featureRelevance") or []):
        return False
    if attr_name in LEGAL_REQUIRED_ATTRIBUTE_NAMES:
        return True
    if attr_name == "Materialzusammensetzung" and str(group_id or "") in TEXTILE_MATERIAL_COMPOSITION_GROUP_IDS:
        return True
    return False


def detect_category_attribute_conflict(
    category_id: str,
    attributes: dict | None,
    product_name: str = "",
) -> str | None:
    """Detect obvious furniture-in-bedding category mismatches (e.g. 15778 Bett-Set for bedroom furniture)."""
    if not category_id:
        return None
    cat = get_category_by_id(category_id)
    if not cat:
        return None
    group_id = str(cat.get("category_group_id") or "")
    if group_id not in BEDDING_TEXTILE_GROUP_IDS:
        return None

    attrs = attributes or {}
    title_lower = str(product_name or "").lower()
    if any(kw in title_lower for kw in _BEDDING_TEXTILE_KEYWORDS):
        return None

    has_furniture_attr = any(str(attrs.get(k) or "").strip() for k in _FURNITURE_CONFLICT_ATTRS)
    mat_text = " ".join(str(attrs.get(k) or "") for k in ("Material", "Material Korpus")).lower()
    has_rigid_material = any(kw in mat_text for kw in _NON_TEXTILE_MATERIAL_KEYWORDS)
    has_furniture_title = any(kw in title_lower for kw in _FURNITURE_CONFLICT_KEYWORDS)

    if has_furniture_attr or has_rigid_material or has_furniture_title:
        cat_name = cat.get("name") or category_id
        return (
            f"Несовместимая категория '{cat_name}' ({category_id}): "
            "товар имеет признаки мебели вместо постельных принадлежностей (проверьте категорию, например 'Schlafzimmer-Set' / 'Polsterbett')."
        )
    return None


def get_legal_blocking_errors(
    category_id: str,
    attributes: dict | None,
    product_name: str = "",
    allow_formatter_defaults: bool = False,
) -> list[str]:
    """Return hard OTTO LEGAL or category-mismatch errors that will fail publication."""
    errors: list[str] = []
    if not category_id:
        return ["Категория не выбрана"]

    cat = get_category_by_id(category_id)
    if not cat:
        return ["Выбранная категория не найдена в базе OKB"]

    conflict = detect_category_attribute_conflict(category_id, attributes, product_name=product_name)
    if conflict:
        errors.append(conflict)

    attrs = attributes or {}
    group_id = str(cat.get("category_group_id") or "")
    group_data = get_groups().get(group_id)
    if group_data:
        raw_attrs = group_data.get("attributes", {}).get("attributes", [])
        for a in raw_attrs:
            attr_name = a.get("name")
            if is_forbidden_warranty_attribute(attr_name):
                continue
            if not is_otto_legal_attribute_required(a, group_id=group_id):
                continue
            if allow_formatter_defaults and attr_name in LEGAL_REQUIRED_ATTRIBUTE_NAMES:
                continue
            val = attrs.get(attr_name, "")
            if (not val or not str(val).strip()) and allow_formatter_defaults and attr_name == "Materialzusammensetzung":
                fallback = attrs.get("Bezug") or attrs.get("Material") or attrs.get("material") or ""
                if str(fallback).strip() and not any(
                    kw in str(fallback).lower() for kw in _NON_TEXTILE_MATERIAL_KEYWORDS
                ):
                    val = fallback
            if not val or not str(val).strip():
                errors.append(f"Отсутствует обязательный LEGAL-атрибут: {attr_name}")
    return errors


def validate_product(ean: str, category_id: str, attributes: dict, product_name: str = "") -> list:
    """
    Validates a product and returns a list of error strings.
    If the list is empty, the product is fully valid.
    """
    errors = []
    
    if not ean or len(ean) != 13 or not ean.isdigit():
        errors.append("EAN must be exactly 13 digits")
        
    if not category_id:
        errors.append("Category is not selected")
    else:
        cat = get_category_by_id(category_id)
        if cat:
            conflict = detect_category_attribute_conflict(category_id, attributes, product_name=product_name)
            if conflict:
                errors.append(conflict)
            group_id = str(cat.get("category_group_id"))
            group_data = get_groups().get(group_id)
            if group_data:
                raw_attrs = group_data.get("attributes", {}).get("attributes", [])
                for a in raw_attrs:
                    attr_name = a.get("name")
                    if is_forbidden_warranty_attribute(attr_name):
                        continue
                    val = attributes.get(attr_name, "")
                    
                    is_req = is_otto_attribute_required(a, group_id=group_id)
                    
                    if is_req:
                        if not val or not str(val).strip():
                            errors.append(f"Обязательное поле не заполнено: {attr_name}")
                            continue
                            
                    # Check length if it is filled
                    if val and str(val).strip():
                        max_len = a.get("maxLength")
                        if max_len and len(str(val).strip()) > int(max_len):
                            errors.append(f"Длина поля '{attr_name}' превышает лимит ({max_len} симв.)")
        else:
            errors.append("Выбранная категория не найдена в базе OKB")
            
    return errors

def get_missing_required_attributes(category_id: str, attributes: dict) -> list[str]:
    """
    Returns a list of required attribute names that are missing from `attributes`.
    """
    missing = []
    if not category_id:
        return missing
    
    cat = get_category_by_id(category_id)
    if cat:
        group_id = str(cat.get("category_group_id"))
        group_data = get_groups().get(group_id)
        if group_data:
            raw_attrs = group_data.get("attributes", {}).get("attributes", [])
            for a in raw_attrs:
                attr_name = a.get("name")
                if is_forbidden_warranty_attribute(attr_name):
                    continue
                val = attributes.get(attr_name, "")
                
                is_req = is_otto_attribute_required(a, group_id=group_id)
                
                if is_req:
                    if not val or not str(val).strip():
                        missing.append(attr_name)
    return missing
