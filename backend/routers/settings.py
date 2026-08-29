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
    openai_api_key: Optional[str] = None

@router.get("/me", response_model=UserSettingsResponse)
def get_my_settings(current_user: User = Depends(auth.get_current_user)):
    return {
        "username": current_user.username,
        "openai_api_key": current_user.openai_api_key
    }

@router.post("/me", response_model=UserSettingsResponse)
def update_my_settings(payload: UserSettingsUpdate, db: Session = Depends(get_db), current_user: User = Depends(auth.get_current_user)):
    if payload.openai_api_key is not None:
        current_user.openai_api_key = payload.openai_api_key
        db.commit()
        import sync_users
        sync_users.dump_users_to_json()
        db.refresh(current_user)
        
    return {
        "username": current_user.username,
        "openai_api_key": current_user.openai_api_key
    }
