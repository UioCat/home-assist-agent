"""Signed web sessions and trusted HTTP authentication."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from fastapi import Request

from iot_mcp.application.policy import SafeControlError, TrustedPrincipal
from iot_mcp.config.settings import Settings


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class SessionCodec:
    def __init__(self, secret: str, ttl_seconds: int) -> None:
        self._secret = secret.encode()
        self._ttl_seconds = ttl_seconds

    def issue(self, actor: str) -> tuple[str, str]:
        csrf = secrets.token_urlsafe(32)
        payload = {
            "actor": actor,
            "csrf": csrf,
            "exp": int(time.time()) + self._ttl_seconds,
            "nonce": secrets.token_urlsafe(16),
        }
        encoded = _b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        )
        signature = _b64encode(hmac.new(self._secret, encoded.encode(), hashlib.sha256).digest())
        return f"{encoded}.{signature}", csrf

    def verify(self, token: str) -> dict[str, Any]:
        try:
            encoded, supplied = token.split(".", 1)
            expected = _b64encode(
                hmac.new(self._secret, encoded.encode(), hashlib.sha256).digest()
            )
            if not hmac.compare_digest(expected, supplied):
                raise ValueError
            payload = json.loads(_b64decode(encoded))
            if (
                not isinstance(payload.get("actor"), str)
                or not isinstance(payload.get("csrf"), str)
                or not isinstance(payload.get("exp"), int)
                or payload["exp"] <= int(time.time())
            ):
                raise ValueError
            return payload
        except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as error:
            raise SafeControlError(
                "session_invalid", "web session is invalid or expired", status_code=401
            ) from error


def verify_admin_token(request: Request, settings: Settings) -> None:
    token = _bearer_token(request)
    if not settings.admin_token or not hmac.compare_digest(token, settings.admin_token):
        raise SafeControlError("unauthorized", "authentication failed", status_code=401)


def authenticate_request(
    request: Request, settings: Settings, *, require_csrf: bool = False
) -> TrustedPrincipal:
    authorization = request.headers.get("authorization")
    if authorization is not None:
        token = _bearer_token(request)
        if settings.admin_token and hmac.compare_digest(token, settings.admin_token):
            return TrustedPrincipal.admin_token()
        for machine_token, actor in settings.machine_tokens.items():
            if hmac.compare_digest(token, machine_token):
                return TrustedPrincipal.machine_token(actor)
        raise SafeControlError("unauthorized", "authentication failed", status_code=401)

    session = request.cookies.get(settings.session_cookie_name)
    if session is None:
        raise SafeControlError("unauthorized", "authentication is required", status_code=401)
    payload = SessionCodec(
        settings.session_signing_secret, settings.session_ttl_seconds
    ).verify(session)
    if require_csrf:
        supplied = request.headers.get("x-csrf-token", "")
        if not supplied or not hmac.compare_digest(supplied, payload["csrf"]):
            raise SafeControlError(
                "csrf_invalid", "CSRF token is missing or invalid", status_code=403
            )
    return TrustedPrincipal.web_session(payload["actor"])


def verified_session_payload(request: Request, settings: Settings) -> dict[str, Any]:
    """Verify only the browser cookie; bearer credentials cannot bootstrap a session."""
    session = request.cookies.get(settings.session_cookie_name)
    if session is None:
        raise SafeControlError(
            "session_invalid", "web session is invalid or expired", status_code=401
        )
    return SessionCodec(
        settings.session_signing_secret, settings.session_ttl_seconds
    ).verify(session)


def require_web_session(request: Request, settings: Settings) -> TrustedPrincipal:
    principal = authenticate_request(request, settings, require_csrf=True)
    if principal.source != "web_session":
        raise SafeControlError(
            "interactive_auth_required",
            "a signed web session and CSRF token are required",
            status_code=403,
        )
    return principal


def _bearer_token(request: Request) -> str:
    value = request.headers.get("authorization", "")
    scheme, separator, token = value.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token:
        raise SafeControlError("unauthorized", "authentication failed", status_code=401)
    return token
