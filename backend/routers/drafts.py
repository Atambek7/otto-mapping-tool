from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from database import get_db
import models, auth
from pydantic import BaseModel
from typing import Optional, Any, Dict

router = APIRouter(prefix="/drafts", tags=["drafts"])

class DraftCreate(BaseModel):
    factory_name: str
    product_ean: str
    data_json: str
    is_ok: bool = False

@router.post("")
@router.post("/")
async def save_draft(
    draft: DraftCreate, 
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(auth.get_current_user)
):
    existing = db.query(models.Draft).filter(
        models.Draft.product_ean == draft.product_ean,
        models.Draft.factory_name == draft.factory_name
    ).first()
    
    if existing:
        existing.data_json = draft.data_json
        existing.is_ok = draft.is_ok
    else:
        new_draft = models.Draft(
            factory_name=draft.factory_name,
            product_ean=draft.product_ean,
            data_json=draft.data_json,
            is_ok=draft.is_ok
        )
        db.add(new_draft)
        
    db.commit()
    return {"status": "success"}

@router.get("/{factory_name}")
async def get_factory_drafts(
    factory_name: str, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    drafts = db.query(models.Draft).filter(models.Draft.factory_name == factory_name).all()
    return [{"product_ean": d.product_ean, "data_json": d.data_json, "is_ok": d.is_ok} for d in drafts]
