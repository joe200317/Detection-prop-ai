from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "qwen_inventory"
    cors_origins: str = (
        "http://localhost:5173,http://localhost:5174,"
        "http://127.0.0.1:5173,http://127.0.0.1:5174"
    )
    upload_dir: str = "uploads"
    max_image_bytes: int = 8_000_000
    max_inventory_images: int = 5
    qwen_api_key: str = ""
    qwen_base_url: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    qwen_vision_model: str = "qwen-vl-plus"
    qwen_embedding_model: str = "multimodal-embedding-v1"
    qwen_embedding_url: str = (
        "https://dashscope-intl.aliyuncs.com/api/v1/services/embeddings/"
        "multimodal-embedding/multimodal-embedding"
    )
    qwen_timeout_seconds: float = 60
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
