from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from app.services.errors import NotFoundError, RequestError
from app.services.ids import next_id
from app.services.matching import process_prop, props_for_scope
from app.services.themes import get_prop, get_theme, list_props, now, refresh_theme_progress


async def enqueue_job(
    db: AsyncIOMotorDatabase,
    theme_id: str,
    scope: str,
    prop_id: str | None = None,
) -> dict[str, Any]:
    await get_theme(db, theme_id)
    if scope == "prop":
        if not prop_id:
            raise RequestError("A prop ID is required")
        await get_prop(db, theme_id, prop_id)
    active = await db.jobs.find_one(
        {"themeId": theme_id, "status": {"$in": ["queued", "running"]}, "scope": scope, "propId": prop_id},
        {"_id": 0},
    )
    if active:
        return active
    timestamp = now()
    document = {
        "jobId": await next_id(db, "job", "JOB"),
        "themeId": theme_id,
        "scope": scope,
        "propId": prop_id,
        "status": "queued",
        "processedProps": 0,
        "totalProps": 0,
        "error": None,
        "createdAt": timestamp,
        "updatedAt": timestamp,
        "startedAt": None,
        "finishedAt": None,
    }
    await db.jobs.insert_one(document)
    document.pop("_id", None)
    return document


async def get_job(db: AsyncIOMotorDatabase, job_id: str) -> dict[str, Any]:
    document = await db.jobs.find_one({"jobId": job_id}, {"_id": 0})
    if document is None:
        raise NotFoundError("Job not found")
    return document


async def active_job(db: AsyncIOMotorDatabase, theme_id: str) -> dict[str, Any] | None:
    return await db.jobs.find_one(
        {"themeId": theme_id, "status": {"$in": ["queued", "running"]}},
        {"_id": 0},
        sort=[("createdAt", -1)],
    )


async def claim_job(db: AsyncIOMotorDatabase) -> dict[str, Any] | None:
    document = await db.jobs.find_one_and_update(
        {"status": "queued"},
        {"$set": {"status": "running", "startedAt": now(), "updatedAt": now()}},
        sort=[("createdAt", 1)],
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    return document


async def run_job(db: AsyncIOMotorDatabase, job: dict[str, Any]) -> None:
    props = await list_props(db, job["themeId"])
    selected = props_for_scope(props, job.get("scope") or "pending", job.get("propId"))
    await db.jobs.update_one(
        {"jobId": job["jobId"]},
        {"$set": {"totalProps": len(selected), "updatedAt": now()}},
    )
    for index, prop in enumerate(selected, start=1):
        try:
            await process_prop(db, prop, use_cache=True)
        except Exception as exc:
            await db.theme_props.update_one(
                {"propId": prop["propId"]},
                {
                    "$set": {
                        "matchStatus": "AI_FAILED",
                        "error": str(exc) or "Image processing failed",
                        "aiReason": str(exc) or "Image processing failed",
                        "updatedAt": now(),
                    }
                },
            )
        await db.jobs.update_one(
            {"jobId": job["jobId"]},
            {"$set": {"processedProps": index, "updatedAt": now()}},
        )
    await refresh_theme_progress(db, job["themeId"])
    await db.jobs.update_one(
        {"jobId": job["jobId"]},
        {"$set": {"status": "completed", "finishedAt": now(), "updatedAt": now(), "error": None}},
    )


async def fail_job(db: AsyncIOMotorDatabase, job_id: str, message: str) -> None:
    await db.jobs.update_one(
        {"jobId": job_id},
        {"$set": {"status": "failed", "error": message, "finishedAt": now(), "updatedAt": now()}},
    )
