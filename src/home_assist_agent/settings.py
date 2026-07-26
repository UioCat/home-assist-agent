from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8080
    ha_mcp_url: str = "http://homeassistant.local:8123/api/mcp"
    ha_token: SecretStr | None = None
    ha_mcp_timeout_seconds: float = 20
    codex_binary: str = "codex"
    frontend_dist: Path = Path("frontend/dist")
    audit_db_path: Path = Path("data/audit.db")
    event_db_path: Path = Path("data/events.db")
