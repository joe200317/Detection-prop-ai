import re
from typing import Any

from app.config import settings

_NAME_WORDS = {"the", "a", "an", "prop", "item", "and"}


def _name_words(value: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", value.lower()) if word not in _NAME_WORDS}


def names_agree(detected: str, inventory_name: str) -> bool:
    left = _name_words(detected)
    right = _name_words(inventory_name)
    if not left or not right:
        return True
    return bool(left & right)


def align_verification(
    detection: dict[str, Any],
    verification: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not verification:
        return verification
    inventory_id = verification.get("inventoryId")
    if not inventory_id:
        return verification
    names = {candidate["inventoryId"]: str(candidate.get("name") or "") for candidate in candidates}
    detected = str(detection.get("detectedObject") or "")
    if names_agree(detected, names.get(str(inventory_id), "")):
        return verification
    return {
        "decision": "NO_MATCH",
        "inventoryId": None,
        "confidence": verification.get("confidence") or 0,
        "reason": f"{detected} is not in inventory.",
    }


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def classify(
    *,
    detection: dict[str, Any],
    candidates: list[dict[str, Any]],
    verification: dict[str, Any] | None,
) -> dict[str, Any]:
    detection_confidence = _number(detection.get("confidence")) or 0
    usable = bool(detection.get("usable")) and bool(str(detection.get("detectedObject") or "").strip())
    if not usable or detection_confidence < settings.detection_min_confidence:
        return _decision(
            "NOT_DETECTED",
            None,
            detection_confidence,
            None,
            None,
            None,
            "The image is unclear or no usable prop is visible.",
        )

    best = candidates[0] if candidates else None
    second = candidates[1] if len(candidates) > 1 else None
    vector = _number(best.get("bestSimilarity")) if best else None
    close = (
        best is not None
        and second is not None
        and vector is not None
        and (vector - float(second["bestSimilarity"])) < settings.close_candidate_gap
    )
    ver_decision = (verification or {}).get("decision")
    ver_confidence = _number((verification or {}).get("confidence"))
    ver_id = (verification or {}).get("inventoryId")
    ver_reason = str((verification or {}).get("reason") or "").strip()
    allowed = {candidate["inventoryId"] for candidate in candidates}
    if ver_id not in allowed:
        ver_id = None

    if best is None or vector is None or vector < settings.similar_threshold:
        if (
            best is not None
            and ver_decision == "EXACT"
            and ver_id == best["inventoryId"]
            and ver_confidence is not None
            and ver_confidence >= settings.exact_threshold
        ):
            return _decision(
                "EXACT",
                ver_id,
                detection_confidence,
                vector,
                ver_confidence,
                ver_confidence,
                ver_reason or "The prop matches the inventory item.",
            )
        if ver_decision == "EXACT" and ver_confidence is not None and ver_confidence >= settings.similar_threshold:
            return _decision(
                "NEEDS_REVIEW",
                ver_id,
                detection_confidence,
                vector,
                ver_confidence,
                min(vector or 0, ver_confidence),
                "Vector search and Nova verification disagree.",
            )
        return _decision(
            "MISSING",
            None,
            detection_confidence,
            vector,
            ver_confidence,
            None,
            ver_reason or "No suitable inventory candidate exists.",
        )

    def uncertain(reason: str, inventory_id: str | None = None) -> dict[str, Any]:
        final = min(vector, ver_confidence) if ver_confidence is not None else vector
        return _decision(
            "NEEDS_REVIEW",
            inventory_id or best["inventoryId"],
            detection_confidence,
            vector,
            ver_confidence,
            final,
            reason,
        )

    if ver_confidence is not None and ver_confidence < settings.similar_threshold:
        return uncertain("Vector similarity and Nova verification do not both support an exact match.")

    agreed_exact = (
        ver_decision == "EXACT"
        and ver_id == best["inventoryId"]
        and vector >= settings.exact_threshold
        and ver_confidence is not None
        and ver_confidence >= settings.exact_threshold
        and not close
    )
    if agreed_exact:
        return _decision(
            "EXACT",
            best["inventoryId"],
            detection_confidence,
            vector,
            ver_confidence,
            min(vector, ver_confidence),
            ver_reason or "The prop matches the inventory item.",
        )

    if ver_decision == "NO_MATCH":
        return uncertain("Vector search and Nova verification disagree.")
    if close:
        return uncertain("Multiple inventory candidates are too close to choose safely.")
    if ver_decision == "EXACT" and (ver_confidence is None or ver_confidence < settings.exact_threshold or vector < settings.exact_threshold):
        return uncertain("The match is not strong enough to call exact.")
    if ver_decision == "SIMILAR" or vector < settings.exact_threshold:
        final = min(vector, ver_confidence) if ver_confidence is not None else vector
        return _decision(
            "SIMILAR",
            ver_id or best["inventoryId"],
            detection_confidence,
            vector,
            ver_confidence,
            final,
            ver_reason or "A candidate looks similar, but it is not confirmed as the same physical item.",
        )
    return uncertain(ver_reason or "The match is uncertain.")


def _decision(
    status: str,
    inventory_id: str | None,
    detection_confidence: float | None,
    vector_similarity: float | None,
    verification_confidence: float | None,
    final_confidence: float | None,
    reason: str,
) -> dict[str, Any]:
    return {
        "matchStatus": status,
        "inventoryItemId": inventory_id,
        "detectionConfidence": _round(detection_confidence),
        "vectorSimilarity": _round(vector_similarity),
        "verificationConfidence": _round(verification_confidence),
        "finalConfidence": _round(final_confidence),
        "aiReason": reason,
    }


def _round(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 4)
