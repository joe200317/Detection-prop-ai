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

_SIGNATURE_PROMPT = """Describe the main physical object in this photo for visual search.
Ignore printed text, logos, letters, numbers, and brand names.
Return JSON only: {"signature":"object type; shape; main colors; material; distinctive physical parts"}
Use short catalog words."""


class GptClient:
    def __init__(self) -> None:
        self.vision_model = settings.openai_model_id
        self.embedding_model = f"{settings.openai_embedding_model_id}:{settings.openai_embedding_dimension}"

    def _headers(self) -> dict[str, str]:
        key = settings.openai_api_key.strip()
        if not key:
            raise AIServiceError("OpenAI API key is not configured", "config")
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    async def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=settings.openai_timeout_seconds) as client:
                response = await client.post(url, json=payload, headers=self._headers())
        except httpx.TimeoutException as exc:
            raise AIServiceError("GPT request timed out", "timeout") from exc
        except httpx.HTTPError as exc:
            raise AIServiceError("GPT request failed", "gpt") from exc
        if response.status_code == 429:
            raise AIServiceError("GPT rate limit reached", "rate_limit")
        if response.status_code >= 400:
            detail = _error_detail(response)
            raise AIServiceError(detail or "GPT request failed", "gpt")
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise AIServiceError("GPT returned invalid JSON", "invalid_json") from exc

    async def _generate(
        self,
        content: list[dict[str, Any]],
        *,
        max_tokens: int,
        effort: str = "none",
    ) -> tuple[str, dict[str, Any]]:
        payload = {
            "model": self.vision_model,
            "reasoning_effort": effort,
            "max_completion_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": content}],
        }
        try:
            body = await self._post("https://api.openai.com/v1/chat/completions", payload)
        except AIServiceError as exc:
            message = str(exc).lower()
            if "reasoning" not in message and "temperature" not in message:
                raise
            payload.pop("reasoning_effort", None)
            body = await self._post("https://api.openai.com/v1/chat/completions", payload)
        return _response_text(body), body.get("usage") or {}

    async def discover(self, image: bytes, content_type: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        text, usage = await self._generate(
            [_image_content(image, content_type), {"type": "text", "text": SCENE_PROMPT}],
            max_tokens=8192,
            effort="low",
        )
        found = normalize_scene(parse_model_json(text))
        if found:
            return found, usage
        text, usage = await self._generate(
            [
                _image_content(image, content_type),
                {
                    "type": "text",
                    "text": (
                        "Name every object you can see, including a hat or clothes worn by a person. "
                        'Return only JSON {"props":[{"detectedObject":"","usable":true,'
                        '"box":{"xmin":0,"ymin":0,"xmax":1000,"ymax":1000}}]}. '
                        "Do not return an empty list."
                    ),
                },
            ],
            max_tokens=4096,
            effort="low",
        )
        return normalize_scene(parse_model_json(text)), usage

    async def refine_box(self, image: bytes, content_type: str, name: str) -> dict[str, float] | None:
        text, _usage = await self._generate(
            [_image_content(image, content_type), {"type": "text", "text": BOX_PROMPT.replace("{name}", name)}],
            max_tokens=512,
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
            _image_content(image, content_type),
            {"type": "text", "text": IDENTIFY_PROMPT},
        ]
        if theme_name:
            content.append({"type": "text", "text": f"Theme context: {theme_name}."})
        if theme_image and theme_type:
            content.append({"type": "text", "text": "Main theme image for context only."})
            content.append(_image_content(theme_image, theme_type))
        text, usage = await self._generate(content, max_tokens=1024)
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
            {"type": "text", "text": "Source prop image."},
            _image_content(image, content_type),
            {"type": "text", "text": VERIFY_PROMPT + "\n" + "\n".join(lines)},
        ]
        for inventory_id, data, media_type in candidate_images:
            content.append({"type": "text", "text": f"Candidate {inventory_id}."})
            content.append(_image_content(data, media_type))
        text, usage = await self._generate(content, max_tokens=1024)
        allowed = {candidate["inventoryId"] for candidate in candidates}
        return normalize_verification(parse_model_json(text), allowed), usage

    async def embed(
        self,
        image: bytes,
        content_type: str,
        *,
        purpose: str = "GENERIC_INDEX",
    ) -> tuple[list[float], dict[str, Any]]:
        del purpose
        text, _usage = await self._generate(
            [_image_content(image, content_type), {"type": "text", "text": _SIGNATURE_PROMPT}],
            max_tokens=256,
        )
        payload = parse_model_json(text)
        signature = str(payload.get("signature") or payload.get("detectedObject") or "").strip()
        if not signature:
            raise AIServiceError("Embedding generation failed", "embedding")
        body = await self._post(
            "https://api.openai.com/v1/embeddings",
            {
                "model": settings.openai_embedding_model_id,
                "input": signature,
                "dimensions": settings.openai_embedding_dimension,
            },
        )
        try:
            vector = body["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIServiceError("Embedding generation failed", "embedding") from exc
        if not isinstance(vector, list) or not vector:
            raise AIServiceError("Embedding generation failed", "embedding")
        width = settings.openai_embedding_dimension
        values = [float(value) for value in vector]
        if len(values) > width:
            values = values[:width]
        return values, {}


def _image_content(data: bytes, content_type: str) -> dict[str, Any]:
    image, image_format = _image_bytes(data, content_type)
    mime = _MIME.get(image_format, "image/jpeg")
    encoded = base64.b64encode(image).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{encoded}", "detail": "high"},
    }


def _response_text(body: dict[str, Any]) -> str:
    try:
        message = body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIServiceError("GPT returned invalid JSON", "invalid_json") from exc
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        text = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    else:
        text = str(content or "")
    if not text.strip():
        raise AIServiceError("GPT returned invalid JSON", "invalid_json")
    return text


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
