from __future__ import annotations

import os
import re
import html
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from typing import Any

from services.brand_guard import is_forbidden_warranty_attribute, sanitize_text_for_warranty


BRAND_KEYS = {"brandId", "BrandId", "Marke"}
DEFAULT_VAT = "FULL"
DEFAULT_CURRENCY = "EUR"
DEFAULT_PACKING_UNIT_COUNT = 1

PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    "https://omt.automatonsoft.de",
).rstrip("/")


def _parse_price(price: Any, ean: str) -> float:
    """
    Parse and validate product price.

    Otto payload expects a numeric amount, while Decimal is used
    internally to avoid floating-point calculation issues.
    """
    if price is None:
        raise ValueError(f"Price is missing for EAN {ean}")

    price_str = str(price).strip().replace(",", ".")

    if not price_str:
        raise ValueError(f"Price is empty for EAN {ean}")

    try:
        value = Decimal(price_str)
    except InvalidOperation as exc:
        raise ValueError(
            f"Invalid price for EAN {ean}: {price!r}"
        ) from exc

    if not value.is_finite():
        raise ValueError(
            f"Price must be finite for EAN {ean}: {price!r}"
        )

    if value <= 0:
        raise ValueError(
            f"Price must be greater than 0 for EAN {ean}: {price!r}"
        )

    return float(value)


def _resolve_brand(mapping: dict[str, Any], ean: str) -> str:
    """
    Resolve Otto brandId from supported mapping keys.
    """
    raw_attrs = mapping.get("attributes") or {}

    if not isinstance(raw_attrs, dict):
        raise TypeError(
            f"mapping['attributes'] must be a dict for EAN {ean}"
        )

    for key in ("brandId", "BrandId", "Marke"):
        value = raw_attrs.get(key)

        if value is not None and str(value).strip():
            raw_brand = str(value).strip()
            brand_upper = raw_brand.upper()
            if brand_upper in ("JVMOEBEL", "JV-MOEBEL", "JV", "46974041"):
                return "UO4EGHSX"
            elif brand_upper in ("XLMOEBEL", "XL-MOEBEL", "XL", "80120806"):
                return "6HMOZBOU"
            return raw_brand

    raise ValueError(f"Brand is missing for EAN {ean}")


def _normalize_attribute_values(value: Any) -> list[str]:
    """
    Convert supported attribute input formats into Otto's
    list[str] representation.
    """
    if value is None:
        return []

    if isinstance(value, list):
        values = value

    elif isinstance(value, tuple):
        values = list(value)

    elif isinstance(value, str):
        value = value.strip()

        if not value:
            return []

        # Existing mapping format uses ';' for multi-value attributes.
        values = value.split(";") if ";" in value else [value]

    else:
        values = [value]

    result: list[str] = []

    for item in values:
        if item is None:
            continue

        normalized = str(item).strip()

        if normalized:
            result.append(normalized)

    return result


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


def _clean_prefix_ca(val: str) -> str:
    val = val.strip()
    return re.sub(r'^(ca\.\s*)+', '', val, flags=re.IGNORECASE).strip()


def _truncate_bullet(text: str, max_len: int = 180) -> str:
    text = text.strip().rstrip(".,;:- ")
    if len(text) <= max_len:
        return text
    truncated = text[:max_len - 3]
    last_space = truncated.rfind(" ")
    if last_space > max_len // 2:
        truncated = truncated[:last_space]
    return truncated.rstrip(".,;:- ") + "..."


def _truncate_text_safely(text: str, max_len: int = 70) -> str:
    """
    Safely truncates text to max_len without cutting words in half.
    Removes trailing punctuation and spaces.
    """
    if not text:
        return ""
    text = str(text).strip()
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    last_space = truncated.rfind(" ")
    if last_space > max_len // 3:
        truncated = truncated[:last_space]
    return truncated.rstrip(" .,;:/-")


