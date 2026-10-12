import os

os.environ["MONGODB_DB"] = "qwen_inventory_test"
os.environ["S3_BUCKET"] = "prop-inventory-test"
os.environ["S3_PREFIX"] = "props"
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_REGION"] = "us-east-1"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["WORKER_POLL_SECONDS"] = "0.05"
os.environ["AWS_BEARER_TOKEN_BEDROCK"] = ""

from moto import mock_aws

_aws = mock_aws()
_aws.start()

import boto3

boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="prop-inventory-test")

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
