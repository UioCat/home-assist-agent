"""Aliyun-compatible Thing Specification Language (TSL) validation."""

from __future__ import annotations

from datetime import datetime
from numbers import Real
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from iot_mcp.domain.enums import AccessMode, DataType


class TslValidationError(ValueError):
    """Raised when a TSL document or a value does not meet its declared contract."""


def _is_number(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool)


class TslDataType(BaseModel):
    """A TSL data type and its type-specific specification."""

    model_config = ConfigDict(populate_by_name=True)

    type: DataType
    specs: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_shorthand(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"type": value, "specs": {}}
        return value

    @model_validator(mode="after")
    def validate_specs(self) -> TslDataType:
        if not isinstance(self.specs, dict):
            raise ValueError("dataType.specs must be an object")
        if self.type in {DataType.INT, DataType.FLOAT, DataType.DOUBLE}:
            self._validate_numeric_specs()
        elif self.type is DataType.TEXT:
            self._validate_text_specs()
        elif self.type is DataType.ENUM:
            self._validate_enum_specs()
        elif self.type is DataType.STRUCT:
            self._validate_struct_specs()
        elif self.type is DataType.ARRAY:
            self._validate_array_specs()
        return self

    def _validate_numeric_specs(self) -> None:
        for key in ("min", "max", "step"):
            if key in self.specs and not _is_number(self.specs[key]) and not isinstance(
                self.specs[key], str
            ):
                raise ValueError(f"{self.type}.specs.{key} must be numeric")
        minimum = self._number_spec("min")
        maximum = self._number_spec("max")
        step = self._number_spec("step")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError("dataType.specs.min must not exceed max")
        if step is not None and step <= 0:
            raise ValueError("dataType.specs.step must be greater than zero")

    def _validate_text_specs(self) -> None:
        length = self.specs.get("length", self.specs.get("maxLength"))
        if length is not None and (
            not isinstance(length, int) or isinstance(length, bool) or length < 0
        ):
            raise ValueError("text.specs.length must be a non-negative integer")

    def _validate_enum_specs(self) -> None:
        if not self.specs:
            raise ValueError("enum dataType requires non-empty specs")
        if not all(isinstance(key, str) for key in self.specs):
            raise ValueError("enum.specs keys must be strings")

    def _validate_struct_specs(self) -> None:
        fields = self.specs.get("fields")
        if not isinstance(fields, list) or not fields:
            raise ValueError("struct dataType requires specs.fields")
        identifiers: set[str] = set()
        for field in fields:
            if not isinstance(field, dict) or not isinstance(field.get("identifier"), str):
                raise ValueError("struct fields require an identifier")
            identifier = field["identifier"]
            if identifier in identifiers:
                raise ValueError(f"duplicate struct field identifier: {identifier}")
            identifiers.add(identifier)
            try:
                TslDataType.model_validate(field.get("dataType"))
            except Exception as error:  # Pydantic formats the nested cause for callers.
                raise ValueError(f"invalid dataType for struct field {identifier}") from error
            if "required" in field and not isinstance(field["required"], bool):
                raise ValueError(f"struct field {identifier}.required must be boolean")

    def _validate_array_specs(self) -> None:
        item = self.specs.get("item")
        if item is None:
            item = self.specs.get("itemType")
        if item is None:
            raise ValueError("array dataType requires specs.item")
        try:
            TslDataType.model_validate(item)
        except Exception as error:
            raise ValueError("array.specs.item must be a valid dataType") from error
        size = self.specs.get("size", self.specs.get("maxLength"))
        if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size < 0):
            raise ValueError("array.specs.size must be a non-negative integer")

    def _number_spec(self, key: str) -> float | None:
        value = self.specs.get(key)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError) as error:
            raise TslValidationError(f"{self.type}.specs.{key} must be numeric") from error

    def validate_value(self, value: Any) -> None:
        """Validate a concrete property or service value against this data type."""
        if self.type is DataType.BOOL:
            if not isinstance(value, bool):
                raise TslValidationError("expected bool")
        elif self.type is DataType.INT:
            if not isinstance(value, int) or isinstance(value, bool):
                raise TslValidationError("expected int")
            self._validate_numeric_value(value)
        elif self.type in {DataType.FLOAT, DataType.DOUBLE}:
            if not _is_number(value):
                raise TslValidationError(f"expected {self.type}")
            self._validate_numeric_value(float(value))
        elif self.type is DataType.TEXT:
            if not isinstance(value, str):
                raise TslValidationError("expected text")
            length = self.specs.get("length", self.specs.get("maxLength"))
            if length is not None and len(value) > length:
                raise TslValidationError("text exceeds declared length")
        elif self.type is DataType.DATE:
            if not isinstance(value, (int, datetime)) or isinstance(value, bool):
                raise TslValidationError("expected epoch milliseconds or datetime")
        elif self.type is DataType.ENUM:
            if str(value) not in self.specs:
                raise TslValidationError("value is not a declared enum member")
        elif self.type is DataType.STRUCT:
            self._validate_struct_value(value)
        elif self.type is DataType.ARRAY:
            self._validate_array_value(value)

    def _validate_numeric_value(self, value: Real) -> None:
        minimum = self._number_spec("min")
        maximum = self._number_spec("max")
        step = self._number_spec("step")
        if minimum is not None and value < minimum:
            raise TslValidationError("value is below declared minimum")
        if maximum is not None and value > maximum:
            raise TslValidationError("value is above declared maximum")
        if minimum is not None and step is not None:
            ratio = (float(value) - minimum) / step
            if abs(ratio - round(ratio)) > 1e-9:
                raise TslValidationError("value does not align with declared step")

    def _validate_struct_value(self, value: Any) -> None:
        if not isinstance(value, dict):
            raise TslValidationError("expected struct object")
        fields = self.specs["fields"]
        declared = {field["identifier"]: field for field in fields}
        unknown = set(value) - set(declared)
        if unknown:
            raise TslValidationError(f"unknown struct fields: {sorted(unknown)}")
        for identifier, field in declared.items():
            if field.get("required", False) and identifier not in value:
                raise TslValidationError(f"missing required struct field: {identifier}")
            if identifier in value:
                TslDataType.model_validate(field["dataType"]).validate_value(value[identifier])

    def _validate_array_value(self, value: Any) -> None:
        if not isinstance(value, list):
            raise TslValidationError("expected array")
        size = self.specs.get("size", self.specs.get("maxLength"))
        if size is not None and len(value) > size:
            raise TslValidationError("array exceeds declared size")
        item = self.specs.get("item", self.specs.get("itemType"))
        item_type = TslDataType.model_validate(item)
        for item_value in value:
            item_type.validate_value(item_value)


