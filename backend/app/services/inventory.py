import re
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.schemas.inventory import InventoryCreate, InventoryUpdate
from app.services.ids import IdAllocationError, next_inventory_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _public(document: dict[str, Any]) -> dict[str, Any]:
    item = dict(document)
    item.pop("_id", None)
    return item


async def create_inventory_item(db: AsyncIOMotorDatabase, payload: InventoryCreate) -> dict[str, Any]:
    for _ in range(3):
        inventory_id = await next_inventory_id(db)
        now = _now()
        document = {
            "inventoryId": inventory_id,
            "name": payload.name,
            "category": payload.category,
            "subcategory": payload.subcategory,
            "description": payload.description,
            "attributes": payload.attributes,
            "status": payload.status.value,
            "createdAt": now,
            "updatedAt": now,
        }
        try:
            await db.inventory_items.insert_one(document)
        except DuplicateKeyError:
            continue
        return _public(document)
    raise IdAllocationError("Could not allocate a unique inventory ID")


async def get_inventory_item(db: AsyncIOMotorDatabase, inventory_id: str) -> dict[str, Any] | None:
    document = await db.inventory_items.find_one({"inventoryId": inventory_id}, {"_id": 0})
    return document


async def list_inventory_items(
    db: AsyncIOMotorDatabase,
    *,
    q: str | None,
    name: str | None,
    category: str | None,
    status: str | None,
    skip: int,
    limit: int,
) -> dict[str, Any]:
    clauses: list[dict[str, Any]] = []
    if name:
        clauses.append({"name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}})
    if q:
        clauses.append({"name": {"$regex": re.escape(q), "$options": "i"}})
    if category:
        clauses.append({"category": category})
    if status:
        clauses.append({"status": status})
    query = {"$and": clauses} if clauses else {}
    total = await db.inventory_items.count_documents(query)
    cursor = (
        db.inventory_items.find(query, {"_id": 0})
        .sort("createdAt", -1)
        .skip(skip)
        .limit(limit)
    )
    items = await cursor.to_list(length=limit)
    return {"items": items, "total": total}


async def update_inventory_item(
    db: AsyncIOMotorDatabase,
    inventory_id: str,
    payload: InventoryUpdate,
) -> dict[str, Any] | None:
    changes = {
        key: value
        for key, value in payload.model_dump(exclude_unset=True).items()
        if value is not None
    }
    if not changes:
        return await get_inventory_item(db, inventory_id)
    if "status" in changes and changes["status"] is not None:
        changes["status"] = changes["status"].value
    changes["updatedAt"] = _now()
    document = await db.inventory_items.find_one_and_update(
        {"inventoryId": inventory_id},
        {"$set": changes},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    return document
