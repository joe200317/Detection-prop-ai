import hashlib
import re
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.config import settings
from app.services.errors import AIServiceError, RequestError

ALLOWED_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

_s3_client = None


def upload_root() -> Path:
    root = Path(settings.upload_dir)
    if not root.is_absolute():
        root = Path(__file__).resolve().parents[2] / root
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


def uses_s3() -> bool:
    return bool(settings.s3_bucket.strip())


def _client():
    global _s3_client
    if _s3_client is None:
        import boto3

        kwargs = {"region_name": settings.aws_region}
        if settings.aws_access_key_id and settings.aws_secret_access_key:
            kwargs["aws_access_key_id"] = settings.aws_access_key_id
            kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
        _s3_client = boto3.client("s3", **kwargs)
    return _s3_client


def _safe_folder(folder: str) -> str:
    return re.sub(r"[^a-zA-Z0-9/_-]", "", folder).strip("/")


def _media_key(image_url: str) -> str | None:
    if not image_url or not image_url.startswith("/media/"):
        return None
    key = image_url.removeprefix("/media/").lstrip("/")
    if not key or ".." in key.split("/"):
        return None
    return key


def save_image(data: bytes, folder: str, content_type: str) -> str:
    if not uses_s3():
        raise RequestError("S3 storage is not configured")
    extension = ALLOWED_TYPES[content_type]
    filename = f"{uuid.uuid4().hex}{extension}"
    safe_folder = _safe_folder(folder)
    prefix = _safe_folder(settings.s3_prefix)
    key = f"{prefix}/{safe_folder}/{filename}" if prefix else f"{safe_folder}/{filename}"
    try:
        _client().put_object(
            Bucket=settings.s3_bucket.strip(),
            Key=key,
            Body=data,
            ContentType=content_type,
        )
    except Exception as exc:
        raise RequestError("Image could not be stored in S3") from exc
    return f"/media/{key}"


def read_stored(image_url: str) -> bytes:
    key = _media_key(image_url)
    if key:
        if not uses_s3():
            raise AIServiceError("Image could not be read", "image")
        try:
            obj = _client().get_object(Bucket=settings.s3_bucket.strip(), Key=key)
        except Exception as exc:
            raise AIServiceError("Image could not be read", "image") from exc
        return obj["Body"].read()
    if not image_url or not image_url.startswith("/uploads/"):
        raise AIServiceError("Image could not be read", "image")
    relative = image_url.removeprefix("/uploads/")
    path = upload_root() / relative
    if not path.is_file():
        raise AIServiceError("Image could not be read", "image")
    return path.read_bytes()


def open_media(key: str) -> tuple[bytes, str]:
    if not uses_s3() or not key or ".." in key.split("/"):
        raise AIServiceError("Image could not be read", "image")
    try:
        obj = _client().get_object(Bucket=settings.s3_bucket.strip(), Key=key)
    except Exception as exc:
        raise AIServiceError("Image could not be read", "image") from exc
    content_type = obj.get("ContentType") or "application/octet-stream"
    return obj["Body"].read(), content_type


def delete_stored(image_url: str) -> None:
    key = _media_key(image_url)
    if key and uses_s3():
        try:
            _client().delete_object(Bucket=settings.s3_bucket.strip(), Key=key)
        except Exception:
            return
        return
    if not image_url or not image_url.startswith("/uploads/"):
        return
    path = upload_root() / image_url.removeprefix("/uploads/")
    if path.is_file():
        path.unlink()
