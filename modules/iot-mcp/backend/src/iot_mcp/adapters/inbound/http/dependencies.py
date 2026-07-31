"""Small dependency helpers shared by HTTP routes."""

from __future__ import annotations

from fastapi import Request

from iot_mcp.adapters.inbound.http.auth import authenticate_request, require_web_session
from iot_mcp.application.policy import TrustedPrincipal


def authenticated(request: Request) -> TrustedPrincipal:
    return authenticate_request(request, request.app.state.settings)


def write_principal(request: Request) -> TrustedPrincipal:
    return authenticate_request(request, request.app.state.settings, require_csrf=True)


def interactive_principal(request: Request) -> TrustedPrincipal:
    return require_web_session(request, request.app.state.settings)
