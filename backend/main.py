import os
import time
import logging
from fastapi import FastAPI, Depends, HTTPException, status, Response
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from datetime import timedelta
from sqlalchemy.orm import Session
from database import engine, Base, get_db
import models, auth

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Otto Mapping Tool API",
    openapi_url="/api/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    servers=[{"url": "/api", "description": "Default API"}]
)

@app.get("/openapi.json", include_in_schema=False)
@app.get("/api/openapi.json", include_in_schema=False)
def get_openapi_endpoint():
    return app.openapi()

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/health/disk-usage")
def disk_usage():
    import shutil
    base_static = os.path.join(os.path.dirname(__file__), "static")
    mirrored_path = os.path.join(base_static, "mirrored")
    cache_path = os.path.join(base_static, "cache", "images")
    
    def get_dir_stats(path):
        if not os.path.exists(path):
            return {"files_count": 0, "total_mb": 0.0}
        files = [os.path.join(path, f) for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]
        total_bytes = sum(os.path.getsize(f) for f in files)
        return {
            "files_count": len(files),
            "total_mb": round(total_bytes / (1024 * 1024), 2),
        }

    disk_info = shutil.disk_usage(base_static if os.path.exists(base_static) else "/")
    return {
        "status": "ok",
        "mirrored": get_dir_stats(mirrored_path),
        "cache": get_dir_stats(cache_path),
        "disk_free_gb": round(disk_info.free / (1024 ** 3), 2),
        "disk_total_gb": round(disk_info.total / (1024 ** 3), 2),
    }



# ====== CORS ======
raw_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
allowed_origins = [o.strip() for o in raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1000)

