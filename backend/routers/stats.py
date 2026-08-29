from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db
import models

router = APIRouter(
    prefix="/stats",
    tags=["stats"],
)

@router.get("/")
async def get_dashboard_stats(db: Session = Depends(get_db)):
    total_products = db.query(models.ProductMapping).count()
    
    ready = db.query(models.ProductMapping).filter(models.ProductMapping.status == "Ready").count()
    error = db.query(models.ProductMapping).filter(models.ProductMapping.status == "Error").count()
    need_attributes = db.query(models.ProductMapping).filter(models.ProductMapping.status == "Need Attributes").count()
    
    mapped = db.query(models.ProductMapping).filter(
        models.ProductMapping.category_id.isnot(None),
        models.ProductMapping.category_id != "",
        ~models.ProductMapping.status.in_(["Ready", "Error", "Need Attributes"])
    ).count()
    
    # Unmapped: category_id is NULL or empty string
    unmapped = db.query(models.ProductMapping).filter(
        (models.ProductMapping.category_id.is_(None)) | (models.ProductMapping.category_id == "")
    ).count()
    
    return {
        "total_products": total_products,
        "ready": ready,
        "error": error,
        "need_attributes": need_attributes,
        "mapped": mapped,
        "unmapped": unmapped
    }
