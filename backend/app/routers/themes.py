import re
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.deps import get_database
from app.services.errors import NotFoundError, RequestError
from app.services.jobs import active_job, enqueue_job, get_job
from app.services.review import apply_review, list_review_items
from app.services.storage import read_image
from app.services.themes import (
    add_prop_image,
    create_theme,
    get_theme,
    list_props,
    list_themes,
    set_main_image,
    theme_result,
    update_theme,
)

router = APIRouter(tags=["themes"])
THEME_ID = re.compile(r"^THEME-\d+$")
PROP_ID = re.compile(r"^PROPIMG-\d+$")
JOB_ID = re.compile(r"^JOB-\d+$")


class ThemeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    status: Literal["active", "archived"] = "active"

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value


class ThemeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: Literal["active", "archived"] | None = None

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value


class RetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["failed", "prop", "all"] = "failed"
    propId: str | None = None


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["approve", "reject", "select", "missing", "rerun"]
    inventoryItemId: str | None = None
    reviewedBy: str = Field(default="", max_length=200)


def _map(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, RequestError):
        return HTTPException(status_code=400, detail=str(exc))
    raise exc


def _theme_or_404(theme_id: str) -> str:
    if not THEME_ID.fullmatch(theme_id):
        raise HTTPException(status_code=404, detail="Theme not found")
    return theme_id


@router.get("/api/themes")
async def get_themes(
    request: Request,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    return await list_themes(await get_database(request), skip, limit)


@router.post("/api/themes", status_code=201)
async def post_theme(payload: ThemeCreate, request: Request) -> dict:
    return await create_theme(await get_database(request), payload.name, payload.status)


@router.get("/api/themes/{theme_id}")
async def get_one_theme(theme_id: str, request: Request) -> dict:
    db = await get_database(request)
    try:
        theme = await get_theme(db, _theme_or_404(theme_id))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    theme["activeJob"] = await active_job(db, theme_id)
    theme["props"] = await list_props(db, theme_id)
    return theme


@router.patch("/api/themes/{theme_id}")
async def patch_theme(theme_id: str, payload: ThemeUpdate, request: Request) -> dict:
    changes = {key: value for key, value in payload.model_dump(exclude_unset=True).items() if value is not None}
    if not changes:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        return await update_theme(await get_database(request), _theme_or_404(theme_id), changes)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/themes/{theme_id}/main-image")
async def post_main_image(theme_id: str, request: Request, file: UploadFile = File(...)) -> dict:
    data, content_type = await read_image(file)
    try:
        return await set_main_image(await get_database(request), _theme_or_404(theme_id), data, content_type)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/themes/{theme_id}/props", status_code=201)
async def post_prop(theme_id: str, request: Request, file: UploadFile = File(...)) -> dict:
    data, content_type = await read_image(file)
    try:
        return await add_prop_image(
            await get_database(request),
            _theme_or_404(theme_id),
            data,
            content_type,
            file.filename or "prop",
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/themes/{theme_id}/result")
async def get_result(theme_id: str, request: Request) -> dict:
    try:
        return await theme_result(await get_database(request), _theme_or_404(theme_id))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/themes/{theme_id}/analyze", status_code=202)
async def analyze(theme_id: str, request: Request) -> dict:
    try:
        job = await enqueue_job(await get_database(request), _theme_or_404(theme_id), "pending")
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"jobId": job["jobId"], "status": job["status"]}


@router.post("/api/themes/{theme_id}/retry", status_code=202)
async def retry(theme_id: str, payload: RetryRequest, request: Request) -> dict:
    if payload.scope == "prop" and (not payload.propId or not PROP_ID.fullmatch(payload.propId)):
        raise HTTPException(status_code=400, detail="A prop ID is required")
    try:
        job = await enqueue_job(
            await get_database(request),
            _theme_or_404(theme_id),
            payload.scope,
            payload.propId,
        )
    except (NotFoundError, RequestError) as exc:
        raise _map(exc) from exc
    return {"jobId": job["jobId"], "status": job["status"]}


@router.get("/api/themes/{theme_id}/logs")
async def get_logs(
    theme_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    db = await get_database(request)
    try:
        await get_theme(db, _theme_or_404(theme_id))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    cursor = db.ai_match_logs.find({"themeId": theme_id}, {"_id": 0}).sort("createdAt", -1).limit(limit)
    return {"items": await cursor.to_list(length=limit)}


@router.post("/api/themes/{theme_id}/props/{prop_id}/review")
async def review_prop(theme_id: str, prop_id: str, payload: ReviewRequest, request: Request) -> dict:
    if not PROP_ID.fullmatch(prop_id):
        raise HTTPException(status_code=404, detail="Prop image not found")
    try:
        return await apply_review(
            await get_database(request),
            _theme_or_404(theme_id),
            prop_id,
            payload.action,
            payload.inventoryItemId,
            payload.reviewedBy.strip(),
        )
    except (NotFoundError, RequestError) as exc:
        raise _map(exc) from exc


@router.get("/api/jobs/{job_id}")
async def get_one_job(job_id: str, request: Request) -> dict:
    if not JOB_ID.fullmatch(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        return await get_job(await get_database(request), job_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/review")
async def get_review_queue(request: Request) -> dict:
    return {"items": await list_review_items(await get_database(request))}
