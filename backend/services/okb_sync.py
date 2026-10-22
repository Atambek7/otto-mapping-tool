import asyncio
import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx

from services.categories_repo import (
    CATEGORIES,
    get_allowed_attribute_names,
    get_numeric_attributes_for_category,
)

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


def _resolve_soft_furniture_set(text: str, context_text: str = "") -> Optional[Tuple[str, str]]:
    """
    Handles Polstergarnitur, Couchgarnitur, Sofa-Set:
    - If mixed with tables (Couchtisch, Beistelltisch, Tisch) -> 20110 (Wohnzimmer-Set)
    - Otherwise -> 25785 (Polstergarnitur)
    """
    if not text:
        return None
    t = text.lower().strip()
    combined = f"{text} {context_text}".lower()

    set_triggers = [
        "polstergarnitur", "couchgarnitur", "sofa-set", "sofa set",
        "sofaset", "sofagarnitur"
    ]
    formula_match = bool(re.search(r"\b\d\s*\+\s*\d(?:\s*\+\s*\d)*\b", combined))

    is_set = any(trigger in t for trigger in set_triggers) or (
        formula_match and ("sofa" in combined or "couch" in combined or "polster" in combined)
    )

    if not is_set:
        return None

    # Check for tables (mix of soft and hard furniture)
    table_match = bool(re.search(r"\b(couchtisch|beistelltisch|wohnzimmertisch|tisch|tische|coffee table)\b", combined))
    if table_match:
        return ("20110", "Wohnzimmer-Set")
    return ("25785", "Polstergarnitur")


def find_best_category(text: str, context_text: str = "") -> Tuple[Optional[str], Optional[str]]:
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

    # Priority check for soft furniture sets (Polstergarnitur, Couchgarnitur, Sofa-Set)
    soft_match = _resolve_soft_furniture_set(raw, context_text)
    if soft_match:
        return soft_match

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
    "Pflegeanleitung": "Pflegehinweise",
    "Montage erforderlich": "Aufbauhinweise",
    "Anzahl Türen": "Anzahl der Türen",
    "Anzahl Schubladen": "Anzahl der Schubladen",
}

FORBIDDEN_EBAY_SPECIFICS = {
    "maße sessel",
    "herstellungsjahr",
    "verpackung",
    "zusätzlich benötigte teile",
    "ean",
    "personalisiert",
    "herstellernummer",
    "herstellergarantie",
    "garantie",
    "garantiedauer",
    "besonderheiten",
    "besondere merkmale",
}

DIMENSION_ATTR_NAMES = {
    "Breite", "Höhe", "Tiefe", "Länge", "Sitzhöhe", "Sitztiefe",
    "Durchmesser", "Gewicht", "Liegefläche", "Stärke", "Belastbarkeit",
    "Belastbarkeit maximal", "Belastbarkeit pro Sitzplatz", "Sitzbreite",
    "Rückenlehnenhöhe", "Armlehnenhöhe", "Höhe Füße", "Liegehöhe",
    "Matratzenhöhe", "Spiegelhöhe", "Spiegelbreite", "Tischplattenstärke",
    "Plattenstärke", "Einlegetiefe", "Kopfteilhöhe", "Fußteilhöhe",
}


