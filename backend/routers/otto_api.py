"""
Deprecated legacy Otto publish router.
All product publishing is managed through the Drip Feed publish pipeline (/pipeline).
"""
from fastapi import APIRouter, HTTPException

router = APIRouter(
    prefix="/otto",
    tags=["otto_api_deprecated"],
)


@router.post("/publish/{factory_id}")
async def publish_to_otto(factory_id: str):
    raise HTTPException(
        status_code=410,
        detail="This legacy endpoint is deprecated. Use the Drip Feed pipeline (/pipeline/create/{factory_id}) instead."
    )


@router.get("/publish/status/{task_id}")
async def get_publish_status(task_id: str):
    raise HTTPException(
        status_code=410,
        detail="This legacy endpoint is deprecated. Use the Drip Feed pipeline (/pipeline/plan/{plan_id}/items) instead."
    )

