import math
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.services.errors import AIServiceError


_generation = 0
_loaded_generation = -1
_catalog: list[dict[str, Any]] | None = None
_embeddings_ready = False


def invalidate_inventory_cache() -> None:
    global _generation, _embeddings_ready
    _generation += 1
    _embeddings_ready = False


def embeddings_ready() -> bool:
    return _embeddings_ready


def mark_embeddings_ready() -> None:
    global _embeddings_ready
    _embeddings_ready = True


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for a, b in zip(left, right):
        dot += a * b
        left_norm += a * a
        right_norm += b * b
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (math.sqrt(left_norm) * math.sqrt(right_norm))


def group_by_inventory(scored_images: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in scored_images:
        inventory_id = row["inventoryId"]
        current = grouped.get(inventory_id)
        score = {
            "imageId": row["imageId"],
            "imageUrl": row["imageUrl"],
            "similarity": round(float(row["similarity"]), 4),
        }
        if current is None:
            current = {
                "inventoryId": inventory_id,
                "name": row.get("name") or "",
                "category": row.get("category") or "",
                "description": row.get("description") or "",
                "status": row.get("status") or "",
                "bestSimilarity": float(row["similarity"]),
                "bestImageId": row["imageId"],
                "bestImageUrl": row["imageUrl"],
                "imageScores": [],
            }
            grouped[inventory_id] = current
        elif row["similarity"] > current["bestSimilarity"]:
            current["bestSimilarity"] = float(row["similarity"])
            current["bestImageId"] = row["imageId"]
            current["bestImageUrl"] = row["imageUrl"]
        current["imageScores"].append(score)
    ranked = sorted(grouped.values(), key=lambda item: item["bestSimilarity"], reverse=True)
    for candidate in ranked:
        candidate["bestSimilarity"] = round(candidate["bestSimilarity"], 4)
        candidate["imageScores"].sort(key=lambda score: score["similarity"], reverse=True)
    return ranked[:limit]


async def _load_catalog(db: AsyncIOMotorDatabase) -> list[dict[str, Any]]:
    global _catalog, _loaded_generation
    if _catalog is not None and _loaded_generation == _generation:
        return _catalog
    items: dict[str, dict[str, Any]] = {}
    item_cursor = db.inventory_items.find(
        {},
        {"_id": 0, "inventoryId": 1, "name": 1, "category": 1, "description": 1, "status": 1},
    )
    async for item in item_cursor:
        items[item["inventoryId"]] = item
    rows: list[dict[str, Any]] = []
    image_cursor = db.inventory_images.find(
        {"embedding": {"$type": "array"}},
        {"_id": 0, "inventoryId": 1, "imageId": 1, "imageUrl": 1, "embedding": 1},
    )
    async for image in image_cursor:
        item = items.get(image.get("inventoryId"))
        vector = image.get("embedding")
        if item is None or not isinstance(vector, list):
            continue
        rows.append(
            {
                "inventoryId": item["inventoryId"],
                "name": item.get("name"),
                "category": item.get("category"),
                "description": item.get("description"),
                "status": item.get("status"),
                "imageId": image.get("imageId"),
                "imageUrl": image.get("imageUrl"),
                "vector": vector,
            }
        )
    _catalog = rows
    _loaded_generation = _generation
    return rows


async def search_inventory_images(
    db: AsyncIOMotorDatabase,
    embedding: list[float],
    limit: int,
) -> list[dict[str, Any]]:
    try:
        catalog = await _load_catalog(db)
        scored = [
            {
                "inventoryId": row["inventoryId"],
                "name": row.get("name"),
                "category": row.get("category"),
                "description": row.get("description"),
                "status": row.get("status"),
                "imageId": row.get("imageId"),
                "imageUrl": row.get("imageUrl"),
                "similarity": cosine(embedding, row["vector"]),
            }
            for row in catalog
        ]
    except Exception as exc:
        raise AIServiceError("Vector search failed", "vector_search") from exc
    return group_by_inventory(scored, limit)
