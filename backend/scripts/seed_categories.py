import os
import sys
import json
import logging

# Add backend directory to sys.path if running as standalone script
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from database import SessionLocal
import models

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def seed_categories():
    data_path = os.path.join(backend_dir, "data", "categories.json")
    if not os.path.exists(data_path):
        logging.error(f"categories.json not found at {data_path}")
        return 0

    logging.info(f"Reading categories from {data_path}...")
    with open(data_path, "r", encoding="utf-8") as f:
        categories_data = json.load(f)

    total_in_json = len(categories_data)
    logging.info(f"Loaded {total_in_json} categories from JSON file.")

    db = SessionLocal()
    try:
        existing_count = db.query(models.CategoryCache).count()
        logging.info(f"Current categories count in DB: {existing_count}")

        # Prepare records for insertion
        records = [
            {
                "id": str(c["categoryId"]),
                "name": c["name"],
                "path": c.get("category_group")
            }
            for c in categories_data
            if "categoryId" in c and "name" in c
        ]

        # Use PostgreSQL ON CONFLICT DO NOTHING for idempotent and safe batch insert
        try:
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            
            batch_size = 2000
            for i in range(0, len(records), batch_size):
                batch = records[i:i + batch_size]
                stmt = pg_insert(models.CategoryCache).values(batch)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["id"],
                    set_={"name": stmt.excluded.name, "path": stmt.excluded.path}
                )
                db.execute(stmt)
                db.commit()
        except Exception as pg_err:
            logging.warning(f"PostgreSQL specific insert failed, falling back to merge: {pg_err}")
            db.rollback()
            for r in records:
                db.merge(models.CategoryCache(**r))
            db.commit()

        new_count = db.query(models.CategoryCache).count()
        logging.info(f"Seeding completed! Total categories in DB: {new_count}")
        return new_count
    except Exception as e:
        db.rollback()
        logging.error(f"Failed to seed categories: {e}", exc_info=True)
        return 0
    finally:
        db.close()

if __name__ == "__main__":
    seed_categories()
