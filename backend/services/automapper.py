import os
import json
import re
from openai import AsyncOpenAI
import openai
from sqlalchemy.orm import Session
import models
from services.validator import validate_product, get_category_by_id
from collections import Counter, defaultdict

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

def get_openai_client(api_key: str) -> AsyncOpenAI:
    if api_key not in _client_cache:
        _client_cache[api_key] = AsyncOpenAI(api_key=api_key)
    return _client_cache[api_key]

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
    cat = next((c for c in CATEGORIES if str(c.get("categoryId")) == str(category_id)), None)
    if not cat: return []
    
    group_id = str(cat.get("category_group_id"))
    group_data = GROUPS.get(group_id)
    if not group_data: return []
    
    raw_attrs = group_data.get("attributes", {}).get("attributes", [])
    schema = []
    for a in raw_attrs:
        raw_rel = a.get("relevance", "OPTIONAL")
        if a.get("name") == "Marke":
            raw_rel = "HIGH"
            
        rel_order = 3
        if raw_rel == "HIGH":
            rel_order = 1
        elif raw_rel == "RECOMMENDED":
            rel_order = 2
            
        schema.append({
            "name": a.get("name"),
            "required": raw_rel == "HIGH",
            "relevance": raw_rel,
            "relevance_order": rel_order,
            "options": [v if isinstance(v, str) else v.get("value", str(v)) for v in a.get("allowedValues", [])] if a.get("allowedValues") else None
        })
    return schema

STOP_WORDS = {"für", "und", "oder", "mit", "von", "in", "an", "auf", "der", "die", "das", "ein", "eine", "ist", "sind"}

def tokenize(text):
    if not text:
        return set()
    text = str(text).lower()
    words = re.findall(r'\b[a-z0-9äöüß]+\b', text)
    return set(w for w in words if w not in STOP_WORDS and len(w) > 1)

# Build inverted index
def build_category_index():
    for c in CATEGORIES:
        cat_name = str(c.get("name", "")).lower()
        cat_path = str(c.get("path", "")).lower()
        
        name_tokens = tokenize(cat_name)
        path_tokens = tokenize(cat_path)
        
        for w in name_tokens:
            WORD_TO_CAT[w].append((c, 3))
            
        for w in path_tokens:
            if w not in name_tokens: # prevent double counting if it's already in name
                WORD_TO_CAT[w].append((c, 1))

build_category_index()

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
    
    if not custom_api_key:
        print("Error: No API key provided")
        return []
    local_client = AsyncOpenAI(api_key=custom_api_key)
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

async def predict_category(product_info: str, title: str, custom_api_key: str = None) -> str:
    keywords = await generate_search_keywords(title, custom_api_key)
    top_categories = get_top_categories(title, keywords, n=100)
    categories_list_str = "\n".join([f"{c.get('categoryId')}: {c.get('path', '')} > {c.get('name', '')}" for c in top_categories])

    prompt = f"""
You are an expert product categorizer for a marketplace.
{product_info}

Here is a list of the most relevant categories (ID: Path):
{categories_list_str}

Based on the product information, choose the single most appropriate category ID for this product from the list above.
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

async def extract_attributes(product_info: str, category_id: str, only_required: bool = False, use_vision: bool = False, gallery_url: str = None, custom_api_key: str = None) -> dict:
    cat_schema = get_category_schema(category_id)
    if not cat_schema:
        return {}

    # Process up to 50 attributes sorted by relevance (High -> Middle -> Low)
    sorted_schema = sorted(cat_schema, key=lambda x: x.get("relevance_order", 3))
    target_attrs = sorted_schema[:50]
    if not target_attrs:
        return {}

    properties = {
        "reasoning": {
            "type": "string",
            "description": "Think step-by-step. Explain which attributes you found in the text/image and which you could not find."
        }
    }
    for a in target_attrs:
        prop = {"type": ["string", "null"]}
        if a["options"]:
            # OpenAI structured outputs require all enums to be strings or null
            prop["enum"] = [str(opt) for opt in a["options"]] + [None]
        properties[a["name"]] = prop

    attr_json_schema = {
        "name": "attribute_extraction",
        "schema": {
            "type": "object",
            "properties": properties,
            "required": ["reasoning"] + [a["name"] for a in target_attrs],
            "additionalProperties": False
        },
        "strict": True
    }

    if use_vision and gallery_url:
        attr_prompt = f"""
You are an expert product attribute extractor.
{product_info}

Analyze the provided product image and text. 
Extract the required attributes for this product.
CRITICAL RULES:
1. You MUST choose from the allowed options if they are provided.
2. If an attribute (like color, material, style, shape) is not explicitly stated in the text, you MUST logically deduce it from the visual appearance of the product in the image.
3. If you ABSOLUTELY cannot find the information in the text and cannot deduce it from the image, output the JSON `null` value for that field (do not guess!).
4. For text fields without options (like Manufacturer or MPN), if it is not explicitly mentioned, output `null`. Do not invent a brand.
"""
        messages = [{"role": "user", "content": [
            {"type": "text", "text": attr_prompt},
            {"type": "image_url", "image_url": {"url": gallery_url}}
        ]}]
    else:
        attr_prompt = f"""
You are an expert product attribute extractor.
{product_info}

Extract the required attributes for this product. 
CRITICAL RULES:
1. You MUST choose from the allowed options if they are provided. Match English/other language words in the text to the German options if necessary.
2. If you ABSOLUTELY cannot find the information in the text, output the JSON `null` value for that field (do not guess!).
3. For text fields without options (like Manufacturer or MPN), if it is not explicitly mentioned, output `null`. Do not invent a brand.
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
        return json.loads(response.choices[0].message.content)
    except openai.OpenAIError as e:
        print(f"OpenAI API Error in attributes: {e}")
        raise e
    except Exception as e:
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
            return {"status": "error", "ean": ean, "message": "No category assigned for attribute extraction", "db_updates": {}}

        extracted_attrs = await extract_attributes(product_info, category_id, only_required=only_required, use_vision=use_vision, gallery_url=gallery_url, custom_api_key=custom_api_key)
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
        all_attributes = existing_attrs_dict.copy()
        
        for k, v in extracted_attrs.items():
            if k == "reasoning":
                continue
            
            # Do not touch already filled fields (e.g. from OKB Sync)
            existing_val = existing_attrs_dict.get(k)
            if existing_val and str(existing_val).strip() and str(existing_val).lower() != "null":
                continue
                
            if v is not None:
                val_str = str(v).strip()
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
