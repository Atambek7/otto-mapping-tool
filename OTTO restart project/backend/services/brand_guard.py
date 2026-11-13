import json
import os
import re
from typing import Any, Dict, List, Optional

_DIR = os.path.dirname(os.path.abspath(__file__))
_CANDIDATE_PATHS = [
    "/app/data/default_attributes.json",
    os.path.join(_DIR, "..", "data", "default_attributes.json"),
    os.path.join(_DIR, "..", "..", "backend", "data", "default_attributes.json"),
]

DEFAULT_ATTRS_FILE = next((p for p in _CANDIDATE_PATHS if os.path.exists(p)), _CANDIDATE_PATHS[1])

BRAND_KEYS = [
    "Marke",
    "Marke laut BattVO",
    "Markeninformation",
    "Markeninformationen",
    "WEEE-Reg.-Nr. DE",
    "Materialhinweis",
]

WARRANTY_FORBIDDEN_ATTRIBUTES = {
    "herstellergarantie",
    "bedingte herstellergarantie",
    "herstellergarantie gesamtprodukt",
    "garantie (kern)",
    "hinweis herstellergarantie",
    "garantieform",
    "garantiedauer",
    "garantie",
}


def load_account_brand_defaults() -> Dict[str, Dict[str, str]]:
    if not os.path.exists(DEFAULT_ATTRS_FILE):
        return {}
    try:
        with open(DEFAULT_ATTRS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("accounts", {})
    except Exception:
        return {}


def is_forbidden_warranty_attribute(name: str) -> bool:
    """
    Checks if the attribute is related to manufacturer warranty (Herstellergarantie).
    On Otto, warranty claims without legally complex warranty conditions cause strict
    rejections/penalties, so Herstellergarantie must NEVER be populated and must stay empty.
    """
    if not name:
        return False
    n = name.strip().lower()
    return (
        n in WARRANTY_FORBIDDEN_ATTRIBUTES
        or "herstellergarantie" in n
        or "garantie" in n
        or "gewährleistung" in n
        or "warranty" in n
        or "guarantee" in n
    )


def get_forbidden_tokens(account: str) -> List[str]:
    """
    Returns substrings/numbers that must NEVER appear in the given account's attributes.
    """
    acc = str(account or "").upper()
    if acc == "JV":
        return ["XLMOEBEL", "XLmoebel", "XL-Moebel", "xl moebel", "80120806", "6HMOZBOU"]
    elif acc == "XL":
        return ["JVMOEBEL", "JVmoebel", "JV-Moebel", "jv moebel", "46974041", "UO4EGHSX"]
    return []


def validate_brand_safety(account: str, attributes: Dict[str, Any] | List[Dict[str, Any]]) -> List[str]:
    """
    Validates that attributes do NOT contain any foreign brand identifiers.
    Returns a list of error strings if leaks are detected.
    """
    errors: List[str] = []
    forbidden = get_forbidden_tokens(account)
    if not forbidden or not attributes:
        return errors

    acc = str(account or "").upper()

    if isinstance(attributes, list):
        attr_dict = {a.get("name", ""): a.get("value", "") for a in attributes if isinstance(a, dict)}
    elif isinstance(attributes, dict):
        attr_dict = attributes
    else:
        return errors

    for k, v in attr_dict.items():
        if not v:
            continue
        v_str = str(v)
        v_lower = v_str.lower()
        matched_tokens = [token for token in forbidden if token.lower() in v_lower]
        if matched_tokens:
            errors.append(
                f"Brand Guard Error: Attribute '{k}' contains prohibited brand reference {matched_tokens} for account '{acc}'."
            )

    return errors


def sanitize_brand_attributes(account: str, attributes: Dict[str, str], ensure_marke: bool = True) -> Dict[str, str]:
    """
    Ensures that brand-identifying attributes match the account exactly,
    overwriting or fixing any conflicting brand values, and strictly
    purging any forbidden warranty attributes.
    """
    acc = str(account or "JV").upper()
    brand_defaults = load_account_brand_defaults().get(acc, {})
    result = dict(attributes or {})

    # Strip any forbidden warranty attributes
    for k in list(result.keys()):
        if is_forbidden_warranty_attribute(k):
            result.pop(k, None)

    brand_map = {k.strip().lower(): (k, v) for k, v in brand_defaults.items()}
    existing_keys_lower = {k.strip().lower(): k for k in list(result.keys())}

    for b_lower, (canon_name, canon_val) in brand_map.items():
        if b_lower in existing_keys_lower:
            orig_k = existing_keys_lower[b_lower]
            if orig_k != canon_name:
                result.pop(orig_k, None)
            result[canon_name] = canon_val
        elif ensure_marke:
            result[canon_name] = canon_val

    return result
