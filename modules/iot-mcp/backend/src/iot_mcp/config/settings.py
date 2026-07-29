"""Runtime settings for the standalone IoT MCP backend."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IOT_MCP_", env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./iot_mcp.db"
    sqlite_echo: bool = False
