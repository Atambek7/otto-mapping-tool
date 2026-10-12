import json
import os
import re
from typing import Dict, List, Tuple, Any, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DEFAULT_ATTRS_FILE = os.path.join(DATA_DIR, "default_attributes.json")

BRAND_KEYS = [
    "Marke",
    "Marke laut BattVO",
    "Markeninformation",
    "WEEE-Reg.-Nr. DE",
    "Materialhinweis"
]

def load_account_brand_defaults() -> Dict[str, Dict[str, str]]:
    if not os.path.exists(DEFAULT_ATTRS_FILE):
        return {}
    with open(DEFAULT_ATTRS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("accounts", {})

def get_forbidden_tokens(account: str) -> List[str]:
    """
    Returns substrings/numbers that must NEVER appear in the given account's attributes.
    """
    acc = str(account).upper()
    if acc == "JV":
        return ["XLMOEBEL", "XLmoebel", "XL-Moebel", "xl moebel", "80120806", "6HMOZBOU"]
    elif acc == "XL":
        return ["JVMOEBEL", "JVmoebel", "JV-Moebel", "jv moebel", "46974041", "UO4EGHSX"]
    return []

def validate_brand_safety(account: str, attributes: Dict[str, str]) -> List[str]:
    """
    Validates that attributes do NOT contain any foreign brand identifiers.
    Returns a list of error strings if leaks are detected.
    """
    errors = []
    forbidden = get_forbidden_tokens(account)
    if not forbidden or not attributes:
        return errors

    acc = str(account).upper()

    for k, v in attributes.items():
        if not v:
            continue
        v_str = str(v)
        v_lower = v_str.lower()
        matched_tokens = []
        for token in forbidden:
            if token.lower() in v_lower:
                matched_tokens.append(token)
        if matched_tokens:
            errors.append(
                f"Brand Guard Error: Attribute '{k}' contains prohibited brand reference {matched_tokens} for account '{acc}'."
            )

    return errors

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

WARRANTY_SYNONYMS_PATTERN = re.compile(
    r"(?i)\b\w*(?:hersteller[- ]?garantie|garantie(?:dauer|zeit|versprechen|anspruch|bedingungen|leistung|zusage|schein|karte|fall)?|gewährleistung(?:sfrist|sdauer|sanspruch)?|warranty|guarantee)\w*\b"
)

def sanitize_text_for_warranty(text: str) -> str:
    """
    Removes any sentences, parenthetical expressions, or bullet points that mention forbidden warranty terms.
    Preserves paragraph structure and all non-warranty sentences.
    """
    if not text:
        return text

    # 1. Remove parenthetical warranty mentions like (inkl. 2 Jahre Garantie)
    text = re.sub(
        r"\s*\([^)]*(?:garantie|gewährleistung|warranty|guarantee)[^)]*\)",
        "",
        text,
        flags=re.IGNORECASE
    )

    lines = text.split("\n")
    clean_lines = []

    for line in lines:
        stripped_line = line.strip()
        if not stripped_line:
            clean_lines.append("")
            continue

        if not WARRANTY_SYNONYMS_PATTERN.search(stripped_line):
            clean_lines.append(line)
            continue

        is_bullet = bool(re.match(r"^[-*•\d+.]\s*", stripped_line))
        parts = re.split(r"(?<=[.!?])\s+", stripped_line)
        clean_parts = [p.strip() for p in parts if p.strip() and not WARRANTY_SYNONYMS_PATTERN.search(p)]

        if clean_parts:
            joined = " ".join(clean_parts)
            if is_bullet and not re.match(r"^[-*•\d+.]\s*", joined):
                joined = "- " + joined
            clean_lines.append(joined)

    # Clean up excess blank lines while preserving paragraph breaks
    result = []
    prev_empty = False
    for l in clean_lines:
        if not l.strip():
            if not prev_empty and result:
                result.append("")
                prev_empty = True
        else:
            result.append(l)
            prev_empty = False

    return "\n".join(result).strip()

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

def sanitize_brand_attributes(account: str, attributes: Dict[str, str], ensure_marke: bool = True) -> Dict[str, str]:
    """
    Ensures that brand-identifying attributes match the account exactly,
    overwriting or fixing any conflicting brand values, and strictly
    purging any forbidden warranty attributes.
    """
    acc = str(account).upper()
    brand_defaults = load_account_brand_defaults().get(acc, {})
    result = dict(attributes or {})

    # Strip any forbidden warranty attributes (Herstellergarantie must always remain completely empty)
    for k in list(result.keys()):
        if is_forbidden_warranty_attribute(k):
            result.pop(k, None)

    # Mapping of lowercase brand keys to canonical names and account values
    brand_map = {k.strip().lower(): (k, v) for k, v in brand_defaults.items()}

    existing_lower = {k.strip().lower(): k for k in result.keys()}

    for b_lower, (canon_k, canon_v) in brand_map.items():
        if b_lower in existing_lower:
            actual_key = existing_lower[b_lower]
            result[actual_key] = canon_v
        elif b_lower == "marke" and ensure_marke:
            # Always ensure Marke exists if attributes are provided
            result["Marke"] = canon_v

    # Strip any foreign brand artifacts from known text fields or replace brand mentions
    forbidden = get_forbidden_tokens(acc)
    for k, v in list(result.items()):
        if not v:
            continue
        v_str = str(v)
        for token in forbidden:
            if token.lower() in v_str.lower():
                # If it's a known brand field, replace with canonical default
                k_lower = k.strip().lower()
                if k_lower in brand_map:
                    result[k] = brand_map[k_lower][1]
                else:
                    # Replace brand token in free-text attributes
                    result[k] = sanitize_brand_text(acc, v_str)

    return result

def sanitize_brand_text(account: str, text: str) -> str:
    """
    Replaces foreign brand mentions with target account brand mentions in free text (titles, descriptions, notes).
    """
    if not text:
        return text
    acc = str(account).upper()
    if acc == "XL":
        # JV -> XL
        replacements = [
            (re.compile(r"\bJVMOEBEL\b", re.IGNORECASE), "XLMOEBEL"),
            (re.compile(r"\bJVMÖBEL\b", re.IGNORECASE), "XLMÖBEL"),
            (re.compile(r"\bJV-Moebel\b", re.IGNORECASE), "XL-Moebel"),
            (re.compile(r"\bJV-Möbel\b", re.IGNORECASE), "XL-Möbel"),
            (re.compile(r"\bJV\s+Moebel\b", re.IGNORECASE), "XL Moebel"),
            (re.compile(r"\bJV\s+Möbel\b", re.IGNORECASE), "XL Möbel"),
            (re.compile(r"\b46974041\b"), "80120806"),
        ]
        res = text
        for pat, repl in replacements:
            res = pat.sub(repl, res)
        return res
    elif acc == "JV":
        # XL -> JV
        replacements = [
            (re.compile(r"\bXLMOEBEL\b", re.IGNORECASE), "JVMOEBEL"),
            (re.compile(r"\bXLMÖBEL\b", re.IGNORECASE), "JVMÖBEL"),
            (re.compile(r"\bXL-Moebel\b", re.IGNORECASE), "JV-Moebel"),
            (re.compile(r"\bXL-Möbel\b", re.IGNORECASE), "JV-Möbel"),
            (re.compile(r"\bXL\s+Moebel\b", re.IGNORECASE), "JV Moebel"),
            (re.compile(r"\bXL\s+Möbel\b", re.IGNORECASE), "JV Möbel"),
            (re.compile(r"\b80120806\b"), "46974041"),
        ]
        res = text
        for pat, repl in replacements:
            res = pat.sub(repl, res)
        return res
    return text

def prepare_attributes_for_transfer(
    source_account: str,
    target_account: str,
    source_attributes: Dict[str, str]
) -> Dict[str, str]:
    """
    Transforms attributes from source_account (e.g. JV) to target_account (e.g. XL).
    All product-specific physical attributes (dimensions, material, style, etc.) are kept.
    All brand-specific attributes are cleanly replaced with target_account's values.
    All default brand attributes for target_account are guaranteed.
    Warranty attributes are strictly stripped.
    """
    target_acc = str(target_account).upper()
    brand_defaults = load_account_brand_defaults().get(target_acc, {})
    result = dict(source_attributes or {})

    # Strip any warranty attributes
    for k in list(result.keys()):
        if is_forbidden_warranty_attribute(k):
            result.pop(k, None)

    # Normalize keys and alias map
    alias_map = {
        "marke": "Marke",
        "brand": "Marke",
        "marke laut battvo": "Marke laut BattVO",
        "marke battvo": "Marke laut BattVO",
        "markeninformation": "Markeninformationen",
        "markeninformationen": "Markeninformationen",
        "materialhinweis": "Materialhinweis",
        "materialhinweise": "Materialhinweis",
        "weee-reg.-nr. de": "WEEE-Reg.-Nr. DE",
        "weee reg nr de": "WEEE-Reg.-Nr. DE",
        "weee-nummer": "WEEE-Reg.-Nr. DE",
        "weee nummer": "WEEE-Reg.-Nr. DE",
    }
    lower_to_target_brand = {k.strip().lower(): (k, v) for k, v in brand_defaults.items()}

    # 1. For each attribute, if it is a brand key or alias, replace it with target brand value
    for k in list(result.keys()):
        k_lower = k.strip().lower()
        canon_name = alias_map.get(k_lower)
        if canon_name and canon_name in brand_defaults:
            if k != canon_name:
                result.pop(k, None)
            result[canon_name] = brand_defaults[canon_name]
        elif k_lower in lower_to_target_brand:
            c_name, c_val = lower_to_target_brand[k_lower]
            result[k] = c_val

    # 2. Guarantee that ALL target brand defaults are present
    for canon_name, canon_val in brand_defaults.items():
        k_lower = canon_name.strip().lower()
        matched = False
        for ex_k in result.keys():
            if ex_k.strip().lower() == k_lower:
                result[ex_k] = canon_val
                matched = True
                break
        if not matched:
            result[canon_name] = canon_val

    # 3. Final sanitization pass
    return sanitize_brand_attributes(target_acc, result)


# ==========================================
# Image Brand Guard (Foreign Logo Filtering)
# ==========================================

# All known binary fingerprints of the JVMÖBEL logo uploaded by suppliers.
# The same logo is re-encoded at different resolutions/JPEG quality levels,
# giving multiple distinct sizes and MD5 hashes.
# Verified 2026-09-21: present in Alpino, ADA, BUROBIG_TR, 448_MERLI, VIONSE,
# Kocak, Evart and other suppliers of factories 483410 and 483411.
KNOWN_JV_LOGO_MD5 = {
    "4ab83837d7f151e5bdc1f310854c97f0",  # original 41627 bytes
    "2ea8fd6021a2ce0db0688a0aa68d97b3",  # 63607 bytes  (1600×1066) – Alpino & others
    "ffa81c811370b53921850ac8512a3f2e",  # 84950 bytes  (1600×1066) – ADA, BUROBIG, 497_deco
    "d5da4f64b2b6bb7db041d2c5c491128d",  # 40484 bytes  (1280×852)  – 448_MERLI
    # Auto-learned by visual detector 2026-09-21 during factory 483410/483411 scan:
    "5246e51f4144bf9863e0336610691dbb",  # 79019 bytes – Titi/Latte/Kinderbett
    "be9fbd51347cc79a646abba4abb31aa4",  # 79034 bytes – Titi/VOYAGER/Sideboard
    "52c9f174d1e627204c2287d56075627f",  # 78953 bytes – Titi/T_R_LYE/Stuhl
    "e680aae52185cb2913a7d74ac8ab554e",  # 78997 bytes – Titi/Latte/Kommode
    "4a6eb59af028c86dbcfa8dcb9bfdf6f1",  # 79002 bytes – Titi/Latte/Bett
    "ef7bc7e3024202eaa9cbbb1176ee73b8",  # 78992 bytes – Titi/BIANCA/Kleiderschrank
}
KNOWN_JV_LOGO_SIZES = {41627, 63607, 84950, 40484, 78953, 78992, 78997, 79002, 79019, 79034}

_LOGO_URL_CACHE: Dict[str, bool] = {}

# Pre-computed reference crop of the JV logo text region (grayscale, 100×32)
# Used by is_jv_logo_by_visual() for perceptual matching.
# Loaded lazily on first use.
_JV_LOGO_REF_ARR: Optional[Any] = None  # numpy array or None


def _get_jv_logo_ref_arr() -> Optional[Any]:
    """Return cached reference numpy array for visual logo comparison."""
    global _JV_LOGO_REF_ARR
    if _JV_LOGO_REF_ARR is not None:
        return _JV_LOGO_REF_ARR
    try:
        import numpy as np
        from PIL import Image
        import io, base64
        # Minimal reference derived from supplier logo: wide horizontal dark text on white
        # Encoded as a 100×32 float array saved as base64 in constants below
        # (populated at first detection call from live image)
        return None
    except Exception:
        return None


def is_jv_logo_by_visual(image_bytes: bytes) -> bool:
    """
    Visual shape-based detector for the JVMÖBEL logo.
    Detects logo by unique visual fingerprint: wide dark text on white background
    occupying a narrow horizontal band (aspect ratio ~3.0–3.2), with low overall
    dark-pixel density (< 18%).

    This catches new logo variants that share the same visual appearance but have
    different compression artefacts (different MD5/size).

    Returns True if image is identified as a brand logo that must be suppressed.
    """
    if not image_bytes or len(image_bytes) < 4000:
        return False
    try:
        import io
        import numpy as np
        from PIL import Image

        im = Image.open(io.BytesIO(image_bytes)).convert("L")
        arr = np.array(im)
        # Threshold: pixels darker than 225 are considered content
        mask = arr < 225
        dark_ratio = float(np.mean(mask))

        # Logo is sparse text on white: between 2% and 18% dark pixels
        if not (0.02 <= dark_ratio <= 0.18):
            return False

        rows = np.any(mask, axis=1)
        cols = np.any(mask, axis=0)
        if not np.any(rows) or not np.any(cols):
            return False

        ymin, ymax = int(np.where(rows)[0][0]), int(np.where(rows)[0][-1])
        xmin, xmax = int(np.where(cols)[0][0]), int(np.where(cols)[0][-1])
        w = xmax - xmin
        h = ymax - ymin
        if h == 0 or w == 0:
            return False

        aspect = w / h
        # JV logo text block spans ~3.0–3.2× wider than tall
        # Normal furniture photos have much lower or higher aspect in their content region
        if not (2.7 <= aspect <= 3.5):
            return False

        # Extra guard: content region must be horizontally centered in the image
        # (logos are centered, furniture silhouettes rarely have such narrow banded dark bbox)
        img_w, img_h = im.size
        center_x_ratio = (xmin + xmax) / 2 / img_w
        center_y_ratio = (ymin + ymax) / 2 / img_h
        if not (0.3 <= center_x_ratio <= 0.7):
            return False  # Not horizontally centered
        if not (0.2 <= center_y_ratio <= 0.8):
            return False  # Not in middle vertical range

        # Content height should occupy a small fraction of the total image (< 60%)
        content_height_ratio = h / img_h
        if content_height_ratio > 0.65:
            return False  # Too tall – probably a furniture photo

        return True

    except Exception:
        return False


def is_forbidden_brand_image_url(account: str, url: str) -> bool:
    """
    Fast string check for forbidden brand image URLs.
    """
    if not url:
        return False
    u_lower = str(url).lower()
    acc = str(account).upper()
    if acc == "XL":
        # URLs explicitly naming jvmoebel/jvmebel or known logo file IDs
        if any(token in u_lower for token in ("jvmoebel", "jv-moebel", "jv_moebel", "jvmebel", "jv-mebel", "jv_mebel", "logo_jv")):
            return True
        if "53416" in u_lower or "32719" in u_lower:
            return True
    return False


async def is_foreign_brand_image(account: str, url: str, client: Any = None) -> bool:
    """
    Checks if an image is a foreign brand logo that must be suppressed for the given account.
    Uses a three-tier strategy:
      1. Fast URL string matching (no network).
      2. Content-length + MD5 hash matching (one GET if content-length matches).
      3. Visual perceptual shape matching (fallback for new variants).
    Caches result to avoid repeated network calls.
    """
    if not url:
        return False

    acc = str(account).upper()
    cache_key = f"{acc}:{url}"
    if cache_key in _LOGO_URL_CACHE:
        return _LOGO_URL_CACHE[cache_key]

    # Tier 1: URL string
    if is_forbidden_brand_image_url(acc, url):
        _LOGO_URL_CACHE[cache_key] = True
        return True

    if acc == "XL":
        import hashlib
        import logging
        import httpx
        close_client = False
        try:
            if client is None:
                client = httpx.AsyncClient(timeout=8.0, headers={"User-Agent": "Mozilla/5.0"})
                close_client = True

            # Tier 2 + Tier 3: fetch image bytes once
            resp = await client.get(url)
            if resp.status_code == 200:
                raw = resp.content
                file_size = len(raw)
                md5 = hashlib.md5(raw).hexdigest()

                # Tier 2: exact MD5 match
                if md5 in KNOWN_JV_LOGO_MD5:
                    logging.info(f"[brand_guard] Exact MD5 match for JV logo: {url} ({file_size} bytes)")
                    _LOGO_URL_CACHE[cache_key] = True
                    if close_client:
                        await client.aclose()
                    return True

                # Tier 3: visual fingerprint (catches new re-encoded variants)
                if is_jv_logo_by_visual(raw):
                    logging.warning(
                        f"[brand_guard] Visual logo detection matched JV logo for XL: "
                        f"{url} (size={file_size}, md5={md5}) — add to KNOWN_JV_LOGO_MD5!"
                    )
                    # Auto-learn: add new hash to in-memory set
                    KNOWN_JV_LOGO_MD5.add(md5)
                    KNOWN_JV_LOGO_SIZES.add(file_size)
                    _LOGO_URL_CACHE[cache_key] = True
                    if close_client:
                        await client.aclose()
                    return True

        except Exception:
            pass
        finally:
            if close_client and client:
                try:
                    await client.aclose()
                except Exception:
                    pass

    _LOGO_URL_CACHE[cache_key] = False
    return False


async def filter_brand_images(account: str, images: List[str]) -> List[str]:
    """
    Filters out any foreign brand logo images for the given account.
    JV account: no filtering, return as-is.
    XL account: removes all detected JVMÖBEL logos, returns only product photos.
    """
    if not images or str(account).upper() != "XL":
        return images

    import httpx
    import logging
    clean = []
    async with httpx.AsyncClient(
        timeout=8.0,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    ) as client:
        for img in images:
            if not img:
                continue
            try:
                is_logo = await is_foreign_brand_image(account, img, client)
            except Exception:
                is_logo = False
            if not is_logo:
                clean.append(img)
            else:
                logging.info(f"[brand_guard] Stripped foreign JV logo image from XL product: {img}")
    return clean


def filter_brand_images_sync(account: str, images: List[str]) -> List[str]:
    """
    Synchronous filter for brand images using fast URL patterns and cached hashes.
    Only uses Tier-1 (URL string) and cache — no network calls.
    For full network-based detection use filter_brand_images() (async).
    """
    if not images or str(account).upper() != "XL":
        return images

    clean = []
    acc = str(account).upper()
    for img in images:
        if not img:
            continue
        cache_key = f"{acc}:{img}"
        if _LOGO_URL_CACHE.get(cache_key) is True:
            continue
        if is_forbidden_brand_image_url(acc, img):
            _LOGO_URL_CACHE[cache_key] = True
            continue
        clean.append(img)
    return clean
