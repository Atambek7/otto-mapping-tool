import os
import sys
import logging

sys.path.insert(0, "/app" if os.path.exists("/app") else os.path.join(os.path.dirname(__file__), ".."))

from database import SessionLocal
import models
from services.categories_repo import get_allowed_attribute_names, get_numeric_attributes_for_category
from services.okb_sync import clean_dimension_value, DIMENSION_ATTR_NAMES, FORBIDDEN_EBAY_SPECIFICS
from services.validator import validate_product

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("clean_db")

def run_cleanup():
    db = SessionLocal()
    try:
        mappings = db.query(models.ProductMapping).all()
        logger.info("Found %d product mappings to process.", len(mappings))

        cleaned_attrs_count = 0
        deleted_attrs_count = 0

        for pm in mappings:
            ean = pm.product_ean
            acc = pm.account_name
            cat_id = pm.category_id

            allowed = get_allowed_attribute_names(cat_id) if cat_id else None
            numeric_specs = get_numeric_attributes_for_category(cat_id) if cat_id else {}

            attrs = db.query(models.ProductAttribute).filter(
                models.ProductAttribute.product_ean == ean,
                models.ProductAttribute.account_name == acc
            ).all()

            remaining_attrs = {}

            for a in attrs:
                name_clean = a.attribute_name.strip()
                name_lower = name_clean.lower()

                # 1. Check if forbidden eBay specific
                if name_lower in FORBIDDEN_EBAY_SPECIFICS or "maße sessel" in name_lower or "zusätzlich benötigte" in name_lower:
                    db.delete(a)
                    deleted_attrs_count += 1
                    continue

                # 2. Check if allowed in category schema (if category is assigned)
                if allowed is not None and name_clean not in allowed:
                    db.delete(a)
                    deleted_attrs_count += 1
                    continue

                # 3. Clean numeric/dimension values (strip cm, mm, etc.)
                if name_clean in numeric_specs or name_clean in DIMENSION_ATTR_NAMES or any(d in name_lower for d in ("breite", "höhe", "tiefe", "länge", "höhe")):
                    cleaned_val = clean_dimension_value(a.attribute_value)
                    if cleaned_val != a.attribute_value:
                        a.attribute_value = cleaned_val
                        cleaned_attrs_count += 1

                remaining_attrs[name_clean] = a.attribute_value

            # 4. Revalidate mapping status if not already published
            if (pm.status or "").lower() != "published" and cat_id:
                errors = validate_product(ean, cat_id, remaining_attrs)
                pm.status = "Error" if errors else "Ready"

        db.commit()
        logger.info(
            "Cleanup complete! Deleted %d invalid/garbage attributes, cleaned %d dimension values.",
            deleted_attrs_count, cleaned_attrs_count
        )
    except Exception as e:
        db.rollback()
        logger.error("Error during DB cleanup: %s", e, exc_info=True)
    finally:
        db.close()

if __name__ == "__main__":
    run_cleanup()
