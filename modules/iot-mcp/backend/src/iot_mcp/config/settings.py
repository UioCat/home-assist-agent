"""Runtime settings for the standalone IoT MCP backend."""

import secrets
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IOT_MCP_", env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./iot_mcp.db"
    sqlite_echo: bool = False
    admin_token: str = ""
    machine_tokens: dict[str, str] = Field(default_factory=dict)
    session_signing_secret: str = Field(default_factory=lambda: secrets.token_urlsafe(32))
    session_cookie_name: str = "iot_mcp_session"
    session_ttl_seconds: int = Field(default=900, gt=0)
    secure_cookies: bool = True
    cookie_http_only: bool = True
    cookie_same_site: Literal["lax", "strict", "none"] = "strict"
    webhook_secret: str = Field(default_factory=lambda: secrets.token_urlsafe(32))
    webhook_send_url: str | None = None
    webhook_timestamp_tolerance_seconds: int = Field(default=300, gt=0)
    allowed_confirmation_actors: set[str] = Field(
        default_factory=lambda: {"owner"}, min_length=1
    )
    confirmation_ttl_seconds: int = Field(default=300, gt=0)
