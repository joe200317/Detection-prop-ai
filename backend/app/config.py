from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), env_ignore_empty=True, extra="ignore")

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "qwen_inventory"
    cors_origins: str = (
        "http://localhost:5173,http://localhost:5174,"
        "http://127.0.0.1:5173,http://127.0.0.1:5174"
    )
    upload_dir: str = "uploads"
    max_image_bytes: int = 8_000_000
    max_inventory_images: int = 5
    aws_region: str = "us-east-1"
    aws_bearer_token_bedrock: str = ""
    nova_model_id: str = "us.amazon.nova-lite-v1:0"
    nova_embedding_model_id: str = "amazon.nova-2-multimodal-embeddings-v1:0"
    nova_embedding_dimension: int = 1024
    nova_timeout_seconds: float = 60

    @field_validator(
        "aws_region",
        "aws_bearer_token_bedrock",
        "nova_model_id",
        "nova_embedding_model_id",
        mode="before",
    )
    @classmethod
    def _strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value
    exact_threshold: float = 0.90
    similar_threshold: float = 0.75
    detection_min_confidence: float = 0.45
    close_candidate_gap: float = 0.04
    candidate_limit: int = 8
    worker_poll_seconds: float = 1.0
    worker_enabled: bool = True

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