def clean_dimension_value(value: Any) -> str:
    """
    Cleans a dimension or numeric value for Otto compatibility:
    - Strips units and count suffixes: 'cm', 'cn', 'mm', 'm', 'kg', 'g', 'grad', '°', 'St.', 'Stück', '-tlg.', '-teilig'
    - Converts mm to cm (/ 10) and m to cm (* 100) if unit was mm or m
    - Extracts the primary dimension from composite sizes like '180 x 200 cm' -> '180'
    - Replaces European comma with dot (e.g. '73,5' -> '73.5')
    - Strips prefixes: 'ca.', 'ca ', '~', 'ab', 'approx.'
    - Formats integer values cleanly (e.g. '73.0' -> '73')
    """
    if value is None:
        return ""
    val_str = str(value).strip()
    if not val_str:
        return ""

    # Check for prefix like ca., ~, ab
    val_str = re.sub(r'^(ca\.?|~|ab|approx\.?)\s*', '', val_str, flags=re.IGNORECASE).strip()

    # If composite dimensions like "180 x 200 cm" or "180x200", extract the first dimension
    m_comp = re.match(r'^([0-9]+(?:[.,][0-9]+)?)\s*(?:x|×|\*)\s*[0-9]+', val_str, flags=re.IGNORECASE)
    if m_comp:
        val_str = m_comp.group(1).strip()

    # Detect numbers with units or count suffixes
    m_unit = re.search(
        r'^([0-9]+(?:[.,][0-9]+)?)\s*(cm|cn|mm|m|kg|g|grad|°|st\.?|stück|-tlg\.?|-teilig)?$',
        val_str,
        flags=re.IGNORECASE
    )
    if m_unit:
        num_str = m_unit.group(1).replace(',', '.')
        unit_str = (m_unit.group(2) or "").lower()
        try:
            num = float(num_str)
            if unit_str == 'mm':
                num = num / 10.0
            elif unit_str == 'm':
                num = num * 100.0
            if num.is_integer():
                return str(int(num))
            return f"{num:.2f}".rstrip('0').rstrip('.')
        except ValueError:
            pass

    # Generic strip of units, count suffixes, or rogue 'cn' typo
    cleaned = re.sub(r'\s*(cm|cn|st\.?|stück|-tlg\.?|-teilig)\b', '', val_str, flags=re.IGNORECASE).strip()
    if re.match(r'^[0-9]+,[0-9]+$', cleaned):
        cleaned = cleaned.replace(',', '.')

    try:
        f = float(cleaned)
        if f.is_integer():
            return str(int(f))
        return f"{f:.2f}".rstrip('0').rstrip('.')
    except ValueError:
        # If there's a leading integer/float in a dirty string, extract it
        m_lead = re.match(r'^([0-9]+(?:[.,][0-9]+)?)', cleaned)
        if m_lead:
            try:
                f2 = float(m_lead.group(1).replace(',', '.'))
                if f2.is_integer():
                    return str(int(f2))
                return f"{f2:.2f}".rstrip('0').rstrip('.')
            except ValueError:
                pass
        return cleaned


def parse_composite_dimensions(dimensions_str: str) -> dict[str, str]:
    """
    Extracts Länge, Breite, Höhe from composite dimension strings like:
    '80 x 73 x 95 cm' or 'ca. 200 x 90 x 45 cm' or '80x73x95'
    """
    if not dimensions_str:
        return {}
    m = re.search(
        r'([0-9]+(?:[.,][0-9]+)?)\s*(?:x|×|\*)\s*([0-9]+(?:[.,][0-9]+)?)(?:\s*(?:x|×|\*)\s*([0-9]+(?:[.,][0-9]+)?))?',
        str(dimensions_str)
    )
    if not m:
        return {}
    parts = [clean_dimension_value(g) for g in m.groups() if g]
    res = {}
    if len(parts) == 3:
        res["Breite"] = parts[0]
        res["Tiefe"] = parts[1]
        res["Höhe"] = parts[2]
    elif len(parts) == 2:
        res["Breite"] = parts[0]
        res["Höhe"] = parts[1]
    return res


