import base64
import json
import re
from typing import Any

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
Inventory status is physical availability. Ignore it when judging identity.
Return only JSON:
decision (EXACT, SIMILAR, or NO_MATCH), inventoryId (a candidate ID or null), confidence (0 to 1), reason (short string).
EXACT means the same physical item. SIMILAR means it looks related but is not confirmed. NO_MATCH means none of the candidates fit.
Choose inventoryId only from the candidates listed."""


def _data_url(data: bytes, content_type: str) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def parse_model_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise AIServiceError("Qwen returned invalid JSON", "invalid_json") from exc
    if not isinstance(parsed, dict):
        raise AIServiceError("Qwen returned invalid JSON", "invalid_json")
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


class QwenClient:
    vision_model = settings.qwen_vision_model
    embedding_model = settings.qwen_embedding_model

    def _headers(self) -> dict[str, str]:
        if not settings.qwen_api_key:
            raise AIServiceError("Qwen API key is not configured", "config")
        return {"Authorization": f"Bearer {settings.qwen_api_key}"}

    async def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=settings.qwen_timeout_seconds) as client:
                response = await client.post(url, json=payload, headers=self._headers())
        except httpx.TimeoutException as exc:
            raise AIServiceError("Qwen request timed out", "timeout") from exc
        except httpx.HTTPError as exc:
            raise AIServiceError("Qwen request failed", "qwen") from exc
        if response.status_code == 429:
            raise AIServiceError("Qwen rate limit reached", "rate_limit")
        if response.status_code >= 400:
            raise AIServiceError("Qwen request failed", "qwen")
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise AIServiceError("Qwen returned invalid JSON", "invalid_json") from exc

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
            {"type": "image_url", "image_url": {"url": _data_url(image, content_type)}},
            {"type": "text", "text": IDENTIFY_PROMPT},
        ]
        if theme_name:
            content.append({"type": "text", "text": f"Theme context: {theme_name}."})
        if theme_image and theme_type:
            content.append({"type": "text", "text": "Main theme image for context only."})
            content.append({"type": "image_url", "image_url": {"url": _data_url(theme_image, theme_type)}})
        body = await self._post(
            f"{settings.qwen_base_url.rstrip('/')}/chat/completions",
            {
                "model": self.vision_model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": content}],
            },
        )
        try:
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIServiceError("Qwen returned invalid JSON", "invalid_json") from exc
        if isinstance(text, list):
            text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
        usage = body.get("usage") or {}
        return normalize_detection(parse_model_json(str(text))), usage

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
            {"type": "image_url", "image_url": {"url": _data_url(image, content_type)}},
            {"type": "text", "text": VERIFY_PROMPT + "\n" + "\n".join(lines)},
        ]
        for inventory_id, data, media_type in candidate_images:
            content.append({"type": "text", "text": f"Candidate {inventory_id}."})
            content.append({"type": "image_url", "image_url": {"url": _data_url(data, media_type)}})
        body = await self._post(
            f"{settings.qwen_base_url.rstrip('/')}/chat/completions",
            {
                "model": self.vision_model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": content}],
            },
        )
        try:
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIServiceError("Qwen returned invalid JSON", "invalid_json") from exc
        if isinstance(text, list):
            text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
        allowed = {candidate["inventoryId"] for candidate in candidates}
        return normalize_verification(parse_model_json(str(text)), allowed), body.get("usage") or {}

    async def embed(self, image: bytes, content_type: str) -> tuple[list[float], dict[str, Any]]:
        body = await self._post(
            settings.qwen_embedding_url,
            {
                "model": self.embedding_model,
                "input": {"contents": [{"image": _data_url(image, content_type)}]},
            },
        )
        embeddings = (body.get("output") or {}).get("embeddings") or body.get("embeddings") or []
        vector = None
        if embeddings and isinstance(embeddings, list):
            vector = embeddings[0].get("embedding")
        if not isinstance(vector, list) or not vector:
            raise AIServiceError("Embedding generation failed", "embedding")
        return [float(value) for value in vector], body.get("usage") or {}


_override: QwenClient | None = None


def set_qwen_client(client: QwenClient | None) -> None:
    global _override
    _override = client


def get_qwen_client() -> QwenClient:
    return _override or QwenClient()
