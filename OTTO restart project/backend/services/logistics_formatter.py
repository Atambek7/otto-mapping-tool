"""OttoFlow logistics rules copied into the isolated Restart workspace."""
from decimal import Decimal, InvalidOperation
import re
import xml.etree.ElementTree as ET
from typing import Any, Mapping
DEFAULT_PACKING_UNIT_COUNT = 1

_NUMERIC_SCHEMA_TYPES = frozenset({"INTEGER", "FLOAT", "DOUBLE", "DECIMAL", "NUMBER"})
_PLACEHOLDER_ATTRIBUTE_TOKENS = frozenset({
    "unbekannt",
    "nicht bekannt",
    "k.a.",
    "k. a.",
    "keine",
    "keine angabe",
    "nicht zutreffend",
    "none",
    "null",
    "n/a",
    "na",
    "-",
    "--",
    "—",
    "?",
})


def is_placeholder_attribute_value(value: Any) -> bool:
    if value is None:
        return True
    cleaned = " ".join(str(value).strip().casefold().split())
    if not cleaned:
        return True
    return cleaned in _PLACEHOLDER_ATTRIBUTE_TOKENS or cleaned.startswith("unbekannt ")


def _format_decimal_clean(value: Decimal, *, integer_only: bool) -> str | None:
    if not value.is_finite():
        return None
    if integer_only:
        if value != value.to_integral_value():
            return None
        return str(int(value))
    normalized = value.normalize()
    text_val = format(normalized, "f")
    if "." in text_val:
        text_val = text_val.rstrip("0").rstrip(".")
    return text_val or "0"


def validate_numeric_attribute_value(value: Any, spec: Mapping[str, Any]) -> str | None:
    """Strictly clean and validate INTEGER/FLOAT attribute values; return None when non-numeric."""
    if is_placeholder_attribute_value(value):
        return None
    attr_type = str(spec.get("type") or "").strip().upper()
    if attr_type not in _NUMERIC_SCHEMA_TYPES:
        return str(value).strip()
    integer_only = attr_type == "INTEGER"
    raw = str(value).strip()
    if ";" in raw:
        if not spec.get("multiValue"):
            return None
        raw_parts = [part.strip() for part in raw.split(";") if part.strip()]
    else:
        raw_parts = [raw]
    if not raw_parts:
        return None
    parsed_parts: list[str] = []
    for part in raw_parts:
        if is_placeholder_attribute_value(part):
            return None
        cleaned = clean_dimension_value(part)
        if not cleaned or not re.fullmatch(r"[+-]?(?:\d+(?:\.\d+)?)", cleaned):
            return None
        try:
            dec = Decimal(cleaned)
        except InvalidOperation:
            return None
        formatted = _format_decimal_clean(dec, integer_only=integer_only)
        if formatted is None:
            return None
        parsed_parts.append(formatted)
    return "; ".join(parsed_parts)


def validate_and_normalize_ai_attribute(value: Any, spec: Mapping[str, Any]) -> str | None:
    """Reject placeholder strings and enforce schema numeric/enum types on AI attribute proposals."""
    if is_placeholder_attribute_value(value):
        return None
    normalized = str(value).strip()[:500]
    if not normalized:
        return None
    attr_type = str(spec.get("type") or "").strip().upper()
    if attr_type in _NUMERIC_SCHEMA_TYPES:
        normalized = validate_numeric_attribute_value(normalized, spec)
        if normalized is None:
            return None
    else:
        normalized = normalize_schema_attribute(normalized, spec)
    choices = spec.get("allowedValues")
    if isinstance(choices, list) and choices:
        if normalized not in choices:
            if not spec.get("multiValue") or any(part.strip() not in choices for part in normalized.split(";")):
                return None
    return normalized

