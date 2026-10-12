import os
import json
import re
from openai import AsyncOpenAI
import openai
from sqlalchemy.orm import Session
import models
from services.validator import validate_product, get_category_by_id
from services.brand_guard import is_forbidden_warranty_attribute, sanitize_text_for_warranty
from collections import Counter, defaultdict
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

from dotenv import load_dotenv

load_dotenv()

MODEL_NAME = os.environ.get("MODEL_NAME", "gpt-4o-mini")

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
CATEGORIES_PATH = os.path.join(DATA_DIR, "categories.json")
GROUPS_PATH = os.path.join(DATA_DIR, "category_groups.json")

# Load data into memory once
CATEGORIES = []
GROUPS = {}

_client_cache = {}

def get_openai_client(api_key: str = None) -> AsyncOpenAI:
    effective_key = (api_key or os.getenv("OPENAI_API_KEY") or "").strip()
    if not effective_key:
        raise openai.OpenAIError("No OpenAI API key provided. Please set it in user settings or .env")
    if effective_key not in _client_cache:
        _client_cache[effective_key] = AsyncOpenAI(api_key=effective_key)
    return _client_cache[effective_key]

# Inverted index: word -> [(category_dict, score_weight)]
# score_weight: 3 for name, 1 for path
WORD_TO_CAT = defaultdict(list)

try:
    if os.path.exists(CATEGORIES_PATH):
        with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
            CATEGORIES = json.load(f)
    if os.path.exists(GROUPS_PATH):
        with open(GROUPS_PATH, "r", encoding="utf-8") as f:
            GROUPS = json.load(f)
except Exception as e:
    print("Warning: Could not load category data:", e)

def get_category_schema(category_id: str):
    cat = get_category_by_id(category_id)
    if not cat: return []
    
    group_id = str(cat.get("category_group_id"))
    group_data = GROUPS.get(group_id)
    if not group_data: return []
    
    raw_attrs = group_data.get("attributes", {}).get("attributes", [])
    schema = []
    for a in raw_attrs:
        raw_rel = a.get("relevance", "OPTIONAL")
        name = a.get("name")
        if is_forbidden_warranty_attribute(name):
            continue
        if name == "Ausführung":
            raw_rel = "OPTIONAL"
        elif name == "Marke" or name in {
            "Farbe", "Grundfarbe", "Farbe Korpus", "Farbe Korpus / Gestell",
            "Farbe Türen", "Farbe Schubladen", "Schubladen", "Anzahl Schubladen",
            "Anzahl Türen", "Art Türen", "Material", "Material Korpus", "Material Türen",
            "Set-Info", "Anzahl Füße", "Anzahl der Füße", "Farbe Füße",
            "Anzahl Griffe", "Farbe Griffe", "Design", "Serie",
            "Bezug", "Bezug Korpus", "Bezug Sitzfläche", "Bezug der Sitzfläche",
            "Bezug Armlehnen", "Bezug der Armlehnen", "Bezug Rückenlehne", "Bezug Rückenkissen",
            "Farbe Bettgestell", "Farbe Fußteil", "Farbe Kopfteil", "Farbe Kopf-/Fußteil", "Farbe Matratze",
            "Holzart", "Holzart Bettgestell", "Wohnraum", "Anzahl Teile",
            "Breite Liegefläche", "Länge Liegefläche", "Liegefläche",
            "Set beinhaltet", "Set-Typ", "Optik", "Optik Korpus",
            "Zielgruppe", "Lieferzustand", "Lieferumfang", "Art Herstellung",
            "Material Gestell", "Material Füße", "Material Griffe",
            "Art Füße", "Art Gestell", "Art Griffe", "Art Bett", "Art Sofa", "Art Tisch", "Art Stuhl",
            "Pflegehinweise", "Pflegehinweise Bezug", "Hinweis Maßangaben", "Montagehinweis", "Montagehinweise",
            "Geräteart laut ElektroG"
        }:
            raw_rel = "HIGH"
            
        rel_order = 3
        if raw_rel == "HIGH":
            rel_order = 1
        elif raw_rel == "RECOMMENDED":
            rel_order = 2
            
        raw_options = [v if isinstance(v, str) else v.get("value", str(v)) for v in a.get("allowedValues", [])] if a.get("allowedValues") else None
        if name in {"Holzart", "Holzart Bettgestell"} and not raw_options:
            raw_options = ["Buche", "Kiefer"]

        schema.append({
            "name": a.get("name"),
            "type": a.get("type"),
            "unit": a.get("unit"),
            "required": raw_rel == "HIGH",
            "relevance": raw_rel,
            "relevance_order": rel_order,
            "options": raw_options
        })
    return schema

STOP_WORDS = {"für", "und", "oder", "mit", "von", "in", "an", "auf", "der", "die", "das", "ein", "eine", "ist", "sind"}

def tokenize(text):
    if not text:
        return set()
    text = str(text).lower()
    words = re.findall(r'\b[a-z0-9äöüß]+\b', text)
    return set(w for w in words if w not in STOP_WORDS and len(w) > 1)

# Inverted index domain synonyms for furniture categories & sets
CATEGORY_SYNONYMS = {
    # 25778: Sitzgruppe (Chairs only / Multi-chair sets)
    25778: [
        "stuhl", "stühle", "stuehle", "stühlen", "esszimmerstuhl", "esszimmerstühle", 
        "schwingstuhl", "schwingstühle", "freischwinger", "klappstuhl", "klappstühle", 
        "armlehnstuhl", "polsterstuhl", "sitzgruppe", "stuhlset"
    ],
    # 25784: Essgruppe (Dining Table + Chairs)
    25784: [
        "essgruppe", "esstischgruppe", "esszimmertisch", "esszimmergruppe", "tischgruppe"
    ],
    # 25785: Polstergarnitur (Sofa / Upholstered sets)
    25785: [
        "polstergarnitur", "sofagarnitur", "couchgarnitur", "wohnlandschaft", "sitzecke", "sofaecke"
    ],
    # 20101: Badmöbel-Set
    20101: ["badmöbelset", "badezimmerset", "badset", "waschtischset"],
    # 20112: Garderoben-Set
    20112: ["garderobenset", "flurset", "dielenset"],
    # 24447: Schlafzimmer-Set
    24447: ["schlafzimmerset"],
    # 20110: Wohnzimmer-Set
    20110: ["wohnzimmerset", "wohnwandset"],
    # 14821: Büromöbel-Set
    14821: ["büromöbelset", "bueromoebelset", "arbeitszimmerset"],
    # 25776: Balkonset
    25776: ["balkonset", "balkonmöbelset"],
    # 25782: Garten-Essgruppe
    25782: ["gartenessgruppe", "gartenmöbelset"],
    # 25786: Gartenlounge-Set
    25786: ["gartenloungeset", "loungemöbelset", "gartenlounge"],
    # 25777: Bargruppe / 20105: Bar-Set
    25777: ["bargruppe", "bartischset", "barset"],
    20105: ["barset", "bartischset"],
}

# Build inverted index
def build_category_index():
    for c in CATEGORIES:
        cid = c.get("categoryId")
        cat_name = str(c.get("name", "")).lower()
        cat_path = str(c.get("path", "")).lower()
        
        name_tokens = tokenize(cat_name)
        path_tokens = tokenize(cat_path)
        
        for w in name_tokens:
            # "set" alone is too common across 400+ categories; give it low weight so it does not drown domain matches
            weight = 1 if w == "set" else 3
            WORD_TO_CAT[w].append((c, weight))
            
        for w in path_tokens:
            if w not in name_tokens: # prevent double counting if it's already in name
                WORD_TO_CAT[w].append((c, 1))

        if cid in CATEGORY_SYNONYMS:
            for syn in CATEGORY_SYNONYMS[cid]:
                for w in tokenize(syn):
                    WORD_TO_CAT[w].append((c, 5))

build_category_index()

