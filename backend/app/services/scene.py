import io
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.services.errors import AIServiceError
from app.services.matching import content_type_for
from app.services.nova import get_nova_client
from app.services.storage import read_stored
from app.services.themes import add_scene_prop, clear_theme_props, get_theme


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
    detections, _usage = await get_nova_client().discover(data, content_type)
    if not detections:
        raise AIServiceError("No props were found in the theme image", "not_detected")
    await clear_theme_props(db, theme_id)
    props = []
    for index, detection in enumerate(detections):
        crop = crop_prop(data, detection.get("box"))
        props.append(await add_scene_prop(db, theme_id, crop, "image/jpeg", detection, index))
    return props
