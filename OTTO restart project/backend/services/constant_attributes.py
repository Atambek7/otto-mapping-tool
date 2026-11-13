import json
import logging
import os
from typing import Any, Dict, List, Optional, Union

from services.brand_guard import (
    BRAND_KEYS,
    get_forbidden_tokens,
    is_forbidden_warranty_attribute,
    validate_brand_safety,
)

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))
_CANDIDATE_PATHS = [
    "/app/data/default_attributes.json",
    os.path.join(_DIR, "..", "data", "default_attributes.json"),
    os.path.join(_DIR, "..", "..", "backend", "data", "default_attributes.json"),
]

DEFAULT_ATTRS_FILE = next((p for p in _CANDIDATE_PATHS if os.path.exists(p)), _CANDIDATE_PATHS[1])
_cached_defaults: Optional[Dict[str, Any]] = None


def get_default_attributes_config() -> Dict[str, Any]:
    global _cached_defaults
    if _cached_defaults is None:
        try:
            if os.path.exists(DEFAULT_ATTRS_FILE):
                with open(DEFAULT_ATTRS_FILE, "r", encoding="utf-8") as f:
                    _cached_defaults = json.load(f)
            else:
                _cached_defaults = {"global": {}, "accounts": {}}
        except Exception as e:
            logger.error("Failed to load default_attributes.json: %s", e)
            _cached_defaults = {"global": {}, "accounts": {}}
    return _cached_defaults


def get_account_specific_defaults(account: str = "JV") -> Dict[str, str]:
    cfg = get_default_attributes_config()
    acc = (account or "JV").upper()
    raw = cfg.get("accounts", {}).get(acc, {})
    return {k: str(v) for k, v in raw.items() if not is_forbidden_warranty_attribute(k)}


def get_defaults_for_account(account: str = "JV") -> Dict[str, str]:
    cfg = get_default_attributes_config()
    acc = (account or "JV").upper()
    merged = dict(cfg.get("global", {}))
    acc_specific = cfg.get("accounts", {}).get(acc, {})
    merged.update(acc_specific)
    return {k: str(v) for k, v in merged.items() if not is_forbidden_warranty_attribute(k)}


def apply_default_attributes_to_dict(
    existing_attrs: Dict[str, Any],
    account: str = "JV",
    category_id_or_name: Optional[Any] = None,
    overwrite_empty_only: bool = True,
) -> Dict[str, str]:
    """
    Applies account defaults and global legal/standard attributes to a dictionary of attributes.
    Enforces brand isolation (Marke, WEEE, etc. always match target account).
    Purges any forbidden warranty attributes.
    """
    acc = (account or "JV").upper()
    result = {
        k: str(v)
        for k, v in (existing_attrs or {}).items()
        if not is_forbidden_warranty_attribute(k)
    }

    defaults = get_defaults_for_account(acc)
    account_brand_keys = set(get_account_specific_defaults(acc).keys())

    allowed_names = None
    if category_id_or_name:
        from services.categories_repo import get_allowed_attribute_names
        allowed_names = get_allowed_attribute_names(category_id_or_name)

    existing_lower_keys = {k.strip().lower(): k for k in result.keys()}

    for def_key, def_val in defaults.items():
        if is_forbidden_warranty_attribute(def_key):
            continue

        # If category schema is known, skip defaults that are not allowed
        if allowed_names is not None and def_key not in allowed_names:
            continue

        matched_key = existing_lower_keys.get(def_key.strip().lower())
        is_brand_attr = def_key in account_brand_keys or def_key in BRAND_KEYS

        if matched_key:
            if is_brand_attr:
                # Brand attributes MUST always strictly match the active account
                if matched_key != def_key:
                    result.pop(matched_key, None)
                result[def_key] = def_val
            elif overwrite_empty_only:
                if not str(result.get(matched_key, "")).strip():
                    result[matched_key] = def_val
            else:
                result[matched_key] = def_val
        else:
            result[def_key] = def_val
            existing_lower_keys[def_key.strip().lower()] = def_key

    return result


def apply_default_attributes_to_list(
    existing_attrs: List[Dict[str, Any]],
    account: str = "JV",
    category_id_or_name: Optional[Any] = None,
    source_ref: Optional[str] = None,
    overwrite_empty_only: bool = True,
) -> List[Dict[str, Any]]:
    """
    Applies account defaults to a list of attribute dictionaries:
    [{"name": "...", "value": "...", "sourceRefs": [...]}]
    Preserves existing sourceRefs and merges defaults with source_ref.
    """
    acc = (account or "JV").upper()
    default_ref = source_ref or f"OTTO:/defaults/{acc.lower()}"

    # Extract dict and existing metadata
    attr_dict: Dict[str, str] = {}
    meta_by_name: Dict[str, Dict[str, Any]] = {}

    for a in existing_attrs or []:
        if not isinstance(a, dict):
            continue
        name = a.get("name")
        val = a.get("value")
        if name and val is not None:
            attr_dict[str(name)] = str(val)
            meta_by_name[str(name)] = {
                "sourceRefs": a.get("sourceRefs", [default_ref]),
                "origin": a.get("origin", "MANUAL"),
            }

    applied_dict = apply_default_attributes_to_dict(
        attr_dict,
        account=acc,
        category_id_or_name=category_id_or_name,
        overwrite_empty_only=overwrite_empty_only,
    )

    out_list: List[Dict[str, Any]] = []
    for k, v in applied_dict.items():
        meta = meta_by_name.get(k, {})
        refs = meta.get("sourceRefs") or [default_ref]
        item: Dict[str, Any] = {
            "name": k,
            "value": v,
            "sourceRefs": refs,
        }
        if "origin" in meta:
            item["origin"] = meta["origin"]
        out_list.append(item)

    return out_list


def apply_default_attributes(
    existing_attrs: Union[Dict[str, Any], List[Dict[str, Any]]],
    account: str = "JV",
    category_id_or_name: Optional[Any] = None,
    source_ref: Optional[str] = None,
    overwrite_empty_only: bool = True,
) -> Union[Dict[str, str], List[Dict[str, Any]]]:
    if isinstance(existing_attrs, list):
        return apply_default_attributes_to_list(
            existing_attrs,
            account=account,
            category_id_or_name=category_id_or_name,
            source_ref=source_ref,
            overwrite_empty_only=overwrite_empty_only,
        )
    return apply_default_attributes_to_dict(
        existing_attrs or {},
        account=account,
        category_id_or_name=category_id_or_name,
        overwrite_empty_only=overwrite_empty_only,
    )
