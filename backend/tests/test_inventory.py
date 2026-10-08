def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "connected"
    assert body["databaseName"] == "qwen_inventory_test"


def test_create_assigns_permanent_sequential_ids(client):
    first = client.post(
        "/api/inventory",
        json={
            "name": " Anchor ",
            "category": "Prop",
            "subcategory": "Nautical",
            "description": "Metal anchor",
        },
    )
    second = client.post(
        "/api/inventory",
        json={"name": "Anchor", "category": "Prop"},
    )
    assert first.status_code == 201
    assert second.status_code == 201
    first_body = first.json()
    second_body = second.json()
    assert first_body["inventoryId"] == "PROP-001"
    assert second_body["inventoryId"] == "PROP-002"
    assert first_body["name"] == "Anchor"
    assert first_body["status"] == "available"
    assert first_body["subcategory"] == "Nautical"


def test_inventory_id_cannot_be_set_or_changed(client):
    created = client.post("/api/inventory", json={"name": "Rope", "category": "Prop"})
    assert created.status_code == 201
    rejected_create = client.post(
        "/api/inventory",
        json={"inventoryId": "PROP-999", "name": "Boat", "category": "Prop"},
    )
    rejected_update = client.patch(
        "/api/inventory/PROP-001",
        json={"inventoryId": "PROP-999", "name": "Rope coil"},
    )
    assert rejected_create.status_code == 422
    assert rejected_update.status_code == 422
    current = client.get("/api/inventory/PROP-001")
    assert current.json()["inventoryId"] == "PROP-001"
    assert current.json()["name"] == "Rope"


def test_update_status_and_get_missing(client):
    client.post("/api/inventory", json={"name": "Cap", "category": "Accessory"})
    updated = client.patch("/api/inventory/PROP-001", json={"status": "reserved"})
    assert updated.status_code == 200
    assert updated.json()["status"] == "reserved"
    assert updated.json()["inventoryId"] == "PROP-001"
    missing = client.get("/api/inventory/PROP-999")
    assert missing.status_code == 404
    empty_patch = client.patch("/api/inventory/PROP-001", json={})
    assert empty_patch.status_code == 400


def test_filter_by_exact_name_and_status(client):
    client.post("/api/inventory", json={"name": "Anchor", "category": "Prop"})
    client.post("/api/inventory", json={"name": "Blanket", "category": "Prop", "status": "in_use"})
    by_name = client.get("/api/inventory", params={"name": "anchor"})
    by_status = client.get("/api/inventory", params={"status": "in_use"})
    assert by_name.status_code == 200
    assert by_name.json()["total"] == 1
    assert by_name.json()["items"][0]["inventoryId"] == "PROP-001"
    assert by_status.json()["total"] == 1
    assert by_status.json()["items"][0]["name"] == "Blanket"


def test_rejects_invalid_status_and_blank_name(client):
    response = client.post(
        "/api/inventory",
        json={"name": "Boat", "category": "Prop", "status": "lost"},
    )
    blank = client.post("/api/inventory", json={"name": "  ", "category": "Prop"})
    assert response.status_code == 422
    assert blank.status_code == 422
