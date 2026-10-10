from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from app.config import settings
from app.services.errors import NotFoundError, RequestError
from app.services.ids import next_id
from app.services.nova import get_nova_client
from app.services.storage import delete_stored, save_image, sha256_bytes

DONE_STATUSES = {"EXACT", "SIMILAR", "MISSING", "NOT_DETECTED", "NEEDS_REVIEW", "AI_FAILED"}
LINK_STATUSES = {"EXACT", "SIMILAR", "NEEDS_REVIEW"}


def now() -> datetime:
    return datetime.now(timezone.utc)


def _public_box(prop: dict[str, Any]) -> dict[str, float] | None:
    detection = prop.get("detection")
    box = detection.get("box") if isinstance(detection, dict) else None
    if not isinstance(box, dict):
        return None
    try:
        values = {key: float(box[key]) for key in ("x", "y", "width", "height")}
    except (KeyError, TypeError, ValueError):
        return None
    if values["width"] <= 0 or values["height"] <= 0:
        return None
    return values


def availability_for(match_status: str | None, inventory_id: str | None) -> str:
    if match_status in {"PENDING", "PROCESSING"}:
        return "checking"
    if inventory_id and match_status in {"EXACT", "SIMILAR", "NEEDS_REVIEW"}:
        return "available"
    return "unavailable"


def empty_progress() -> dict[str, int]:
    return {
        "totalProps": 0,
        "processedProps": 0,
        "successfulProps": 0,
        "failedProps": 0,
        "reviewRequired": 0,
        "missingProps": 0,
    }


def public(document: dict[str, Any] | None) -> dict[str, Any] | None:
    if document is None:
        return None
    item = dict(document)
    item.pop("_id", None)
    item.pop("embedding", None)
    return item


async def create_theme(db: AsyncIOMotorDatabase, name: str, status: str = "active") -> dict[str, Any]:
    timestamp = now()
    document = {
        "themeId": await next_id(db, "theme", "THEME"),
        "name": name,
        "mainImage": None,
        "status": status,
        "progress": empty_progress(),
        "createdAt": timestamp,
        "updatedAt": timestamp,
    }
    await db.themes.insert_one(document)
    return public(document)


async def get_theme(db: AsyncIOMotorDatabase, theme_id: str) -> dict[str, Any]:
    document = await db.themes.find_one({"themeId": theme_id}, {"_id": 0})
    if document is None:
        raise NotFoundError("Theme not found")
    return document


async def list_themes(db: AsyncIOMotorDatabase, skip: int, limit: int) -> dict[str, Any]:
    total = await db.themes.count_documents({})
    cursor = db.themes.find({}, {"_id": 0}).sort("createdAt", -1).skip(skip).limit(limit)
    return {"items": await cursor.to_list(length=limit), "total": total}


