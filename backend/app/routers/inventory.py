import re

from fastapi import APIRouter, HTTPException, Query, Request
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import PyMongoError

from app.database import ensure_database

from app.schemas.inventory import InventoryCreate, InventoryItem, InventoryList, InventoryStatus, InventoryUpdate
from app.services.ids import IdAllocationError
from app.services.inventory import (
    create_inventory_item,
    get_inventory_item,
    list_inventory_items,
    update_inventory_item,
)

router = APIRouter(prefix="/api/inventory", tags=["inventory"])

INVENTORY_ID = re.compile(r"^PROP-\d+$")


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


def _require_known_id(inventory_id: str) -> str:
    if not INVENTORY_ID.fullmatch(inventory_id):
        raise HTTPException(status_code=404, detail="Inventory item not found")
    return inventory_id


@router.get("", response_model=InventoryList)
async def list_items(
    request: Request,
    q: str | None = Query(default=None, max_length=200),
    name: str | None = Query(default=None, max_length=200),
    category: str | None = Query(default=None, max_length=100),
    status: InventoryStatus | None = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> InventoryList:
    result = await list_inventory_items(
        await get_database(request),
        q=q.strip() if q else None,
        name=name.strip() if name else None,
        category=category.strip() if category else None,
        status=status.value if status else None,
        skip=skip,
        limit=limit,
    )
    return InventoryList.model_validate(result)


@router.post("", response_model=InventoryItem, status_code=201)
async def create_item(payload: InventoryCreate, request: Request) -> InventoryItem:
    try:
        document = await create_inventory_item(await get_database(request), payload)
    except IdAllocationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return InventoryItem.model_validate(document)


@router.get("/{inventory_id}", response_model=InventoryItem)
async def get_item(inventory_id: str, request: Request) -> InventoryItem:
    document = await get_inventory_item(await get_database(request), _require_known_id(inventory_id))
    if document is None:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    return InventoryItem.model_validate(document)


@router.patch("/{inventory_id}", response_model=InventoryItem)
async def update_item(inventory_id: str, payload: InventoryUpdate, request: Request) -> InventoryItem:
    changes = {
        key: value
        for key, value in payload.model_dump(exclude_unset=True).items()
        if value is not None
    }
    if not changes:
        raise HTTPException(status_code=400, detail="No fields to update")
    document = await update_inventory_item(
        await get_database(request),
        _require_known_id(inventory_id),
        payload,
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    return InventoryItem.model_validate(document)