def _clean_html_for_otto(text: str) -> str:
    """
    Cleans and strips all HTML tags for Otto product description,
    converting line break tags to newlines and producing clean plain text.
    """
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r'</?(p|div|li|h[1-6]|br)\b[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('\xa0', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def generate_bullet_points(
    merkmale: dict[str, list[str]],
    product_name: str,
    description: str = "",
) -> list[str]:
    """
    Generates up to 5 rich, SEO-optimized German bullet points directly based
    on the product description and Merkmale, strictly conforming to Otto's max 180 chars limit.
    """
    name = (product_name or "").strip()
    desc_clean = _clean_html_for_otto(description)
    
    # 1. Scope / Type / Set (from merkmale or description)
    scope_raw_list = _get_merkmale_vals(merkmale, "Lieferumfang", "Set enthält", "Produktart", "Typ")
    scope_cleaned_list = []
    for s in scope_raw_list:
        s_c = re.sub(r'^(es wird alles laut produktbeschreibung geliefert[,;\s]*)+', '', s, flags=re.IGNORECASE).strip()
        s_c = re.sub(r'^(schlafzimmer\s*set[,;\s]*)+', '', s_c, flags=re.IGNORECASE).strip()
        if s_c:
            scope_cleaned_list.append(s_c)
    scope = ", ".join(scope_cleaned_list[:4])
    if not scope and "Lieferumfang:" in desc_clean:
        m_scope = re.search(r'Lieferumfang:\s*([^\n\r]+)', desc_clean)
        if m_scope:
            s_c = re.sub(r'^(es wird alles laut produktbeschreibung geliefert[,;\s]*)+', '', m_scope.group(1).strip(), flags=re.IGNORECASE).strip()
            s_c = re.sub(r'^(schlafzimmer\s*set[,;\s]*)+', '', s_c, flags=re.IGNORECASE).strip()
            scope = s_c

    room = _get_merkmale_str(merkmale, "Zimmer", "Einsatzbereich", separator=", ")
    col_str = _get_merkmale_str(merkmale, "Farbe", "Farbton")
    if not col_str and "Farbe:" in desc_clean:
        m_col = re.search(r'Farbe:\s*([^\n\r]+)', desc_clean)
        if m_col:
            col_str = m_col.group(1).strip()

    if scope:
        if "set" in name.lower() or "set" in scope.lower() or "teilig" in name.lower():
            b1 = f"Komplettes Set: Inklusive {scope}"
        else:
            b1 = f"Lieferumfang & Typ: {scope}"
        if col_str and f" in {col_str.lower()}" not in b1.lower():
            b1 += f" in stilvollem {col_str}"
        elif room:
            b1 += f" – ideal für {room}"
        else:
            b1 += " – stilvolle Bereicherung für Ihr modernes Wohnambiente"
    elif name:
        b1 = f"Modell: {name} – erstklassiges Design für zeitgemäßen Wohnkomfort"
    else:
        b1 = "Exklusives Design: Stilvolle und funktionale Bereicherung für Ihren Wohnbereich"
    
    # 2. Comfort / Upholstery / Liegefläche
    liege = _clean_prefix_ca(_get_merkmale_str(merkmale, "Liegefläche"))
    if not liege and "Liegefläche:" in desc_clean:
        m_liege = re.search(r'Liegefläche:\s*([^\n\r]+)', desc_clean)
        if m_liege:
            liege = _clean_prefix_ca(m_liege.group(1).strip())

    seating_vals = _get_merkmale_vals(merkmale, "Sitzfläche", "Rückenlehne", "Polsterung", "Polsterdesign", "Armlehnen")[:3]
    seating = ", ".join(seating_vals)
    features_vals = _get_merkmale_vals(merkmale, "Besonderheiten", "Eigenschaften", "Funktion")[:3]
    features = ", ".join(features_vals)

    if liege:
        b2 = f"Design & Komfort: Modernes Design mit großzügiger Liegefläche (ca. {liege}) für erholsamen Schlaf"
    elif seating:
        b2 = f"Design & Komfort: {seating} für ein rundum entspanntes und ergonomisches Sitzerlebnis"
    elif features:
        b2 = f"Praktische Ausstattung: {features} – komfortabel und funktional im Alltag"
    else:
        b2 = "Design & Komfort: Ergonomisch durchdacht und ideal für den täglichen Gebrauch gestaltet"
        
    # 3. Materials & Colors
    mat_vals = _get_merkmale_vals(merkmale, "Material", "Materialart", "Bezug", "Gestell", "Tischplattenmaterial")[:3]
    materials = ", ".join(mat_vals)
    if not materials and "Material:" in desc_clean:
        m_mat = re.search(r'Material:\s*([^\n\r]+)', desc_clean)
        if m_mat:
            materials = m_mat.group(1).strip()

    if materials and col_str:
        b3 = f"Hochwertige Materialien: Robuste Kombination aus {materials} in ansprechendem {col_str} für hohe Langlebigkeit"
    elif materials:
        b3 = f"Hochwertige Materialien: Ausgewähltes {materials} für edle Haptik und verlässliche Langlebigkeit"
    elif col_str:
        b3 = f"Harmonische Farbgebung: Zeitloses Design in {col_str} fügt sich nahtlos in jeden Einrichtungsstil ein"
    else:
        b3 = "Erlesene Werkstoffe: Strapazierfähige, langlebige Materialien mit pflegeleichter Oberfläche"

    # 4. Construction, Frame & Style
    frame_vals = _get_merkmale_vals(merkmale, "Gestell", "Beine", "Aufstellung")[:2]
    frame = ", ".join(frame_vals)
    style_vals = _get_merkmale_vals(merkmale, "Stil", "Design", "Muster")[:2]
    style = " / ".join(style_vals)

    if frame:
        b4 = f"Stabilität & Pflege: Solide Konstruktion ({frame}) mit pflegeleichten und strapazierfähigen Oberflächen"
    elif style:
        b4 = f"Stabilität & Pflege: Solide Verarbeitung im zeitlosen {style}-Stil, robust und pflegeleicht im Alltag"
    else:
        b4 = "Stabilität & Pflege: Solide und präzise Verarbeitung für dauerhafte Stabilität mit pflegeleichten Oberflächen"

    # 5. Exact Dimensions & Measurements
    dim_items: list[str] = []
    for k, v in merkmale.items():
        if k.lower().startswith("maße") or k.lower().startswith("masse"):
            label = re.sub(r'^(maße|masse)\s*', '', k, flags=re.IGNORECASE).strip()
            val_s = ", ".join(v)
            dim_items.append(f"{label}: {val_s}" if label else val_s)
            
    if not dim_items and "Maße & Produktdetails:" in desc_clean:
        lines = desc_clean.split("\n")
        in_dim = False
        for l in lines:
            if "Maße & Produktdetails:" in l:
                in_dim = True
                continue
            if in_dim and l.startswith("-"):
                line_val = l.lstrip("- ").strip()
                if not any(x in line_val.lower() for x in ["material", "farbe", "lieferumfang", "liegefläche"]):
                    dim_items.append(line_val)

    length = _get_merkmale_str(merkmale, "Länge", "Länge (cm)")
    width = _get_merkmale_str(merkmale, "Breite", "Breite (cm)")
    height = _get_merkmale_str(merkmale, "Höhe", "Höhe (cm)")
    depth = _get_merkmale_str(merkmale, "Tiefe", "Tiefe (cm)")
    
    if dim_items:
        b5 = f"Maße: {', '.join(dim_items)}"
    elif length or width or height:
        dims_list = []
        if length: dims_list.append(f"Länge {length}")
        if width: dims_list.append(f"Breite {width}")
        if height: dims_list.append(f"Höhe {height}")
        if depth: dims_list.append(f"Tiefe {depth}")
        b5 = f"Maße: {', '.join(dims_list)}"
    else:
        b5 = "Harmonische Proportionen: Perfekt dimensioniert für vielseitige Aufstellmöglichkeiten im Raum"

    bullets = [
        sanitize_text_for_warranty(_truncate_bullet(b1, 180)),
        sanitize_text_for_warranty(_truncate_bullet(b2, 180)),
        sanitize_text_for_warranty(_truncate_bullet(b3, 180)),
        sanitize_text_for_warranty(_truncate_bullet(b4, 180)),
        sanitize_text_for_warranty(_truncate_bullet(b5, 180)),
    ]
    bullets = [b for b in bullets if b]
    return bullets[:5]


