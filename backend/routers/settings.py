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
        new_key = val if val else None
        current_user.openai_api_key = new_key

        # If admin updates the key, propagate to all users without a key and to AccountSettings
        if current_user.is_admin:
            import models
            for u in db.query(models.User).all():
                u.openai_api_key = new_key
            for acc in ("JV", "XL"):
                s = db.query(models.AccountSettings).filter_by(account_name=acc).first()
                if not s:
                    s = models.AccountSettings(account_name=acc)
                    db.add(s)
                s.openai_api_key = new_key

        db.commit()
        import sync_users
        sync_users.dump_users_to_json()
        db.refresh(current_user)
        
    return {
        "username": current_user.username,
        "has_openai_key": bool(current_user.openai_api_key),
        "openai_api_key_masked": mask_key(current_user.openai_api_key)
    }

@router.post("/check-key")
async def check_openai_key(
    payload: Optional[UserSettingsUpdate] = None,
    current_user: User = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    key = None
    if payload and payload.openai_api_key and payload.openai_api_key.strip():
        key = payload.openai_api_key.strip()
    if not key:
        from services.ai_title_generator import resolve_openai_key
        key = resolve_openai_key(db=db, user=current_user)

    if not key:
        return {
            "status": "missing",
            "message": "API-ключ OpenAI не настроен в системе"
        }

    import urllib.request
    import json

    org_title = None
    user_email = None
    has_limit = False
    try:
        req_me = urllib.request.Request("https://api.openai.com/v1/me", headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req_me, timeout=7) as resp_me:
            me_json = json.loads(resp_me.read().decode())
            user_email = me_json.get("email")
            has_limit = bool(me_json.get("has_payg_project_spend_limit"))
            orgs = me_json.get("orgs", {}).get("data", [])
            if orgs:
                org_title = orgs[0].get("title") or orgs[0].get("name")
    except Exception:
        pass

    try:
        req_chat = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            data=json.dumps({"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}).encode("utf-8")
        )
        with urllib.request.urlopen(req_chat, timeout=7) as resp_chat:
            return {
                "status": "active",
                "message": "Ключ OpenAI полностью активен и баланс доступен!",
                "email": user_email,
                "organization": org_title,
                "has_spend_limit": has_limit
            }
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        try:
            err_json = json.loads(err_body).get("error", {})
            err_code = err_json.get("code")
            err_msg = err_json.get("message")
        except Exception:
            err_code = str(e.code)
            err_msg = err_body

        if err_code == "credit_balance_exhausted" or "credit" in str(err_msg).lower() or "quota" in str(err_msg).lower():
            return {
                "status": "exhausted",
                "code": "credit_balance_exhausted",
                "message": err_msg or "На балансе OpenAI закончились средства или превышен лимит расходов проекта",
                "email": user_email,
                "organization": org_title,
                "has_spend_limit": has_limit,
                "raw": err_msg
            }
        return {
            "status": "error",
            "code": err_code,
            "message": err_msg or f"Ошибка OpenAI (HTTP {e.code})",
            "email": user_email,
            "organization": org_title
        }
    except Exception as ex:
        return {"status": "error", "message": str(ex)}
