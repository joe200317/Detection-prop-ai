import io
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.services.errors import AIServiceError
from app.services.matching import content_type_for
from app.services.nova import get_nova_client
from app.services.storage import read_stored
from app.services.themes import add_scene_prop, clear_theme_props, collapse_overlapping, get_theme


def union_box(left: dict[str, float] | None, right: dict[str, float] | None) -> dict[str, float] | None:
    if not left:
        return right
    if not right:
        return left
    x = min(left["x"], right["x"])
    y = min(left["y"], right["y"])
    right_edge = max(left["x"] + left["width"], right["x"] + right["width"])
    bottom = max(left["y"] + left["height"], right["y"] + right["height"])
    return {"x": x, "y": y, "width": right_edge - x, "height": bottom - y}


def enclose_box(box: dict[str, float] | None, pad: float = 0.04) -> dict[str, float] | None:
    if not box:
        return None
    x = max(0, box["x"] - pad)
    y = max(0, box["y"] - pad)
    right_edge = min(1, box["x"] + box["width"] + pad)
    bottom = min(1, box["y"] + box["height"] + pad)
    return {"x": x, "y": y, "width": right_edge - x, "height": bottom - y}


def _area(box: dict[str, float]) -> float:
    return max(box["width"], 0) * max(box["height"], 0)


def _overlap(left: dict[str, float], right: dict[str, float]) -> float:
    x1 = max(left["x"], right["x"])
    y1 = max(left["y"], right["y"])
    x2 = min(left["x"] + left["width"], right["x"] + right["width"])
    y2 = min(left["y"] + left["height"], right["y"] + right["height"])
    if x2 <= x1 or y2 <= y1:
        return 0
    return (x2 - x1) * (y2 - y1)


def focused_box(original: dict[str, float] | None, refined: dict[str, float] | None) -> dict[str, float] | None:
    if not original:
        return refined
    if not refined:
        return original
    overlap = _overlap(original, refined)
    original_area = _area(original)
    refined_area = _area(refined)
    if original_area <= 0:
        return refined
    if overlap / original_area < 0.25:
        return original
    if refined_area > original_area * 2.2 and (refined_area <= 0 or overlap / refined_area < 0.55):
        return original
    return union_box(original, refined)


def crop_prop(data: bytes, box: dict[str, Any] | None) -> bytes:
    from PIL import Image

    image = Image.open(io.BytesIO(data)).convert("RGB")
    width, height = image.size
    if box:
        left = max(0, int(float(box["x"]) * width))
        top = max(0, int(float(box["y"]) * height))
        right = min(width, int((float(box["x"]) + float(box["width"])) * width))
        bottom = min(height, int((float(box["y"]) + float(box["height"])) * height))
        if right - left >= 8 and bottom - top >= 8:
            image = image.crop((left, top, right, bottom))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


async def materialize_scene_props(db: AsyncIOMotorDatabase, theme_id: str) -> list[dict[str, Any]]:
    theme = await get_theme(db, theme_id)
    if not theme.get("mainImage"):
        raise AIServiceError("Upload a theme image first", "image")
    try:
        data = read_stored(theme["mainImage"])
    except AIServiceError as exc:
        raise AIServiceError("Theme image could not be read. Upload the theme photo again.", "image") from exc
    content_type = content_type_for(theme["mainImage"])
    client = get_nova_client()
    detections, _usage = await client.discover(data, content_type)
    if not detections:
        raise AIServiceError("No props were found in the theme image", "not_detected")
    prepared = []
    for detection in detections:
        item = dict(detection)
        item["box"] = await _full_object_box(client, data, content_type, item)
        prepared.append(item)
    prepared = collapse_overlapping(prepared)
    if not prepared:
        raise AIServiceError("No props were found in the theme image", "not_detected")
    await clear_theme_props(db, theme_id)
    props = []
    for index, detection in enumerate(prepared):
        crop = crop_prop(data, detection.get("box"))
        props.append(await add_scene_prop(db, theme_id, crop, "image/jpeg", detection, index))
    return props


async def _full_object_box(client, data: bytes, content_type: str, detection: dict[str, Any]) -> dict[str, float] | None:
    original = detection.get("box")
    refined = None
    try:
        refined = await client.refine_box(data, content_type, detection["detectedObject"])
    except AIServiceError:
        refined = None
    original_box = original if isinstance(original, dict) else None
    return enclose_box(focused_box(original_box, refined), pad=0.02)
