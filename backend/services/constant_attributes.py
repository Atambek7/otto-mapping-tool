import os
import json
import logging

_DEFAULTS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "default_attributes.json")
_cached_defaults = None

def get_default_attributes_config() -> dict:
    global _cached_defaults
    if _cached_defaults is None:
        try:
            if os.path.exists(_DEFAULTS_FILE):
                with open(_DEFAULTS_FILE, "r", encoding="utf-8") as f:
                    _cached_defaults = json.load(f)
            else:
                _cached_defaults = {"global": {}, "accounts": {}}
        except Exception as e:
            logging.error(f"Failed to load default_attributes.json: {e}")
            _cached_defaults = {"global": {}, "accounts": {}}
    return _cached_defaults

from services.brand_guard import is_forbidden_warranty_attribute

def get_account_specific_defaults(account: str = "JV") -> dict:
    cfg = get_default_attributes_config()
    acc = (account or "JV").upper()
    raw = cfg.get("accounts", {}).get(acc, {})
    return {k: v for k, v in raw.items() if not is_forbidden_warranty_attribute(k)}

def get_defaults_for_account(account: str = "JV") -> dict:
    cfg = get_default_attributes_config()
    acc = (account or "JV").upper()
    merged = dict(cfg.get("global", {}))
    acc_specific = cfg.get("accounts", {}).get(acc, {})
    merged.update(acc_specific)
    return {k: v for k, v in merged.items() if not is_forbidden_warranty_attribute(k)}

def get_presets_payload(account: str = "JV") -> dict:
    cfg = get_default_attributes_config()
    acc = (account or "JV").upper()
    gl = {k: v for k, v in cfg.get("global", {}).items() if not is_forbidden_warranty_attribute(k)}
    acc_sp = {k: v for k, v in cfg.get("accounts", {}).get(acc, {}).items() if not is_forbidden_warranty_attribute(k)}
    return {
        "account": acc,
        "global": gl,
        "account_specific": acc_sp,
        "merged_defaults": get_defaults_for_account(acc),
        "design_options": cfg.get("design_options", ["Modern", "Klassisch"]),
        "zielgruppe_options": [
            "Erwachsene ; Jugendliche",
            "Kinder"
        ]
    }

def get_attributes_for_apply(
    account: str = "JV",
    target_group: str = None,
    design: str = None,
    **kwargs
) -> dict:
    attrs = get_defaults_for_account(account)
    if target_group:
        attrs["Zielgruppe"] = target_group
    if design:
        attrs["Design"] = design
    return {k: v for k, v in attrs.items() if not is_forbidden_warranty_attribute(k)}

def apply_default_attributes(
    existing_attrs: dict,
    account: str = "JV",
    target_group: str = None,
    design: str = None,
    overwrite_empty_only: bool = True,
    category_id: str = None,
    **kwargs
) -> dict:
    """
    Fills attributes according to account and options.
    If category_id is provided, only attributes allowed in the category schema are applied.
    If overwrite_empty_only is True (default), does NOT overwrite non-empty values.
    Herstellergarantie and warranty attributes are strictly purged.
    """
    result = {k: v for k, v in (existing_attrs or {}).items() if not is_forbidden_warranty_attribute(k)}
    defaults = get_attributes_for_apply(
        account=account,
        target_group=target_group,
        design=design
    )

    allowed_names = None
    if category_id:
        from services.categories_repo import get_allowed_attribute_names
        allowed_names = get_allowed_attribute_names(category_id)

    existing_lower_keys = {k.strip().lower(): k for k in result.keys()}

    for def_key, def_val in defaults.items():
        if is_forbidden_warranty_attribute(def_key):
            continue

        # Whitelisting: skip defaults that do not belong to this category schema
        if allowed_names is not None and def_key not in allowed_names:
            continue

        matched_key = existing_lower_keys.get(def_key.strip().lower())
        if matched_key:
            if overwrite_empty_only:
                if not str(result.get(matched_key, "")).strip():
                    result[matched_key] = def_val
            else:
                result[matched_key] = def_val
        else:
            result[def_key] = def_val
            existing_lower_keys[def_key.strip().lower()] = def_key

    return result