def heuristic_category_prediction(title: str, product_info: str = "") -> str | None:
    text = f"{title} {product_info}".lower()
    
    # 1. Sofa / Polstergarnitur (takes precedence over chairs if sofa/couch is present)
    if any(w in text for w in ["polstergarnitur", "sofagarnitur", "couchgarnitur", "wohnlandschaft"]):
        return "25785"
    if any(w in text for w in ["sofa", "couch", "polster"]) and any(w in text for w in ["garnitur", "3+2", "3+2+1", "2+1", "wohnlandschaft"]):
        return "25785"
        
    # 2. Balkonset
    if "balkonset" in text or ("balkon" in text and any(w in text for w in ["set", "garnitur"])):
        return "25776"

    # 3. Garden lounge / lounge set
    if ("gartenlounge" in text or "loungeset" in text) or (any(w in text for w in ["garten", "outdoor"]) and "lounge" in text and any(w in text for w in ["set", "garnitur"])):
        return "25786" if any(w in text for w in ["garten", "outdoor"]) else "25781"

    # 4. Dining Table + Chairs -> Essgruppe (25784) or Garten-Essgruppe (25782)
    is_garden = any(w in text for w in ["garten", "outdoor", "terrasse"])
    has_table = any(w in text for w in ["esstisch", "küchentisch", "esszimmertisch", "tisch"])
    has_chairs = any(w in text for w in ["stuhl", "stühle", "stühlen", "stuehle", "schwingstuhl", "schwingstühle", "freischwinger", "armlehnstuhl"])
    
    if "garten-essgruppe" in text or ("garten" in text and "essgruppe" in text):
        return "25782"
    if "essgruppe" in text or "esstischgruppe" in text:
        return "25784"
        
    if has_table and has_chairs:
        # Check that table is part of the set, not "passend für Tisch"
        if not re.search(r'\b(für|passend\s+(zu|für))\s+(den\s+)?(ess)?tisch\b', text):
            return "25782" if is_garden else "25784"
            
    # 5. Only Chairs (multi-piece / set of chairs) -> Sitzgruppe (25778)
    if has_chairs and not has_table:
        is_chair_set = (
            any(w in text for w in ["set", "gruppe", "garnitur", "tlg", "teilig", "stück", "stueck"])
            or re.search(r'\b(\d+)\s*(er|-er)?\s*(-|\s)?(set|stühle|stuehle|stuhl|pack)\b', text)
            or re.search(r'\b(2|4|6|8)\s*x\s+', text)
            or "stühle" in text or "stuehle" in text or "stühlen" in text
        )
        if is_chair_set:
            return "25778"  # Sitzgruppe

    # 6. Specific Room Furniture Sets
    has_set_indicator = any(w in text for w in ["set", "garnitur", "komplett", "tlg", "teilig"])
    if has_set_indicator:
        if any(w in text for w in ["badmöbel", "badezimmer", "waschtisch"]):
            return "20101" # Badmöbel-Set
        if any(w in text for w in ["garderobe", "garderoben", "diele", "flur"]):
            return "20112" # Garderoben-Set
        if any(w in text for w in ["schlafzimmer"]):
            return "24447" # Schlafzimmer-Set
        if any(w in text for w in ["wohnzimmer", "wohnwand"]):
            return "20110" # Wohnzimmer-Set
        if any(w in text for w in ["bar", "bartisch", "stehtisch"]) and any(w in text for w in ["hocker", "barhocker", "set", "gruppe"]):
            return "25777" if "gruppe" in text else "20105" # Bargruppe / Bar-Set

    return None

@retry(wait=wait_exponential(multiplier=1, min=4, max=15), stop=stop_after_attempt(3), retry=retry_if_exception_type(openai.OpenAIError), reraise=True)
async def generate_search_keywords(title: str, custom_api_key: str = None) -> list:
    prompt = f"""
    You are an expert in e-commerce categorization.
    Given the product title: "{title}"
    Generate a list of 5 related German keywords, synonyms, or generic terms that describe the type of product this is.
    Output ONLY a JSON array of strings. No markdown formatting.
    """
    schema = {
        "name": "keyword_generation",
        "schema": {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"}
                }
            },
            "required": ["keywords"],
            "additionalProperties": False
        },
        "strict": True
    }
    
    try:
        local_client = get_openai_client(custom_api_key)
    except openai.OpenAIError as e:
        print(f"Error getting OpenAI client: {e}")
        return []
    try:
        response = await local_client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_schema", "json_schema": schema},
            temperature=0.0
        )
        res = json.loads(response.choices[0].message.content)
        return res.get("keywords", [])
    except openai.OpenAIError as e:
        print(f"OpenAI API Error in keywords: {e}")
        raise e
    except Exception as e:
        print(f"Error generating keywords: {e}")
        return []

def get_top_categories(title: str, keywords: list = None, n=100):
    if not CATEGORIES:
        return []
        
    search_text = title
    if keywords:
        search_text += " " + " ".join(keywords)
        
    title_tokens = tokenize(search_text)
    if not title_tokens:
        return CATEGORIES[:n]
        
    # Dictionary to keep track of scores: category_id -> (score, category_dict)
    # Using category_id as key because dictionaries are unhashable
    cat_scores = {}
    
    for token in title_tokens:
        if token in WORD_TO_CAT:
            for cat, weight in WORD_TO_CAT[token]:
                cid = cat.get("categoryId")
                if cid not in cat_scores:
                    cat_scores[cid] = [0, cat]
                cat_scores[cid][0] += weight
                
    # Boost domain-specific categories based on full title analysis
    title_lower = title.lower()
    has_chairs = any(w in title_lower for w in ["stuhl", "stühle", "stühlen", "stuehle", "schwingstuhl", "schwingstühle", "freischwinger", "armlehnstuhl"])
    has_table = any(w in title_lower for w in ["esstisch", "küchentisch", "esszimmertisch", "tisch"])
    
    from services.categories_repo import get_category_by_id
    
    # Chairs only: boost Sitzgruppe (25778)
    if has_chairs and not has_table:
        c_sitz = get_category_by_id("25778")
        if c_sitz:
            if 25778 not in cat_scores:
                cat_scores[25778] = [0, c_sitz]
            cat_scores[25778][0] += 50
            
    # Table + Chairs: boost Essgruppe (25784)
    if has_chairs and has_table:
        c_ess = get_category_by_id("25784")
        if c_ess:
            if 25784 not in cat_scores:
                cat_scores[25784] = [0, c_ess]
            cat_scores[25784][0] += 50

    # Furniture sets: boost relevant set category
    if any(w in title_lower for w in ["set", "garnitur", "gruppe", "tlg", "teilig", "komplett"]):
        set_matches = [
            (["badmöbel", "badezimmer", "waschtisch"], "20101"),
            (["garderobe", "garderoben", "diele", "flur"], "20112"),
            (["schlafzimmer"], "24447"),
            (["wohnzimmer", "wohnwand"], "20110"),
            (["sofa", "couch", "polster"], "25785"),
            (["balkon"], "25776"),
        ]
        for keywords_match, target_cid in set_matches:
            if any(kw in title_lower for kw in keywords_match):
                cat_obj = get_category_by_id(target_cid)
                cid_int = int(target_cid)
                if cat_obj:
                    if cid_int not in cat_scores:
                        cat_scores[cid_int] = [0, cat_obj]
                    cat_scores[cid_int][0] += 50
                
    if not cat_scores:
        return CATEGORIES[:n]
        
    # Sort by score descending
    scores = sorted(cat_scores.values(), key=lambda x: x[0], reverse=True)
    
    return [c for score, c in scores[:n]]

def format_product_info(title: str, description: str, specifics: str, gallery_url: str, picture_urls: str) -> str:
    product_info = f"Title: {title}"
    if description:
        product_info += f"\nDescription: {description}"
    if specifics:
        product_info += f"\nItem Specifics: {specifics}"
    if gallery_url:
        product_info += f"\nGallery URL: {gallery_url}"
    if picture_urls:
        product_info += f"\nPicture URLs: {picture_urls}"
    return product_info

@retry(wait=wait_exponential(multiplier=1, min=4, max=15), stop=stop_after_attempt(3), retry=retry_if_exception_type(openai.OpenAIError), reraise=True)
async def predict_category(product_info: str, title: str, custom_api_key: str = None) -> str:
    # 1. Check high-confidence heuristic rule first
    heuristic_id = heuristic_category_prediction(title, product_info)
    if heuristic_id:
        from services.categories_repo import get_category_by_id
        cat = get_category_by_id(heuristic_id)
        if cat:
            return str(cat.get("categoryId", heuristic_id))

    keywords = await generate_search_keywords(title, custom_api_key)
    top_categories = get_top_categories(title, keywords, n=100)
    categories_list_str = "\n".join([f"{c.get('categoryId')}: {c.get('path', '')} > {c.get('name', '')}" for c in top_categories])

    prompt = f"""
You are an expert product categorizer for a marketplace.
{product_info}

Here is a list of the most relevant categories (ID: Path):
{categories_list_str}

CRITICAL CATEGORIZATION RULES FOR FURNITURE & SETS:
1. CHAIRS ONLY / CHAIR SET:
   - If the product consists of chairs only (e.g., "2er-Set Esszimmerstühle", "4er-Set Stühle", "Stuhl-Set", "Freischwinger 6er Set") without a dining table, the category MUST be "Sitzgruppe" (ID: 25778). Do NOT classify multi-chair sets as single "Stuhl" or "Esszimmerstuhl".
2. DINING TABLE + CHAIRS:
   - If the product consists of a dining table AND chairs (e.g., "Esstisch mit 4 Stühlen", "Essgruppe", "Esszimmergarnitur"), the category MUST be "Essgruppe" (ID: 25784). Do NOT classify as just a table ("Esstisch").
3. SETS & BUNDLES (contains "Set", "Garnitur", "3-tlg.", "x-teilig", or multiple combined furniture items):
   - When a product is a multi-piece furniture set or bundle, you MUST choose the corresponding multi-piece SET or GARNITUR category (such as "Badmöbel-Set" [20101], "Schlafzimmer-Set" [24447], "Wohnzimmer-Set" [20110], "Garderoben-Set" [20112], "Polstergarnitur" [25785], "Essgruppe" [25784], "Sitzgruppe" [25778], "Bar-Set" [20105], "Balkonset" [25776], "Gartenlounge-Set" [25786], "Regal-Set" [20107], "Schrank-Set" [20103]) rather than classifying it as an individual item (e.g. do NOT pick just 'Schrank', 'Spiegel', 'Bett', or 'Sofa').

Based on the product information and rules above, choose the single most appropriate category ID for this product from the list above.
"""

    category_schema = {
        "name": "category_selection",
        "schema": {
            "type": "object",
            "properties": {
                "reasoning": {
                    "type": "string",
                    "description": "Think step-by-step about why the selected category fits best based on the title and specifics."
                },
                "category_id": {
                    "type": "string",
                    "description": "The selected category ID from the provided list"
                }
            },
            "required": ["reasoning", "category_id"],
            "additionalProperties": False
        },
        "strict": True
    }

    if not custom_api_key:
        print("Error: No API key provided")
        return None
    local_client = get_openai_client(custom_api_key)
    try:
        response = await local_client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_schema", "json_schema": category_schema},
            temperature=0.0
        )
        cat_result = json.loads(response.choices[0].message.content)
        predicted_id = str(cat_result.get("category_id", "")).strip()
        
        from services.categories_repo import get_category_by_id
        cat = get_category_by_id(predicted_id)
        if cat:
            return str(cat.get("categoryId", predicted_id))
        return predicted_id
    except openai.OpenAIError as e:
        print(f"OpenAI API Error in category: {e}")
        raise e
    except Exception as e:
        print(f"Error calling OpenAI for category: {e}")
        return None

