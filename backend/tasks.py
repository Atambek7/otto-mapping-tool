import asyncio
import json
from celery import shared_task
from sqlalchemy.orm import Session
from database import SessionLocal
import models
from services.otto_client import OttoMarketClient
from services.otto_formatter import format_otto_product
# fetch_product_info_for_export removed
@shared_task(bind=True)
def publish_factory_to_otto_task(self, factory_id: str, account_name: str, client_id: str, client_secret: str):
    db: Session = SessionLocal()
    try:
        # Update state to started
        self.update_state(state="PROGRESS", meta={"current": 0, "total": 100, "message": "Starting..."})
        
        mappings = db.query(models.ProductMapping).filter(
            models.ProductMapping.factory_id == factory_id,
            models.ProductMapping.status == "Ready"
        ).all()
        
        if not mappings:
            return {"status": "success", "published": 0, "message": "No Ready products found"}
            
        payloads = []
        mapping_lookup = {}
        
        # Prepare payloads
        for m in mappings:
            info = {
                "name": m.product_name or "",
                "price": m.price or "",
                "image_url": m.image_url or ""
            }
            cat = db.query(models.CategoryCache).filter(models.CategoryCache.id == m.category_id).first()
            cat_name = cat.name if cat else ""
            
            # Reconstruct attributes from ProductAttribute table
            attrs = db.query(models.ProductAttribute).filter(models.ProductAttribute.product_ean == m.product_ean).all()
            attr_dict = {a.attribute_name: a.attribute_value for a in attrs}
            mapping_data = {"attributes": attr_dict}
            
            formatted = format_otto_product(m.product_ean, info, mapping_data, cat_name)
            payloads.append(formatted)
            mapping_lookup[m.product_ean] = m
            
        total = len(payloads)
        self.update_state(state="PROGRESS", meta={"current": 0, "total": total, "message": "Publishing..."})
        
        client = OttoMarketClient(client_id, client_secret)
        
        batch_size = 100
        published_count = 0
        errors = []
        
        for i in range(0, total, batch_size):
            batch = payloads[i:i+batch_size]
            res = client.publish_products(batch)
            
            if res.get("status_code") in [200, 201, 202, 204]:
                published_count += len(batch)
                # Mark as Published in DB
                for p in batch:
                    ean = p["ean"]
                    if ean in mapping_lookup:
                        mapping_lookup[ean].status = "Published"
                db.commit()
            else:
                errors.append({"batch_start": i, "error": res})
                
            self.update_state(state="PROGRESS", meta={"current": published_count, "total": total, "message": f"Published {published_count} of {total}"})
            
        return {
            "status": "success" if not errors else "partial_error",
            "published": published_count,
            "errors": errors
        }
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
