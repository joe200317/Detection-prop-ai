import os
import tempfile

os.environ["MONGODB_DB"] = "qwen_inventory_test"
os.environ["UPLOAD_DIR"] = tempfile.mkdtemp(prefix="qwen-uploads-")
os.environ["WORKER_POLL_SECONDS"] = "0.05"
os.environ["AWS_BEARER_TOKEN_BEDROCK"] = ""

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from app.config import settings
from app.main import app


@pytest.fixture
def client():
    probe = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=2000)
    try:
        probe.admin.command("ping")
    except PyMongoError:
        probe.close()
        pytest.skip("MongoDB is not running on localhost:27017")
    probe.drop_database(settings.mongodb_db)
    probe.close()

    with TestClient(app) as test_client:
        yield test_client

    cleanup = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=2000)
    cleanup.drop_database(settings.mongodb_db)
    cleanup.close()