OTTO_GRUNDFARBEN = [
    'beige', 'blau', 'braun', 'bunt', 'gelb', 'goldfarben', 'grau', 
    'grün', 'lila', 'natur', 'orange', 'rosa', 'rot', 'schwarz', 
    'silberfarben', 'transparent', 'weiß'
]

COLOR_KEYWORD_TO_GRUNDFARBE = {
    # Whites
    'weiß': 'weiß', 'weiss': 'weiß', 'white': 'weiß', 'schneeweiß': 'weiß', 'reinweiß': 'weiß', 'polarweiß': 'weiß',
    # Blacks
    'schwarz': 'schwarz', 'black': 'schwarz', 'ebenholz': 'schwarz',
    # Grays & Metallics
    'grau': 'grau', 'grey': 'grau', 'gray': 'grau', 'anthrazit': 'grau', 'schiefer': 'grau', 'graphit': 'grau',
    'beton': 'grau', 'silber': 'silberfarben', 'silberfarben': 'silberfarben', 'chrom': 'silberfarben', 'edelstahl': 'silberfarben',
    # Browns & Wood species
    'braun': 'braun', 'brown': 'braun', 'eiche': 'braun', 'walnuss': 'braun', 'nussbaum': 'braun',
    'wenge': 'braun', 'sonoma': 'braun', 'buche': 'braun', 'teak': 'braun', 'kastanie': 'braun',
    'mahagoni': 'braun', 'holz': 'braun', 'dunkelbraun': 'braun', 'hellbraun': 'braun', 'mokka': 'braun',
    'schoko': 'braun', 'akazie': 'braun', 'pinie': 'braun', 'kiefer': 'braun', 'esche': 'braun',
    # Beiges & Creams
    'beige': 'beige', 'creme': 'beige', 'sand': 'beige', 'taupe': 'beige', 'elfenbein': 'beige',
    'champagner': 'beige', 'vanille': 'beige', 'ivory': 'beige',
    # Natur
    'natur': 'natur', 'nature': 'natur', 'rattan': 'natur', 'bambus': 'natur', 'jute': 'natur',
    # Blues
    'blau': 'blau', 'blue': 'blau', 'dunkelblau': 'blau', 'marine': 'blau', 'navy': 'blau',
    'petrol': 'blau', 'royalblau': 'blau', 'hellblau': 'blau', 'türkis': 'türkis', 'cyan': 'türkis', 'aqua': 'türkis',
    # Greens
    'grün': 'grün', 'gruen': 'grün', 'green': 'grün', 'oliv': 'grün', 'smaragd': 'grün', 'salbei': 'grün',
    'moos': 'grün', 'mint': 'grün', 'khaki': 'grün',
    # Reds
    'rot': 'rot', 'red': 'rot', 'weinrot': 'rot', 'bordeaux': 'rot', 'rubin': 'rot', 'kirsch': 'rot',
    # Pinks & Purples
    'rosa': 'rosa', 'pink': 'rosa', 'altrosa': 'rosa', 'lila': 'lila', 'violett': 'lila', 'purple': 'lila',
    'flieder': 'lila', 'lavendel': 'lila', 'brombeer': 'lila', 'aubergine': 'lila', 'magenta': 'lila',
    # Yellows & Oranges
    'gelb': 'gelb', 'yellow': 'gelb', 'senf': 'gelb', 'ocker': 'gelb', 'gold': 'goldfarben', 'goldfarben': 'goldfarben',
    'messing': 'goldfarben', 'orange': 'orange', 'terrakotta': 'orange', 'rost': 'orange', 'kupfer': 'orange',
    # Multi / Transparent
    'bunt': 'bunt', 'mehrfarbig': 'bunt', 'farbig': 'bunt', 'marmoriert': 'bunt', 'patchwork': 'bunt',
    'transparent': 'transparent', 'glas': 'transparent', 'klar': 'transparent'
}

def resolve_image_url(gallery_url: str = None, picture_urls: str = None) -> str:
    """Finds the first valid HTTP/HTTPS image URL from gallery_url or picture_urls."""
    if gallery_url and str(gallery_url).strip().startswith("http"):
        return str(gallery_url).strip()
    if picture_urls:
        for part in re.split(r'[;,\s\n\r]+', str(picture_urls)):
            p = part.strip()
            if p.startswith("http"):
                return p
    return None

def match_closest_option(val: str, options: list) -> str:
    if not val or not options:
        return None
    val_lower = val.strip().lower()
    for opt in options:
        if str(opt).strip().lower() == val_lower:
            return str(opt)
    for opt in options:
        opt_str = str(opt).strip().lower()
        if opt_str in val_lower or val_lower in opt_str:
            return str(opt)
    return None

def map_to_otto_grundfarbe(color_name: str, allowed_options: list = None) -> str:
    if not color_name:
        return None
    c_lower = str(color_name).strip().lower()
    
    # 1. Direct match in allowed options
    if allowed_options:
        for opt in allowed_options:
            if str(opt).strip().lower() == c_lower:
                return str(opt)
                
    # 2. Search keyword dictionary
    found_gf = None
    for kw, gf in COLOR_KEYWORD_TO_GRUNDFARBE.items():
        if kw in c_lower:
            found_gf = gf
            break
            
    if not found_gf:
        found_gf = "bunt"
        
    if allowed_options:
        for opt in allowed_options:
            if str(opt).strip().lower() == found_gf:
                return str(opt)
        return allowed_options[0] if allowed_options else found_gf
    return found_gf

