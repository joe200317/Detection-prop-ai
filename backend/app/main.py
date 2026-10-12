import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pymongo.errors import PyMongoError

from app.config import settings
from app.database import close, ensure_database
from app.routers.images import router as image_router
from app.routers.inventory import router as inventory_router
from app.routers.themes import router as theme_router
from app.services.errors import AIServiceError
from app.services.storage import open_media, upload_root
from app.worker import job_worker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = None
    app.state.db_error = None
    try:
        app.state.db = await ensure_database()
    except PyMongoError as exc:
        app.state.db_error = "MongoDB is not available. Start MongoDB on localhost:27017."
        logger.error("%s (%s)", app.state.db_error, exc.__class__.__name__)
    stop = asyncio.Event()
    worker = asyncio.create_task(job_worker(stop)) if settings.worker_enabled else None
    yield
    stop.set()
    if worker is not None:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
    await close()
    app.state.db = None


app = FastAPI(title="Prop Inventory", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(inventory_router)
app.include_router(image_router)
app.include_router(theme_router)
_legacy_uploads = upload_root()
if _legacy_uploads.is_dir():
    app.mount("/uploads", StaticFiles(directory=str(_legacy_uploads)), name="uploads")


@app.get("/media/{key:path}")
async def media(key: str) -> Response:
    try:
        data, content_type = open_media(key)
    except AIServiceError:
        raise HTTPException(status_code=404, detail="Image not found") from None
    return Response(content=data, media_type=content_type, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/health")
async def health() -> dict[str, str]:
    if app.state.db is None:
        try:
            app.state.db = await ensure_database()
        except PyMongoError:
            app.state.db = None
    if app.state.db is None:
        return {
            "status": "ok",
            "database": "disconnected",
            "databaseName": settings.mongodb_db,
        }
    return {
        "status": "ok",
        "database": "connected",
        "databaseName": settings.mongodb_db,
    }