def generate_seo_description(
    merkmale: dict[str, list[str]],
    product_name: str,
    fallback_desc: str = "",
) -> str:
    """
    Generates a structured, SEO-optimized plain text product description for Otto Market.
    NO HTML tags (no <strong>, no <br>), uses clean paragraph spacing and clear bullet points.
    Max length: 2000 characters.
    """
    title = (product_name or "").strip()
    
    # If fallback_desc has substantial content and merkmale is minimal, clean tags and return
    if len(merkmale) < 2 and fallback_desc and len(fallback_desc) > 50:
        cleaned = _clean_html_for_otto(fallback_desc)
        cleaned = sanitize_text_for_warranty(cleaned)
        if len(cleaned) > 1980:
            cleaned = cleaned[:1970].rstrip() + "..."
        return cleaned

    scope_vals_raw = _get_merkmale_vals(merkmale, "Lieferumfang", "Set enthält", "Produktart", "Typ")
    scope_vals = []
    for s in scope_vals_raw:
        s_c = re.sub(r'^(es wird alles laut produktbeschreibung geliefert[,;\s]*)+', '', s, flags=re.IGNORECASE).strip()
        s_c = re.sub(r'^(schlafzimmer\s*set[,;\s]*)+', '', s_c, flags=re.IGNORECASE).strip()
        if s_c:
            scope_vals.append(s_c)
    scope = ", ".join(scope_vals[:5])
    style_vals = _get_merkmale_vals(merkmale, "Stil", "Design")[:2]
    style = " / ".join(style_vals)
    mat_vals = _get_merkmale_vals(merkmale, "Material", "Materialart", "Bezug", "Gestell")[:3]
    materials = ", ".join(mat_vals)
    col_vals = _get_merkmale_vals(merkmale, "Farbe", "Farbton", "Gestellfarbe", "Bezugfarbe")[:2]
    colors = " / ".join(col_vals)
    seating_vals = _get_merkmale_vals(merkmale, "Sitzfläche", "Rückenlehne", "Polsterung", "Polsterdesign", "Armlehnen")[:4]
    seating = ", ".join(seating_vals)
    frame_vals = _get_merkmale_vals(merkmale, "Gestell", "Beine")[:2]
    frame = ", ".join(frame_vals)
    features_vals = _get_merkmale_vals(merkmale, "Besonderheiten", "Eigenschaften")[:3]
    features = ", ".join(features_vals)
    
    dim_items: list[str] = []
    for k, v in merkmale.items():
        if k.lower().startswith("maße") or k.lower().startswith("masse"):
            label = re.sub(r'^(maße|masse)\s*', '', k, flags=re.IGNORECASE).strip()
            val_s = ", ".join(v)
            dim_items.append(f"{label}: {val_s}" if label else f"Abmessungen: {val_s}")
    
    length = _get_merkmale_str(merkmale, "Länge", "Länge (cm)")
    width = _get_merkmale_str(merkmale, "Breite", "Breite (cm)")
    height = _get_merkmale_str(merkmale, "Höhe", "Höhe (cm)")
    depth = _get_merkmale_str(merkmale, "Tiefe", "Tiefe (cm)")
    liege = _clean_prefix_ca(_get_merkmale_str(merkmale, "Liegefläche"))
    
    sections: list[str] = []
    
    # 1. Title & Intro
    intro_sentences = []
    if style and colors:
        intro_sentences.append(f"Dieses Modell begeistert durch sein ansprechendes Design im {style}-Stil und die elegante Farbgebung in {colors}.")
    elif style:
        intro_sentences.append(f"Dieses Modell setzt stilvolle Akzente im zeitlosen {style}-Design in jedem Wohnbereich.")
    elif colors:
        intro_sentences.append(f"Das Möbelstück besticht durch seine geschmackvolle Farbgebung in {colors} und harmonische Optik.")
    else:
        intro_sentences.append("Ein elegantes Möbelstück, das zeitloses Design mit durchdachter Alltagstauglichkeit verbindet.")
        
    if materials:
        intro_sentences.append(f"Die sorgfältige Verarbeitung von {materials} garantiert eine edle Haptik sowie dauerhafte Freude an diesem Produkt.")
    
    intro_block = f"{title}\n\n" + " ".join(intro_sentences)
    sections.append(intro_block)
    
    # 2. Design & Komfort
    comfort_details = []
    if seating:
        comfort_details.append(f"Ausgestattet mit {seating} bietet das Möbelstück einen hervorragenden Sitz- und Liegekomfort.")
    if liege:
        comfort_details.append(f"Die komfortable Liegefläche (ca. {liege}) lädt zu erholsamen Ruhestunden ein.")
    if features:
        comfort_details.append(f"Besondere Highlights: {features}.")
    if not comfort_details:
        comfort_details.append("Die ergonomische Formgebung und die feine Linienführung schaffen ein angenehmes Wohlfühlambiente.")
    sections.append("Design & Komfort:\n" + " ".join(comfort_details))
    
    # 3. Materialien & Stabilität
    mat_details = []
    if frame:
        mat_details.append(f"Die stabile Konstruktion ({frame}) sorgt für absolute Standfestigkeit und Zuverlässigkeit.")
    else:
        mat_details.append("Die solide und präzise Verarbeitung gewährleistet dauerhafte Stabilität und hohe Belastbarkeit.")
    mat_details.append("Die Oberflächen zeichnen sich durch ihre Pflegeleichtigkeit und Langlebigkeit aus.")
    sections.append("Materialien & Stabilität:\n" + " ".join(mat_details))
    
    # 4. Maße & Produktdetails
    detail_lines = []
    if dim_items:
        for d in dim_items:
            detail_lines.append(f"- {d}")
    elif length or width or height:
        dim_str = " x ".join([x for x in [length, width, height] if x])
        detail_lines.append(f"- Abmessungen: ca. {dim_str} (L x B x H)")
    if depth:
        detail_lines.append(f"- Tiefe: ca. {depth}")
    if liege and not any("liegefläche" in d.lower() for d in dim_items):
        detail_lines.append(f"- Liegefläche: ca. {liege}")
    if materials:
        detail_lines.append(f"- Material: {materials}")
    if colors:
        detail_lines.append(f"- Farbe: {colors}")
    if scope:
        detail_lines.append(f"- Lieferumfang: {scope}")
        
    if not detail_lines:
        detail_lines.append("- Detaillierte Maße entnehmen Sie bitte der Produktabbildung")
        
    sections.append("Maße & Produktdetails:\n" + "\n".join(detail_lines))
    
    result = "\n\n".join(sections)
    result = _clean_html_for_otto(result)
    result = sanitize_text_for_warranty(result)
    if len(result) > 1980:
        result = result[:1970].rstrip() + "..."
    return result


