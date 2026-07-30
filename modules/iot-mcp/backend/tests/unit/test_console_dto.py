from iot_mcp.adapters.inbound.http.console_dto import (
    operation_console_dto,
    operation_public_dto,
    redact_sensitive,
    safe_action_dto,
)
from iot_mcp.application.policy import ControlAction
from iot_mcp.domain.enums import InteractionMode
from iot_mcp.domain.models import ControlOperation


def test_recursive_redaction_covers_nested_secret_keys() -> None:
    value = {
        "pin": "1234",
        "credential": "credential-value",
        "nested": {
            "accessToken": "token-value",
            "api-token-value": "hyphen-token-value",
            "authorization": "Bearer secret",
            "private_key": "key-value",
            "secret": "secret-value",
            "items": [{"password": "password-value"}, {"safe": "visible"}],
        },
    }
    assert redact_sensitive(value) == {
        "pin": "[REDACTED]",
        "credential": "[REDACTED]",
        "nested": {
            "accessToken": "[REDACTED]",
            "api-token-value": "[REDACTED]",
            "authorization": "[REDACTED]",
            "private_key": "[REDACTED]",
            "secret": "[REDACTED]",
            "items": [{"password": "[REDACTED]"}, {"safe": "visible"}],
        },
    }


def _operation(action: ControlAction) -> ControlOperation:
    return ControlOperation(
        device_id="door",
        initiator="machine_token:agent",
        interaction_mode=InteractionMode.AUTONOMOUS,
        action=action.model_dump(mode="json"),
        idempotency_key="test-operation",
    )


def test_property_summary_shows_exact_safe_values_and_redacts_only_secrets() -> None:
    dto = operation_console_dto(
        _operation(
            ControlAction.properties(
                {"LockState": "UNLOCK", "pin": "839201", "KeypadLock": "OPEN"}
            )
        )
    )

    assert dto["action_summary"] == (
        "写入属性：KeypadLock=OPEN、LockState=UNLOCK、pin=[REDACTED]"
    )
    assert dto["sensitive_values_redacted"] is True
    assert "839201" not in str(dto)
    assert "UNLOCK" in str(dto)
    assert "OPEN" in str(dto)


def test_keypad_lock_only_summary_does_not_collapse_to_zero() -> None:
    dto = operation_console_dto(
        _operation(ControlAction.properties({"KeypadLock": "LOCK"}))
    )

    assert dto["action_summary"] == "写入属性：KeypadLock=LOCK"
    assert dto["sensitive_values_redacted"] is False


def test_service_summary_keeps_identifier_and_real_parameter_count() -> None:
    dto = operation_console_dto(
        _operation(
            ControlAction.invoke_service(
                "TemporaryUnlock",
                {"pin": "839201", "duration": 30, "accessKey": "hidden"},
            )
        )
    )

    assert dto["action_summary"] == (
        "调用服务 TemporaryUnlock：accessKey=[REDACTED]、duration=30、pin=[REDACTED]"
    )
    assert dto["sensitive_values_redacted"] is True
    assert "839201" not in str(dto)
    assert "hidden" not in str(dto)
    assert "duration=30" in dto["action_summary"]


def test_public_operation_uses_one_safe_action_contract_and_omits_raw_ledger_fields() -> None:
    operation = _operation(
        ControlAction.invoke_service(
            "TemporaryUnlock",
            {
                "duration": 30,
                "credential": {"pin": "839201"},
                "nested": [{"authorization": "Bearer provider-secret"}],
            },
        )
    ).model_copy(
        update={
            "provider_request": {"token": "provider-request-secret"},
            "provider_result": {"password": "provider-result-secret"},
        }
    )

    action = safe_action_dto(operation.action)
    dto = operation_public_dto(operation)

    assert action == {
        "kind": "service",
        "service": "TemporaryUnlock",
        "inputs": {
            "duration": 30,
            "credential": "[REDACTED]",
            "nested": [{"authorization": "[REDACTED]"}],
        },
    }
    assert dto["action"] == action
    assert "idempotency_key" not in dto
    assert "provider_request" not in dto
    assert "provider_result" not in dto
    serialized = str(dto)
    assert "839201" not in serialized
    assert "provider-secret" not in serialized