def parse_custom_item_specifics(xml_str: str | None) -> dict[str, list[str]]:
    """
    Parses Afterbuy/Aftercool CustomItemSpecifics XML into a dictionary:
    { "Name": ["Value1", "Value2"], ... }
    """
    if not xml_str or not isinstance(xml_str, str):
        return {}
    xml_clean = xml_str.strip()
    if not xml_clean or not (xml_clean.startswith("<") and xml_clean.endswith(">")):
        return {}
    
    # Pre-clean unescaped ampersands to avoid xml parsing syntax errors
    xml_clean = re.sub(r'&(?!(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)', '&amp;', xml_clean)
    
    try:
        root = ET.fromstring(xml_clean)
        merkmale: dict[str, list[str]] = {}
        for nvl in root.findall(".//NameValueList"):
            name_node = nvl.find("Name")
            if name_node is None or not name_node.text:
                continue
            name = name_node.text.strip()
            val_nodes = nvl.findall("Value")
            vals = [v.text.strip() for v in val_nodes if v.text and v.text.strip()]
            if vals:
                if name in merkmale:
                    for v in vals:
                        if v not in merkmale[name]:
                            merkmale[name].append(v)
                else:
                    merkmale[name] = vals
        return merkmale
    except Exception:
        return {}

def _get_merkmale_vals(merkmale: dict[str, list[str]], *keys: str) -> list[str]:
    """Find values matching any of the given keys (case-insensitive)."""
    found: list[str] = []
    lowered_keys = {k.lower() for k in keys}
    for m_key, m_vals in merkmale.items():
        if m_key.lower() in lowered_keys:
            for v in m_vals:
                if v and v not in found:
                    found.append(v)
    return found

def _get_merkmale_str(merkmale: dict[str, list[str]], *keys: str, separator: str = ", ") -> str:
    vals = _get_merkmale_vals(merkmale, *keys)
    return separator.join(vals)