class TslFeature(BaseModel):
    """Shared fields for a TSL property, service, or event."""

    model_config = ConfigDict(populate_by_name=True)

    identifier: str = Field(min_length=1)
    name: str = Field(min_length=1)
    required: bool = False


class TslProperty(TslFeature):
    access_mode: AccessMode = Field(alias="accessMode")
    data_type: TslDataType = Field(alias="dataType")


class TslParameter(TslFeature):
    """A service/event payload field; unlike a property it has no access mode."""

    data_type: TslDataType = Field(alias="dataType")


class TslService(TslFeature):
    call_type: str = Field(default="async", alias="callType", min_length=1)
    input_data: list[TslParameter] = Field(default_factory=list, alias="inputData")
    output_data: list[TslParameter] = Field(default_factory=list, alias="outputData")

    @model_validator(mode="after")
    def validate_input_identifiers(self) -> TslService:
        for features, name in ((self.input_data, "inputData"), (self.output_data, "outputData")):
            identifiers = [feature.identifier for feature in features]
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"duplicate service {name} identifier")
        return self

    def validate_inputs(self, values: dict[str, Any]) -> None:
        declared = {feature.identifier: feature for feature in self.input_data}
        unknown = set(values) - set(declared)
        if unknown:
            raise TslValidationError(f"unknown service inputs: {sorted(unknown)}")
        for identifier, feature in declared.items():
            if feature.required and identifier not in values:
                raise TslValidationError(f"missing required service input: {identifier}")
            if identifier in values:
                feature.data_type.validate_value(values[identifier])


class TslEvent(TslFeature):
    type: str = Field(min_length=1)
    output_data: list[TslParameter] = Field(default_factory=list, alias="outputData")

    @model_validator(mode="after")
    def validate_output_identifiers(self) -> TslEvent:
        identifiers = [feature.identifier for feature in self.output_data]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("duplicate event outputData identifier")
        return self


class TslDocument(BaseModel):
    """Validated TSL document, retaining the standard Aliyun top-level structure."""

    model_config = ConfigDict(populate_by_name=True)

    schema_: str = Field(alias="schema", min_length=1)
    profile: dict[str, Any]
    properties: list[TslProperty] = Field(default_factory=list)
    services: list[TslService] = Field(default_factory=list)
    events: list[TslEvent] = Field(default_factory=list)

    @field_validator("profile")
    @classmethod
    def validate_profile(cls, profile: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(profile.get("productKey"), str) or not profile["productKey"]:
            raise ValueError("profile.productKey is required")
        return profile

    @model_validator(mode="after")
    def validate_feature_identifiers(self) -> TslDocument:
        identifiers = [
            *(feature.identifier for feature in self.properties),
            *(feature.identifier for feature in self.services),
            *(feature.identifier for feature in self.events),
        ]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError(
                "feature identifiers must be unique across properties, services, and events"
            )
        return self

    def property(self, identifier: str) -> TslProperty:
        for feature in self.properties:
            if feature.identifier == identifier:
                return feature
        raise TslValidationError(f"unknown property: {identifier}")

    def service(self, identifier: str) -> TslService:
        for feature in self.services:
            if feature.identifier == identifier:
                return feature
        raise TslValidationError(f"unknown service: {identifier}")

    def validate_property_write(self, identifier: str, value: Any) -> None:
        property_ = self.property(identifier)
        if property_.access_mode is not AccessMode.READ_WRITE:
            raise TslValidationError(f"property is not writable: {identifier}")
        property_.data_type.validate_value(value)

    def validate_service_inputs(self, identifier: str, values: dict[str, Any]) -> None:
        self.service(identifier).validate_inputs(values)
