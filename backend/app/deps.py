from fastapi import HTTPException, Request
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import PyMongoError

from app.database import ensure_database


async def get_database(request: Request) -> AsyncIOMotorDatabase:
    try:
        db = await ensure_database()
    except PyMongoError:
        request.app.state.db = None
        raise HTTPException(
            status_code=503,
            detail="MongoDB is not available. Start MongoDB on localhost:27017.",
        ) from None
    request.app.state.db = db
    return db
