from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from database import get_db
from models import User
import auth

router = APIRouter(prefix="/settings", tags=["settings"])

class UserSettingsUpdate(BaseModel):
    openai_api_key: Optional[str] = None

class UserSettingsResponse(BaseModel):
    username: str
    has_openai_key: bool
    openai_api_key_masked: Optional[str] = None

def mask_key(key: Optional[str]) -> Optional[str]:
    if not key:
        return None
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}...{key[-4:]}"

@router.get("/me", response_model=UserSettingsResponse)
def get_my_settings(current_user: User = Depends(auth.get_current_user)):
    return {
        "username": current_user.username,
        "has_openai_key": bool(current_user.openai_api_key),
        "openai_api_key_masked": mask_key(current_user.openai_api_key)
    }

@router.post("/me", response_model=UserSettingsResponse)
def update_my_settings(payload: UserSettingsUpdate, db: Session = Depends(get_db), current_user: User = Depends(auth.get_current_user)):
    if payload.openai_api_key is not None:
        val = payload.openai_api_key.strip()
        current_user.openai_api_key = val if val else None
        db.commit()
        import sync_users
        sync_users.dump_users_to_json()
        db.refresh(current_user)
        
    return {
        "username": current_user.username,
        "has_openai_key": bool(current_user.openai_api_key),
        "openai_api_key_masked": mask_key(current_user.openai_api_key)
    }
