from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
import models
from tasks import publish_factory_to_otto_task
from celery.result import AsyncResult

router = APIRouter(
    prefix="/otto",
    tags=["otto_api"],
)

@router.post("/publish/{factory_id}")
async def publish_to_otto(factory_id: str, db: Session = Depends(get_db)):
    # Retrieve account settings to get credentials (assuming default account "JV" for now)
    settings = db.query(models.AccountSettings).filter(models.AccountSettings.account_name == "JV").first()
    if not settings or not settings.otto_client_id or not settings.otto_client_secret:
        raise HTTPException(status_code=400, detail="Otto API credentials missing. Please configure in settings.")

    # Dispatch Celery task
    try:
        task = publish_factory_to_otto_task.delay(factory_id, "JV", settings.otto_client_id, settings.otto_client_secret)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to queue task. Is Redis running? Error: {str(e)}")
    
    return {
        "status": "queued",
        "task_id": task.id,
        "message": "Publishing task started in background."
    }

@router.get("/publish/status/{task_id}")
async def get_publish_status(task_id: str):
    task_result = AsyncResult(task_id)
    response = {
        "task_id": task_id,
        "status": task_result.status,
        "result": task_result.result if task_result.ready() else None,
    }
    if task_result.status == "PROGRESS":
        response["progress"] = task_result.info
    elif task_result.status == "FAILURE":
        response["error"] = str(task_result.info)
    return response
