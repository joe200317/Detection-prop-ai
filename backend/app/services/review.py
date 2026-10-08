from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.services.errors import NotFoundError, RequestError
from app.services.jobs import enqueue_job
from app.services.themes import get_prop, now, save_prop_result


async def list_review_items(db: AsyncIOMotorDatabase) -> list[dict[str, Any]]:
    cursor = db.theme_props.find(
        {"matchStatus": {"$in": ["SIMILAR", "NEEDS_REVIEW"]}},
        {"_id": 0},
    ).sort("updatedAt", -1)
    props = await cursor.to_list(length=200)
    theme_ids = list({prop["themeId"] for prop in props})
    themes = {}
    if theme_ids:
        async for theme in db.themes.find({"themeId": {"$in": theme_ids}}, {"_id": 0, "themeId": 1, "name": 1}):
            themes[theme["themeId"]] = theme.get("name")
    for prop in props:
        prop["themeName"] = themes.get(prop["themeId"])
    return props


async def apply_review(
    db: AsyncIOMotorDatabase,
    theme_id: str,
    prop_id: str,
    action: str,
    inventory_item_id: str | None,
    reviewed_by: str,
) -> dict[str, Any]:
    prop = await get_prop(db, theme_id, prop_id)
    if action == "rerun":
        job = await enqueue_job(db, theme_id, "prop", prop_id)
        prop["job"] = job
        return prop
    timestamp = now()
    if action == "approve":
        if not prop.get("inventoryItemId"):
            raise RequestError("There is no candidate to approve")
        changes = {"matchStatus": "EXACT", "reviewStatus": "approved"}
    elif action == "reject":
        changes = {
            "matchStatus": "NEEDS_REVIEW",
            "inventoryItemId": None,
            "reviewStatus": "rejected",
            "aiReason": "The suggested inventory match was rejected.",
        }
    elif action == "missing":
        changes = {
            "matchStatus": "MISSING",
            "inventoryItemId": None,
            "reviewStatus": "approved",
            "finalConfidence": None,
            "aiReason": "Marked missing by review. No inventory item was created.",
        }
    elif action == "select":
        if not inventory_item_id:
            raise RequestError("Choose an inventory item")
        item = await db.inventory_items.find_one({"inventoryId": inventory_item_id}, {"_id": 1})
        if item is None:
            raise NotFoundError("Inventory item not found")
        changes = {
            "matchStatus": "EXACT",
            "inventoryItemId": inventory_item_id,
            "reviewStatus": "approved",
            "aiReason": f"Reviewer selected {inventory_item_id}.",
        }
    else:
        raise RequestError("Unknown review action")
    changes["reviewedBy"] = reviewed_by or None
    changes["reviewedAt"] = timestamp
    prop.update(changes)
    return await save_prop_result(db, prop, changes)
