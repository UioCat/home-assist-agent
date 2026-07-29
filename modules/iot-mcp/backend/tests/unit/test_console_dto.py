from iot_mcp.adapters.inbound.http.console_dto import (
    operation_console_dto,
    redact_sensitive,
)
from iot_mcp.application.policy import ControlAction
from iot_mcp.domain.enums import InteractionMode
from iot_mcp.domain.models import ControlOperation


def test_recursive_redaction_covers_nested_secret_keys() -> None:
    value = {
        "pin": "1234",
        "nested": {
            "accessToken": "token-value",
            "api-token-value": "hyphen-token-value",
            "items": [{"password": "password-value"}, {"safe": "visible"}],
        },
    }
    assert redact_sensitive(value) == {
        "pin": "[REDACTED]",
        "nested": {
            "accessToken": "[REDACTED]",
            "api-token-value": "[REDACTED]",
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


def test_property_summary_keeps_all_identifiers_but_never_values() -> None:
    dto = operation_console_dto(
        _operation(
            ControlAction.properties(
                {"LockState": "UNLOCK", "pin": "839201", "KeypadLock": "OPEN"}
            )
        )
    )

    assert dto["action_summary"] == "写入 3 个属性：KeypadLock、LockState、pin"
    assert dto["sensitive_values_redacted"] is True
    assert "839201" not in str(dto)
    assert "UNLOCK" not in str(dto)
    assert "OPEN" not in str(dto)


def test_keypad_lock_only_summary_does_not_collapse_to_zero() -> None:
    dto = operation_console_dto(
        _operation(ControlAction.properties({"KeypadLock": "LOCK"}))
    )

    assert dto["action_summary"] == "写入 1 个属性：KeypadLock"
    assert dto["sensitive_values_redacted"] is True


def test_service_summary_keeps_identifier_and_real_parameter_count() -> None:
    dto = operation_console_dto(
        _operation(
            ControlAction.invoke_service(
                "TemporaryUnlock",
                {"pin": "839201", "duration": 30, "accessKey": "hidden"},
            )
        )
    )

    assert (
        dto["action_summary"]
        == "调用服务 TemporaryUnlock（3 个参数：accessKey、duration、pin）"
    )
    assert dto["sensitive_values_redacted"] is True
    assert "839201" not in str(dto)
    assert "hidden" not in str(dto)
