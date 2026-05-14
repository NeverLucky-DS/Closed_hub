from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="")


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request):
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        return JSONResponse(status_code=503, content={"ready": False})
    try:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except Exception:
        return JSONResponse(status_code=503, content={"ready": False})
    return {"ready": True}
