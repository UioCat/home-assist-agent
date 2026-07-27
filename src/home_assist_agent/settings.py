from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8080
    ha_base_url: str = "http://homeassistant.local:8123"
    ha_mcp_url: str = "http://homeassistant.local:8123/api/mcp"
    ha_token: SecretStr | None = None
    ha_catalog_timeout_seconds: float = 10
    ha_mcp_timeout_seconds: float = 20
    home_id: str = Field(default="local-home", min_length=1, max_length=200)
    person_id: str = Field(default="local-user", min_length=1, max_length=200)
    term_db_path: Path = Path("data/terms.db")
    target_resolution_enabled: bool = True
    target_resolution_confidence: float = Field(default=0.80, ge=0, le=1)
    target_candidate_limit: int = Field(default=20, ge=1, le=20)
    term_provisional_seconds: int = Field(default=600, ge=1)
    codex_binary: str = "codex"
    frontend_dist: Path = Path("frontend/dist")
    audit_db_path: Path = Path("data/audit.db")
    event_db_path: Path = Path("data/events.db")