def enforce_furniture_color_rules(extracted_attrs: dict, cat_schema: list, product_info: str = None, cat_name: str = None) -> dict:
    """
    Deterministic safety cascade & logical furniture attribute deduction:
    1. Farbe = dominant color
    2. Grundfarbe = canonical Otto base color corresponding to Farbe
    3. Farbe Korpus = same as Farbe
    4. If drawers present -> Farbe Schubladen = Farbe
    5. If doors present -> Farbe Türen = Farbe
    6. Holzart / Holzart Bettgestell -> strictly Buche or Kiefer
    7. Wohnraum -> deduced logically from category / title (Schlafzimmer, Wohnzimmer, etc.)
    8. Liegefläche -> parsed into pure numbers for Breite Liegefläche & Länge Liegefläche
    9. Anzahl Teile -> counted for sets or 1 for single pieces
    10. Material -> synthesized from Material Korpus and Bezug
    """
    if extracted_attrs is None or not isinstance(extracted_attrs, dict):
        return extracted_attrs
        
    schema_map = {a["name"]: a for a in cat_schema}
    
    # Combined context text for keyword & dimension analysis
    combined_text = f"{product_info or ''} {cat_name or ''} {extracted_attrs.get('Material', '')} {extracted_attrs.get('Material Korpus', '')} {extracted_attrs.get('Material Gestell', '')} {extracted_attrs.get('reasoning', '')}".lower()
    
    farbe = extracted_attrs.get("Farbe")
    if farbe and str(farbe).strip() and str(farbe).lower() != "null":
        farbe_val = str(farbe).strip()
        
        # A. Grundfarbe synchronization
        if "Grundfarbe" in schema_map:
            current_gf = extracted_attrs.get("Grundfarbe")
            if not current_gf or str(current_gf).strip().lower() == "null":
                gf_options = schema_map["Grundfarbe"].get("options") or OTTO_GRUNDFARBEN
                mapped_gf = map_to_otto_grundfarbe(farbe_val, gf_options)
                if mapped_gf:
                    extracted_attrs["Grundfarbe"] = mapped_gf

        # B. Farbe Korpus synchronization
        korpus_key = "Farbe Korpus" if "Farbe Korpus" in schema_map else ("Farbe Korpus / Gestell" if "Farbe Korpus / Gestell" in schema_map else None)
        if korpus_key:
            current_fk = extracted_attrs.get(korpus_key)
            if not current_fk or str(current_fk).strip().lower() == "null":
                korpus_opts = schema_map[korpus_key].get("options")
                if korpus_opts:
                    matched = match_closest_option(farbe_val, korpus_opts)
                    extracted_attrs[korpus_key] = matched or farbe_val
                else:
                    extracted_attrs[korpus_key] = farbe_val

        # B2. Bed components color cascade (Farbe Bettgestell, Farbe Fußteil, Farbe Kopfteil, etc.)
        bed_color_keys = [
            "Farbe Bettgestell",
            "Farbe Fußteil",
            "Farbe Kopfteil",
            "Farbe Kopf-/Fußteil",
            "Farbe Seitenteil",
            "Farbe Seitenteile",
            "Farbe Unterbox"
        ]
        for bed_key in bed_color_keys:
            if bed_key in schema_map:
                current_bed_col = extracted_attrs.get(bed_key)
                if not current_bed_col or str(current_bed_col).strip().lower() == "null":
                    b_opts = schema_map[bed_key].get("options")
                    if b_opts:
                        matched = match_closest_option(farbe_val, b_opts)
                        extracted_attrs[bed_key] = matched or farbe_val
                    else:
                        extracted_attrs[bed_key] = farbe_val
    else:
        farbe_val = None

    # B3. Farbe Matratze check (50/50 probability - evaluated by AI, normalize option if set)
    if "Farbe Matratze" in schema_map:
        current_fm = extracted_attrs.get("Farbe Matratze")
        if current_fm and str(current_fm).strip().lower() != "null":
            fm_opts = schema_map["Farbe Matratze"].get("options")
            if fm_opts:
                matched = match_closest_option(str(current_fm).strip(), fm_opts)
                if matched:
                    extracted_attrs["Farbe Matratze"] = matched

    # B4. Bezug (Cover / Upholstery) synchronization & cascade
    bezug_target_keys = [
        "Bezug Korpus",
        "Bezug Sitzfläche",
        "Bezug der Sitzfläche",
        "Bezug Armlehnen",
        "Bezug der Armlehnen",
        "Bezug Rückenlehne",
        "Bezug Rückenkissen",
        "Bezug Rückenfläche",
        "Bezug Bettgestell",
        "Bezug Kopfteil",
        "Bezug Fußteil",
        "Bezug Kopf-/Fußteil",
        "Bezug Seitenteil",
        "Bezug Unterbox"
    ]
    
    bezug = extracted_attrs.get("Bezug")
    if not bezug or str(bezug).strip().lower() == "null":
        # Check if any component Bezug was extracted
        for b_cand in bezug_target_keys:
            cand_val = extracted_attrs.get(b_cand)
            if cand_val and str(cand_val).strip().lower() != "null":
                bezug = str(cand_val).strip()
                if "Bezug" in schema_map:
                    extracted_attrs["Bezug"] = bezug
                break

    if bezug and str(bezug).strip().lower() != "null":
        bezug_val = str(bezug).strip()
        if "Bezug" in schema_map and schema_map["Bezug"].get("options"):
            matched_b = match_closest_option(bezug_val, schema_map["Bezug"].get("options"))
            if matched_b:
                bezug_val = matched_b
                extracted_attrs["Bezug"] = matched_b

        for b_key in bezug_target_keys:
            if b_key in schema_map:
                current_b = extracted_attrs.get(b_key)
                if not current_b or str(current_b).strip().lower() == "null":
                    b_opts = schema_map[b_key].get("options")
                    if b_opts:
                        matched = match_closest_option(bezug_val, b_opts)
                        extracted_attrs[b_key] = matched or bezug_val
                    else:
                        extracted_attrs[b_key] = bezug_val

    # C. Drawers check
    has_drawers = extracted_attrs.get("has_drawers")
    if has_drawers is None:
        anzahl_schub = extracted_attrs.get("Anzahl Schubladen")
        schub_status = str(extracted_attrs.get("Schubladen", "")).lower()
        if anzahl_schub is not None and str(anzahl_schub).strip() not in ["0", "ohne", "null", ""]:
            has_drawers = True
        elif "mit schublade" in schub_status or schub_status == "ja":
            has_drawers = True
        elif "ohne schublade" in schub_status or "keine schublade" in schub_status or schub_status in ["nein", "0"]:
            has_drawers = False
        else:
            reasoning = str(extracted_attrs.get("reasoning", "")).lower()
            if re.search(r'\b(no|without|keine|ohne)\b.*?\b(drawer|schublade)', reasoning):
                has_drawers = False
            elif re.search(r'\b(has|with|mit|features|includes|\d+)\b.*?\b(drawer|schublade)', reasoning):
                has_drawers = True
            else:
                has_drawers = False
    
    if "Farbe Schubladen" in schema_map:
        current_fs = extracted_attrs.get("Farbe Schubladen")
        if has_drawers and farbe_val and (not current_fs or str(current_fs).strip().lower() == "null"):
            fs_opts = schema_map["Farbe Schubladen"].get("options")
            if fs_opts:
                matched = match_closest_option(farbe_val, fs_opts)
                extracted_attrs["Farbe Schubladen"] = matched or farbe_val
            else:
                extracted_attrs["Farbe Schubladen"] = farbe_val
        elif not has_drawers and (not current_fs or str(current_fs).strip().lower() == "null"):
            extracted_attrs["Farbe Schubladen"] = None

    # D. Doors check
    has_doors = extracted_attrs.get("has_doors")
    if has_doors is None:
        anzahl_tueren = extracted_attrs.get("Anzahl Türen")
        tueren_status = str(extracted_attrs.get("Türen", "")).lower()
        if anzahl_tueren is not None and str(anzahl_tueren).strip() not in ["0", "ohne", "null", ""]:
            has_doors = True
        elif "mit tür" in tueren_status or tueren_status == "ja":
            has_doors = True
        elif "ohne tür" in tueren_status or "keine tür" in tueren_status or tueren_status in ["nein", "0"]:
            has_doors = False
        else:
            reasoning = str(extracted_attrs.get("reasoning", "")).lower()
            if re.search(r'\b(no|without|keine|ohne)\b.*?\b(door|tür)', reasoning):
                has_doors = False
            elif re.search(r'\b(has|with|mit|features|includes|\d+)\b.*?\b(door|tür)', reasoning):
                has_doors = True
            else:
                has_doors = False
    
    if "Farbe Türen" in schema_map:
        current_ft = extracted_attrs.get("Farbe Türen")
        if has_doors and farbe_val and (not current_ft or str(current_ft).strip().lower() == "null"):
            ft_opts = schema_map["Farbe Türen"].get("options")
            if ft_opts:
                matched = match_closest_option(farbe_val, ft_opts)
                extracted_attrs["Farbe Türen"] = matched or farbe_val
            else:
                extracted_attrs["Farbe Türen"] = farbe_val
        elif not has_doors and (not current_ft or str(current_ft).strip().lower() == "null"):
            extracted_attrs["Farbe Türen"] = None

    # E. Feet / Legs check
    has_legs = extracted_attrs.get("has_legs")
    anzahl_fuesse = extracted_attrs.get("Anzahl Füße") or extracted_attrs.get("Anzahl der Füße")
    if has_legs is None:
        if anzahl_fuesse is not None and str(anzahl_fuesse).strip() not in ["0", "ohne", "null", ""]:
            has_legs = True
        else:
            reasoning = str(extracted_attrs.get("reasoning", "")).lower()
            if re.search(r'\b(no|without|keine|ohne)\b.*?\b(foot|feet|leg|fuß|füße|beine)', reasoning):
                has_legs = False
            elif re.search(r'\b(has|with|mit|features|includes|\d+)\b.*?\b(foot|feet|leg|fuß|füße|beine)', reasoning):
                has_legs = True
            else:
                has_legs = True

    if "Farbe Füße" in schema_map:
        current_ff = extracted_attrs.get("Farbe Füße")
        if not has_legs or (anzahl_fuesse is not None and str(anzahl_fuesse).strip() in ["0", "ohne", "ohne Füße"]):
            extracted_attrs["Farbe Füße"] = None
        elif current_ff and str(current_ff).strip().lower() != "null":
            ff_opts = schema_map["Farbe Füße"].get("options")
            if ff_opts:
                matched = match_closest_option(str(current_ff), ff_opts)
                extracted_attrs["Farbe Füße"] = matched or str(current_ff).strip()

    # F. Handles check
    has_handles = extracted_attrs.get("has_handles")
    anzahl_griffe = extracted_attrs.get("Anzahl Griffe")
    if has_handles is None:
        if anzahl_griffe is not None and str(anzahl_griffe).strip() not in ["0", "ohne", "grifflos", "null", ""]:
            has_handles = True
        else:
            reasoning = str(extracted_attrs.get("reasoning", "")).lower()
            if re.search(r'\b(no|without|keine|ohne|grifflos)\b.*?\b(handle|griff)', reasoning):
                has_handles = False
            elif re.search(r'\b(has|with|mit|features|includes|\d+)\b.*?\b(handle|griff)', reasoning):
                has_handles = True
            else:
                has_handles = False

    if "Farbe Griffe" in schema_map:
        current_fg = extracted_attrs.get("Farbe Griffe")
        if not has_handles or (anzahl_griffe is not None and str(anzahl_griffe).strip() in ["0", "ohne", "grifflos"]):
            extracted_attrs["Farbe Griffe"] = None
        elif current_fg and str(current_fg).strip().lower() != "null":
            fg_opts = schema_map["Farbe Griffe"].get("options")
            if fg_opts:
                matched = match_closest_option(str(current_fg), fg_opts)
                extracted_attrs["Farbe Griffe"] = matched or str(current_fg).strip()

    # G. Design normalization (strictly Modern vs Klassisch)
    if "Design" in schema_map:
        current_des = extracted_attrs.get("Design")
        des_lower = str(current_des or "").strip().lower()
        if any(w in des_lower for w in ["klassisch", "classic", "barock", "vintage", "antik", "chesterfield", "retro"]):
            canonical_des = "Klassisch"
        else:
            canonical_des = "Modern"
        
        des_opts = schema_map["Design"].get("options")
        if des_opts:
            matched = match_closest_option(canonical_des, des_opts)
            extracted_attrs["Design"] = matched or des_opts[0]
        else:
            extracted_attrs["Design"] = canonical_des

    # H. Clean Serie & Set-Info
    if "Serie" in schema_map and extracted_attrs.get("Serie"):
        extracted_attrs["Serie"] = str(extracted_attrs["Serie"]).strip().strip('"\'')
    if "Set-Info" in schema_map and extracted_attrs.get("Set-Info"):
        extracted_attrs["Set-Info"] = str(extracted_attrs["Set-Info"]).strip().strip('"\'')

    # I. Holzart normalization & deterministic selection (Buche / Kiefer)
    for h_key in ["Holzart", "Holzart Bettgestell"]:
        if h_key in schema_map:
            h_opts = schema_map[h_key].get("options") or ["Buche", "Kiefer"]
            current_h = extracted_attrs.get(h_key)
            if current_h and str(current_h).strip().lower() != "null":
                matched = match_closest_option(str(current_h).strip(), h_opts)
                if matched:
                    extracted_attrs[h_key] = matched
                else:
                    extracted_attrs[h_key] = match_closest_option("Buche", h_opts) or "Buche"
            else:
                # If null/empty, check text for pine vs hardwood/beech
                if any(w in combined_text for w in ["kiefer", "pinie", "kiefernholz", "nadelholz"]):
                    extracted_attrs[h_key] = match_closest_option("Kiefer", h_opts) or "Kiefer"
                else:
                    # Standard default in German furniture: Buche (beech)
                    extracted_attrs[h_key] = match_closest_option("Buche", h_opts) or "Buche"

    # J. Wohnraum (Living space / Room) deduction
    if "Wohnraum" in schema_map:
        current_w = extracted_attrs.get("Wohnraum")
        w_opts = schema_map["Wohnraum"].get("options")
        if current_w and str(current_w).strip().lower() != "null":
            if w_opts:
                matched_w = match_closest_option(str(current_w).strip(), w_opts)
                if matched_w:
                    extracted_attrs["Wohnraum"] = matched_w
        else:
            deduced_room = None
            if any(w in combined_text for w in ["schlaf", "bett", "nachttisch", "kleiderschrank", "matratze", "topper", "lattenrost", "schlafzimmer"]):
                deduced_room = "Schlafzimmer"
            elif any(w in combined_text for w in ["wohn", "sofa", "couch", "polster", "wohnwand", "couchtisch", "fernseh", "tv-", "sessel", "wohnlandschaft"]):
                deduced_room = "Wohnzimmer"
            elif any(w in combined_text for w in ["ess", "esstisch", "essgruppe", "sitzgruppe", "stuhl", "stühle", "stuehle", "barstuhl", "barhocker", "tischgruppe", "esszimmer"]):
                deduced_room = "Esszimmer"
            elif any(w in combined_text for w in ["büro", "buero", "arbeit", "schreib", "schreibtisch", "drehstuhl", "bürostuhl", "rollcontainer", "arbeitszimmer"]):
                deduced_room = "Arbeitszimmer"
            elif any(w in combined_text for w in ["flur", "diele", "garderob", "schuhschrank", "schuhkipper"]):
                deduced_room = "Flur/Diele"
            elif any(w in combined_text for w in ["bad", "badezimmer", "waschtisch", "spiegelschrank"]):
                deduced_room = "Badezimmer"
            elif any(w in combined_text for w in ["kind", "jugend", "baby", "kinderzimmer"]):
                deduced_room = "Kinderzimmer"
            elif any(w in combined_text for w in ["garten", "outdoor", "balkon", "terrasse", "lounge"]):
                deduced_room = "Garten"
            else:
                deduced_room = "Wohnzimmer"
            
            if w_opts:
                matched_w = match_closest_option(deduced_room, w_opts)
                extracted_attrs["Wohnraum"] = matched_w or deduced_room
            else:
                extracted_attrs["Wohnraum"] = deduced_room

    # K. Liegefläche dimensions parsing (e.g. 180 x 200 cm)
    if any(k in schema_map for k in ["Breite Liegefläche", "Länge Liegefläche", "Liegefläche"]):
        m_dim = re.search(r'(?:liegefl[äa]che|ma[ßs]e|abmessungen)?[^\d]*?(\d{2,3})\s*(?:x|\*|/)\s*(\d{2,3})', combined_text, re.IGNORECASE)
        if m_dim:
            d1, d2 = int(m_dim.group(1)), int(m_dim.group(2))
            w = min(d1, d2) if (d1 >= 60 and d2 >= 60) else d1
            l = max(d1, d2) if (d1 >= 60 and d2 >= 60) else d2
            if "Breite Liegefläche" in schema_map and (not extracted_attrs.get("Breite Liegefläche") or str(extracted_attrs.get("Breite Liegefläche")).strip().lower() == "null"):
                extracted_attrs["Breite Liegefläche"] = str(w)
            if "Länge Liegefläche" in schema_map and (not extracted_attrs.get("Länge Liegefläche") or str(extracted_attrs.get("Länge Liegefläche")).strip().lower() == "null"):
                extracted_attrs["Länge Liegefläche"] = str(l)
            if "Liegefläche" in schema_map and (not extracted_attrs.get("Liegefläche") or str(extracted_attrs.get("Liegefläche")).strip().lower() == "null"):
                lf_opts = schema_map["Liegefläche"].get("options")
                cand = f"{w}x{l}"
                matched_lf = match_closest_option(cand, lf_opts) or match_closest_option(f"{w} x {l}", lf_opts) if lf_opts else None
                extracted_attrs["Liegefläche"] = matched_lf or cand

    # L. Anzahl Teile & Set parsing
    if "Anzahl Teile" in schema_map:
        current_at = extracted_attrs.get("Anzahl Teile")
        if not current_at or str(current_at).strip().lower() in ["null", "", "0"]:
            deduced_cnt = "1"
            m_set = re.search(r'(\d+)\s*(?:-|er\s*|-tlg\.?\s*)(?:set|teilig|tlg|gruppe)', combined_text, re.IGNORECASE)
            if m_set:
                deduced_cnt = m_set.group(1)
            elif re.search(r'3\s*\+\s*2\s*\+\s*1', combined_text):
                deduced_cnt = "3"
            elif re.search(r'3\s*\+\s*2', combined_text):
                deduced_cnt = "2"
            elif "bett" in combined_text and ("2x nachttisch" in combined_text or "2 nachttisch" in combined_text):
                deduced_cnt = "4" if ("schrank" in combined_text or "kleiderschrank" in combined_text) else "3"
            elif "tisch" in combined_text and re.search(r'(\d+)\s*(?:x\s*)?st[üu]hl', combined_text):
                m_ch = re.search(r'(\d+)\s*(?:x\s*)?st[üu]hl', combined_text)
                deduced_cnt = str(1 + int(m_ch.group(1)))
            elif any(s_kw in (cat_name or "").lower() for s_kw in ["set", "gruppe", "garnitur"]):
                deduced_cnt = "2"
            extracted_attrs["Anzahl Teile"] = str(deduced_cnt)

    # M. Material synthesis (Material Korpus + Bezug)
    if "Material" in schema_map:
        current_mat = extracted_attrs.get("Material")
        if not current_mat or str(current_mat).strip().lower() == "null":
            bezug = extracted_attrs.get("Bezug")
            korpus_mat = extracted_attrs.get("Material Korpus") or "Holz"
            if bezug and str(bezug).strip().lower() not in ["null", "ohne", "kein", "keine", "holz"]:
                synth_mat = f"{korpus_mat} / {bezug}"
            else:
                synth_mat = korpus_mat
            mat_opts = schema_map["Material"].get("options")
            if mat_opts:
                matched_m = match_closest_option(synth_mat, mat_opts) or match_closest_option(korpus_mat, mat_opts) or match_closest_option(str(bezug or ""), mat_opts)
                extracted_attrs["Material"] = matched_m or mat_opts[0]
            else:
                extracted_attrs["Material"] = synth_mat
        else:
            mat_opts = schema_map["Material"].get("options")
            if mat_opts:
                matched_m = match_closest_option(str(current_mat).strip(), mat_opts)
                if matched_m:
                    extracted_attrs["Material"] = matched_m

    # N. Feet & Handle materials
    if has_legs:
        ff = str(extracted_attrs.get("Farbe Füße") or "").lower()
        val_f = "Metall" if any(m in ff for m in ["gold", "silber", "chrom", "metall", "stahl", "alu", "eis"]) else "Holz"
        for f_key in ["Art Füße", "Material Füße"]:
            if f_key in schema_map and (not extracted_attrs.get(f_key) or str(extracted_attrs.get(f_key)).strip().lower() == "null"):
                f_opts = schema_map[f_key].get("options")
                extracted_attrs[f_key] = match_closest_option(val_f, f_opts) or val_f

    if has_handles:
        fg = str(extracted_attrs.get("Farbe Griffe") or "").lower()
        val_g = "Holz" if any(h in fg for h in ["braun", "holz", "eiche", "buche"]) else "Metall"
        for g_key in ["Art Griffe", "Material Griffe"]:
            if g_key in schema_map and (not extracted_attrs.get(g_key) or str(extracted_attrs.get(g_key)).strip().lower() == "null"):
                g_opts = schema_map[g_key].get("options")
                extracted_attrs[g_key] = match_closest_option(val_g, g_opts) or val_g

    # O. Standard attributes filling (Zielgruppe, Optik, Lieferzustand, Pflegehinweise, etc.)
    if "Zielgruppe" in schema_map and (not extracted_attrs.get("Zielgruppe") or str(extracted_attrs.get("Zielgruppe")).strip().lower() == "null"):
        zg_opts = schema_map["Zielgruppe"].get("options")
        default_zg = "Erwachsene ; Jugendliche"
        extracted_attrs["Zielgruppe"] = match_closest_option(default_zg, zg_opts) or (zg_opts[0] if zg_opts else default_zg)

    if "Optik" in schema_map and (not extracted_attrs.get("Optik") or str(extracted_attrs.get("Optik")).strip().lower() == "null"):
        opt_opts = schema_map["Optik"].get("options")
        default_opt = "uni"
        extracted_attrs["Optik"] = match_closest_option(default_opt, opt_opts) or (opt_opts[0] if opt_opts else default_opt)

    if "Lieferzustand" in schema_map and (not extracted_attrs.get("Lieferzustand") or str(extracted_attrs.get("Lieferzustand")).strip().lower() == "null"):
        lz_opts = schema_map["Lieferzustand"].get("options")
        default_lz = "teilmontiert"
        extracted_attrs["Lieferzustand"] = match_closest_option(default_lz, lz_opts) or (lz_opts[0] if lz_opts else default_lz)

    for simple_key, simple_val in [
        ("Pflegehinweise", "keine aggressiven Reinigungsmittel verwenden"),
        ("Pflegehinweise Bezug", "keine aggressiven Reinigungsmittel verwenden"),
        ("Hinweis Maßangaben", "Alle Angaben sind ca. Maße"),
        ("Montagehinweis", "Es wird eine zweite Person zum Aufbau empfohlen"),
        ("Montagehinweise", "Es wird eine zweite Person zum Aufbau empfohlen"),
        ("Lieferumfang", "Es wird alles laut Produktbeschreibung geliefert"),
        ("Geräteart laut ElektroG", "Produkt fällt nicht unter das ElektroG."),
        ("Art Herstellung", "Fertigung aus Europa")
    ]:
        if simple_key in schema_map and (not extracted_attrs.get(simple_key) or str(extracted_attrs.get(simple_key)).strip().lower() == "null"):
            opts = schema_map[simple_key].get("options")
            extracted_attrs[simple_key] = match_closest_option(simple_val, opts) if opts else simple_val

    # P. Clean dimensions & numeric attributes (strip "cm", "mm", "kg", etc.)
    for k, v in list(extracted_attrs.items()):
        if v and isinstance(v, str):
            val_str = v.strip()
            schema_attr = schema_map.get(k)
            is_numeric_attr = False
            if schema_attr and (schema_attr.get("type") in ("FLOAT", "INTEGER") or schema_attr.get("unit")):
                is_numeric_attr = True
            elif any(dim in k.lower() for dim in ["breite", "höhe", "tiefe", "länge", "maß", "gewicht", "durchmesser", "abstand", "stärke", "sitzhöhe", "sitztiefe", "liegefläche"]):
                is_numeric_attr = True
            
            if is_numeric_attr:
                cleaned = re.sub(r'^(ca\.?|~|ab|approx\.?)\s*', '', val_str, flags=re.IGNORECASE).strip()
                m_unit = re.search(r'^([0-9]+(?:[.,][0-9]+)?)\s*(?:cm|mm|m|kg|g|grad|°)?$', cleaned, flags=re.IGNORECASE)
                if m_unit:
                    num_str = m_unit.group(1).replace(',', '.')
                    try:
                        f_val = float(num_str)
                        if f_val.is_integer():
                            extracted_attrs[k] = str(int(f_val))
                        else:
                            extracted_attrs[k] = f"{f_val:.2f}".rstrip('0').rstrip('.')
                    except ValueError:
                        extracted_attrs[k] = num_str
                elif re.search(r'\bcm\b', cleaned, flags=re.IGNORECASE):
                    extracted_attrs[k] = re.sub(r'\s*cm\b', '', cleaned, flags=re.IGNORECASE).strip()

    # Strip internal/meta keys and forbidden warranty attributes
    for meta_key in ["reasoning", "has_drawers", "has_doors", "has_legs", "has_handles"]:
        extracted_attrs.pop(meta_key, None)

    for k in list(extracted_attrs.keys()):
        if is_forbidden_warranty_attribute(k):
            extracted_attrs.pop(k, None)

    return extracted_attrs

