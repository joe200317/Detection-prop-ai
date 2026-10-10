import hashlib
import struct
import time
import zlib

from app.services.errors import AIServiceError
from app.services.nova import set_nova_client


def tiny_png(red: int, green: int, blue: int) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"\x00" + bytes((red, green, blue))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


class FakeNova:
    vision_model = "fake-vision"
    embedding_model = "fake-embed"

    def __init__(self) -> None:
        self.identify_calls = 0
        self.embed_calls = 0
        self.verify_calls = 0
        self.last_candidate_count = 0
        self.detections = {}
        self.vectors = {}
        self.verdicts = {}
        self.fail_names = set()
        self.discover_props = []
        self.identify_queue = []
        self.embed_queue = []

    async def discover(self, image, content_type):
        return [dict(item) for item in self.discover_props], {}

    async def refine_box(self, image, content_type, name):
        for item in self.discover_props:
            if item.get("detectedObject") == name and isinstance(item.get("box"), dict):
                return dict(item["box"])
        return None

    async def identify(self, image, content_type, **_kwargs):
        self.identify_calls += 1
        detected = self.detections.get(hashlib.sha256(image).hexdigest())
        if detected is None and self.identify_queue:
            detected = self.identify_queue.pop(0)
        if detected is None:
            detected = {
                "detectedObject": "",
                "usable": False,
                "confidence": 0.1,
                "role": "other",
            }
        return detected, {"image_count": 1}

    async def embed(self, image, content_type, *, purpose="GENERIC_INDEX"):
        self.embed_calls += 1
        vector = self.vectors.get(hashlib.sha256(image).hexdigest())
        if vector is None and self.embed_queue:
            vector = self.embed_queue.pop(0)
        if vector is None:
            raise AIServiceError("Embedding generation failed", "embedding")
        return list(vector), {"image_count": 1}

    async def verify(self, image, content_type, detection, candidates, candidate_images):
        self.verify_calls += 1
        self.last_candidate_count = len(candidates)
        name = detection.get("detectedObject")
        if name in self.fail_names:
            self.fail_names.remove(name)
            raise AIServiceError("Nova request timed out", "timeout")
        verdict = self.verdicts.get(detection.get("detectedObject"))
        if verdict is None:
            verdict = {"decision": "NO_MATCH", "inventoryId": None, "confidence": 0.2, "reason": "No candidate fits."}
        return verdict, {"image_count": len(candidate_images)}


def detection(name: str, confidence: float = 0.96, usable: bool = True) -> dict:
    return {
        "detectedObject": name,
        "category": "Prop",
        "role": "prop",
        "color": "gray",
        "material": "metal",
        "shape": name,
        "visualFeatures": [name],
        "confidence": confidence,
        "usable": usable,
    }


def remember(fake: FakeNova, image: bytes, vector=None, detected=None) -> None:
    digest = hashlib.sha256(image).hexdigest()
    if vector is not None:
        fake.vectors[digest] = vector
    if detected is not None:
        fake.detections[digest] = detected