async def update_theme(db: AsyncIOMotorDatabase, theme_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    await get_theme(db, theme_id)
    changes["updatedAt"] = now()
    document = await db.themes.find_one_and_update(
        {"themeId": theme_id},
        {"$set": changes},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    return document


async def set_main_image(
    db: AsyncIOMotorDatabase,
    theme_id: str,
    data: bytes,
    content_type: str,
) -> dict[str, Any]:
    theme = await get_theme(db, theme_id)
    if theme.get("mainImage"):
        delete_stored(theme["mainImage"])
    image_url = save_image(data, f"themes/{theme_id}", content_type)
    return await update_theme(db, theme_id, {"mainImage": image_url})


async def clear_theme_props(db: AsyncIOMotorDatabase, theme_id: str) -> None:
    await get_theme(db, theme_id)
    props = await db.theme_props.find({"themeId": theme_id}).to_list(length=1000)
    for prop in props:
        delete_stored(prop.get("sourceImage"))
    await db.theme_props.delete_many({"themeId": theme_id})
    await db.theme_items.delete_many({"themeId": theme_id})
    await refresh_theme_progress(db, theme_id)


async def add_scene_prop(
    db: AsyncIOMotorDatabase,
    theme_id: str,
    data: bytes,
    content_type: str,
    detection: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    await get_theme(db, theme_id)
    name = str(detection.get("detectedObject") or f"Prop {index + 1}")
    image_hash = sha256_bytes(data + f":{index}:{name}".encode())
    timestamp = now()
    document = {
        "propId": await next_id(db, "theme_prop", "PROPIMG"),
        "themeId": theme_id,
        "sourceImage": save_image(data, f"themes/{theme_id}/props", content_type),
        "imageHash": image_hash,
        "filename": f"{name}.jpg",
        "contentType": content_type,
        "detectedObject": name,
        "detection": detection,
        "matchStatus": "PENDING",
        "inventoryItemId": None,
        "role": detection.get("role") or "prop",
        "required": True,
        "detectionConfidence": detection.get("confidence"),
        "vectorSimilarity": None,
        "verificationConfidence": None,
        "finalConfidence": None,
        "aiReason": None,
        "candidates": [],
        "reviewStatus": None,
        "reviewedBy": None,
        "reviewedAt": None,
        "error": None,
        "source": "theme",
        "createdAt": timestamp,
        "updatedAt": timestamp,
    }
    await db.theme_props.insert_one(document)
    await refresh_theme_progress(db, theme_id)
    return public(document)


async def add_prop_image(
    db: AsyncIOMotorDatabase,
    theme_id: str,
    data: bytes,
    content_type: str,
    filename: str,
) -> dict[str, Any]:
    await get_theme(db, theme_id)
    image_hash = sha256_bytes(data)
    existing = await db.theme_props.find_one({"themeId": theme_id, "imageHash": image_hash}, {"_id": 0})
    if existing:
        existing["alreadyExists"] = True
        return existing
    timestamp = now()
    document = {
        "propId": await next_id(db, "theme_prop", "PROPIMG"),
        "themeId": theme_id,
        "sourceImage": save_image(data, f"themes/{theme_id}/props", content_type),
        "imageHash": image_hash,
        "filename": filename,
        "contentType": content_type,
        "detectedObject": None,
        "detection": None,
        "matchStatus": "PENDING",
        "inventoryItemId": None,
        "role": None,
        "required": True,
        "detectionConfidence": None,
        "vectorSimilarity": None,
        "verificationConfidence": None,
        "finalConfidence": None,
        "aiReason": None,
        "candidates": [],
        "reviewStatus": None,
        "reviewedBy": None,
        "reviewedAt": None,
        "error": None,
        "createdAt": timestamp,
        "updatedAt": timestamp,
    }
    await db.theme_props.insert_one(document)
    await refresh_theme_progress(db, theme_id)
    return public(document)


async def list_props(db: AsyncIOMotorDatabase, theme_id: str) -> list[dict[str, Any]]:
    await get_theme(db, theme_id)
    cursor = db.theme_props.find({"themeId": theme_id}, {"_id": 0}).sort("createdAt", 1)
    return await cursor.to_list(length=500)


async def get_prop(db: AsyncIOMotorDatabase, theme_id: str, prop_id: str) -> dict[str, Any]:
    document = await db.theme_props.find_one({"themeId": theme_id, "propId": prop_id}, {"_id": 0})
    if document is None:
        raise NotFoundError("Prop image not found")
    return document


async def refresh_theme_progress(db: AsyncIOMotorDatabase, theme_id: str) -> dict[str, int]:
    props = await db.theme_props.find({"themeId": theme_id}, {"matchStatus": 1}).to_list(length=1000)
    progress = empty_progress()
    progress["totalProps"] = len(props)
    for prop in props:
        status = prop.get("matchStatus")
        if status in DONE_STATUSES:
            progress["processedProps"] += 1
        if status == "EXACT":
            progress["successfulProps"] += 1
        if status == "AI_FAILED":
            progress["failedProps"] += 1
        if status in {"SIMILAR", "NEEDS_REVIEW"}:
            progress["reviewRequired"] += 1
        if status == "MISSING":
            progress["missingProps"] += 1
    await db.themes.update_one(
        {"themeId": theme_id},
        {"$set": {"progress": progress, "updatedAt": now()}},
    )
    return progress


async def save_prop_result(db: AsyncIOMotorDatabase, prop: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    result["updatedAt"] = now()
    await db.theme_props.find_one_and_update(
        {"propId": prop["propId"]},
        {"$set": result},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    fresh = await db.theme_props.find_one({"propId": prop["propId"]}, {"_id": 0})
    await sync_theme_link(db, fresh)
    fresh = await db.theme_props.find_one({"propId": prop["propId"]}, {"_id": 0})
    await refresh_theme_progress(db, prop["themeId"])
    return fresh


async def sync_theme_link(db: AsyncIOMotorDatabase, prop: dict[str, Any]) -> None:
    theme_id = prop["themeId"]
    prop_id = prop["propId"]
    inventory_id = prop.get("inventoryItemId")
    status = prop.get("matchStatus")
    if status in LINK_STATUSES and inventory_id:
        timestamp = now()
        link = {
            "themeId": theme_id,
            "inventoryItemId": inventory_id,
            "propId": prop_id,
            "role": prop.get("role") or "prop",
            "required": True,
            "sourceImage": prop.get("sourceImage"),
            "matchStatus": status,
            "detectionConfidence": prop.get("detectionConfidence"),
            "vectorSimilarity": prop.get("vectorSimilarity"),
            "verificationConfidence": prop.get("verificationConfidence"),
            "finalConfidence": prop.get("finalConfidence"),
            "aiReason": prop.get("aiReason"),
            "reviewStatus": prop.get("reviewStatus"),
            "reviewedBy": prop.get("reviewedBy"),
            "reviewedAt": prop.get("reviewedAt"),
            "updatedAt": timestamp,
        }
        await db.theme_items.update_one(
            {"themeId": theme_id, "propId": prop_id},
            {"$set": link, "$setOnInsert": {"createdAt": timestamp}},
            upsert=True,
        )
        return
    await db.theme_items.delete_many({"themeId": theme_id, "propId": prop_id})


async def theme_result(db: AsyncIOMotorDatabase, theme_id: str) -> dict[str, Any]:
    theme = await get_theme(db, theme_id)
    props = await list_props(db, theme_id)
    inventory_ids = [prop["inventoryItemId"] for prop in props if prop.get("inventoryItemId")]
    items: dict[str, dict[str, Any]] = {}
    if inventory_ids:
        cursor = db.inventory_items.find(
            {"inventoryId": {"$in": inventory_ids}},
            {"_id": 0, "inventoryId": 1, "name": 1, "status": 1},
        )
        async for item in cursor:
            items[item["inventoryId"]] = item
    rows = []
    for prop in props:
        inventory = items.get(prop.get("inventoryItemId") or "")
        inventory_status = None if inventory is None else inventory.get("status")
        rows.append(
            {
                "propId": prop["propId"],
                "detectedObject": prop.get("detectedObject"),
                "sourceImage": prop.get("sourceImage"),
                "box": _public_box(prop),
                "inventoryItemId": prop.get("inventoryItemId"),
                "inventoryName": None if inventory is None else inventory.get("name"),
                "inventoryStatus": inventory_status,
                "availability": availability_for(prop.get("matchStatus"), prop.get("inventoryItemId")),
                "finalConfidence": prop.get("finalConfidence"),
                "matchStatus": prop.get("matchStatus"),
                "detectionConfidence": prop.get("detectionConfidence"),
                "vectorSimilarity": prop.get("vectorSimilarity"),
                "verificationConfidence": prop.get("verificationConfidence"),
                "aiReason": prop.get("aiReason"),
                "reviewStatus": prop.get("reviewStatus"),
                "candidates": prop.get("candidates") or [],
            }
        )
    return {"theme": theme, "rows": rows}


async def add_inventory_image(
    db: AsyncIOMotorDatabase,
    inventory_id: str,
    data: bytes,
    content_type: str,
    filename: str,
) -> dict[str, Any]:
    item = await db.inventory_items.find_one({"inventoryId": inventory_id}, {"_id": 0, "inventoryId": 1})
    if item is None:
        raise NotFoundError("Inventory item not found")
    count = await db.inventory_images.count_documents({"inventoryId": inventory_id})
    if count >= settings.max_inventory_images:
        raise RequestError(f"An inventory item can have at most {settings.max_inventory_images} reference images")
    image_hash = sha256_bytes(data)
    embedding = None
    warning = None
    client = get_nova_client()
    try:
        embedding, _usage = await embed_with_cache(db, data, content_type, client, purpose="GENERIC_INDEX")
    except Exception as exc:
        warning = str(exc)
    timestamp = now()
    document = {
        "imageId": await next_id(db, "inventory_image", "IMG"),
        "inventoryId": inventory_id,
        "imageUrl": save_image(data, f"inventory/{inventory_id}", content_type),
        "imageHash": image_hash,
        "embedding": embedding,
        "metadata": {
            "filename": filename,
            "contentType": content_type,
            "size": len(data),
            "embeddingError": warning,
            "embeddingModel": getattr(client, "embedding_model", None) if embedding else None,
        },
        "createdAt": timestamp,
        "updatedAt": timestamp,
    }
    await db.inventory_images.insert_one(document)
    stored = public(document)
    stored["embeddingReady"] = embedding is not None
    if warning:
        stored["warning"] = warning
    return stored


async def list_inventory_images(db: AsyncIOMotorDatabase, inventory_id: str) -> list[dict[str, Any]]:
    cursor = db.inventory_images.find({"inventoryId": inventory_id}).sort("createdAt", 1)
    images = []
    async for image in cursor:
        image.pop("_id", None)
        embedding = image.pop("embedding", None)
        image["embeddingReady"] = isinstance(embedding, list) and len(embedding) > 0
        images.append(image)
    return images


async def delete_inventory_image(db: AsyncIOMotorDatabase, inventory_id: str, image_id: str) -> None:
    document = await db.inventory_images.find_one_and_delete(
        {"inventoryId": inventory_id, "imageId": image_id}
    )
    if document is None:
        raise NotFoundError("Inventory image not found")
    delete_stored(document.get("imageUrl"))


async def embed_with_cache(
    db: AsyncIOMotorDatabase,
    data: bytes,
    content_type: str,
    client,
    *,
    purpose: str = "GENERIC_INDEX",
) -> tuple[list[float], bool]:
    from app.services.matching import cached_embedding

    return await cached_embedding(db, data, content_type, client, purpose=purpose)
