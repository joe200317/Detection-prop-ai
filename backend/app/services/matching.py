import time
import uuid
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import settings
from app.services.classify import classify
from app.services.errors import AIServiceError
from app.services.qwen import get_qwen_client
from app.services.storage import read_stored, sha256_bytes
from app.services.themes import get_theme, now, save_prop_result
from app.services.vector_search import search_inventory_images

SKIP_ON_ANALYZE = {"EXACT", "SIMILAR", "MISSING", "NOT_DETECTED", "NEEDS_REVIEW"}


async def cached_value(db: AsyncIOMotorDatabase, image_hash: str, kind: str, model: str) -> dict[str, Any] | None:
    return await db.analysis_cache.find_one(
        {"imageHash": image_hash, "kind": kind, "model": model},
        {"_id": 0},
    )


async def store_cache(
    db: AsyncIOMotorDatabase,
    image_hash: str,
    kind: str,
    model: str,
    result: Any,
) -> None:
    await db.analysis_cache.update_one(
        {"imageHash": image_hash, "kind": kind, "model": model},
        {"$set": {"result": result, "updatedAt": now()}, "$setOnInsert": {"createdAt": now()}},
        upsert=True,
    )


async def cached_embedding(db: AsyncIOMotorDatabase, data: bytes, content_type: str, client) -> tuple[list[float], bool]:
    image_hash = sha256_bytes(data)
    cached = await cached_value(db, image_hash, "embedding", client.embedding_model)
    if cached and isinstance(cached.get("result"), list):
        return cached["result"], True
    vector, _usage = await client.embed(data, content_type)
    await store_cache(db, image_hash, "embedding", client.embedding_model, vector)
    return vector, False


def content_type_for(url: str, fallback: str = "image/jpeg") -> str:
    lower = (url or "").lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    return fallback


async def process_prop(db: AsyncIOMotorDatabase, prop: dict[str, Any], *, use_cache: bool = True) -> dict[str, Any]:
    started = time.perf_counter()
    client = get_qwen_client()
    theme = await get_theme(db, prop["themeId"])
    await db.theme_props.update_one(
        {"propId": prop["propId"]},
        {"$set": {"matchStatus": "PROCESSING", "error": None, "updatedAt": now()}},
    )
    detection = None
    verification = None
    candidates: list[dict[str, Any]] = []
    usage: dict[str, Any] = {}
    try:
        image = read_stored(prop["sourceImage"])
        content_type = prop.get("contentType") or content_type_for(prop["sourceImage"])
        image_hash = prop.get("imageHash") or sha256_bytes(image)
        detection, detection_cached = await _detect(db, client, image, content_type, image_hash, theme, use_cache)
        usage["detectionCached"] = detection_cached
        if not detection.get("usable"):
            decision = classify(detection=detection, candidates=[], verification=None)
        else:
            embedding, embedding_cached = await cached_embedding(db, image, content_type, client)
            usage["embeddingCached"] = embedding_cached
            candidates = await search_inventory_images(db, embedding, settings.candidate_limit)
            verification = None
            strong = [candidate for candidate in candidates if candidate["bestSimilarity"] >= settings.similar_threshold]
            if strong:
                verification, verify_usage = await _verify(client, image, content_type, detection, strong)
                usage["verification"] = verify_usage
                candidates = strong
            decision = classify(detection=detection, candidates=candidates, verification=verification)
        result = {
            "detectedObject": detection.get("detectedObject") or None,
            "detection": detection,
            "role": detection.get("role"),
            "matchStatus": decision["matchStatus"],
            "inventoryItemId": decision["inventoryItemId"],
            "detectionConfidence": decision["detectionConfidence"],
            "vectorSimilarity": decision["vectorSimilarity"],
            "verificationConfidence": decision["verificationConfidence"],
            "finalConfidence": decision["finalConfidence"],
            "aiReason": decision["aiReason"],
            "candidates": [_candidate_summary(candidate) for candidate in candidates[: settings.candidate_limit]],
            "error": None,
            "reviewStatus": "pending" if decision["matchStatus"] in {"SIMILAR", "NEEDS_REVIEW"} else None,
        }
    except AIServiceError as exc:
        result = _failed(str(exc))
        detection = detection or {}
    except Exception as exc:
        result = _failed("Image processing failed")
        detection = {"error": exc.__class__.__name__}
    elapsed = int((time.perf_counter() - started) * 1000)
    saved = await save_prop_result(db, prop, result)
    await db.ai_match_logs.insert_one(
        {
            "requestId": str(uuid.uuid4()),
            "themeId": prop["themeId"],
            "propId": prop["propId"],
            "sourceImage": prop.get("sourceImage"),
            "detectedObject": saved.get("detectedObject") or (detection or {}).get("detectedObject"),
            "candidates": saved.get("candidates") or [],
            "selectedInventoryId": saved.get("inventoryItemId"),
            "detectionConfidence": saved.get("detectionConfidence"),
            "vectorSimilarity": saved.get("vectorSimilarity"),
            "verificationConfidence": saved.get("verificationConfidence"),
            "finalDecision": saved.get("matchStatus"),
            "qwenResponse": {"detection": detection, "verification": verification},
            "model": getattr(client, "vision_model", settings.qwen_vision_model),
            "processingTime": elapsed,
            "usage": usage,
            "error": saved.get("error"),
            "createdAt": now(),
        }
    )
    return saved


