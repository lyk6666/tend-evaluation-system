from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = PROJECT_ROOT.parent


class Settings(BaseSettings):
    """Environment-backed settings without exposing secrets through API responses."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_base_url: str = Field(
        default="https://api.openai.com/v1", alias="OPENAI_BASE_URL"
    )
    model: str = Field(default="gpt-5.6-luna", alias="TEND_EVAL_MODEL")
    reasoning_effort: str = Field(
        default="medium", alias="TEND_EVAL_REASONING_EFFORT"
    )
    llm_stub: bool = Field(default=False, alias="TEND_EVAL_LLM_STUB")
    provider_max_retries: int = Field(default=5, ge=-1, alias="TEND_EVAL_PROVIDER_MAX_RETRIES")

    mongodb_uri: str = Field(
        default="mongodb://127.0.0.1:27017", alias="MONGODB_URI"
    )
    evaluation_mongo_max_time_ms: int = Field(
        default=120_000,
        ge=30_000,
        le=900_000,
        alias="TEND_EVAL_MONGO_MAX_TIME_MS",
    )
    tend_source_dir: Path = Field(
        default=WORKSPACE_ROOT / "TEND_QueryCraft", alias="TEND_SOURCE_DIR"
    )
    tend_release_dir: Path = Field(
        default=WORKSPACE_ROOT
        / "TEND_QueryCraft"
        / "release"
        / "tend-native-mongodb-v1",
        alias="TEND_RELEASE_DIR",
    )
    runtime_dir: Path = Field(
        default=PROJECT_ROOT / "data" / "runtime", alias="TEND_EVAL_RUNTIME_DIR"
    )

    default_concurrency: int = Field(
        default=4, ge=1, le=128, alias="TEND_EVAL_DEFAULT_CONCURRENCY"
    )
    baseline_sample_size: int = Field(default=8, ge=1, le=100)
    baseline_witness_k: int = Field(default=3, ge=0, le=20)
    frontend_origin: str = Field(
        default="http://127.0.0.1:5173", alias="TEND_EVAL_FRONTEND_ORIGIN"
    )
    backend_host: str = Field(default="127.0.0.1", alias="TEND_EVAL_BACKEND_HOST")
    backend_port: int = Field(default=8000, alias="TEND_EVAL_BACKEND_PORT")

    def model_post_init(self, __context: object) -> None:
        for field_name in ("tend_source_dir", "tend_release_dir", "runtime_dir"):
            path = getattr(self, field_name)
            if not path.is_absolute():
                setattr(self, field_name, (PROJECT_ROOT / path).resolve())

    @property
    def dataset_path(self) -> Path:
        return self.tend_release_dir / "data" / "TEND.json"

    @property
    def schema_dir(self) -> Path:
        return self.tend_release_dir / "schema" / "mongodb_schema"

    @property
    def sqlite_path(self) -> Path:
        return self.runtime_dir / "tend-evaluation.sqlite3"

    @property
    def api_key_configured(self) -> bool:
        return bool(self.openai_api_key and not self.openai_api_key.startswith("your-"))

    @property
    def provider_ready(self) -> bool:
        return self.api_key_configured or self.llm_stub


@lru_cache
def get_settings() -> Settings:
    return Settings()