from fastapi.staticfiles import StaticFiles
os.makedirs("static/mirrored", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

from routers import aftercool, drafts, mapping, validation, export, stats, csv_workflow, settings, publish_pipeline, analytics, published_export

# Secure all data routers behind authentication
auth_dep = [Depends(auth.get_current_user)]
app.include_router(aftercool.router, dependencies=auth_dep)
app.include_router(drafts.router, dependencies=auth_dep)
app.include_router(mapping.router, dependencies=auth_dep)
app.include_router(validation.router, dependencies=auth_dep)
app.include_router(export.router, dependencies=auth_dep)
app.include_router(stats.router, dependencies=auth_dep)
app.include_router(csv_workflow.router, dependencies=auth_dep)
app.include_router(settings.router, dependencies=auth_dep)
app.include_router(publish_pipeline.router, dependencies=auth_dep)
app.include_router(analytics.router, dependencies=auth_dep)

# Published products export for external services (has its own dual X-API-Key / JWT authentication)
app.include_router(published_export.router)

# ====== AUTH ROUTES ======
@app.post("/token")
async def login_for_access_token(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    user = db.query(models.User).filter(models.User.username == form_data.username).first()
    if not user or not auth.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=auth.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = auth.create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    
    # Set HttpOnly cookie for secure browser session
    is_secure = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        samesite="lax",
        secure=is_secure,
        max_age=auth.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    return {"access_token": access_token, "token_type": "bearer", "is_admin": user.is_admin}

@app.post("/logout")
async def logout(response: Response):
    response.delete_cookie("access_token")
    return {"status": "success", "message": "Logged out successfully"}

@app.get("/users/me")
async def read_users_me(current_user: models.User = Depends(auth.get_current_user)):
    return {"username": current_user.username, "is_admin": current_user.is_admin}

# ====== INIT SCRIPT ======
@app.on_event("startup")
def apply_safe_migrations():
    import logging
    from database import engine
    from sqlalchemy import text
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE publish_queue_items ADD COLUMN IF NOT EXISTS missing_attributes JSON;"))
            conn.execute(text("ALTER TABLE product_mappings ADD COLUMN IF NOT EXISTS missing_attributes JSON;"))
            conn.execute(text("ALTER TABLE product_mappings ADD COLUMN IF NOT EXISTS last_error TEXT;"))
            conn.execute(text("ALTER TABLE product_mappings ADD COLUMN IF NOT EXISTS has_ai_title BOOLEAN DEFAULT FALSE;"))
            conn.execute(text("ALTER TABLE product_mappings ADD COLUMN IF NOT EXISTS has_manual_price BOOLEAN DEFAULT FALSE;"))
            conn.execute(text("ALTER TABLE product_mappings ADD COLUMN IF NOT EXISTS source_account VARCHAR;"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_product_mappings_source_account ON product_mappings(source_account);"))
            conn.execute(text("""
                UPDATE product_mappings 
                SET has_ai_title = TRUE 
                WHERE account_name = 'XL' 
                  AND product_name IS NOT NULL 
                  AND LENGTH(TRIM(product_name)) > 0 
                  AND LENGTH(TRIM(product_name)) <= 70 
                  AND (has_ai_title IS NULL OR has_ai_title = FALSE);
            """))
            logging.info("Safe migrations applied successfully.")
    except Exception as e:
        logging.warning(f"Safe migrations failed (might be already applied or unsupported DB): {e}")

@app.on_event("startup")
def create_initial_admin():
    admin_username = os.getenv("ADMIN_USERNAME", "yana")
    admin_password = os.getenv("ADMIN_PASSWORD")
    if not admin_password:
        return
        
    import time
    for attempt in range(5):
        try:
            db = next(get_db())
            admin_user = db.query(models.User).filter(models.User.username == admin_username).first()
            hashed_password = auth.get_password_hash(admin_password)
            if not admin_user:
                # Check if legacy "admin" exists and rename to configured admin_username
                legacy_admin = db.query(models.User).filter(models.User.username == "admin").first()
                if legacy_admin and admin_username != "admin":
                    legacy_admin.username = admin_username
                    legacy_admin.hashed_password = hashed_password
                    legacy_admin.is_admin = True
                    admin_user = legacy_admin
                else:
                    new_admin = models.User(username=admin_username, hashed_password=hashed_password, is_admin=True)
                    db.add(new_admin)
            else:
                admin_user.hashed_password = hashed_password
            db.commit()
            import sync_users
            sync_users.dump_users_to_json()

            # Ensure account_settings has entries for JV and XL from env if present
            for acc in ("JV", "XL"):
                setting = db.query(models.AccountSettings).filter(models.AccountSettings.account_name == acc).first()
                if not setting:
                    setting = models.AccountSettings(
                        account_name=acc,
                        otto_client_id=os.getenv(f"OTTO_{acc}_CLIENT_ID"),
                        otto_client_secret=os.getenv(f"OTTO_{acc}_CLIENT_SECRET"),
                        otto_partner_id=os.getenv(f"OTTO_{acc}_PARTNER_ID"),
                        otto_brand_id=os.getenv(f"OTTO_{acc}_BRAND_ID"),
                        otto_app_id=os.getenv(f"OTTO_{acc}_APP_ID"),
                    )
                    db.add(setting)
                else:
                    if not setting.otto_client_id and os.getenv(f"OTTO_{acc}_CLIENT_ID"):
                        setting.otto_client_id = os.getenv(f"OTTO_{acc}_CLIENT_ID")
                    if not setting.otto_client_secret and os.getenv(f"OTTO_{acc}_CLIENT_SECRET"):
                        setting.otto_client_secret = os.getenv(f"OTTO_{acc}_CLIENT_SECRET")
                    if not setting.otto_partner_id and os.getenv(f"OTTO_{acc}_PARTNER_ID"):
                        setting.otto_partner_id = os.getenv(f"OTTO_{acc}_PARTNER_ID")
                    if not setting.otto_brand_id and os.getenv(f"OTTO_{acc}_BRAND_ID"):
                        setting.otto_brand_id = os.getenv(f"OTTO_{acc}_BRAND_ID")
                    if not setting.otto_app_id and os.getenv(f"OTTO_{acc}_APP_ID"):
                        setting.otto_app_id = os.getenv(f"OTTO_{acc}_APP_ID")
            db.commit()
            break
        except Exception as e:
            logging.warning(f"Database connection attempt {attempt + 1}/5 failed: {e}. Retrying in 2s...")
            time.sleep(2)

@app.on_event("startup")
def seed_initial_categories():
    try:
        from scripts.seed_categories import seed_categories
        seed_categories()
    except Exception as e:
        logging.warning(f"Failed to auto-seed categories on startup: {e}")

# ====== USER MANAGEMENT (ADMIN ONLY) ======
from pydantic import BaseModel

class UserCreate(BaseModel):
    username: str
    password: str
    is_admin: bool = False

@app.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(user: UserCreate, db: Session = Depends(get_db), current_admin: models.User = Depends(auth.get_current_admin_user)):
    existing = db.query(models.User).filter(models.User.username == user.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already registered")
    
    hashed_password = auth.get_password_hash(user.password)
    db_user = models.User(username=user.username, hashed_password=hashed_password, is_admin=user.is_admin)
    db.add(db_user)
    db.commit()
    import sync_users
    sync_users.dump_users_to_json()
    db.refresh(db_user)
    return {"msg": "User created", "id": db_user.id}

@app.get("/users")
async def get_users(db: Session = Depends(get_db), current_admin: models.User = Depends(auth.get_current_admin_user)):
    users = db.query(models.User).all()
    return [{"id": u.id, "username": u.username, "is_admin": u.is_admin} for u in users]
