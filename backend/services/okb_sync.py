import asyncio
import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx

from services.categories_repo import CATEGORIES

logger = logging.getLogger("okb_sync")


# ---------------------------------------------------------------------------
# Category indexes
# ---------------------------------------------------------------------------

def _normalize_text(text: str) -> str:
    text = str(text).strip().lower()
    text = re.sub(r"[\-_/,;|\\]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


CATEGORIES_BY_ID: Dict[str, dict] = {
    str(category["categoryId"]): category
    for category in CATEGORIES
    if category.get("categoryId")
}

CATEGORIES_BY_LOWER: Dict[str, dict] = {}
for category in CATEGORIES:
    raw_name = category.get("name")
    if raw_name:
        name_str = str(raw_name).strip().lower()
        CATEGORIES_BY_LOWER[name_str] = category
        CATEGORIES_BY_LOWER[_normalize_text(raw_name)] = category

# Several Otto categories can produce the same stem. Never overwrite them.
CATEGORIES_BY_STEM: Dict[str, List[dict]] = {}

# Common German furniture/product noun normalization. Keep this intentionally
# conservative: generic suffix stripping can create false matches.
GERMAN_STEM_RULES = (
    (r"tische$", "tisch"),
    (r"stühle$", "stuhl"),
    (r"schränke$", "schrank"),
    (r"wände$", "wand"),
    (r"betten$", "bett"),
    (r"sofas$", "sofa"),
    (r"regale$", "regal"),
    (r"bänke$", "bank"),
    (r"kommoden$", "kommode"),
    (r"spiegel$", "spiegel"),
    (r"kissen$", "kissen"),
    (r"lampen$", "lampe"),
    (r"leuchten$", "leuchte"),
    (r"teppiche$", "teppich"),
    (r"matratzen$", "matratze"),
    (r"vitrinen$", "vitrine"),
    (r"gardinen$", "gardine"),
    (r"sesseln?$", "sessel"),
)


@dataclass(frozen=True)
class CategoryMatch:
    category_id: str
    category_name: str
    score: float
    reason: str


CATEGORY_MIN_SCORE = 70.0


def stem_german_word(word: str) -> str:
    """Conservatively normalize common German product nouns."""
    word = str(word).lower().strip()
    if not word:
        return ""

    for pattern, replacement in GERMAN_STEM_RULES:
        if re.search(pattern, word):
            return re.sub(pattern, replacement, word)

    return word


def _build_category_stem_index() -> None:
    for category in CATEGORIES:
        name = category.get("name")
        if not name:
            continue

        stem = stem_german_word(name)
        if not stem:
            continue

        CATEGORIES_BY_STEM.setdefault(stem, []).append(category)


_build_category_stem_index()


def _tokenize(text: str) -> List[str]:
    normalized = _normalize_text(text)
    return [token for token in normalized.split() if len(token) >= 3]


def _stemmed_tokens(text: str) -> set[str]:
    return {stem_german_word(token) for token in _tokenize(text) if token}


def _make_category_match(category: dict, score: float, reason: str) -> Optional[CategoryMatch]:
    category_id = category.get("categoryId")
    category_name = category.get("name")

    if not category_id or not category_name:
        return None

    return CategoryMatch(
        category_id=str(category_id),
        category_name=str(category_name),
        score=score,
        reason=reason,
    )


def _score_category(text: str, category: dict) -> Optional[CategoryMatch]:
    """Score one category against arbitrary category/title text."""
    text_norm = _normalize_text(text)
    text_tokens = set(_tokenize(text_norm))
    text_stems = _stemmed_tokens(text_norm)

    name = str(category.get("name", "")).strip()
    if not name:
        return None

    name_norm = _normalize_text(name)
    name_tokens = set(_tokenize(name_norm))
    name_stems = _stemmed_tokens(name_norm)

    # Highest confidence: exact category name.
    if text_norm == name_norm:
        return _make_category_match(category, 150.0, "exact_name")

    # Exact phrase inside a longer value.
    padded_text = f" {text_norm} "
    if f" {name_norm} " in padded_text:
        return _make_category_match(category, 120.0 + min(len(name_norm), 30), "exact_phrase")

    # Exact token set / stemmed token set. This handles simple German plural
    # differences such as Nachttisch/Nachttische without fuzzy guessing.
    if name_tokens and name_tokens.issubset(text_tokens):
        score = 105.0 + len(name_tokens) * 5.0 + min(len(name_norm), 20) * 0.2
        return _make_category_match(category, score, "all_tokens")

    if name_stems and name_stems.issubset(text_stems):
        score = 95.0 + len(name_stems) * 5.0 + min(len(name_norm), 20) * 0.2
        return _make_category_match(category, score, "stemmed_tokens")

    # For a single-word category, allow a stem match against an individual
    # token. This is weaker than exact phrase/token matching.
    if len(name_stems) == 1:
        category_stem = next(iter(name_stems))
        if category_stem in text_stems:
            return _make_category_match(category, 85.0 + min(len(name_norm), 20) * 0.2, "single_stem")

    # German compound noun suffix match (e.g., "Modulsofa" -> "Sofa", "Holzbett" -> "Bett").
    # Applied when candidate category is a single word and text has at most 2 tokens.
    if len(name_tokens) == 1 and len(text_tokens) <= 2 and len(name_norm) >= 4:
        for t in text_tokens:
            if t.endswith(name_norm) and len(t) > len(name_norm):
                return _make_category_match(category, 88.0 + min(len(name_norm), 20) * 0.2, "compound_suffix")

    # Substring fallback is deliberately weak and only accepted for sufficiently
    # long category names. This prevents tiny/common words from hijacking a title.
    if len(name_norm) >= 6 and name_norm in text_norm:
        return _make_category_match(category, 72.0 + min(len(name_norm), 20) * 0.1, "substring")

    return None


def find_best_category(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Resolve an Otto category from an ID, exact name, Produktart or title.

    Returns (category_id, category_name). Low-confidence matches are rejected
    rather than silently assigning a potentially wrong category.
    """
    if not text:
        return None, None

    raw = str(text).strip()
    if not raw:
        return None, None

    # 1. Exact ID.
    category = CATEGORIES_BY_ID.get(raw)
    if category:
        return str(category["categoryId"]), category.get("name")

    # 2. Exact full name.
    category = CATEGORIES_BY_LOWER.get(_normalize_text(raw))
    if category:
        return str(category["categoryId"]), category.get("name")

    # 3. Explicit alternative category values separated by common delimiters.
    parts = [part.strip() for part in re.split(r"[/,;|\\]", raw) if part.strip()]
    explicit_matches: List[CategoryMatch] = []
    for part in parts:
        category = CATEGORIES_BY_LOWER.get(_normalize_text(part))
        if category:
            match = _make_category_match(category, 140.0, "exact_part")
            if match:
                explicit_matches.append(match)
            continue

        part_stem = stem_german_word(part)
        for candidate in CATEGORIES_BY_STEM.get(part_stem, []):
            match = _make_category_match(candidate, 100.0, "stemmed_part")
            if match:
                explicit_matches.append(match)

    if explicit_matches:
        best = max(explicit_matches, key=lambda item: (item.score, len(item.category_name)))
        return best.category_id, best.category_name

    # 4. Score all categories. Categories are usually small enough for this;
    # the explicit indexes above keep the common fast paths cheap.
    matches: List[CategoryMatch] = []
    for category in CATEGORIES:
        match = _score_category(raw, category)
        if match:
            matches.append(match)

    if not matches:
        return None, None

    matches.sort(key=lambda item: (item.score, len(item.category_name)), reverse=True)
    best = matches[0]

    if best.score < CATEGORY_MIN_SCORE:
        logger.debug(
            "Category match rejected: text=%r best=%s score=%.1f reason=%s",
            raw,
            best.category_name,
            best.score,
            best.reason,
        )
        return None, None

    logger.debug(
        "Category resolved: text=%r category=%s score=%.1f reason=%s",
        raw,
        best.category_name,
        best.score,
        best.reason,
    )
    return best.category_id, best.category_name


def find_category_id_by_name(category_name: str) -> Optional[str]:
    category_id, _ = find_best_category(category_name)
    return category_id


# ---------------------------------------------------------------------------
# Aftercool specifics parsing / normalization
# ---------------------------------------------------------------------------


def _append_attribute(attrs: Dict[str, str], key: Any, value: Any) -> None:
    key_clean = str(key).strip() if key is not None else ""
    value_clean = str(value).strip() if value is not None else ""

    if not key_clean or not value_clean:
        return

    current = attrs.get(key_clean)
    if not current:
        attrs[key_clean] = value_clean
        return

    # Avoid accidental duplicate values while preserving the original order.
    existing_values = [part.strip() for part in current.split(",")]
    if value_clean not in existing_values:
        attrs[key_clean] = f"{current}, {value_clean}"


def parse_aftercool_specifics(xml_or_json_str: Any) -> Dict[str, str]:
    """Parse Aftercool XML/JSON item specifics into a flat dictionary."""
    attrs: Dict[str, str] = {}

    if not xml_or_json_str:
        return attrs

    if isinstance(xml_or_json_str, (dict, list)):
        data: Any = xml_or_json_str
    else:
        raw = str(xml_or_json_str).strip()
        if not raw:
            return attrs

        if raw.startswith("<"):
            try:
                root = ET.fromstring(raw)
            except ET.ParseError as exc:
                logger.warning("Malformed XML specifics: %s", exc)
                return attrs

            for nvl in root.findall(".//NameValueList"):
                name_elem = nvl.find("Name")
                if name_elem is None or not name_elem.text:
                    continue

                values = [
                    value.text.strip()
                    for value in nvl.findall("Value")
                    if value.text and value.text.strip()
                ]
                for value in values:
                    _append_attribute(attrs, name_elem.text, value)

            return attrs

        if raw.startswith("[") or raw.startswith("{"):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                logger.warning("Malformed JSON specifics: %s", exc)
                return attrs
        else:
            logger.debug("Unsupported specifics format")
            return attrs

    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue

            key = item.get("Name") or item.get("name")
            value = item.get("Value") or item.get("value")
            if key is None or value is None:
                continue

            if isinstance(value, list):
                for item_value in value:
                    _append_attribute(attrs, key, item_value)
            else:
                _append_attribute(attrs, key, value)

    elif isinstance(data, dict):
        for key, value in data.items():
            if value is None:
                continue

            if isinstance(value, list):
                for item_value in value:
                    _append_attribute(attrs, key, item_value)
            else:
                _append_attribute(attrs, key, value)

    return attrs


ATTR_SYNONYMS = {
    "Stil": "Wohnstil",
    "Zimmer": "Wohnraum",
    "Besonderheiten": "Besondere Merkmale",
    "Pflegeanleitung": "Pflegehinweise",
    "Montage erforderlich": "Aufbauhinweise",
    "Anzahl Türen": "Anzahl der Türen",
    "Anzahl Schubladen": "Anzahl der Schubladen",
}


def normalize_attributes(raw_attrs: Dict[str, str]) -> Dict[str, str]:
    """Normalize known Aftercool attribute aliases into canonical Otto names."""
    normalized: Dict[str, str] = {}

    for key, value in raw_attrs.items():
        key_clean = str(key).strip()
        value_clean = str(value).strip()

        if not key_clean or not value_clean:
            continue

        target_key = ATTR_SYNONYMS.get(key_clean, key_clean)
        _append_attribute(normalized, target_key, value_clean)

    return normalized


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

_http_client: Optional[httpx.AsyncClient] = None
_http_client_lock = asyncio.Lock()


async def get_http_client() -> httpx.AsyncClient:
    """Return a process-wide AsyncClient with connection pooling."""
    global _http_client

    if _http_client is not None and not _http_client.is_closed:
        return _http_client

    async with _http_client_lock:
        if _http_client is None or _http_client.is_closed:
            _http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(15.0, connect=5.0),
                limits=httpx.Limits(
                    max_keepalive_connections=20,
                    max_connections=50,
                ),
            )

    return _http_client


async def close_http_client() -> None:
    """Close the shared HTTP client during application shutdown."""
    global _http_client

    if _http_client is not None and not _http_client.is_closed:
        await _http_client.aclose()

    _http_client = None


# ---------------------------------------------------------------------------
# Remote data fetchers
# ---------------------------------------------------------------------------


async def fetch_product_from_okb(ean: str) -> Optional[dict]:
    client = await get_http_client()
    url = "https://okb.automatonsoft.de/extermal/get_products"

    try:
        response = await client.get(url, params={"sku": ean})
        response.raise_for_status()

        data = response.json()
        if not isinstance(data, dict):
            logger.warning("Unexpected OKB response type for %s: %s", ean, type(data).__name__)
            return None

        variations = data.get("productVariations")
        if not isinstance(variations, list):
            return None

        for variation in variations:
            if isinstance(variation, dict):
                return variation

    except httpx.HTTPStatusError as exc:
        logger.warning("OKB returned HTTP %s for %s", exc.response.status_code, ean)
    except httpx.HTTPError as exc:
        logger.warning("HTTP error fetching %s from OKB: %s", ean, exc)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("Invalid JSON from OKB for %s: %s", ean, exc)
    except Exception:
        logger.exception("Unexpected error fetching %s from OKB", ean)

    return None


async def fetch_product_from_aftercool(
    ean: str,
    factory_id: str,
    account: str,
) -> Optional[dict]:
    from routers.aftercool import AFTERCOOL_BASE_URL, get_aftercool_session

    client = await get_http_client()

    try:
        session = await get_aftercool_session()
        params = {
            "factory_id": factory_id,
            "account": account,
            "dataset": "lister",
            "q": ean,
            "limit": 1,
            "include_row": 1,
        }

        response = await client.get(
            f"{AFTERCOOL_BASE_URL}/api/products",
            params=params,
            cookies={"session": session},
        )

        if response.status_code in (401, 403):
            session = await get_aftercool_session(force_refresh=True)
            response = await client.get(
                f"{AFTERCOOL_BASE_URL}/api/products",
                params=params,
                cookies={"session": session},
            )

        response.raise_for_status()

        data = response.json()
        items = data.get("items", data) if isinstance(data, dict) else data
        if not isinstance(items, list) or not items:
            return None

        first_item = items[0]
        if not isinstance(first_item, dict):
            return None

        row = first_item.get("row")
        return row if isinstance(row, dict) else None

    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Aftercool returned HTTP %s for %s",
            exc.response.status_code,
            ean,
        )
    except httpx.HTTPError as exc:
        logger.warning("HTTP error fetching %s from Aftercool: %s", ean, exc)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("Invalid JSON from Aftercool for %s: %s", ean, exc)
    except Exception:
        logger.exception("Unexpected error fetching %s from Aftercool", ean)

    return None


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def _extract_okb_data(okb_data: Optional[dict]) -> Tuple[Optional[str], Optional[str], Dict[str, str]]:
    category_id: Optional[str] = None
    category_name: Optional[str] = None
    attributes: Dict[str, str] = {}

    if not isinstance(okb_data, dict):
        return category_id, category_name, attributes

    description = okb_data.get("productDescription")
    if not isinstance(description, dict):
        return category_id, category_name, attributes

    raw_category = description.get("category")
    if raw_category:
        category_id, category_name = find_best_category(str(raw_category))

    attributes_list = description.get("attributes")
    if isinstance(attributes_list, list):
        for attribute in attributes_list:
            if not isinstance(attribute, dict):
                continue

            name = attribute.get("name")
            values = attribute.get("values")
            if not name or not isinstance(values, list):
                continue

            clean_values = [
                str(value).strip()
                for value in values
                if value is not None and str(value).strip()
            ]

            if clean_values:
                _append_attribute(attributes, str(name).strip(), ", ".join(clean_values))

    return category_id, category_name, attributes


def _extract_aftercool_data(
    row: Optional[dict],
) -> Tuple[Optional[str], Optional[str], Dict[str, str]]:
    category_id: Optional[str] = None
    category_name: Optional[str] = None
    attributes: Dict[str, str] = {}

    if not isinstance(row, dict) or not row:
        return category_id, category_name, attributes

    raw_specifics = row.get("CustomItemSpecifics") or row.get("ItemSpecifics") or ""
    attributes = parse_aftercool_specifics(raw_specifics)

    produktart = attributes.get("Produktart", "")
    if produktart:
        category_id, category_name = find_best_category(produktart)

    if category_id is None:
        title = row.get("Artikelbeschreibung") or row.get("name") or ""
        if title:
            category_id, category_name = find_best_category(str(title))

    return category_id, category_name, attributes


# ---------------------------------------------------------------------------
# Main service
# ---------------------------------------------------------------------------


async def sync_product_data(
    ean: str,
    factory_id: str,
    account: str = "JV",
    row: Optional[dict] = None,
) -> dict:
    """
    Sync one product from OKB and Aftercool.

    Contract:
      - OKB + Aftercool are fetched concurrently when Aftercool row is absent.
      - Category is resolved independently from both sources.
      - OKB category takes precedence only after successful resolution.
      - Aftercool attributes are the base; OKB attributes override matching keys.
      - source reflects whether each upstream actually returned product data.
    """

    # When row is pre-provided, we only need OKB remotely.
    if not row:
        okb_data, aftercool_row = await asyncio.gather(
            fetch_product_from_okb(ean),
            fetch_product_from_aftercool(ean, factory_id, account),
        )
    else:
        okb_data = await fetch_product_from_okb(ean)
        aftercool_row = row

    # Extract normalized pieces from both sources.
    okb_category_id, okb_category_name, okb_attributes = _extract_okb_data(okb_data)
    ac_category_id, ac_category_name, ac_attributes = _extract_aftercool_data(aftercool_row)

    okb_found = isinstance(okb_data, dict)
    aftercool_found = isinstance(aftercool_row, dict) and bool(aftercool_row)

    if not okb_found and not aftercool_found:
        return {
            "ean": ean,
            "status": "not_found",
            "message": "Product not found in OKB or Aftercool",
        }

    # Resolved category source: prefer OKB when it contains a valid category,
    # otherwise use Aftercool.
    final_category_id = okb_category_id or ac_category_id
    final_category_name = okb_category_name or ac_category_name

    # Attributes: Aftercool is base; OKB wins for conflicting canonical keys.
    merged_attributes = normalize_attributes(ac_attributes)
    for key, value in okb_attributes.items():
        clean_value = str(value).strip() if value is not None else ""
        if not clean_value:
            continue

        canonical_key = ATTR_SYNONYMS.get(str(key).strip(), str(key).strip())
        merged_attributes[canonical_key] = clean_value

    source = (
        "okb+aftercool"
        if okb_found and aftercool_found
        else "okb"
        if okb_found
        else "aftercool"
    )

    # A source can legitimately contain a product without yielding a category
    # or attributes. Do not falsely call it not_found.
    if final_category_id is None and not merged_attributes:
        return {
            "ean": ean,
            "status": "unresolved",
            "message": "Product found, but no category or attributes could be resolved",
            "source": source,
        }

    return {
        "ean": ean,
        "status": "success",
        "category": final_category_name,
        "category_id": final_category_id,
        "attributes": merged_attributes,
        "source": source,
    }


async def sync_product_from_okb(
    ean: str,
    factory_id: str,
    account: str = "JV",
) -> dict:
    """Backwards-compatible alias for single-product sync."""
    return await sync_product_data(ean, factory_id, account)
