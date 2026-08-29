import json
import os
from database import SessionLocal
import models

JSON_PATH = os.path.join(os.path.dirname(__file__), "users.json")

def dump_users_to_json():
    """Сохраняет всех пользователей из БД в users.json для удобного просмотра."""
    db = SessionLocal()
    try:
        users = db.query(models.User).all()
        data = []
        for u in users:
            data.append({
                "id": u.id,
                "username": u.username,
                "is_admin": u.is_admin,
                "openai_api_key": u.openai_api_key,
                # Пароль хранится в виде хэша, поэтому мы не можем показать его исходный вид.
                "hashed_password": u.hashed_password
            })
            
        with open(JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
            
    except Exception as e:
        print(f"Ошибка при сохранении users.json: {e}")
    finally:
        db.close()