async def _detect(db, client, image, content_type, image_hash, theme, use_cache):
    if use_cache:
        cached = await cached_value(db, image_hash, "detection", client.vision_model)
        if cached and isinstance(cached.get("result"), dict):
            return cached["result"], True
    theme_image = None
    theme_type = None
    if theme.get("mainImage"):
        try:
            theme_image = read_stored(theme["mainImage"])
            theme_type = content_type_for(theme["mainImage"])
        except AIServiceError:
            theme_image = None
    detection, _usage = await client.identify(
        image,
        content_type,
        theme_name=theme.get("name") or "",
        theme_image=theme_image,
        theme_type=theme_type,
    )
    await store_cache(db, image_hash, "detection", client.vision_model, detection)
    return detection, False


async def _verify(client, image, content_type, detection, candidates):
    payload = []
    for candidate in candidates:
        try:
            data = read_stored(candidate["bestImageUrl"])
        except AIServiceError:
            continue
        payload.append((candidate["inventoryId"], data, content_type_for(candidate["bestImageUrl"])))
    if not payload:
        raise AIServiceError("Candidate images could not be read", "image")
    return await client.verify(image, content_type, detection, candidates, payload)


def _candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "inventoryId": candidate["inventoryId"],
        "name": candidate.get("name"),
        "category": candidate.get("category"),
        "status": candidate.get("status"),
        "bestSimilarity": candidate.get("bestSimilarity"),
        "bestImageId": candidate.get("bestImageId"),
        "bestImageUrl": candidate.get("bestImageUrl"),
        "imageScores": candidate.get("imageScores") or [],
    }


def _failed(message: str) -> dict[str, Any]:
    return {
        "matchStatus": "AI_FAILED",
        "inventoryItemId": None,
        "detectionConfidence": None,
        "vectorSimilarity": None,
        "verificationConfidence": None,
        "finalConfidence": None,
        "aiReason": message,
        "candidates": [],
        "error": message,
        "reviewStatus": None,
    }


def props_for_scope(props: list[dict[str, Any]], scope: str, prop_id: str | None) -> list[dict[str, Any]]:
    if scope == "prop":
        return [prop for prop in props if prop["propId"] == prop_id]
    if scope == "failed":
        return [prop for prop in props if prop.get("matchStatus") == "AI_FAILED"]
    if scope == "all":
        return props
    return [prop for prop in props if prop.get("matchStatus") not in SKIP_ON_ANALYZE]
