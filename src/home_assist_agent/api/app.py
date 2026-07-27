from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Protocol

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from home_assist_agent.api.models import CommandRequest, HealthResponse
from home_assist_agent.audit.models import AuditEvent, AuditMessageSummary
from home_assist_agent.audit.recorder import AuditQueryProtocol
from home_assist_agent.commands.models import CommandResponse
from home_assist_agent.events.models import EventRequest, EventResponse


class CommandServiceProtocol(Protocol):
    async def execute(
        self,
        command: str,
        reasoning: str,
        message_id: str | None = None,
    ) -> CommandResponse: ...


class HealthServiceProtocol(Protocol):
    async def snapshot(self) -> HealthResponse: ...


class EventServiceProtocol(Protocol):
    async def handle(self, event: EventRequest) -> EventResponse: ...


class PromotionWorkerProtocol(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...


def create_app(
    command_service: CommandServiceProtocol,
    health_service: HealthServiceProtocol,
    audit_query: AuditQueryProtocol,
    event_service: EventServiceProtocol | None = None,
    frontend_dist: Path | None = None,
    promotion_worker: PromotionWorkerProtocol | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if promotion_worker is not None:
            await promotion_worker.start()
        try:
            yield
        finally:
            if promotion_worker is not None:
                await promotion_worker.stop()

    app = FastAPI(
        title="Home Assist Agent",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.term_promotion_worker = promotion_worker

    @app.post("/api/commands", response_model=CommandResponse)
    async def execute_command(request: CommandRequest) -> CommandResponse:
        return await command_service.execute(
            request.command,
            request.reasoning,
            request.message_id,
        )

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return await health_service.snapshot()

    @app.post("/api/events", response_model=EventResponse)
    async def receive_event(request: EventRequest) -> EventResponse:
        if event_service is None:
            raise HTTPException(
                status_code=503,
                detail="事件通道尚未配置。",
            )
        return await event_service.handle(request)

    @app.get("/api/audit", response_model=list[AuditMessageSummary])
    async def audit_messages(
        limit: int = Query(default=50, ge=1, le=100),
    ) -> list[AuditMessageSummary]:
        return await audit_query.list_messages(limit)

    @app.get("/api/audit/{message_id}", response_model=list[AuditEvent])
    async def audit_events(message_id: str) -> list[AuditEvent]:
        events = await audit_query.list_events(message_id)
        if not events:
            raise HTTPException(status_code=404, detail="未找到该消息的审计记录。")
        return events

    if frontend_dist is not None:
        index_file = frontend_dist / "index.html"
        assets_dir = frontend_dist / "assets"
        if index_file.is_file():

            @app.get("/audit", include_in_schema=False)
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