def _parse_dimension_value(val: Any) -> float | None:
    """
    Parses a dimension or weight string into a numeric float in cm or kg.
    Handles formats like '200 cm', '200.5', '200,5', 'ca. 200 cm', '2 m', '2000 mm'.
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val) if val > 0 else None

    val_str = str(val).strip()
    if not val_str:
        return None

    match = re.search(r'([0-9]+(?:[.,][0-9]+)?)\s*([a-zA-Z]*)', val_str)
    if not match:
        return None

    num_str, unit = match.group(1).replace(',', '.'), match.group(2).lower()
    try:
        num = float(num_str)
        if num <= 0:
            return None
        if unit == 'm':
            num *= 100.0
        elif unit == 'mm':
            num /= 10.0
        return round(num, 1)
    except ValueError:
        return None

def _build_logistics(
    aftercool_info: dict[str, Any],
    mapping: dict[str, Any] | None = None,
    merkmale: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """
    Build logistics section.
    Extracts real dimensions (Breite, Höhe, Tiefe/Länge, Gewicht)
    from mapping attributes, merkmale, or aftercool_info, falling back to safe minimums.
    """
    logistics = aftercool_info.get("logistics")

    if isinstance(logistics, dict) and "packingUnits" in logistics:
        return logistics

    raw_attrs = (mapping.get("attributes") or {}) if isinstance(mapping, dict) else {}

    width = None
    height = None
    length = None
    weight = None

    for k, v in raw_attrs.items():
        k_lower = str(k).lower().strip()
        if isinstance(v, list) and v:
            v_val = v[0]
        elif isinstance(v, str) and ";" in v:
            v_val = v.split(";")[0]
        else:
            v_val = v

        if k_lower in ("breite", "gesamtbreite", "breite (cm)"):
            width = _parse_dimension_value(v_val)
        elif k_lower in ("höhe", "gesamthöhe", "höhe (cm)"):
            height = _parse_dimension_value(v_val)
        elif k_lower in ("tiefe", "länge", "gesamttiefe", "tiefe (cm)", "länge (cm)"):
            length = _parse_dimension_value(v_val)
        elif k_lower in ("gewicht", "artikelgewicht", "gesamtgewicht", "gewicht (kg)"):
            weight = _parse_dimension_value(v_val)

    # Check merkmale if any dimension is still missing
    if merkmale and (width is None or height is None or length is None or weight is None):
        if width is None:
            w_val = _get_merkmale_str(merkmale, "Breite", "Breite (cm)")
            if w_val:
                width = _parse_dimension_value(w_val)
        if height is None:
            h_val = _get_merkmale_str(merkmale, "Höhe", "Höhe (cm)")
            if h_val:
                height = _parse_dimension_value(h_val)
        if length is None:
            l_val = _get_merkmale_str(merkmale, "Länge", "Tiefe", "Länge (cm)", "Tiefe (cm)")
            if l_val:
                length = _parse_dimension_value(l_val)
        if weight is None:
            wg_val = _get_merkmale_str(merkmale, "Gewicht", "Artikelgewicht", "Gewicht (kg)")
            if wg_val:
                weight = _parse_dimension_value(wg_val)

        # If still missing, check Maße ... keys like '57 x 60 x 83 cm'
        if width is None or height is None or length is None:
            for m_k, m_v in merkmale.items():
                if m_k.lower().startswith("maße") or m_k.lower().startswith("masse"):
                    m_str = " ".join(m_v)
                    match = re.search(r'([0-9]+(?:[.,][0-9]+)?)\s*x\s*([0-9]+(?:[.,][0-9]+)?)\s*x\s*([0-9]+(?:[.,][0-9]+)?)', m_str)
                    if match:
                        d1 = _parse_dimension_value(match.group(1))
                        d2 = _parse_dimension_value(match.group(2))
                        d3 = _parse_dimension_value(match.group(3))
                        if length is None and d1: length = d1
                        if width is None and d2: width = d2
                        if height is None and d3: height = d3
                        break

    final_width = int(round(width)) if width and width >= 1 else 10
    final_height = int(round(height)) if height and height >= 1 else 10
    final_length = int(round(length)) if length and length >= 1 else 10
    final_weight = int(round(weight)) if weight and weight >= 1 else 1

    return {
        "packingUnitCount": DEFAULT_PACKING_UNIT_COUNT,
        "packingUnits": [
            {
                "weight": final_weight,
                "width": final_width,
                "height": final_height,
                "length": final_length,
            }
        ],
    }

def build_logistics(product, aftercool, attributes):
    aftercool = aftercool or {}
    if isinstance(product.get('logistics'),dict) and product['logistics'].get('packingUnits'):
        return dict(product['logistics']), {'strategy':'OTTO_SOURCE'}
    merkmale=parse_custom_item_specifics(aftercool.get('custom_item_specifics') or aftercool.get('CustomItemSpecifics'))
    data=_build_logistics(aftercool,{'attributes':attributes},merkmale)
    evidence={'strategy':'OTTOFLOW_FORMATTER','attributes':dict(attributes),'characteristics':merkmale,
              'defaults':{'width':10,'height':10,'length':10,'weight':1},
              'notice':'Product dimensions and fallback values follow OttoFlow; not verified packaging measurements.'}
    aliases={'width':('breite','gesamtbreite','breite (cm)'),
             'height':('höhe','gesamthöhe','höhe (cm)'),
             'length':('tiefe','länge','gesamttiefe','tiefe (cm)','länge (cm)'),
             'weight':('gewicht','artikelgewicht','gesamtgewicht','gewicht (kg)')}
    evidence['fields']={}
    for field,names in aliases.items():
        matched=next((name for name,value in attributes.items()
                      if name.casefold() in names and _parse_dimension_value(value) is not None),None)
        characteristic=next((name for name,values in merkmale.items()
                             if name.casefold() in names and _parse_dimension_value(', '.join(values)) is not None),None)
        composite=field!='weight' and any(name.casefold().startswith(('maße','masse')) for name in merkmale)
        origin='ATTRIBUTE' if matched else 'AFTERCOOL_CHARACTERISTIC' if characteristic or composite else 'DEFAULT'
        if isinstance(aftercool.get('logistics'),dict) and 'packingUnits' in aftercool['logistics']:
            origin='AFTERCOOL_LOGISTICS'
        evidence['fields'][field]={'source':origin,'sourceField':matched or characteristic,
            'value':data.get('packingUnits',[{}])[0].get(field) if data.get('packingUnits') else None}
    return data,evidence


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


def normalize_schema_attribute(value, spec):
    value=str(value or '').strip()
    if spec.get('type') in {'INTEGER','FLOAT','DOUBLE','DECIMAL','NUMBER'}:
        cleaned=clean_dimension_value(value)
        if cleaned:
            if spec.get('type')=='INTEGER':
                try: cleaned=str(int(float(cleaned)))
                except ValueError: pass
            value=cleaned
    allowed=spec.get('allowedValues') or []
    if spec.get('multiValue') and value not in allowed and ',' in value:
        parts=[part.strip() for part in value.split(',') if part.strip()]
        if parts and all(part in allowed for part in parts):
            value='; '.join(parts)
    return value
