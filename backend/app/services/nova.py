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

SCENE_PROMPT = """List every physical prop in this photo, including objects a person is wearing.
A hat, clothing, gift, tree, and decoration all count. Skip only the person's face and plain empty walls.
Do not return an empty list when any object is visible. Set usable to true for every object you can name.
Return only JSON with this shape:
{"props":[{"detectedObject":"","category":"","role":"prop","color":"","material":"","shape":"","visualFeatures":[],"confidence":0.0,"usable":true,"box":{"xmin":0,"ymin":0,"xmax":1000,"ymax":1000}}]}
Box coordinates run from 0 to 1000 across the full photo.
Each box must cover the entire object, from its leftmost pixel to its rightmost pixel and from its top to its bottom.
Include parts that stick out, such as a hat brim, tail, and pom-pom. A thin slice is wrong.
Include one entry per physical item. Do not invent an inventory ID."""

BOX_PROMPT = """Locate the entire "{name}" in this photo.
Return only JSON: {{"box":{{"xmin":0,"ymin":0,"xmax":1000,"ymax":1000}}}}
Coordinates run from 0 to 1000 across the full width and the full height.
The box must contain every pixel of that object, including parts that stick out.
For a hat, include the crown, brim, tail, and pom-pom. Do not return only one edge.
Including a little background is acceptable. Cutting off part of the object is not."""

_IMAGE_FORMATS = {
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


def _as_object(parsed: Any) -> dict[str, Any] | None:
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        return {"props": parsed}
    return None


def _loads(text: str) -> dict[str, Any] | None:
    try:
        return _as_object(json.loads(text))
    except json.JSONDecodeError:
        return None


def _repair_json(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    if start < 0:
        start = text.find("[")
    if start < 0:
        return None
    chunk = text[start:]
    last_brace = max(chunk.rfind("}"), chunk.rfind("]"))
    if last_brace >= 0:
        chunk = chunk[: last_brace + 1]
    chunk = re.sub(r",\s*([}\]])", r"\1", chunk)
    extra_brackets = chunk.count("[") - chunk.count("]")
    extra_braces = chunk.count("{") - chunk.count("}")
    if extra_brackets > 0:
        chunk += "]" * extra_brackets
    if extra_braces > 0:
        chunk += "}" * extra_braces
    return _loads(chunk)


def parse_model_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    parsed = _loads(cleaned)
    if parsed is None:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            parsed = _loads(cleaned[start : end + 1])
    if parsed is None:
        parsed = _repair_json(cleaned)
    if parsed is None:
        preview = " ".join(cleaned.split())[:180]
        raise AIServiceError(f"Nova returned invalid JSON: {preview}", "invalid_json")
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
        if any(key in value for key in ("xmin", "xmax", "ymin", "ymax")):
            xmin = float(value.get("xmin") or 0)
            ymin = float(value.get("ymin") or 0)
            xmax = float(value.get("xmax") or 0)
            ymax = float(value.get("ymax") or 0)
            scale = 1000 if max(xmin, ymin, xmax, ymax) > 1.5 else 1
            x = xmin / scale
            y = ymin / scale
            width = (xmax - xmin) / scale
            height = (ymax - ymin) / scale
        else:
            x = float(value.get("x"))
            y = float(value.get("y"))
            width = float(value.get("width") if value.get("width") is not None else value.get("w"))
            height = float(value.get("height") if value.get("height") is not None else value.get("h"))
            if max(x, y, width, height) > 1:
                x, y, width, height = x / 100, y / 100, width / 100, height / 100
    except (TypeError, ValueError):
        return None
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


def _prop_name(item: dict[str, Any]) -> str:
    for key in ("detectedObject", "name", "object", "label", "item", "prop"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _prop_list(payload: dict[str, Any]) -> list[Any] | None:
    for key in ("props", "objects", "items", "detections"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    for value in payload.values():
        if isinstance(value, list) and value and all(isinstance(entry, dict) for entry in value):
            return value
    if _prop_name(payload):
        return [payload]
    return None


def normalize_scene(payload: dict[str, Any]) -> list[dict[str, Any]]:
    props = _prop_list(payload)
    if props is None:
        raise AIServiceError("Nova returned invalid JSON", "invalid_json")
    found: list[dict[str, Any]] = []
    for item in props:
        if not isinstance(item, dict):
            continue
        named = dict(item)
        name = _prop_name(named)
        if not name:
            continue
        named["detectedObject"] = name
        detection = normalize_detection(named)
        detection["detectedObject"] = name
        detection["usable"] = True
        box = named.get("box") or named.get("boundingBox") or named.get("bbox")
        detection["box"] = _box(box) if isinstance(box, dict) else None
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
            max_tokens=4096,
        )
        found = normalize_scene(parse_model_json(text))
        if found:
            return found, usage
        text, usage = await self._converse(
            [
                _image_block(image, content_type),
                {
                    "text": (
                        "Name every object you can see, including a hat or clothes worn by a person. "
                        'Return only JSON {"props":[{"detectedObject":"","usable":true,'
                        '"box":{"xmin":0,"ymin":0,"xmax":1000,"ymax":1000}}]}. '
                        "Do not return an empty list."
                    )
                },
            ],
            max_tokens=2048,
        )
        return normalize_scene(parse_model_json(text)), usage

    async def refine_box(self, image: bytes, content_type: str, name: str) -> dict[str, float] | None:
        text, _usage = await self._converse(
            [_image_block(image, content_type), {"text": BOX_PROMPT.format(name=name)}],
            max_tokens=300,
        )
        payload = parse_model_json(text)
        return _box(payload.get("box") if isinstance(payload.get("box"), dict) else payload)

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
