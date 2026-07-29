from iot_mcp.application.policy import (
    ControlAction,
    ControlPolicy,
    TrustedPrincipal,
    canonical_action_hash,
)
from iot_mcp.domain.enums import InteractionMode, RiskLevel


def test_only_trusted_web_principal_is_human_interactive() -> None:
    assert TrustedPrincipal.web_session("owner").mode is InteractionMode.HUMAN_INTERACTIVE
    assert TrustedPrincipal.admin_token("owner").mode is InteractionMode.AUTONOMOUS
    assert TrustedPrincipal.machine_token("agent").mode is InteractionMode.AUTONOMOUS
    assert TrustedPrincipal.mcp("tool-call").mode is InteractionMode.AUTONOMOUS
    assert TrustedPrincipal.anonymous().mode is InteractionMode.AUTONOMOUS


def test_high_risk_autonomous_action_requires_confirmation() -> None:
    policy = ControlPolicy()

    assert policy.requires_confirmation(
        principal=TrustedPrincipal.mcp("tool-call"),
        risk=RiskLevel.HIGH,
    )
    assert not policy.requires_confirmation(
        principal=TrustedPrincipal.web_session("owner"),
        risk=RiskLevel.HIGH,
    )
    assert not policy.requires_confirmation(
        principal=TrustedPrincipal.machine_token("agent"),
        risk=RiskLevel.LOW,
    )


def test_action_hash_binds_device_action_and_binding_revision() -> None:
    action = ControlAction.properties({"LockState": "UNLOCK"})

    first = canonical_action_hash("front-door", action, binding_revision=1)
    assert first == canonical_action_hash("front-door", action, binding_revision=1)
    assert first != canonical_action_hash("front-door", action, binding_revision=2)
    assert first != canonical_action_hash(
        "front-door",
        ControlAction.properties({"LockState": "LOCK"}),
        binding_revision=1,
    )
