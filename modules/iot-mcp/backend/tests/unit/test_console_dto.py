from iot_mcp.adapters.inbound.http.console_dto import redact_sensitive


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
