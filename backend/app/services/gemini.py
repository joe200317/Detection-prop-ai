import base64
import json
from typing import Any

import httpx

from app.config import settings
from app.services.errors import AIServiceError
from app.services.nova import (
    BOX_PROMPT,
    IDENTIFY_PROMPT,
    SCENE_PROMPT,
    VERIFY_PROMPT,
    _box,
    _image_bytes,
    normalize_detection,
    normalize_scene,
    normalize_verification,
    parse_model_json,
)

_MIME = {
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
}


class GeminiClient:
    def __init__(self) -> None:
        self.vision_model = settings.gemini_model_id
        self.embedding_model = f"{settings.gemini_embedding_model_id}:{settings.gemini_embedding_dimension}"

    def _headers(self) -> dict[str, str]:
        key = settings.gemini_api_key.strip()
        if not key:
            raise AIServiceError("Gemini API key is not configured", "config")
        return {"x-goog-api-key": key, "Content-Type": "application/json"}

    def _url(self, model_id: str, action: str) -> str:
        return f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:{action}"

    async def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=settings.gemini_timeout_seconds) as client:
                response = await client.post(url, json=payload, headers=self._headers())
        except httpx.TimeoutException as exc:
            raise AIServiceError("Gemini request timed out", "timeout") from exc
        except httpx.HTTPError as exc:
            raise AIServiceError("Gemini request failed", "gemini") from exc
        if response.status_code == 429:
            raise AIServiceError("Gemini rate limit reached", "rate_limit")
        if response.status_code >= 400:
            detail = _error_detail(response)
            raise AIServiceError(detail or "Gemini request failed", "gemini")
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise AIServiceError("Gemini returned invalid JSON", "invalid_json") from exc

    def _response_text(self, body: dict[str, Any]) -> str:
        try:
            parts = body["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIServiceError("Gemini returned invalid JSON", "invalid_json") from exc
        text = "".join(
            part.get("text", "")
            for part in parts
            if isinstance(part, dict) and not part.get("thought")
        )
        if not text.strip():
            raise AIServiceError("Gemini returned invalid JSON", "invalid_json")
        return text

    async def _generate(self, parts: list[dict[str, Any]], *, max_tokens: int) -> tuple[str, dict[str, Any]]:
        config = {
            "temperature": 0,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingBudget": 0},
        }
        url = self._url(self.vision_model, "generateContent")
        try:
            body = await self._post(url, {"contents": [{"role": "user", "parts": parts}], "generationConfig": config})
        except AIServiceError as exc:
            if "thinking" not in str(exc).lower():
                raise
            config.pop("thinkingConfig", None)
            body = await self._post(url, {"contents": [{"role": "user", "parts": parts}], "generationConfig": config})
        return self._response_text(body), body.get("usageMetadata") or {}

    async def discover(self, image: bytes, content_type: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        text, usage = await self._generate(
            [_image_part(image, content_type), {"text": SCENE_PROMPT}],
            max_tokens=8192,
        )
        found = normalize_scene(parse_model_json(text))
        if found:
            return found, usage
        text, usage = await self._generate(
            [
                _image_part(image, content_type),
                {
                    "text": (
                        "Name every object you can see, including a hat or clothes worn by a person. "
                        'Return only JSON {"props":[{"detectedObject":"","usable":true,'
                        '"box":{"xmin":0,"ymin":0,"xmax":1000,"ymax":1000}}]}. '
                        "Do not return an empty list."
                    )
                },
            ],
            max_tokens=4096,
        )
        return normalize_scene(parse_model_json(text)), usage

    async def refine_box(self, image: bytes, content_type: str, name: str) -> dict[str, float] | None:
        text, _usage = await self._generate(
            [_image_part(image, content_type), {"text": BOX_PROMPT.replace("{name}", name)}],
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
        parts: list[dict[str, Any]] = [_image_part(image, content_type), {"text": IDENTIFY_PROMPT}]
        if theme_name:
            parts.append({"text": f"Theme context: {theme_name}."})
        if theme_image and theme_type:
            parts.append({"text": "Main theme image for context only."})
            parts.append(_image_part(theme_image, theme_type))
        text, usage = await self._generate(parts, max_tokens=1024)
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
        parts: list[dict[str, Any]] = [
            {"text": "Source prop image."},
            _image_part(image, content_type),
            {"text": VERIFY_PROMPT + "\n" + "\n".join(lines)},
        ]
        for inventory_id, data, media_type in candidate_images:
            parts.append({"text": f"Candidate {inventory_id}."})
            parts.append(_image_part(data, media_type))
        text, usage = await self._generate(parts, max_tokens=1024)
        allowed = {candidate["inventoryId"] for candidate in candidates}
        return normalize_verification(parse_model_json(text), allowed), usage

    async def embed(
        self,
        image: bytes,
        content_type: str,
        *,
        purpose: str = "GENERIC_INDEX",
    ) -> tuple[list[float], dict[str, Any]]:
        task = "RETRIEVAL_QUERY" if purpose == "IMAGE_RETRIEVAL" else "RETRIEVAL_DOCUMENT"
        try:
            body = await self._embed(image, content_type, task)
        except AIServiceError as exc:
            if "task" not in str(exc).lower():
                raise
            body = await self._embed(image, content_type, None)
        vector = _embedding_values(body)
        if not vector:
            raise AIServiceError("Embedding generation failed", "embedding")
        width = settings.gemini_embedding_dimension
        if len(vector) > width:
            vector = vector[:width]
        return vector, {}

    async def _embed(self, image: bytes, content_type: str, task: str | None) -> dict[str, Any]:
        config: dict[str, Any] = {"outputDimensionality": settings.gemini_embedding_dimension}
        if task:
            config["taskType"] = task
        return await self._post(
            self._url(settings.gemini_embedding_model_id, "embedContent"),
            {"content": {"parts": [_image_part(image, content_type)]}, "embedContentConfig": config},
        )


def _image_part(data: bytes, content_type: str) -> dict[str, Any]:
    image, image_format = _image_bytes(data, content_type)
    return {
        "inline_data": {
            "mime_type": _MIME.get(image_format, "image/jpeg"),
            "data": base64.b64encode(image).decode("ascii"),
        }
    }


def _embedding_values(body: dict[str, Any]) -> list[float]:
    embedding = body.get("embedding")
    values = embedding.get("values") if isinstance(embedding, dict) else None
    if not isinstance(values, list) or not values:
        embeddings = body.get("embeddings") or []
        if embeddings and isinstance(embeddings[0], dict):
            values = embeddings[0].get("values")
    if not isinstance(values, list) or not values:
        return []
    return [float(value) for value in values]


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except json.JSONDecodeError:
        payload = None
    message = ""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "").strip()
        if not message:
            message = str(payload.get("message") or "").strip()
    if not message:
        message = response.text.strip()
    message = " ".join(message.split())
    if len(message) > 240:
        message = message[:240].rstrip()
    return message
