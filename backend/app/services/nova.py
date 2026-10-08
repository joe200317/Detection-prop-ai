import base64
import io
import json
import re
from typing import Any
from urllib.parse import quote

import httpx

from app.config import settings
from app.services.errors import AIServiceError

IDENTIFY_PROMPT = """Identify the physical object in the prop image.
Return only JSON with these keys:
detectedObject (string), category (string), role (one of: prop, baby accessory, clothing, furniture, setup, accessory, background item, other),
color, material, shape, visualFeatures (array of short strings), confidence (number from 0 to 1), usable (boolean).
Set usable to false when the image is blurry, empty, or has no identifiable prop.
Do not invent an inventory ID."""

VERIFY_PROMPT = """Decide whether the source prop is the same physical inventory item as one candidate.
The detected object name came from the theme photo. Do not rename that object.
Inventory status is physical availability. Ignore it when judging identity.
Return only JSON:
decision (EXACT, SIMILAR, or NO_MATCH), inventoryId (a candidate ID or null), confidence (0 to 1), reason (short string).
EXACT means the same physical item and the same kind of object as the detected name.
SIMILAR means the same kind of object but it is not confirmed.
NO_MATCH means it is a different object. A gift is not a Santa hat. Similar color or shape is not enough.
Choose inventoryId only from the candidates listed. Use null when the decision is NO_MATCH."""

SCENE_PROMPT = """Look at this theme photo and list every distinct physical prop you can see.
Skip people, plain walls, and empty background.
Return only JSON with this shape:
{"props":[{"detectedObject":"","category":"","role":"prop","color":"","material":"","shape":"","visualFeatures":[],"confidence":0.0,"usable":true,"box":{"x":0,"y":0,"width":0,"height":0}}]}
box x, y, width, and height are fractions from 0 to 1. x and y are the top-left corner.
Include one entry per physical item. Do not invent an inventory ID."""

