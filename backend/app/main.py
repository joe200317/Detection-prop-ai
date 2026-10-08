import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pymongo.errors import PyMongoError

from app.config import settings
from app.database import close, ensure_database
from app.routers.images import router as image_router
from app.routers.inventory import router as inventory_router
from app.routers.themes import router as theme_router
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
Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.upload_dir), name="uploads")


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
