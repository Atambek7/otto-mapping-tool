import getpass
from database import SessionLocal
import models
import auth

def create_user():
    print("=== Создание нового пользователя ===")
    username = input("Введите логин: ").strip()
    if not username:
        print("Логин не может быть пустым.")
        return

    password = getpass.getpass("Введите пароль: ").strip()
    if not password:
        print("Пароль не может быть пустым.")
        return
        
    is_admin_input = input("Сделать администратором? (y/n) [n]: ").strip().lower()
    is_admin = is_admin_input == 'y'

    db = SessionLocal()
    try:
        # Проверяем, существует ли пользователь
        existing = db.query(models.User).filter(models.User.username == username).first()
        if existing:
            print(f"Ошибка: Пользователь '{username}' уже существует!")
            return

        # Хэшируем пароль и создаем пользователя
        hashed_password = auth.get_password_hash(password)
        new_user = models.User(
            username=username, 
            hashed_password=hashed_password, 
            is_admin=is_admin
        )
        db.add(new_user)
        db.commit()
        
        import sync_users
        sync_users.dump_users_to_json()
        
        print(f"\nУспех! Пользователь '{username}' успешно создан.")
        print(f"Теперь вы можете войти под логином: {username}")
        
    except Exception as e:
        print(f"Произошла ошибка при создании пользователя: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    create_user()
