import os
import json
from services.categories_repo import get_category_by_id, get_groups
from services.brand_guard import is_forbidden_warranty_attribute

def validate_product(ean: str, category_id: str, attributes: dict) -> list:
    """
    Validates a product and returns a list of error strings.
    If the list is empty, the product is fully valid.
    """
    errors = []
    
    if not ean or len(ean) != 13 or not ean.isdigit():
        errors.append("EAN must be exactly 13 digits")
        
    if not category_id:
        errors.append("Category is not selected")
    else:
        cat = get_category_by_id(category_id)
        if cat:
            group_id = str(cat.get("category_group_id"))
            group_data = get_groups().get(group_id)
            if group_data:
                raw_attrs = group_data.get("attributes", {}).get("attributes", [])
                for a in raw_attrs:
                    attr_name = a.get("name")
                    if is_forbidden_warranty_attribute(attr_name):
                        continue
                    val = attributes.get(attr_name, "")
                    
                    is_req = a.get("relevance") == "HIGH" or attr_name == "Marke"
                    if attr_name == "Ausführung":
                        is_req = False
                    
                    if is_req:
                        if not val or not str(val).strip():
                            errors.append(f"Обязательное поле не заполнено: {attr_name}")
                            continue
                            
                    # Check length if it is filled
                    if val and str(val).strip():
                        max_len = a.get("maxLength")
                        if max_len and len(str(val).strip()) > int(max_len):
                            errors.append(f"Длина поля '{attr_name}' превышает лимит ({max_len} симв.)")
        else:
            errors.append("Выбранная категория не найдена в базе OKB")
            
    return errors

def get_missing_required_attributes(category_id: str, attributes: dict) -> list[str]:
    """
    Returns a list of required attribute names that are missing from `attributes`.
    """
    missing = []
    if not category_id:
        return missing
    
    cat = get_category_by_id(category_id)
    if cat:
        group_id = str(cat.get("category_group_id"))
        group_data = get_groups().get(group_id)
        if group_data:
            raw_attrs = group_data.get("attributes", {}).get("attributes", [])
            for a in raw_attrs:
                attr_name = a.get("name")
                if is_forbidden_warranty_attribute(attr_name):
                    continue
                val = attributes.get(attr_name, "")
                
                is_req = a.get("relevance") == "HIGH" or attr_name == "Marke"
                if attr_name == "Ausführung":
                    is_req = False
                
                if is_req:
                    if not val or not str(val).strip():
                        missing.append(attr_name)
    return missing
