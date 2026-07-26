from pathlib import Path
from typing import Protocol

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from home_assist_agent.api.models import CommandRequest, HealthResponse
from home_assist_agent.commands.models import CommandResponse


class CommandServiceProtocol(Protocol):
    async def execute(
        self,
        command: str,
        reasoning: str,
    ) -> CommandResponse: ...


class HealthServiceProtocol(Protocol):
    async def snapshot(self) -> HealthResponse: ...


def create_app(
    command_service: CommandServiceProtocol,
    health_service: HealthServiceProtocol,
    frontend_dist: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="Home Assist Agent", version="0.1.0")

    @app.post("/api/commands", response_model=CommandResponse)
    async def execute_command(request: CommandRequest) -> CommandResponse:
        return await command_service.execute(
            request.command,
            request.reasoning,
        )

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return await health_service.snapshot()

    if frontend_dist is not None:
        index_file = frontend_dist / "index.html"
        assets_dir = frontend_dist / "assets"
        if index_file.is_file():

            @app.get("/", include_in_schema=False)
            async def frontend_index() -> FileResponse:
                return FileResponse(index_file)

        if assets_dir.is_dir():
            app.mount(
                "/assets",
                StaticFiles(directory=assets_dir),
                name="frontend-assets",
            )

    return app
