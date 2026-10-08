from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument


class IdAllocationError(RuntimeError):
    pass


async def next_id(db: AsyncIOMotorDatabase, counter: str, prefix: str) -> str:
    result = await db.counters.find_one_and_update(
        {"_id": counter},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    if result is None or "seq" not in result:
        raise IdAllocationError(f"Could not allocate a {prefix} ID")
    return f"{prefix}-{int(result['seq']):03d}"


async def next_inventory_id(db: AsyncIOMotorDatabase) -> str:
    return await next_id(db, "inventory", "PROP")
