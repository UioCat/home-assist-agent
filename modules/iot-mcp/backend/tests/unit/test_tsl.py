from __future__ import annotations

import pytest
from pydantic import ValidationError

from iot_mcp.domain.tsl import TslDocument, TslValidationError


def _document() -> dict:
    return {
        "schema": "https://iotx-tsl.example/schema.json",
        "profile": {"productKey": "lamp-v1"},
        "properties": [
            {
                "identifier": "brightness",
                "name": "Brightness",
                "accessMode": "rw",
                "dataType": {"type": "int", "specs": {"min": 0, "max": 100, "step": 5}},
            },
            {
                "identifier": "metadata",
                "name": "Metadata",
                "accessMode": "rw",
                "dataType": {
                    "type": "struct",
                    "specs": {
                        "fields": [
                            {
                                "identifier": "label",
                                "required": True,
                                "dataType": {"type": "text", "specs": {"length": 20}},
                            }
                        ]
                    },
                },
            },
        ],
        "services": [
            {
                "identifier": "flash",
                "name": "Flash",
                "inputData": [
                    {
                        "identifier": "seconds",
                        "name": "Seconds",
                        "required": True,
                        "dataType": {"type": "float", "specs": {"min": 0.5, "max": 10}},
                    }
                ],
            }
        ],
        "events": [
            {
                "identifier": "overheated",
                "name": "Overheated",
                "type": "alert",
                "outputData": [
                    {
                        "identifier": "temperature",
                        "name": "Temperature",
                        "dataType": {"type": "double", "specs": {"min": -50, "max": 200}},
                    }
                ],
            }
        ],
    }


def test_tsl_validates_writes_service_inputs_and_struct_fields() -> None:
    tsl = TslDocument.model_validate(_document())

    tsl.validate_property_write("brightness", 55)
    tsl.validate_property_write("metadata", {"label": "living room"})
    tsl.validate_service_inputs("flash", {"seconds": 1.5})

    with pytest.raises(TslValidationError, match="step"):
        tsl.validate_property_write("brightness", 53)
    with pytest.raises(TslValidationError, match="missing required struct"):
        tsl.validate_property_write("metadata", {})
    with pytest.raises(TslValidationError, match="missing required service input"):
        tsl.validate_service_inputs("flash", {})


def test_tsl_rejects_duplicate_identifiers_invalid_access_and_specs() -> None:
    duplicate = _document()
    duplicate["events"][0]["identifier"] = "brightness"
    with pytest.raises(ValidationError, match="identifiers must be unique"):
        TslDocument.model_validate(duplicate)

    invalid_access = _document()
    invalid_access["properties"][0]["accessMode"] = "write"
    with pytest.raises(ValidationError, match="accessMode"):
        TslDocument.model_validate(invalid_access)

    invalid_specs = _document()
    invalid_specs["properties"][0]["dataType"]["specs"] = {"min": 10, "max": 1}
    with pytest.raises(ValidationError, match="must not exceed"):
        TslDocument.model_validate(invalid_specs)