@retry(wait=wait_exponential(multiplier=1, min=4, max=15), stop=stop_after_attempt(3), retry=retry_if_exception_type(openai.OpenAIError), reraise=True)
async def extract_attributes(product_info: str, category_id: str, only_required: bool = False, use_vision: bool = False, gallery_url: str = None, picture_urls: str = None, custom_api_key: str = None) -> dict:
    cat = get_category_by_id(category_id)
    if cat and cat.get("categoryId"):
        category_id = str(cat["categoryId"])
    cat_schema = get_category_schema(category_id)
    if not cat_schema:
        return {}

    cat_name = cat.get("name", "") if cat else ""

    # Process up to 75 attributes sorted by relevance (High -> Middle -> Low), strictly excluding warranty fields
    sorted_schema = sorted(cat_schema, key=lambda x: x.get("relevance_order", 3))
    target_attrs = [a for a in sorted_schema if not is_forbidden_warranty_attribute(a.get("name", ""))][:75]
    if not target_attrs:
        return {}

    properties = {
        "reasoning": {
            "type": "string",
            "description": "Think step-by-step. Break down furniture surface colors with area percentages (e.g. 75% Braun, 20% Schwarz), dominant color, check drawers/doors, count feet/legs (multiply by items in set), count handles, classify design (Modern or Klassisch), extract set info, and suggest aesthetic Serie."
        },
        "has_drawers": {
            "type": ["boolean", "null"],
            "description": "True if sliding drawers are visually present on the furniture piece, False otherwise."
        },
        "has_doors": {
            "type": ["boolean", "null"],
            "description": "True if cabinet/wardrobe doors are visually present on the furniture piece, False otherwise."
        },
        "has_legs": {
            "type": ["boolean", "null"],
            "description": "True if feet/legs are visually present beneath the furniture piece, False if flat base/plinth or wall-hung."
        },
        "has_handles": {
            "type": ["boolean", "null"],
            "description": "True if handles/knobs are visually present on drawer or door fronts, False if handleless/push-to-open."
        }
    }
    for a in target_attrs:
        prop = {"type": ["string", "null"]}
        if a.get("options"):
            # OpenAI structured outputs require all enums to be strings or null
            prop["enum"] = [str(opt) for opt in a["options"]] + [None]
        elif a.get("type") in ("FLOAT", "INTEGER") or a.get("unit") or any(dim in a["name"].lower() for dim in ["breite", "höhe", "tiefe", "länge", "maß", "gewicht", "durchmesser", "abstand", "stärke", "sitzhöhe", "sitztiefe", "liegefläche"]):
            prop["description"] = "Pure numeric value ONLY (e.g. '180'). NEVER include units like 'cm', 'mm', 'm', 'kg'!"
        else:
            prop["description"] = "Text value, strictly maximum 70 characters. Never exceed 70 characters!"
        properties[a["name"]] = prop

    for i in range(1, 6):
        properties[f"Bullet Point {i}"] = {
            "type": ["string", "null"],
            "description": f"Highlight bullet point {i} (max 180 chars)"
        }

    attr_json_schema = {
        "name": "attribute_extraction",
        "schema": {
            "type": "object",
            "properties": properties,
            "required": ["reasoning", "has_drawers", "has_doors", "has_legs", "has_handles", 
                         "Bullet Point 1", "Bullet Point 2", "Bullet Point 3", "Bullet Point 4", "Bullet Point 5"] + [a["name"] for a in target_attrs],
            "additionalProperties": False
        },
        "strict": True
    }

    img_url = resolve_image_url(gallery_url, picture_urls)

    rules_text = """
CRITICAL RULES FOR ATTRIBUTE EXTRACTION & LOGICAL DEDUCTION:
1. UNIVERSAL COLOR ANALYSIS & AREA PERCENTAGE (CRITICAL):
   - Visually isolate the main furniture piece from the background (completely ignore floors, room walls, carpets, windows, lamps, and decorative staging props).
   - Identify ALL distinct colors and materials on the furniture piece itself across the full color spectrum (white, black, gray, anthracite, browns, woods like oak/walnut/wenge/sonoma/beech, beige, cream, blues, greens, reds, yellows, metallics like gold/brass/silver/chrome).
   - In the "reasoning" field, write down the approximate visual surface area percentage for EACH color on the furniture piece (e.g. "Color 1 (Dunkelbraun): 75%, Color 2 (Schwarz): 20%, Color 3 (Gold): 5%").
   - The color with the HIGHEST percentage is the DOMINANT color.
   - Assign this dominant color to "Farbe". If "Farbe" has allowed options in the schema, choose the closest matching German allowed option.

2. CASCADING COLOR SYNCHRONIZATION:
   - "Grundfarbe": Must be the canonical base color family matching the dominant color. Choose from the allowed Grundfarbe options: (beige, blau, braun, bunt, gelb, goldfarben, grau, grün, lila, natur, orange, rosa, rot, schwarz, silberfarben, transparent, weiß).
   - "Farbe Korpus" (or "Farbe Korpus / Gestell"): Set to the EXACT SAME color as "Farbe".

3. DRAWERS (Schubladen):
   - Inspect the furniture piece for sliding drawers (Schubladen).
   - If drawers exist:
     * If "Farbe Schubladen" is in the schema, assign it the same color as "Farbe" (or drawer front color if distinct).
     * If "Schubladen" or "Anzahl Schubladen" is in the schema, fill it with the visible status or count.
   - If drawers DO NOT exist:
     * Leave "Farbe Schubladen" as null.
     * If "Schubladen" has an option like "ohne Schubladen", select "ohne Schubladen", otherwise null.

4. DOORS (Türen):
   - Inspect the furniture piece for cabinet/wardrobe doors (Türen).
   - If doors exist:
     * If "Farbe Türen" is in the schema, assign it the same color as "Farbe" (or door front color if distinct).
     * If "Anzahl Türen" or "Türen" is in the schema, fill it with the count or status.
   - If doors DO NOT exist:
     * Leave "Farbe Türen" as null.

5. FEET / LEGS (Anzahl Füße, Farbe Füße, Art Füße, Material Füße):
   - Search the text description FIRST for the number of feet/legs, otherwise count them.
   - Cross-reference with title & description for multi-item sets: calculate total feet = (legs per piece) * (number of pieces in set).
     * 1 chair with 4 legs = 4; Set of 5 chairs with 4 legs = 20 (Anzahl Füße = 20).
     * Dining table (4 legs) + 4 chairs (16 legs) = 20 legs total.
   - If furniture has NO legs (rests directly on flat floor plinth/base or is wall-hung): set "has_legs": false, "Anzahl Füße": "0", "Farbe Füße": null.
   - If legs exist: set "has_legs": true, fill "Anzahl Füße", and assign the visible feet color to "Farbe Füße" (e.g. Schwarz, Gold, Silber, Chrom, Eiche, Walnuss, Braun).
   - If "Art Füße" or "Material Füße" is in schema: choose "Metall" if legs are metallic (gold, silver, chrome, black metal) or "Holz" if wooden/brown.

6. HANDLES (Anzahl Griffe, Farbe Griffe, Art Griffe, Material Griffe):
   - Search text first for handles/knobs, or count visible drawer/door handles.
   - If handleless / push-to-open / recessed grooves: set "has_handles": false, "Anzahl Griffe": "0", "Farbe Griffe": null.
   - If handles exist: set "has_handles": true, and assign handle color to "Farbe Griffe" (e.g. Gold, Schwarz, Silber, Chrom).
   - If "Art Griffe" or "Material Griffe" in schema: assign "Metall" or "Holz".

7. DESIGN:
   - Classify strictly into either "Modern" OR "Klassisch".
   - "Modern": sleek, straight minimalist lines, contemporary, geometric, scandi, industrial/loft.
   - "Klassisch": baroque, chesterfield, ornamental carvings, antique, curved vintage legs.

8. SET PIECE COUNT & INFO (Anzahl Teile, Set-Info, Set beinhaltet):
   - For all products, determine the total number of physical pieces in the set:
     * Single furniture piece: "Anzahl Teile": "1", "Set-Info": "1-tlg."
     * Chair set: 2er Set -> "2", 4er Set -> "4", 6er Set -> "6".
     * Dining groups: 1 Tisch + 4 Stühle -> "5"; 1 Tisch + 6 Stühle -> "7".
     * Bedroom sets: e.g. Bett + 2 Nachttische -> "3"; Bett + 2 Nachttische + Kleiderschrank -> "4".
     * Sofa sets: 3+2+1 -> "3" (3-Sitzer, 2-Sitzer, Sessel); 3+2 -> "2".
   - Fill "Anzahl Teile" as pure integer (e.g. "1", "3", "7").
   - Fill "Set-Info" (e.g. "3-tlg.", "Bett, 2x Nachttisch").
   - Fill "Set beinhaltet" with the items listing.

9. SERIE:
   - Generate a fictitious, elegant, stylish furniture collection name (1-2 words, catchy, high-end European sound, e.g. "Milano", "Verona", "Nordic", "Loft Line", "Elegance", "Aura", "Palermo", "Novara", "Stella") based on the style and title. If the title explicitly mentions a collection name, extract it.

10. BULLET POINTS (Bullet Point 1 to 5):
    - Carefully read the text description and extract 5 of the most important product features, selling points, or dimensions.
    - Write exactly 5 bullet points. Each bullet point MUST NOT exceed 180 characters.
    - Output into keys "Bullet Point 1" to "Bullet Point 5".
    - CRITICAL: DO NOT mention any warranty or guarantee claims (Herstellergarantie, Garantie, Gewährleistung, etc.) anywhere!

11. SOFA SETS & POLSTERGARNITUR:
    - If category is "Polstergarnitur", "Sofa-Set", or "Couchgarnitur", parse configuration:
      * "Set beinhaltet" (e.g. "3-Sitzer, 2-Sitzer, Sessel")
      * "Anzahl Teile" (e.g. "3")
      * "Set-Info" (e.g. "3-2-1")
      * "Anzahl Sitzflächen" (e.g. "6 St." for a 3+2+1 set)
      * "Anzahl Sofas" (e.g. "2 St.")
      * "Anzahl Sessel" (e.g. "1 St.")
      * "Art Sofa"

12. UPHOLSTERY & COVER MATERIAL (Bezug, Bezug Korpus, Bezug Sitzfläche, Bezug Armlehnen):
    - Identify the upholstery or cover material (Stoff, Samt, Velours, Kunstleder, Leder, Textil, Mikrofaser, Webstoff, Strukturstoff).
    - If "Bezug" is present, assign this cover material.
    - Cascade "Bezug" to "Bezug Korpus", "Bezug Sitzfläche", "Bezug Armlehnen", "Bezug Rückenlehne", "Bezug Rückenkissen", "Bezug Bettgestell", etc.

13. BED COMPONENTS & MATTRESS COLOR:
    - "Farbe Bettgestell", "Farbe Kopfteil", "Farbe Fußteil", "Farbe Kopf-/Fußteil": Set to dominant bed "Farbe" unless distinctly different.
    - "Farbe Matratze" (50/50 PROBABILITY):
      * In boxspring beds with matching mattress cover: same as bed frame color.
      * In beds with standard white/cream mattress: "Weiß".
      * If frame only without mattress: output null.

14. WOOD TYPE (Holzart, Holzart Bettgestell) - CRITICAL:
    - When "Holzart" or "Holzart Bettgestell" is in the category schema:
      * Standard European furniture catalogs are based on beech ("Buche") or pine ("Kiefer").
      * YOU MUST CHOOSE STRICTLY BETWEEN "Buche" OR "Kiefer"!
      * If pine, softwood, pinie, or Kiefer is mentioned, choose "Kiefer".
      * Otherwise (for hardwood, beech, oak, MDF/Holzwerkstoff, or general wood frame/legs), choose "Buche".
      * NEVER leave "Holzart" as null when the product contains wood, wooden frame, legs, or is in a wood furniture category!

15. ROOM / LIVING SPACE (Wohnraum) - CRITICAL:
    - Deduce the room logically from the product and category:
      * "Schlafzimmer": beds, bedside tables, wardrobes, bedroom sets, mattresses.
      * "Wohnzimmer": sofas, couches, coffee tables, living units, armchairs.
      * "Esszimmer": dining tables, dining chairs, dining sets, barstools.
      * "Arbeitszimmer": desks, office chairs, office cabinets.
      * "Flur/Diele": coat racks, shoe benches, hallway mirrors.
      * "Badezimmer": bathroom vanities, cabinets.
      * "Kinderzimmer": kids / youth furniture.
      * "Garten": outdoor / patio furniture.
      * Default for general indoor furniture: "Wohnzimmer".
    - If allowed options exist in schema, choose the exact matching German option.

16. BED DIMENSIONS & LIEGEFLÄCHE (Breite Liegefläche, Länge Liegefläche, Liegefläche):
    - When a bed, sofa bed, or bedroom set has lying dimensions (e.g. "180 x 200 cm", "140x200"):
      * "Breite Liegefläche": pure width number (e.g. "180").
      * "Länge Liegefläche": pure length number (e.g. "200").
      * NEVER append unit strings like "cm"! Output pure numbers.

17. BASE MATERIAL (Material):
    - Combine frame/body and upholstery: e.g. "Holz", "Holz / Textil", "Holz / Samt", "Holz / Kunstleder", "Metall", "Textil". Select closest allowed option if options provided.

18. TARGET GROUP (Zielgruppe) & OPTICS (Optik) & DELIVERY (Lieferzustand):
    - "Zielgruppe": default to "Erwachsene ; Jugendliche" or "Erwachsene".
    - "Optik": "uni" (plain/monochrome) or "Holzoptik" (woodgrain).
    - "Lieferzustand": "teilmontiert" or "zerlegt".
    - "Montagehinweis": "Es wird eine zweite Person zum Aufbau empfohlen".
    - "Hinweis Maßangaben": "Alle Angaben sind ca. Maße".
    - "Pflegehinweise": "keine aggressiven Reinigungsmittel verwenden".

19. NUMERIC ATTRIBUTES & DIMENSIONS (CRITICAL - STRICTLY NO UNITS / KEINE EINHEITEN WIE "cm"):
    - For all dimension, measurement, and numeric fields (Breite, Höhe, Tiefe, Sitzhöhe, Sitztiefe, Liegefläche, Gewicht, etc.):
      * Output ONLY THE PURE NUMBER (e.g. "180", "200", "75").
      * NEVER append unit symbols like "cm", "mm", "m", "kg", or prefixes like "ca."!

20. GENERAL RULES & LOGICAL COMPLETENESS:
    - You are an expert furniture catalog analyst. Logically and properly fill in ALL attributes that can be inferred from the product type, category, title, description, and visual appearance.
    - If an attribute has allowed options, you MUST choose strictly from the provided options.
    - Only output null for fields that are truly inapplicable to this product (e.g. battery parameters for non-electric furniture).
    - Never invent manufacturer brand names for Brand fields.
    - NEVER mention warranties or guarantees (Herstellergarantie, Garantie, etc.) anywhere.
    - ATTRIBUTE VALUE LENGTH (CRITICAL): Every text attribute value MUST strictly be 70 characters or less.
"""

    if use_vision and img_url:
        attr_prompt = f"""
You are an expert furniture attribute specialist and computer vision analyst for Otto marketplace.
Product Category: {cat_name}
{product_info}

Analyze the provided product image carefully and cross-reference with the text details.
Extract the required attributes for this product.

{rules_text}
"""
        messages = [{"role": "user", "content": [
            {"type": "text", "text": attr_prompt},
            {"type": "image_url", "image_url": {"url": img_url}}
        ]}]
    else:
        attr_prompt = f"""
You are an expert furniture attribute specialist for Otto marketplace.
Product Category: {cat_name}
{product_info}

Extract the required attributes for this product based on the title, description, and specifications.

{rules_text}
"""
        messages = [{"role": "user", "content": attr_prompt}]

    if not custom_api_key:
        print("Error: No API key provided")
        return None
    local_client = get_openai_client(custom_api_key)
    try:
        response = await local_client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            response_format={"type": "json_schema", "json_schema": attr_json_schema},
            temperature=0.0
        )
        raw_res = json.loads(response.choices[0].message.content)
        # Apply deterministic furniture color and parts cascading rules
        return enforce_furniture_color_rules(raw_res, cat_schema, product_info=product_info, cat_name=cat_name)
    except Exception as e:
        # If OpenAI failed because of a broken/404 image URL, rate limit or any vision issue, gracefully fall back to text-only mode
        if use_vision and img_url:
            print(f"[Automapper] Vision call failed ({e}). Gracefully falling back to text-only analysis...")
            fallback_prompt = f"""
You are an expert furniture attribute specialist for Otto marketplace.
Product Category: {cat_name}
{product_info}

Extract the required attributes for this product based on the title, description, and specifications.

{rules_text}
"""
            fallback_messages = [{"role": "user", "content": fallback_prompt}]
            try:
                fallback_resp = await local_client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=fallback_messages,
                    response_format={"type": "json_schema", "json_schema": attr_json_schema},
                    temperature=0.0
                )
                raw_res = json.loads(fallback_resp.choices[0].message.content)
                return enforce_furniture_color_rules(raw_res, cat_schema, product_info=product_info, cat_name=cat_name)
            except Exception as fb_err:
                print(f"[Automapper] Fallback text-only call failed: {fb_err}")
                if isinstance(fb_err, openai.OpenAIError):
                    raise fb_err
                return None
        if isinstance(e, openai.OpenAIError):
            raise e
        print(f"Error calling OpenAI for attributes: {e}")
        return None

