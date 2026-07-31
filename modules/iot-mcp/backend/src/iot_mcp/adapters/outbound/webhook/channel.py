"""Structured signed-webhook channel.

Signatures are HMAC-SHA256 over the exact bytes
``timestamp + "." + nonce + "." + raw_body``. Inbound bodies are verified
before JSON parsing so alternate serializations cannot change the signed data.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx

from iot_mcp.adapters.outbound.persistence.repositories import WebhookNonceRepository
from iot_mcp.application.policy import SafeControlError


class SignedWebhookMessageChannel:
    def __init__(
        self,
        *,
        secret: str,
        allowed_actor_ids: set[str],
        nonces: WebhookNonceRepository,
        timestamp_tolerance_seconds: int = 300,
        send_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._secret = secret.encode()
        self._allowed_actor_ids = allowed_actor_ids
        self._timestamp_tolerance_seconds = timestamp_tolerance_seconds
        self._send_url = send_url
        self._client = client
        self._nonces = nonces

    async def send_confirmation(self, payload: dict[str, Any]) -> None:
        await self._send("confirmation", payload)

    async def send_result(self, payload: dict[str, Any]) -> None:
        await self._send("result", payload)

    async def send_alert(self, payload: dict[str, Any]) -> None:
        await self._send("alert", payload)

    async def verify(self, raw_body: bytes, headers: Mapping[str, str]) -> None:
        timestamp_text = headers.get("x-iot-timestamp")
        nonce = headers.get("x-iot-nonce")
        supplied = headers.get("x-iot-signature")
        if not timestamp_text or not nonce or not supplied:
            raise SafeControlError(
                "webhook_signature_missing",
                "required webhook signature headers are missing",
                status_code=401,
            )
        try:
            timestamp = int(timestamp_text)
        except ValueError as error:
            raise SafeControlError(
                "webhook_timestamp_invalid", "webhook timestamp is invalid", status_code=401
            ) from error
        now = time.time()
        if abs(now - timestamp) > self._timestamp_tolerance_seconds:
            raise SafeControlError(
                "webhook_timestamp_invalid",
                "webhook timestamp is outside the accepted window",
                status_code=401,
            )
        signed = f"{timestamp}.{nonce}.".encode() + raw_body
        expected = "sha256=" + hmac.new(self._secret, signed, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, supplied):
            raise SafeControlError(
                "webhook_signature_invalid", "webhook signature is invalid", status_code=401
            )
        expires_at = datetime.fromtimestamp(
            timestamp + self._timestamp_tolerance_seconds, UTC
        )
        if not await self._nonces.consume(
            nonce,
            signed_timestamp=timestamp,
            expires_at=expires_at,
            now=datetime.fromtimestamp(now, UTC),
        ):
            raise SafeControlError(
                "webhook_replay", "webhook nonce was already used", status_code=409
            )

    def verify_actor(self, actor: str) -> None:
        if actor not in self._allowed_actor_ids:
            raise SafeControlError(
                "actor_not_authorized", "actor is not authorized", status_code=403
            )

    async def _send(self, message_type: str, payload: dict[str, Any]) -> None:
        if self._send_url is None:
            return
        raw = json.dumps(
            {"type": message_type, "payload": payload},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        timestamp = int(time.time())
        nonce = hashlib.sha256(raw + str(time.time_ns()).encode()).hexdigest()[:32]
        signed = f"{timestamp}.{nonce}.".encode() + raw
        signature = hmac.new(self._secret, signed, hashlib.sha256).hexdigest()
        client = self._client or httpx.AsyncClient()
        try:
            response = await client.post(
                self._send_url,
                content=raw,
                headers={
                    "Content-Type": "application/json",
                    "X-IoT-Timestamp": str(timestamp),
                    "X-IoT-Nonce": nonce,
                    "X-IoT-Signature": f"sha256={signature}",
                },
            )
            response.raise_for_status()
        finally:
            if self._client is None:
                await client.aclose()
