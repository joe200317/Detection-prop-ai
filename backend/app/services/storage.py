import hashlib
import re
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.config import settings
from app.services.errors import AIServiceError

ALLOWED_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def upload_root() -> Path:
    root = Path(settings.upload_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sniff_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


async def read_image(upload: UploadFile) -> tuple[bytes, str]:
    data = await upload.read()
    if not data:
        raise HTTPException(status_code=400, detail="Image is empty")
    if len(data) > settings.max_image_bytes:
        raise HTTPException(status_code=400, detail="Image is too large")
    sniffed = _sniff_type(data)
    if sniffed is None or sniffed not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported image")
    return data, sniffed


def save_image(data: bytes, folder: str, content_type: str) -> str:
    extension = ALLOWED_TYPES[content_type]
    safe_folder = re.sub(r"[^a-zA-Z0-9/_-]", "", folder).strip("/")
    relative = f"{safe_folder}/{uuid.uuid4().hex}{extension}"
    path = upload_root() / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return f"/uploads/{relative}"


def read_stored(image_url: str) -> bytes:
    if not image_url or not image_url.startswith("/uploads/"):
        raise AIServiceError("Image could not be read", "image")
    relative = image_url.removeprefix("/uploads/")
    path = upload_root() / relative
    if not path.is_file():
        raise AIServiceError("Image could not be read", "image")
    return path.read_bytes()


def delete_stored(image_url: str) -> None:
    if not image_url or not image_url.startswith("/uploads/"):
        return
    path = upload_root() / image_url.removeprefix("/uploads/")
    if path.is_file():
        path.unlink()
