from app.services.classify import align_verification, classify
from app.services.nova import _box, parse_model_json
from app.services.scene import enclose_box, union_box
from app.services.vector_search import cosine, group_by_inventory


def _candidate(inventory_id: str, score: float) -> dict:
    return {
        "inventoryId": inventory_id,
        "name": inventory_id,
        "bestSimilarity": score,
        "bestImageUrl": f"/uploads/{inventory_id}.jpg",
    }


def test_exact_requires_both_scores():
    detection = {"detectedObject": "Anchor", "usable": True, "confidence": 0.96}
    decision = classify(
        detection=detection,
        candidates=[_candidate("PROP-001", 0.94)],
        verification={"decision": "EXACT", "inventoryId": "PROP-001", "confidence": 0.94, "reason": "same arms"},
    )
    assert decision["matchStatus"] == "EXACT"
    assert decision["inventoryItemId"] == "PROP-001"
    assert decision["finalConfidence"] == 0.94


def test_high_vector_and_low_verification_needs_review():
    decision = classify(
        detection={"detectedObject": "Anchor", "usable": True, "confidence": 0.96},
        candidates=[_candidate("PROP-001", 0.93)],
        verification={"decision": "EXACT", "inventoryId": "PROP-001", "confidence": 0.55, "reason": "unsure"},
    )
    assert decision["matchStatus"] == "NEEDS_REVIEW"
    assert decision["matchStatus"] != "EXACT"


def test_parse_model_json_accepts_wrapped_and_truncated_lists():
    wrapped = parse_model_json('Here is the result:\n```json\n{"props":[{"detectedObject":"Santa hat"}]}\n```')
    assert wrapped["props"][0]["detectedObject"] == "Santa hat"
    listed = parse_model_json('[{"detectedObject":"Gift"}]')
    assert listed["props"][0]["detectedObject"] == "Gift"
    truncated = parse_model_json('{"props":[{"detectedObject":"Santa hat"},{"detectedObject":"Gift"')
    assert [item["detectedObject"] for item in truncated["props"]] == ["Santa hat"]


def test_box_covers_full_object_extent():
    box = _box({"xmin": 100, "ymin": 50, "xmax": 700, "ymax": 450})
    assert box == {"x": 0.1, "y": 0.05, "width": 0.6, "height": 0.4}
    sliver = {"x": 0.02, "y": 0.1, "width": 0.05, "height": 0.4}
    full = union_box(sliver, box)
    covered = enclose_box(full, pad=0.04)
    assert covered["x"] <= 0.1
    assert covered["y"] <= 0.05
    assert covered["x"] + covered["width"] >= 0.7
    assert covered["y"] + covered["height"] >= 0.45


def test_different_object_stays_unmatched():
    verification = align_verification(
        {"detectedObject": "Gift"},
        {"decision": "SIMILAR", "inventoryId": "PROP-002", "confidence": 0.7, "reason": "similar shape"},
        [_candidate("PROP-002", 0.62) | {"name": "Santa hat"}],
    )
    assert verification["decision"] == "NO_MATCH"
    assert verification["inventoryId"] is None
    decision = classify(
        detection={"detectedObject": "Gift", "usable": True, "confidence": 0.9},
        candidates=[_candidate("PROP-002", 0.62) | {"name": "Santa hat"}],
        verification=verification,
    )
    assert decision["matchStatus"] == "MISSING"
    assert decision["inventoryItemId"] is None


def test_nova_exact_matches_when_crop_similarity_is_moderate():
    decision = classify(
        detection={"detectedObject": "Santa hat", "usable": True, "confidence": 0.96},
        candidates=[_candidate("PROP-002", 0.62), _candidate("PROP-001", 0.29)],
        verification={"decision": "EXACT", "inventoryId": "PROP-002", "confidence": 0.95, "reason": "same hat"},
    )
    assert decision["matchStatus"] == "EXACT"
    assert decision["inventoryItemId"] == "PROP-002"


def test_similar_and_missing_and_not_detected():
    similar = classify(
        detection={"detectedObject": "Wooden Chest", "usable": True, "confidence": 0.9},
        candidates=[_candidate("PROP-024", 0.88)],
        verification={"decision": "SIMILAR", "inventoryId": "PROP-024", "confidence": 0.8, "reason": "similar chest"},
    )
    missing = classify(
        detection={"detectedObject": "Pirate Chest", "usable": True, "confidence": 0.91},
        candidates=[_candidate("PROP-001", 0.4)],
        verification=None,
    )
    not_detected = classify(
        detection={"detectedObject": "", "usable": False, "confidence": 0.2},
        candidates=[],
        verification=None,
    )
    assert similar["matchStatus"] == "SIMILAR"
    assert missing["matchStatus"] == "MISSING"
    assert missing["inventoryItemId"] is None
    assert not_detected["matchStatus"] == "NOT_DETECTED"


def test_close_candidates_need_review():
    decision = classify(
        detection={"detectedObject": "Rope", "usable": True, "confidence": 0.95},
        candidates=[_candidate("PROP-002", 0.93), _candidate("PROP-010", 0.91)],
        verification={"decision": "EXACT", "inventoryId": "PROP-002", "confidence": 0.95, "reason": "rope"},
    )
    assert decision["matchStatus"] == "NEEDS_REVIEW"


def test_group_limit_keeps_strongest_candidates():
    rows = [
        {
            "inventoryId": f"PROP-{index:03d}",
            "name": "Item",
            "imageId": "a",
            "imageUrl": "a.jpg",
            "similarity": 1 - (index * 0.01),
            "status": "available",
        }
        for index in range(1, 6)
    ]
    assert [item["inventoryId"] for item in group_by_inventory(rows, 2)] == ["PROP-001", "PROP-002"]


def test_vector_search_keeps_best_angle():
    grouped = group_by_inventory(
        [
            {"inventoryId": "PROP-001", "name": "Anchor", "imageId": "front", "imageUrl": "front.jpg", "similarity": 0.91, "status": "reserved"},
            {"inventoryId": "PROP-001", "name": "Anchor", "imageId": "side", "imageUrl": "side.jpg", "similarity": 0.97, "status": "reserved"},
            {"inventoryId": "PROP-023", "name": "Hook", "imageId": "front", "imageUrl": "hook.jpg", "similarity": 0.79, "status": "available"},
        ],
        limit=5,
    )
    assert grouped[0]["inventoryId"] == "PROP-001"
    assert grouped[0]["bestImageId"] == "side"
    assert grouped[0]["bestSimilarity"] == 0.97
    assert grouped[0]["status"] == "reserved"
    assert len(grouped[0]["imageScores"]) == 2
    assert cosine([1, 0], [1, 0]) == 1
    assert cosine([1, 0], [0, 1]) == 0
