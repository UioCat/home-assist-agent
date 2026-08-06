from iot_mcp.application.query_service import _primary_control


def test_primary_control_keeps_normalized_suffix_for_multi_entity_device() -> None:
    control = _primary_control(
        {
            "PowerSwitch_1": {
                "identifier": "PowerSwitch_1",
                "name": "主灯电源",
                "accessMode": "rw",
                "dataType": {"type": "bool", "specs": {}},
            }
        },
        {"PowerSwitch_1": True},
        {"PowerSwitch_1": "low"},
        "low",
    )

    assert control == {
        "kind": "property",
        "identifier": "PowerSwitch_1",
        "name": "主灯电源",
        "data_type": {"type": "bool", "specs": {}},
        "current_value": True,
        "risk_level": "low",
    }