_IMAGE_FORMATS = {
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


def parse_model_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise AIServiceError("Nova returned invalid JSON", "invalid_json") from exc
    if not isinstance(parsed, dict):
        raise AIServiceError("Nova returned invalid JSON", "invalid_json")
    return parsed


def normalize_detection(payload: dict[str, Any]) -> dict[str, Any]:
    features = payload.get("visualFeatures") or []
    if not isinstance(features, list):
        features = [str(features)]
    try:
        confidence = float(payload.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    confidence = min(max(confidence, 0), 1)
    usable = payload.get("usable")
    if usable is None:
        usable = bool(str(payload.get("detectedObject") or "").strip()) and confidence >= 0.45
    return {
        "detectedObject": str(payload.get("detectedObject") or "").strip(),
        "category": str(payload.get("category") or "").strip(),
        "role": str(payload.get("role") or "other").strip() or "other",
        "color": str(payload.get("color") or "").strip(),
        "material": str(payload.get("material") or "").strip(),
        "shape": str(payload.get("shape") or "").strip(),
        "visualFeatures": [str(feature) for feature in features if str(feature).strip()],
        "confidence": confidence,
        "usable": bool(usable),
    }


def normalize_verification(payload: dict[str, Any], allowed_ids: set[str]) -> dict[str, Any]:
    decision = str(payload.get("decision") or "NO_MATCH").strip().upper()
    if decision not in {"EXACT", "SIMILAR", "NO_MATCH"}:
        decision = "NO_MATCH"
    inventory_id = payload.get("inventoryId")
    inventory_id = str(inventory_id).strip() if inventory_id else None
    if inventory_id not in allowed_ids:
        inventory_id = None
        if decision == "EXACT":
            decision = "NO_MATCH"
    try:
        confidence = float(payload.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    return {
        "decision": decision,
        "inventoryId": inventory_id,
        "confidence": min(max(confidence, 0), 1),
        "reason": str(payload.get("reason") or "").strip(),
    }


def _as_jpeg(data: bytes) -> tuple[bytes, str]:
    try:
        from PIL import Image
    except ImportError as exc:
        raise AIServiceError("This image format is not supported by Nova", "unsupported") from exc
    image = Image.open(io.BytesIO(data)).convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue(), "jpeg"


def _image_bytes(data: bytes, content_type: str) -> tuple[bytes, str]:
    image_format = _IMAGE_FORMATS.get((content_type or "").split(";", 1)[0].strip().lower())
    if image_format:
        return data, image_format
    return _as_jpeg(data)


def _box(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        x = float(value.get("x"))
        y = float(value.get("y"))
        width = float(value.get("width") if value.get("width") is not None else value.get("w"))
        height = float(value.get("height") if value.get("height") is not None else value.get("h"))
    except (TypeError, ValueError):
        return None
    if max(x, y, width, height) > 1:
        x, y, width, height = x / 100, y / 100, width / 100, height / 100
    x = min(max(x, 0), 1)
    y = min(max(y, 0), 1)
    width = min(max(width, 0), 1)
    height = min(max(height, 0), 1)
    if width < 0.02 or height < 0.02:
        return None
    if x + width > 1:
        width = 1 - x
    if y + height > 1:
        height = 1 - y
    return {"x": x, "y": y, "width": width, "height": height}


def normalize_scene(payload: dict[str, Any]) -> list[dict[str, Any]]:
    props = payload.get("props")
    if props is None and payload.get("detectedObject"):
        props = [payload]
    if not isinstance(props, list):
        raise AIServiceError("Nova returned invalid JSON", "invalid_json")
    found: list[dict[str, Any]] = []
    for item in props:
        if not isinstance(item, dict):
            continue
        detection = normalize_detection(item)
        if not detection["usable"] or not detection["detectedObject"]:
            continue
        detection["box"] = _box(item.get("box"))
        found.append(detection)
        if len(found) >= 12:
            break
    return found


def _image_block(data: bytes, content_type: str) -> dict[str, Any]:
    image, image_format = _image_bytes(data, content_type)
    return {
        "image": {
            "format": image_format,
            "source": {"bytes": base64.b64encode(image).decode("ascii")},
        }
    }


class NovaClient:
    def __init__(self) -> None:
        self.vision_model = settings.nova_model_id
        self.embedding_model = f"{settings.nova_embedding_model_id}:{settings.nova_embedding_dimension}"

    def _headers(self) -> dict[str, str]:
        token = settings.aws_bearer_token_bedrock.strip()
        if not token:
            raise AIServiceError("Bedrock API key is not configured", "config")
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _url(self, model_id: str, action: str) -> str:
        region = settings.aws_region.strip() or "us-east-1"
        return f"https://bedrock-runtime.{region}.amazonaws.com/model/{quote(model_id, safe='')}/{action}"

    async def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=settings.nova_timeout_seconds) as client:
                response = await client.post(url, json=payload, headers=self._headers())
        except httpx.TimeoutException as exc:
            raise AIServiceError("Nova request timed out", "timeout") from exc
        except httpx.HTTPError as exc:
            raise AIServiceError("Nova request failed", "nova") from exc
        if response.status_code == 429:
            raise AIServiceError("Nova rate limit reached", "rate_limit")
        if response.status_code >= 400:
            detail = _error_detail(response)
            raise AIServiceError(detail or "Nova request failed", "nova")
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise AIServiceError("Nova returned invalid JSON", "invalid_json") from exc

    def _response_text(self, body: dict[str, Any]) -> str:
        try:
            parts = body["output"]["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise AIServiceError("Nova returned invalid JSON", "invalid_json") from exc
        text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
        if not text.strip():
            raise AIServiceError("Nova returned invalid JSON", "invalid_json")
        return text

    async def _converse(self, content: list[dict[str, Any]], *, max_tokens: int = 1024) -> tuple[str, dict[str, Any]]:
        body = await self._post(
            self._url(self.vision_model, "converse"),
            {
                "messages": [{"role": "user", "content": content}],
                "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0},
            },
        )
        return self._response_text(body), body.get("usage") or {}

    async def discover(self, image: bytes, content_type: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        text, usage = await self._converse(
            [_image_block(image, content_type), {"text": SCENE_PROMPT}],
            max_tokens=2048,
        )
        return normalize_scene(parse_model_json(text)), usage

    async def identify(
        self,
        image: bytes,
        content_type: str,
        *,
        theme_name: str = "",
        theme_image: bytes | None = None,
        theme_type: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        content: list[dict[str, Any]] = [
            _image_block(image, content_type),
            {"text": IDENTIFY_PROMPT},
        ]
        if theme_name:
            content.append({"text": f"Theme context: {theme_name}."})
        if theme_image and theme_type:
            content.append({"text": "Main theme image for context only."})
            content.append(_image_block(theme_image, theme_type))
        text, usage = await self._converse(content)
        return normalize_detection(parse_model_json(text)), usage

    async def verify(
        self,
        image: bytes,
        content_type: str,
        detection: dict[str, Any],
        candidates: list[dict[str, Any]],
        candidate_images: list[tuple[str, bytes, str]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        lines = [
            f"Detected: {detection.get('detectedObject')} ({detection.get('category')}), "
            f"material {detection.get('material')}, color {detection.get('color')}, shape {detection.get('shape')}."
        ]
        for candidate in candidates:
            lines.append(
                f"{candidate['inventoryId']} name={candidate.get('name')} "
                f"category={candidate.get('category')} status={candidate.get('status')} "
                f"similarity={candidate.get('bestSimilarity')}"
            )
        content: list[dict[str, Any]] = [
            {"text": "Source prop image."},
            _image_block(image, content_type),
            {"text": VERIFY_PROMPT + "\n" + "\n".join(lines)},
        ]
        for inventory_id, data, media_type in candidate_images:
            content.append({"text": f"Candidate {inventory_id}."})
            content.append(_image_block(data, media_type))
        text, usage = await self._converse(content)
        allowed = {candidate["inventoryId"] for candidate in candidates}
        return normalize_verification(parse_model_json(text), allowed), usage

    async def embed(
        self,
        image: bytes,
        content_type: str,
        *,
        purpose: str = "GENERIC_INDEX",
    ) -> tuple[list[float], dict[str, Any]]:
        image, image_format = _image_bytes(image, content_type)
        body = await self._post(
            self._url(settings.nova_embedding_model_id, "invoke"),
            {
                "schemaVersion": "nova-multimodal-embed-v1",
                "taskType": "SINGLE_EMBEDDING",
                "singleEmbeddingParams": {
                    "embeddingPurpose": purpose,
                    "embeddingDimension": settings.nova_embedding_dimension,
                    "image": {
                        "format": image_format,
                        "detailLevel": "STANDARD_IMAGE",
                        "source": {"bytes": base64.b64encode(image).decode("ascii")},
                    },
                },
            },
        )
        embeddings = body.get("embeddings") or []
        vector = embeddings[0].get("embedding") if embeddings and isinstance(embeddings[0], dict) else None
        if not isinstance(vector, list) or not vector:
            raise AIServiceError("Embedding generation failed", "embedding")
        return [float(value) for value in vector], {}


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except json.JSONDecodeError:
        payload = None
    message = ""
    if isinstance(payload, dict):
        message = str(payload.get("message") or payload.get("Message") or "").strip()
    if not message:
        message = response.text.strip()
    message = " ".join(message.split())
    if len(message) > 240:
        message = message[:240].rstrip()
    return message


_override: NovaClient | None = None


def set_nova_client(client: NovaClient | None) -> None:
    global _override
    _override = client


def get_nova_client() -> NovaClient:
    return _override or NovaClient()