def round_msrp_to_49_or_99(value: float, standard_price: float = 0.0) -> float:
    """
    Rounds MSRP (UVP) value according to the user rule:
    Take the two digits before decimal point and determine whether they are closer to 49 or 99.
    - Closer to 49 (<= 74) -> replaces last two digits with 49.00
    - Closer to 99 (> 74) -> replaces last two digits with 99.00
    Guarantees MSRP is never lower than standard_price.
    """
    if value <= 0:
        return 0.0

    int_val = int(value)
    prefix = (int_val // 100) * 100
    last_two = int_val % 100

    dist_49 = abs(last_two - 49)
    dist_99 = abs(last_two - 99)

    if dist_49 <= dist_99:
        rounded = float(prefix + 49)
    else:
        rounded = float(prefix + 99)

    if standard_price > 0 and rounded < standard_price:
        if prefix + 99 >= standard_price:
            rounded = float(prefix + 99)
        else:
            rounded = float(prefix + 149)

    return rounded


def resolve_origin_and_delivery_info(
    aftercool_row: Optional[dict] = None,
    attributes: Optional[dict] = None,
    product_title: str = "",
) -> dict:
    """
    Detects product origin (Turkey vs China vs Europe) and delivery timeframe.
    User rule:
      China: 8-12 weeks, ~56 days processing time.
      Non-China (Europe / Turkey): 4-8 weeks, ~37 days processing time.
      Turkey identified by: INE, Inegöl, Bursa, TR, Türkei, Turkey.
    Returns:
      {
        "origin_country": "Türkei" | "China" | "Deutschland" | "Polen" | ... | None,
        "delivery_weeks": "4-8 Wochen" | "8-12 Wochen",
        "delivery_days": 37 | 56,
        "is_china": bool,
      }
    """
    combined_text = []
    if product_title:
        combined_text.append(str(product_title))

    if aftercool_row:
        for fld in ("Artikelbeschreibung", "name", "kurzbeschreibung", "Ort", "City", "Standort", "Location"):
            val = aftercool_row.get(fld)
            if val:
                combined_text.append(str(val))

    if attributes:
        for k, v in attributes.items():
            combined_text.append(f"{k}: {v}")

    full_text = " ".join(combined_text).lower()

    # Priority 0: Explicit country attribute in attributes or aftercool_row
    explicit_country = None
    for src in (attributes, aftercool_row):
        if not src or not isinstance(src, dict):
            continue
        for k in ("Herstellungsland", "herstellungsland", "Ursprungsland", "ursprungsland", "Herkunftsland", "herkunftsland", "Produktionsland", "produktionsland", "Herstellerland", "herstellerland"):
            v = src.get(k)
            if v and str(v).strip():
                explicit_country = str(v).strip()
                break
        if explicit_country:
            break

    if explicit_country:
        exp_lower = explicit_country.lower()
        if any(m in exp_lower for m in ("china", "cn", "prc", "vr china")):
            return {
                "origin_country": "China",
                "delivery_weeks": "8-12 Wochen",
                "delivery_days": 56,
                "is_china": True,
            }
        if any(m in exp_lower for m in ("türkei", "turkey", "inegöl", "inegol", "bursa")) or exp_lower == "tr":
            normalized_c = "Türkei"
        elif any(m in exp_lower for m in ("deutschland", "germany")) or exp_lower == "de":
            normalized_c = "Deutschland"
        elif any(m in exp_lower for m in ("polen", "poland")) or exp_lower == "pl":
            normalized_c = "Polen"
        elif any(m in exp_lower for m in ("italien", "italy")) or exp_lower == "it":
            normalized_c = "Italien"
        elif any(m in exp_lower for m in ("frankreich", "france")) or exp_lower == "fr":
            normalized_c = "Frankreich"
        elif any(m in exp_lower for m in ("spanien", "spain")) or exp_lower == "es":
            normalized_c = "Spanien"
        elif any(m in exp_lower for m in ("österreich", "austria")) or exp_lower == "at":
            normalized_c = "Österreich"
        elif any(m in exp_lower for m in ("niederlande", "netherlands", "holland")) or exp_lower == "nl":
            normalized_c = "Niederlande"
        elif "vietnam" in exp_lower or exp_lower == "vn":
            normalized_c = "Vietnam"
        elif any(m in exp_lower for m in ("indien", "india")):
            normalized_c = "Indien"
        else:
            normalized_c = explicit_country

        return {
            "origin_country": normalized_c,
            "delivery_weeks": "4-8 Wochen",
            "delivery_days": 37,
            "is_china": False,
        }

    # Priority 1: Turkey / Inegöl markers (INE, Inegöl, Bursa, TR, Türkei, Turkey)
    turkey_markers = ["inegöl", "inegol", "bursa", "türkei", "turkey"]
    is_turkey = any(m in full_text for m in turkey_markers)
    if not is_turkey:
        if re.search(r'\b(ine|tr)\b', full_text, flags=re.IGNORECASE):
            is_turkey = True

    # Priority 2: China markers
    china_markers = ["china", "vr china", "volksrepublik china", "prc", "chinesisch"]
    is_china = any(m in full_text for m in china_markers)
    if not is_china and re.search(r'\bcn\b', full_text, flags=re.IGNORECASE):
        is_china = True

    if is_turkey:
        return {
            "origin_country": "Türkei",
            "delivery_weeks": "4-8 Wochen",
            "delivery_days": 37,
            "is_china": False,
        }
    elif is_china:
        return {
            "origin_country": "China",
            "delivery_weeks": "8-12 Wochen",
            "delivery_days": 56,
            "is_china": True,
        }
    else:
        if "deutschland" in full_text or "germany" in full_text:
            country = "Deutschland"
        elif "polen" in full_text or "poland" in full_text:
            country = "Polen"
        elif "italien" in full_text or "italy" in full_text:
            country = "Italien"
        elif "frankreich" in full_text or "france" in full_text:
            country = "Frankreich"
        elif "spanien" in full_text or "spain" in full_text:
            country = "Spanien"
        elif "österreich" in full_text or "austria" in full_text:
            country = "Österreich"
        elif "niederlande" in full_text or "netherlands" in full_text or "holland" in full_text:
            country = "Niederlande"
        elif "vietnam" in full_text:
            country = "Vietnam"
        elif "indien" in full_text or "india" in full_text:
            country = "Indien"
        else:
            country = None

        return {
            "origin_country": country,
            "delivery_weeks": "4-8 Wochen",
            "delivery_days": 37,
            "is_china": False,
        }


def normalize_attributes(raw_attrs: Dict[str, str]) -> Dict[str, str]:
    """
    Normalize known Aftercool attribute aliases into canonical Otto names.
    Also removes forbidden eBay garbage specifics and cleans dimension units (cm).
    """
    normalized: Dict[str, str] = {}
    composite_dims: Optional[str] = None

    for key, value in raw_attrs.items():
        key_clean = str(key).strip()
        value_clean = str(value).strip()

        if not key_clean or not value_clean:
            continue

        # Filter out forbidden eBay specifics
        if key_clean.lower() in FORBIDDEN_EBAY_SPECIFICS:
            continue

        # Check for composite dimensions field, e.g. "Maße: 80 x 73 x 95 cm"
        if key_clean.lower() in ("maße", "abmessungen", "dimensionen", "produktabmessungen"):
            composite_dims = value_clean
            continue

        target_key = ATTR_SYNONYMS.get(key_clean, key_clean)

        # Clean dimension attributes (strip 'cm', 'mm' etc.)
        if target_key in DIMENSION_ATTR_NAMES or any(d in target_key.lower() for d in ("breite", "höhe", "tiefe", "länge", "höhe")):
            value_clean = clean_dimension_value(value_clean)

        _append_attribute(normalized, target_key, value_clean)

    # If composite dimensions found and individual dims not set, populate them
    if composite_dims:
        extracted = parse_composite_dimensions(composite_dims)
        for d_key, d_val in extracted.items():
            if d_key not in normalized:
                normalized[d_key] = d_val

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
    from services.aftercool_service import fetch_aftercool_product

    product = await fetch_aftercool_product(ean, factory_id, account)
    return product.raw_item.get("row") if product else None


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
    title = row.get("Artikelbeschreibung") or row.get("name") or ""
    desc = row.get("Description") or ""
    context_text = f"{title} {raw_specifics} {desc}"

    if produktart:
        category_id, category_name = find_best_category(produktart, context_text=context_text)

    if category_id is None:
        if title:
            category_id, category_name = find_best_category(str(title), context_text=context_text)

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
      - Fresh OKB + Aftercool data are fetched concurrently.
      - Aftercool category and attributes take precedence.
      - OKB fills compatible gaps; prices come exclusively from Aftercool.
      - source reflects whether each upstream actually returned product data.
    """

    # Client rows may come from a stale browser cache. Explicit synchronization
    # always reads Aftercool afresh and never falls back to old client prices.
    try:
        okb_data, aftercool_row = await asyncio.gather(
            fetch_product_from_okb(ean),
            fetch_product_from_aftercool(ean, factory_id, account),
        )
    except Exception:
        logger.exception("Aftercool sync failed for %s (%s)", ean, account)
        return {"ean": ean, "status": "error", "message": "Aftercool could not be read; synchronization was not applied"}

    # OKB must match the requested product as well.
    if okb_data and any(str(okb_data.get(k)).strip() != str(ean).strip()
                        for k in ("sku", "ean") if okb_data.get(k)):
        okb_data = None

    okb_category_id, okb_category_name, okb_attributes = _extract_okb_data(okb_data)
    ac_category_id, ac_category_name, ac_attributes = _extract_aftercool_data(aftercool_row)
    okb_found = isinstance(okb_data, dict)
    aftercool_found = isinstance(aftercool_row, dict) and bool(aftercool_row)

    if not okb_found and not aftercool_found:
        return {"ean": ean, "status": "not_found", "message": "Product not found in OKB or Aftercool"}

    # Aftercool is authoritative. OKB only fills gaps compatible with its category.
    final_category_id = ac_category_id or okb_category_id
    final_category_name = ac_category_name or okb_category_name
    merged_attributes = normalize_attributes(okb_attributes) if (
        not ac_category_id or okb_category_id == ac_category_id
    ) else {}
    merged_attributes.update(normalize_attributes(ac_attributes))

    from services.aftercool_service import parse_aftercool_product
    product = parse_aftercool_product({"ean": ean, "row": aftercool_row or {}}, factory_id)
    metadata = {
        "price": str(product.price) if product and product.price is not None else None,
        "price_af": str(product.price_af) if product and product.price_af is not None else None,
        "product_name": product.name if product else None,
        "image_url": product.image_url if product else None,
    } if aftercool_found else {}

    # Category Schema Whitelisting and Numeric Cleaning
    if final_category_id:
        allowed = get_allowed_attribute_names(final_category_id)
        numeric_specs = get_numeric_attributes_for_category(final_category_id)

        filtered_attributes = {}
        for k, v in merged_attributes.items():
            k_clean = str(k).strip()
            if k_clean in allowed:
                # If numeric or dimension attribute, ensure 'cm' is stripped
                if k_clean in numeric_specs or k_clean in DIMENSION_ATTR_NAMES:
                    filtered_attributes[k_clean] = clean_dimension_value(v)
                else:
                    filtered_attributes[k_clean] = v

        merged_attributes = filtered_attributes

    # Resolve origin (Turkey vs China vs Europe) and delivery weeks/days
    delivery_info = resolve_origin_and_delivery_info(
        aftercool_row=aftercool_row,
        attributes=merged_attributes,
        product_title=str((aftercool_row or {}).get("Artikelbeschreibung") or "")
    )

    # If category allows Herkunftsland / Ursprungsland / Herstellungsland / Produktionsland, set it if missing and country detected
    if final_category_id:
        allowed = get_allowed_attribute_names(final_category_id)
        origin_country = delivery_info.get("origin_country")
        if origin_country:
            for attr_k in ("Herkunftsland", "Ursprungsland", "Herstellungsland", "Produktionsland", "Herstellerland"):
                if attr_k in allowed and attr_k not in merged_attributes:
                    merged_attributes[attr_k] = origin_country

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
            "metadata": metadata,
        }

    return {
        "ean": ean,
        "status": "success",
        "category": final_category_name,
        "category_id": final_category_id,
        "category_source": "aftercool" if ac_category_id else "okb" if okb_category_id else None,
        "metadata": metadata,
        "attributes": merged_attributes,
        "origin_country": delivery_info["origin_country"],
        "delivery_weeks": delivery_info["delivery_weeks"],
        "delivery_days": delivery_info["delivery_days"],
        "is_china": delivery_info["is_china"],
        "source": source,
    }


async def sync_product_from_okb(
    ean: str,
    factory_id: str,
    account: str = "JV",
) -> dict:
    """Backwards-compatible alias for single-product sync."""
    return await sync_product_data(ean, factory_id, account)
