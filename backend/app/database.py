import asyncio
import logging
import time
from datetime import timezone

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import PyMongoError, ServerSelectionTimeoutError

from app.config import settings

logger = logging.getLogger(__name__)

client: AsyncIOMotorClient | None = None
database: AsyncIOMotorDatabase | None = None
_connect_lock = asyncio.Lock()
_last_failure = 0.0


async def connect() -> AsyncIOMotorDatabase:
    global client, database
    new_client = AsyncIOMotorClient(
        settings.mongodb_uri,
        serverSelectionTimeoutMS=3000,
        tz_aware=True,
        tzinfo=timezone.utc,
    )
    try:
        await new_client.admin.command("ping")
    except PyMongoError:
        new_client.close()
        raise
    if client is not None:
        client.close()
    client = new_client
    database = client[settings.mongodb_db]
    await ensure_indexes(database)
    logger.info("Connected to MongoDB database %s", settings.mongodb_db)
    return database


async def ensure_database() -> AsyncIOMotorDatabase:
    global _last_failure
    if database is not None:
        return database
    async with _connect_lock:
        if database is not None:
            return database
        if time.monotonic() - _last_failure < 2:
            raise ServerSelectionTimeoutError("MongoDB is not available")
        try:
            return await connect()
        except PyMongoError:
            _last_failure = time.monotonic()
            raise


async def close() -> None:
    global client, database
    if client is not None:
        client.close()
    client = None
    database = None


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.inventory_items.create_index("inventoryId", unique=True)
    await db.inventory_items.create_index("status")
    await db.inventory_items.create_index("category")
    await db.inventory_items.create_index("name")
    await db.inventory_images.create_index("imageId", unique=True)
    await db.inventory_images.create_index("inventoryId")
    await db.inventory_images.create_index("imageHash")
    await db.themes.create_index("themeId", unique=True)
    await db.theme_props.create_index("propId", unique=True)
    await db.theme_props.create_index([("themeId", 1), ("imageHash", 1)], unique=True)
    await db.theme_props.create_index([("themeId", 1), ("matchStatus", 1)])
    await db.theme_items.create_index([("themeId", 1), ("inventoryItemId", 1)], unique=True)
    await db.theme_items.create_index("propId")
    await db.ai_match_logs.create_index("requestId", unique=True)
    await db.ai_match_logs.create_index([("themeId", 1), ("createdAt", -1)])
    await db.analysis_cache.create_index(
        [("imageHash", 1), ("kind", 1), ("model", 1)],
        unique=True,
    )
    await db.jobs.create_index("jobId", unique=True)
    await db.jobs.create_index([("status", 1), ("createdAt", 1)])
    await db.jobs.create_index("themeId")
