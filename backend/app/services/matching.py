import time
import uuid
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import settings
from app.services.classify import align_verification, classify
from app.services.errors import AIServiceError
from app.services.nova import get_nova_client
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


async def ensure_inventory_embeddings(db: AsyncIOMotorDatabase, client) -> None:
    cursor = db.inventory_images.find({}, {"_id": 0, "imageId": 1, "imageUrl": 1, "embedding": 1, "metadata": 1})
    async for image in cursor:
        vector = image.get("embedding")
        stored_model = (image.get("metadata") or {}).get("embeddingModel")
        if isinstance(vector, list) and vector and stored_model == getattr(client, "embedding_model", None):
            continue
        image_url = image.get("imageUrl") or ""
        try:
            data = read_stored(image_url)
        except AIServiceError:
            continue
        content_type = (image.get("metadata") or {}).get("contentType") or content_type_for(image_url)
        try:
            embedded, _cached = await cached_embedding(db, data, content_type, client, purpose="GENERIC_INDEX")
        except AIServiceError as exc:
            await db.inventory_images.update_one(
                {"imageId": image["imageId"]},
                {"$set": {"metadata.embeddingError": str(exc), "updatedAt": now()}},
            )
            continue
        await db.inventory_images.update_one(
            {"imageId": image["imageId"]},
            {
                "$set": {
                    "embedding": embedded,
                    "metadata.embeddingError": None,
                    "metadata.embeddingModel": getattr(client, "embedding_model", None),
                    "updatedAt": now(),
                }
            },
        )


async def cached_embedding(
    db: AsyncIOMotorDatabase,
    data: bytes,
    content_type: str,
    client,
    *,
    purpose: str = "GENERIC_INDEX",
) -> tuple[list[float], bool]:
    image_hash = sha256_bytes(data)
    cache_model = f"{client.embedding_model}:{purpose}"
    cached = await cached_value(db, image_hash, "embedding", cache_model)
    if cached and isinstance(cached.get("result"), list):
        return cached["result"], True
    vector, _usage = await client.embed(data, content_type, purpose=purpose)
    await store_cache(db, image_hash, "embedding", cache_model, vector)
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
    client = get_nova_client()
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
        scene_detection = _scene_detection(prop)
        if scene_detection is not None:
            detection = scene_detection
            usage["detectionCached"] = True
        else:
            detection, detection_cached = await _detect(db, client, image, content_type, image_hash, theme, use_cache)
            usage["detectionCached"] = detection_cached
        if not detection.get("usable"):
            decision = classify(detection=detection, candidates=[], verification=None)
        else:
            await ensure_inventory_embeddings(db, client)
            embedding, embedding_cached = await cached_embedding(
                db, image, content_type, client, purpose="IMAGE_RETRIEVAL"
            )
            usage["embeddingCached"] = embedding_cached
            candidates = await search_inventory_images(db, embedding, settings.candidate_limit)
            verification = None
            comparable = [
                candidate
                for candidate in candidates
                if candidate["bestSimilarity"] >= settings.detection_min_confidence
            ]
            if comparable:
                verification, verify_usage = await _verify(client, image, content_type, detection, comparable)
                usage["verification"] = verify_usage
                candidates = comparable
            verification = align_verification(detection, verification, candidates)
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
            "modelResponse": {"detection": detection, "verification": verification},
            "model": getattr(client, "vision_model", settings.openai_model_id),
            "processingTime": elapsed,
            "usage": usage,
            "error": saved.get("error"),
            "createdAt": now(),
        }
    )
    return saved


def _scene_detection(prop: dict[str, Any]) -> dict[str, Any] | None:
    if prop.get("source") != "theme" or not isinstance(prop.get("detection"), dict):
        return None
    detection = dict(prop["detection"])
    name = str(detection.get("detectedObject") or "").strip()
    if not name:
        return None
    detection["detectedObject"] = name
    detection["usable"] = True
    try:
        confidence = float(detection.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    detection["confidence"] = max(confidence, 0.8)
    return detection


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
        best = candidates[0]
        decision = "EXACT" if float(best.get("bestSimilarity") or 0) >= settings.exact_threshold else "SIMILAR"
        return {
            "decision": decision,
            "inventoryId": best["inventoryId"],
            "confidence": float(best.get("bestSimilarity") or 0),
            "reason": "Matched from the saved inventory photo.",
        }, {}
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
