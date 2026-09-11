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

def get_account_specific_defaults(account: str = "JV") -> dict:
    cfg = get_default_attributes_config()
    acc = (account or "JV").upper()
    return cfg.get("accounts", {}).get(acc, {})

def get_defaults_for_account(account: str = "JV") -> dict:
    cfg = get_default_attributes_config()
    acc = (account or "JV").upper()
    merged = dict(cfg.get("global", {}))
    acc_specific = cfg.get("accounts", {}).get(acc, {})
    merged.update(acc_specific)
    return merged

def apply_default_attributes(existing_attrs: dict, account: str = "JV") -> dict:
    """
    Fills in empty or missing attributes with constant values according to account.
    Does NOT overwrite already non-empty user-supplied attributes.
    """
    result = dict(existing_attrs or {})
    defaults = get_defaults_for_account(account)

    # Build a lookup for case-insensitive matching
    existing_lower_keys = {k.strip().lower(): k for k in result.keys()}

    for def_key, def_val in defaults.items():
        matched_key = existing_lower_keys.get(def_key.strip().lower())
        if matched_key:
            # Key exists in product attributes; only fill if currently empty
            if not str(result.get(matched_key, "")).strip():
                result[matched_key] = def_val
        else:
            # Key does not exist; add it with default value
            result[def_key] = def_val
            existing_lower_keys[def_key.strip().lower()] = def_key

    return result
