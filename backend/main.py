from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from datetime import timedelta
from sqlalchemy.orm import Session
from database import engine, Base, get_db
import models, auth

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Otto Mapping Tool API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # For dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

from routers import aftercool, drafts, mapping, validation, export, stats, csv_workflow, settings

app.include_router(aftercool.router)
app.include_router(drafts.router)
app.include_router(mapping.router)
app.include_router(validation.router)
app.include_router(export.router)
app.include_router(stats.router)
app.include_router(csv_workflow.router)
app.include_router(settings.router)
# ====== AUTH ROUTES ======
@app.post("/token")
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
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
    return {"access_token": access_token, "token_type": "bearer", "is_admin": user.is_admin}

@app.get("/users/me")
async def read_users_me(current_user: models.User = Depends(auth.get_current_user)):
    return {"username": current_user.username, "is_admin": current_user.is_admin}

# ====== INIT SCRIPT ======
@app.on_event("startup")
def create_initial_admin():
    db = next(get_db())
    admin_user = db.query(models.User).filter(models.User.username == "admin").first()
    if not admin_user:
        hashed_password = auth.get_password_hash("admin")
        new_admin = models.User(username="admin", hashed_password=hashed_password, is_admin=True)
        db.add(new_admin)
        db.commit()
        import sync_users
        sync_users.dump_users_to_json()

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