async def auto_map_product(ean: str, title: str, description: str, specifics: str, gallery_url: str, picture_urls: str, factory_id: str, existing_category_id: str, existing_attrs_dict: dict, only_required: bool = False, use_vision: bool = False, target: str = "all", custom_api_key: str = None):
    if not ean or not title:
        return {"status": "error", "ean": ean, "message": "Missing EAN or Title"}
        
    product_info = format_product_info(title, description, specifics, gallery_url, picture_urls)

    category_id = existing_category_id
    mapping_status = "Unmapped"

    # Stage 1: Category
    if target in ["all", "categories"]:
        category_id = await predict_category(product_info, title, custom_api_key)
        if not category_id:
            return {"status": "error", "ean": ean, "message": "Failed to determine category", "db_updates": {}}
        
        mapping_status = "Need Attributes"
        cat = get_category_by_id(category_id)
        cat_name = cat.get("name", category_id) if cat else category_id
        
        if target == "categories":
            return {
                "status": "success", 
                "ean": ean, 
                "category_id": category_id, 
                "category_name": cat_name, 
                "message": "Category mapped",
                "db_updates": {
                    "category_id": category_id,
                    "mapping_status": mapping_status,
                    "attributes_to_save": {}
                }
            }

    # Stage 2: Attributes
    if target in ["all", "attributes"]:
        if not category_id:
            # If target is attributes but category is missing, auto-predict category first!
            category_id = await predict_category(product_info, title, custom_api_key)
            if not category_id:
                return {"status": "error", "ean": ean, "message": "No category assigned for attribute extraction", "db_updates": {}}

        resolved_img = resolve_image_url(gallery_url, picture_urls)
        # If product has an image and vision is allowed or requested, activate vision
        try:
            extracted_attrs = await extract_attributes(
                product_info, category_id,
                only_required=only_required,
                use_vision=(use_vision and bool(resolved_img)),
                gallery_url=resolved_img,
                picture_urls=picture_urls,
                custom_api_key=custom_api_key
            )
        except openai.OpenAIError as e:
            print(f"[Automapper] OpenAI error during attribute extraction for {ean}: {e}")
            raise e
        except Exception as e:
            print(f"[Automapper] Failed attribute extraction for {ean}: {e}")
            extracted_attrs = None

        if extracted_attrs is None:
            return {
                "status": "partial", 
                "ean": ean, 
                "category_id": category_id, 
                "message": "Failed attribute extraction",
                "db_updates": {
                    "category_id": category_id,
                    "mapping_status": "Need Attributes",
                    "attributes_to_save": {}
                }
            }

        attributes_to_save = {}
        all_attributes = {k: v for k, v in existing_attrs_dict.items() if not is_forbidden_warranty_attribute(k)}
        
        for k, v in extracted_attrs.items():
            if k in ["reasoning", "has_drawers", "has_doors", "has_legs", "has_handles"] or is_forbidden_warranty_attribute(k):
                continue
            
            # Do not touch already filled fields (e.g. from OKB Sync)
            existing_val = existing_attrs_dict.get(k)
            if existing_val and str(existing_val).strip() and str(existing_val).lower() != "null":
                continue
                
            if v is not None:
                val_str = str(v).strip()
                if k.startswith("Bullet Point") or "bullet" in k.lower():
                    val_str = sanitize_text_for_warranty(val_str)
                if val_str and val_str.lower() != "null":
                    attributes_to_save[k] = val_str
                    all_attributes[k] = val_str
                
        errors = validate_product(ean, category_id, all_attributes)
        mapping_status = "Error" if errors else "Ready"
        
        cat = get_category_by_id(category_id)
        cat_name = cat.get("name", category_id) if cat else category_id
        
        return {
            "status": "success", 
            "ean": ean, 
            "category_id": category_id, 
            "category_name": cat_name, 
            "attributes": all_attributes, 
            "errors": errors,
            "db_updates": {
                "category_id": category_id,
                "mapping_status": mapping_status,
                "attributes_to_save": attributes_to_save
            }
        }
