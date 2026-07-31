"""Stable values shared by the IoT MCP domain."""

from enum import StrEnum


class DataType(StrEnum):
    INT = "int"
    FLOAT = "float"
    DOUBLE = "double"
    TEXT = "text"
    DATE = "date"
    BOOL = "bool"
    ENUM = "enum"
    STRUCT = "struct"
    ARRAY = "array"


class AccessMode(StrEnum):
    READ_ONLY = "r"
    READ_WRITE = "rw"


class ModelStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class InteractionMode(StrEnum):
    HUMAN_INTERACTIVE = "human_interactive"
    AUTONOMOUS = "autonomous"


class OperationStatus(StrEnum):
    REQUESTED = "requested"
    PENDING_CONFIRMATION = "pending_confirmation"
    APPROVED = "approved"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    NO_OP = "no_op"
    ACCEPTED = "accepted"
    FAILED = "failed"
    UNKNOWN = "unknown"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ConfirmationDecision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class Freshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"