def upload_reference(client, fake, inventory_id: str, image: bytes, vector: list[float]) -> dict:
    remember(fake, image, vector=vector)
    response = client.post(
        f"/api/inventory/{inventory_id}/images",
        files={"file": ("ref.png", image, "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def upload_prop(client, fake, theme_id: str, image: bytes, detected=None, vector=None) -> dict:
    remember(fake, image, vector=vector, detected=detected)
    response = client.post(
        f"/api/themes/{theme_id}/props",
        files={"file": ("prop.png", image, "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def wait_job(client, job_id: str) -> dict:
    last = None
    for _ in range(80):
        last = client.get(f"/api/jobs/{job_id}").json()
        if last["status"] in {"completed", "failed"}:
            return last
        time.sleep(0.05)
    raise AssertionError(last)


def test_pilot_matching_pipeline(client):
    fake = FakeNova()
    set_nova_client(fake)
    try:
        anchor = client.post("/api/inventory", json={"name": "Anchor", "category": "Prop"}).json()
        rope = client.post("/api/inventory", json={"name": "Rope", "category": "Prop"}).json()
        chest = client.post("/api/inventory", json={"name": "Wooden Chest", "category": "Prop", "status": "reserved"}).json()
        assert [anchor["inventoryId"], rope["inventoryId"], chest["inventoryId"]] == ["PROP-001", "PROP-002", "PROP-003"]

        front = upload_reference(client, fake, "PROP-001", tiny_png(1, 0, 0), [1, 0, 0])
        upload_reference(client, fake, "PROP-001", tiny_png(2, 0, 0), [0.82, 0.2, 0])
        upload_reference(client, fake, "PROP-002", tiny_png(0, 1, 0), [0, 1, 0])
        upload_reference(client, fake, "PROP-003", tiny_png(0, 0, 1), [0, 0, 1])
        assert front["embeddingReady"] is True

        theme = client.post("/api/themes", json={"name": "Sailor Theme"}).json()
        main = client.post(
            f"/api/themes/{theme['themeId']}/main-image",
            files={"file": ("theme.png", tiny_png(9, 9, 9), "image/png")},
        )
        assert main.status_code == 200

        upload_prop(client, fake, theme["themeId"], tiny_png(10, 0, 0), detection("Anchor"), [0.99, 0.02, 0])
        upload_prop(client, fake, theme["themeId"], tiny_png(0, 10, 0), detection("Rope"), [0.02, 0.99, 0])
        upload_prop(client, fake, theme["themeId"], tiny_png(0, 0, 10), detection("Wooden Chest"), [0.05, 0.05, 0.98])
        upload_prop(client, fake, theme["themeId"], tiny_png(4, 4, 4), detection("Pirate Chest"), [0.2, 0.2, 0.2])
        upload_prop(
            client,
            fake,
            theme["themeId"],
            tiny_png(5, 5, 5),
            detection("", confidence=0.2, usable=False),
        )
        duplicate = upload_prop(client, fake, theme["themeId"], tiny_png(10, 0, 0), detection("Anchor"), [0.99, 0.02, 0])
        assert duplicate["alreadyExists"] is True
        second_anchor = upload_prop(client, fake, theme["themeId"], tiny_png(11, 0, 0), detection("Anchor"), [0.98, 0.03, 0])
        upload_prop(client, fake, theme["themeId"], tiny_png(12, 3, 0), detection("Cap"), [0.97, 0.04, 0])

        fake.verdicts["Anchor"] = {"decision": "EXACT", "inventoryId": "PROP-001", "confidence": 0.95, "reason": "Same anchor arms."}
        fake.verdicts["Rope"] = {"decision": "EXACT", "inventoryId": "PROP-002", "confidence": 0.93, "reason": "Same rope."}
        fake.verdicts["Wooden Chest"] = {"decision": "SIMILAR", "inventoryId": "PROP-003", "confidence": 0.82, "reason": "Similar chest."}
        fake.verdicts["Cap"] = {"decision": "NO_MATCH", "inventoryId": None, "confidence": 0.4, "reason": "Not the anchor."}
        fake.fail_names.add("Cap")
        before = fake.identify_calls

        started = client.post(f"/api/themes/{theme['themeId']}/analyze")
        assert started.status_code == 202
        assert started.json()["status"] == "queued"
        job = wait_job(client, started.json()["jobId"])
        assert job["status"] == "completed"
        assert job["totalProps"] == 7

        rows = {row["detectedObject"]: row for row in client.get(f"/api/themes/{theme['themeId']}/result").json()["rows"]}
        statuses = {row["matchStatus"] for row in rows.values()}
        assert "AI_FAILED" in statuses
        assert client.get("/api/inventory").json()["total"] == 3

        retried = wait_job(client, client.post(f"/api/themes/{theme['themeId']}/retry", json={"scope": "failed"}).json()["jobId"])
        assert retried["status"] == "completed"
        result = client.get(f"/api/themes/{theme['themeId']}/result").json()
        by_prop = {row["propId"]: row for row in result["rows"]}
        by_name = {}
        for row in result["rows"]:
            by_name.setdefault(row["detectedObject"], []).append(row)

        assert by_name["Anchor"][0]["matchStatus"] == "EXACT"
        assert by_name["Anchor"][0]["inventoryItemId"] == "PROP-001"
        assert by_name["Anchor"][0]["inventoryStatus"] == "available"
        assert by_name["Rope"][0]["matchStatus"] == "EXACT"
        assert by_name["Rope"][0]["inventoryItemId"] == "PROP-002"
        assert by_name["Wooden Chest"][0]["matchStatus"] == "SIMILAR"
        assert by_name["Wooden Chest"][0]["inventoryStatus"] == "reserved"
        assert by_name["Pirate Chest"][0]["matchStatus"] == "MISSING"
        assert by_name["Pirate Chest"][0]["inventoryItemId"] is None
        assert any(row["matchStatus"] == "NOT_DETECTED" for row in result["rows"])
        assert by_prop[second_anchor["propId"]]["matchStatus"] == "EXACT"
        assert by_prop[second_anchor["propId"]]["inventoryItemId"] == "PROP-001"
        anchor_row = by_name["Anchor"][0]
        assert anchor_row["candidates"][0]["bestImageId"] == front["imageId"]

        links = result["theme"]["progress"]
        assert links["successfulProps"] == 3
        assert links["missingProps"] == 1
        assert links["failedProps"] == 0
        assert client.get("/api/inventory").json()["total"] == 3

        calls_after_first = fake.identify_calls
        again = wait_job(client, client.post(f"/api/themes/{theme['themeId']}/retry", json={"scope": "all"}).json()["jobId"])
        assert again["status"] == "completed"
        assert fake.identify_calls == calls_after_first
        assert fake.identify_calls > before

        logs = client.get(f"/api/themes/{theme['themeId']}/logs").json()["items"]
        assert logs
        assert logs[0]["processingTime"] >= 0
        assert "usage" in logs[0]
        assert logs[0]["model"] == "fake-vision"
        assert logs[0]["requestId"]

        approved = client.post(
            f"/api/themes/{theme['themeId']}/props/{by_name['Wooden Chest'][0]['propId']}/review",
            json={"action": "approve", "reviewedBy": "Asha"},
        )
        assert approved.status_code == 200
        assert approved.json()["matchStatus"] == "EXACT"
        assert approved.json()["reviewStatus"] == "approved"

        rejected = client.post(
            f"/api/themes/{theme['themeId']}/props/{second_anchor['propId']}/review",
            json={"action": "missing", "reviewedBy": "Asha"},
        )
        assert rejected.status_code == 200
        assert rejected.json()["matchStatus"] == "MISSING"
        assert client.get("/api/inventory").json()["total"] == 3
    finally:
        set_nova_client(None)


def theme_photo() -> bytes:
    import io

    from PIL import Image

    image = Image.new("RGB", (32, 32))
    for x in range(32):
        color = (180, 20, 20) if x < 16 else (20, 140, 40)
        for y in range(32):
            image.putpixel((x, y), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_theme_photo_checks_inventory(client):
    fake = FakeNova()
    set_nova_client(fake)
    try:
        anchor = client.post("/api/inventory", json={"name": "Anchor", "category": "Prop"}).json()
        rope = client.post("/api/inventory", json={"name": "Rope", "category": "Prop", "status": "reserved"}).json()
        upload_reference(client, fake, anchor["inventoryId"], tiny_png(1, 0, 0), [1, 0, 0])
        upload_reference(client, fake, rope["inventoryId"], tiny_png(0, 1, 0), [0, 1, 0])
        fake.discover_props = [
            {**detection("Anchor"), "box": {"x": 0, "y": 0, "width": 0.5, "height": 1}},
            {**detection("Rope"), "box": {"x": 0.5, "y": 0, "width": 0.5, "height": 1}},
        ]
        fake.identify_queue = [detection("Anchor"), detection("Rope")]
        fake.embed_queue = [[0.99, 0.02, 0], [0.02, 0.99, 0]]
        fake.verdicts["Anchor"] = {"decision": "EXACT", "inventoryId": anchor["inventoryId"], "confidence": 0.95, "reason": "Same anchor."}
        fake.verdicts["Rope"] = {"decision": "EXACT", "inventoryId": rope["inventoryId"], "confidence": 0.94, "reason": "Same rope."}

        theme = client.post("/api/themes", json={"name": "Sailor Theme"}).json()
        uploaded = client.post(
            f"/api/themes/{theme['themeId']}/main-image",
            files={"file": ("theme.png", theme_photo(), "image/png")},
        )
        assert uploaded.status_code == 200
        started = client.post(f"/api/themes/{theme['themeId']}/scan")
        assert started.status_code == 202, started.text
        job = wait_job(client, started.json()["jobId"])
        assert job["status"] == "completed", job
        rows = {row["detectedObject"]: row for row in client.get(f"/api/themes/{theme['themeId']}/result").json()["rows"]}
        assert rows["Anchor"]["availability"] == "available"
        assert rows["Anchor"]["inventoryItemId"] == anchor["inventoryId"]
        assert rows["Rope"]["availability"] == "available"
        assert rows["Rope"]["inventoryItemId"] == rope["inventoryId"]
    finally:
        set_nova_client(None)
