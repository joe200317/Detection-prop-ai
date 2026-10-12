from pathlib import PurePath
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.services.errors import AIServiceError
from app.services.matching import process_prop
from app.services.scene import materialize_scene_props
from app.services.themes import create_theme, list_props, refresh_theme_progress, set_main_image, theme_result

ACCEPTED_STATUSES = {"EXACT", "SIMILAR"}
REJECTED_STATUSES = {"MISSING", "NOT_DETECTED", "AI_FAILED"}


def _theme_name(filename: str) -> str:
    stem = PurePath(PurePath(filename or "").name).stem.strip()
    return (stem or "Theme photo")[:200]


async def detect_uploaded_image(
    db: AsyncIOMotorDatabase,
    data: bytes,
    content_type: str,
    filename: str,
) -> dict[str, Any]:
    theme = await create_theme(db, _theme_name(filename))
    theme_id = theme["themeId"]
    await set_main_image(db, theme_id, data, content_type)
    try:
        await materialize_scene_props(db, theme_id)
    except AIServiceError as exc:
        if exc.code != "not_detected":
            raise
    else:
        for prop in await list_props(db, theme_id):
            await process_prop(db, prop, use_cache=True)
        await refresh_theme_progress(db, theme_id)
    return await detection_report(db, theme_id)


async def detection_report(db: AsyncIOMotorDatabase, theme_id: str) -> dict[str, Any]:
    result = await theme_result(db, theme_id)
    stored = {prop["propId"]: prop for prop in await list_props(db, theme_id)}
    rows = []
    for row in result["rows"]:
        prop = stored.get(row["propId"]) or {}
        item = dict(row)
        item["error"] = prop.get("error")
        item["role"] = prop.get("role")
        rows.append(item)
    accepted = [row for row in rows if row.get("matchStatus") in ACCEPTED_STATUSES]
    rejected = [row for row in rows if row.get("matchStatus") in REJECTED_STATUSES]
    review = [row for row in rows if row.get("matchStatus") == "NEEDS_REVIEW"]
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("matchStatus") or "UNKNOWN")
        counts[status] = counts.get(status, 0) + 1
    return {
        "themeId": theme_id,
        "detectedCount": len(rows),
        "acceptedCount": len(accepted),
        "rejectedCount": len(rejected),
        "reviewCount": len(review),
        "counts": counts,
        "message": None if rows else "No props were found in the image.",
        "detected": rows,
        "accepted": accepted,
        "rejected": rejected,
        "needsReview": review,
    }