def _build_attributes_and_bullets(
    mapping: dict[str, Any],
    category_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Build Otto product attributes and extract bullet points from mapping.
    Strictly filters against category schema (whitelisting) and cleans dimension units (cm).
    Ensures that attributes that do not belong to the category schema are never sent to Otto.
    """
    raw_attrs = mapping.get("attributes") or {}

    if not isinstance(raw_attrs, dict):
        raise TypeError("mapping['attributes'] must be a dict")

    target_cat = category_id or mapping.get("category_id") or mapping.get("category")
    allowed = None
    allowed_map = {}
    numeric_specs = {}
    if target_cat:
        from services.categories_repo import get_allowed_attribute_names, get_numeric_attributes_for_category
        allowed = get_allowed_attribute_names(target_cat)
        if allowed:
            allowed_map = {str(a).strip().lower(): str(a).strip() for a in allowed}
        numeric_specs = get_numeric_attributes_for_category(target_cat)

    from services.okb_sync import clean_dimension_value, DIMENSION_ATTR_NAMES

    attributes: list[dict[str, Any]] = []
    bullet_dict: dict[int, str] = {}

    for key, value in raw_attrs.items():
        key_str = str(key).strip()

        if not key_str:
            continue

        # Brand is represented separately as productDescription.brandId.
        if key_str in BRAND_KEYS:
            continue

        # Warranty-related attributes are explicitly forbidden.
        if is_forbidden_warranty_attribute(key_str):
            continue

        # Intercept Bullet Points and store them separately.
        if key_str.startswith("Bullet Point ") or key_str.startswith("bullet_point_"):
            try:
                clean_num = key_str.replace("Bullet Point ", "").replace("bullet_point_", "").strip()
                idx = int(clean_num)
                val_list = _normalize_attribute_values(value)
                if val_list:
                    bullet_dict[idx] = val_list[0]
            except ValueError:
                pass
            continue

        # Whitelist check: attribute must be valid for this category schema.
        # Strict enforcement: normalize casing to canonical schema name and reject anything not allowed.
        if allowed_map:
            canonical_key = allowed_map.get(key_str.lower())
            if not canonical_key:
                # Disallowed / extra attribute that does NOT belong to this category schema -> strictly skip
                continue
            key_str = canonical_key
        elif allowed is not None and key_str not in allowed:
            continue

        values = _normalize_attribute_values(value)

        # Clean numeric/dimension attributes to ensure clean numbers without units/suffixes reach Otto
        spec = numeric_specs.get(key_str) or numeric_specs.get(key_str.lower())
        if spec or key_str in DIMENSION_ATTR_NAMES:
            cleaned_vals = []
            for v in values:
                c = clean_dimension_value(v)
                if not c:
                    continue
                if spec and spec.get("type") == "INTEGER":
                    try:
                        c = str(int(float(c)))
                    except Exception:
                        pass
                cleaned_vals.append(c)
            values = cleaned_vals
        else:
            # Non-numeric text attributes must not exceed Otto limit of 70 characters
            values = [_truncate_text_safely(v, 70) for v in values if _truncate_text_safely(v, 70)]

        if not values:
            continue

        attributes.append(
            {
                "name": key_str,
                "values": values,
                "additional": False,
            }
        )

    # Ensure legal Geräteart laut ElektroG attribute if allowed by category schema and not already present
    has_elektrog = any(a.get("name") == "Geräteart laut ElektroG" for a in attributes)
    if not has_elektrog and allowed_map and "geräteart laut elektrog" in allowed_map:
        attributes.append({
            "name": "Geräteart laut ElektroG",
            "values": ["Produkt fällt nicht unter das ElektroG."],
            "additional": False,
        })

    # Ensure textile composition if category is textile/bedding and allowed by schema
    target_cat_lower = str(target_cat or "").lower()
    is_textile_cat = any(t in target_cat_lower for t in ["bett", "kissen", "textil", "wäsche", "waesche", "decke"])
    if is_textile_cat and allowed_map and "materialzusammensetzung" in allowed_map:
        has_mat = any(a.get("name") == allowed_map["materialzusammensetzung"] for a in attributes)
        if not has_mat:
            comp_val = raw_attrs.get("Bezug") or raw_attrs.get("Material") or raw_attrs.get("material") or "100% Polyester"
            comp_val_str = _truncate_text_safely(str(comp_val), 70)
            if comp_val_str:
                attributes.append({
                    "name": allowed_map["materialzusammensetzung"],
                    "values": [comp_val_str],
                    "additional": False,
                })

    # Sort bullet points by their index to ensure correct ordering 1 to 5.
    bullet_points = [bullet_dict[i] for i in sorted(bullet_dict.keys())]

    return attributes, bullet_points


def _normalize_image_url(
    image: Any,
    base_url: str = PUBLIC_BASE_URL,
) -> str | None:
    """
    Convert relative image paths into absolute public URLs.
    """
    if not isinstance(image, str):
        return None

    image = image.strip()

    if not image:
        return None

    if image.startswith(("http://", "https://")):
        return image

    if image.startswith("/"):
        return f"{base_url}{image}"

    return f"{base_url}/{image.lstrip('/')}"


def _build_media_assets(images: Any) -> list[dict[str, str]]:
    """
    Build Otto mediaAssets from product images.
    Capped at 10 images (OTTO Market API limit).
    Deduplicates URLs while preserving original gallery ordering.
    Filters invalid/empty/non-HTTP URLs.
    """
    if not images:
        return []

    if not isinstance(images, (list, tuple)):
        raise TypeError("images must be a list or tuple")

    media_assets: list[dict[str, str]] = []
    seen_locations: set[str] = set()

    for image in images:
        location = _normalize_image_url(image)

        if not location:
            continue

        if location in seen_locations:
            continue

        seen_locations.add(location)
        media_assets.append(
            {
                "type": "IMAGE",
                "location": location,
            }
        )

        if len(media_assets) >= 10:
            break

    return media_assets


def _resolve_description(
    aftercool_info: dict[str, Any],
    ean: str,
) -> str:
    """
    Resolve product description with name fallback.
    """
    name = str(aftercool_info.get("name") or "").strip()
    description = str(
        aftercool_info.get("description") or name
    ).strip()

    if not name:
        raise ValueError(f"Product name is missing for EAN {ean}")

    if not description:
        raise ValueError(
            f"Product description is missing for EAN {ean}"
        )

    return description


def round_msrp(value: float, standard_price: float = 0.0) -> float:
    """
    Rounds MSRP (UVP) value according to the following rule:
    Take the two digits before decimal point and determine whether they are closer to 49 or 99.
    - Closer to 49 (or tied at 74) -> replaces last two digits with 49.00
    - Closer to 99 -> replaces last two digits with 99.00
    Guarantees MSRP is never lower than standard_price (OTTO requirement).
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


def calculate_msrp(price: float) -> float:
    """
    Calculate MSRP (UVP) based on standard price and round to X49.00 / X99.00:
    - price > 5000: * 1.10
    - 2500 <= price <= 5000: * 1.18
    - 1000 <= price < 2500: * 1.25
    - price < 1000: * 1.35
    """
    if price > 5000:
        value = price * 1.10
    elif price >= 2500:
        value = price * 1.18
    elif price >= 1000:
        value = price * 1.25
    else:
        value = price * 1.35
    return round_msrp(value, standard_price=price)


def _build_pricing(
    price: Any,
    ean: str,
) -> dict[str, Any]:
    """
    Build Otto pricing section with standardPrice and MSRP (UVP).
    """
    amount = _parse_price(price, ean)
    msrp_amount = calculate_msrp(amount)

    return {
        "standardPrice": {
            "amount": amount,
            "currency": DEFAULT_CURRENCY,
        },
        "msrp": {
            "amount": msrp_amount,
            "currency": DEFAULT_CURRENCY,
        },
        "vat": DEFAULT_VAT,
    }


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


PRODUCT_SAFETY_COMPLIANCE = {
    "JV": {
        "productSafety": {
            "addresses": [
                {
                    "name": "AEA GmbH & Co. KG",
                    "address": "Am Flugplatz 28, 88483 Burgrieden",
                    "regionCode": "DE",
                    "roles": ["DISTRIBUTOR"],
                    "email": "info@jvmoebel.de",
                    "phone": "07392 - 93 78 44 0",
                    "url": "https://www.jvmoebel.de/Infos/Kontakt.htm",
                    "components": [],
                }
            ]
        }
    },
    "XL": {
        "productSafety": {
            "addresses": [
                {
                    "name": "XL MOEBEL GmbH",
                    "address": "Am Flugplatz 26, 88483 Burgrieden",
                    "regionCode": "DE",
                    "roles": ["DISTRIBUTOR"],
                    "email": "info@xlmoebel.de",
                    "phone": "+49 7392 9378188",
                    "url": "https://www.xlmoebel.de/Infos/Kontakt.htm",
                    "components": [],
                }
            ]
        }
    },
}


def format_otto_product(
    ean: str,
    aftercool_info: dict[str, Any],
    mapping: dict[str, Any],
    category_name: str,
    account_name: str = "JV",
) -> dict[str, Any]:
    """
    Convert internal product data into an Otto product payload.

    Raises:
        ValueError:
            When required product data is missing or invalid.
        TypeError:
            When input structures have unexpected types.
    """
    if not ean or not str(ean).strip():
        raise ValueError("EAN is required")

    if not isinstance(aftercool_info, dict):
        raise TypeError("aftercool_info must be a dict")

    if not isinstance(mapping, dict):
        raise TypeError("mapping must be a dict")

    category = str(category_name or "").strip()

    if not category:
        raise ValueError(f"Category is missing for EAN {ean}")

    ean = str(ean).strip()

    name = str(
        mapping.get("product_name")
        or aftercool_info.get("name")
        or ""
    ).strip()
    if not name:
        raise ValueError(f"Product name is missing for EAN {ean}")

    brand_id = _resolve_brand(mapping, ean)

    # 1. Parse merkmale from CustomItemSpecifics XML
    xml_str = aftercool_info.get("custom_item_specifics") or ""
    merkmale = parse_custom_item_specifics(xml_str)

    # Merge raw mapping attributes into merkmale if not already present
    raw_attrs = mapping.get("attributes") or {}
    if isinstance(raw_attrs, dict):
        for r_k, r_v in raw_attrs.items():
            if r_k not in merkmale and r_v:
                if isinstance(r_v, list):
                    merkmale[r_k] = [str(x).strip() for x in r_v if str(x).strip()]
                else:
                    merkmale[r_k] = [str(r_v).strip()]

    # 2. Build attributes and bullets strictly whitelisted for the category
    target_cat = mapping.get("category_id") or mapping.get("category") or category
    attributes, bullet_points = _build_attributes_and_bullets(mapping, category_id=target_cat)

    # Ensure Modellbezeichnung is in attributes for Otto Basic Data
    has_modell = any(a.get("name") == "Modellbezeichnung" for a in attributes)
    safe_name = _truncate_text_safely(name, 70)
    if not has_modell and safe_name:
        attributes.append({
            "name": "Modellbezeichnung",
            "values": [safe_name],
            "additional": False,
        })

    # 3. Generate structured SEO description (clean plain text, no HTML tags)
    fallback_desc = str(aftercool_info.get("description") or name).strip()
    description = generate_seo_description(merkmale, name, fallback_desc=fallback_desc)

    # Generate or sanitize 5 rich bullets ensuring strict warranty suppression and 180 chars limit
    if not bullet_points:
        bullet_points = generate_bullet_points(merkmale, name, description=description)
    else:
        bullet_points = [sanitize_text_for_warranty(_truncate_bullet(b, 180)) for b in bullet_points]
        bullet_points = [b for b in bullet_points if b][:5]
        if not bullet_points:
            bullet_points = generate_bullet_points(merkmale, name, description=description)

    media_assets = _build_media_assets(
        aftercool_info.get("images", [])
    )

    pricing = _build_pricing(
        mapping.get("price") or aftercool_info.get("price"),
        ean,
    )

    logistics = _build_logistics(aftercool_info, mapping, merkmale)

    acc_key = str(mapping.get("account_name") or account_name or "JV").upper()
    compliance = PRODUCT_SAFETY_COMPLIANCE.get(acc_key, PRODUCT_SAFETY_COMPLIANCE["JV"])

    return {
        "productReference": ean,
        "sku": ean,
        "ean": ean,
        "productDescription": {
            "category": category,
            "brandId": brand_id,
            "productLine": safe_name,
            "description": description,
            "attributes": attributes,
            "bulletPoints": bullet_points,
        },
        "mediaAssets": media_assets,
        "pricing": pricing,
        "logistics": logistics,
        "compliance": compliance,
    }
