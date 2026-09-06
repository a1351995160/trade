from __future__ import annotations

from chanlun_trader.research_factory.autonomous_control_plane import (
    AgentCapabilityRegistryV1,
    ResearchActionV1,
)


def test_default_registry_keeps_later_side_effects_description_only() -> None:
    registry = AgentCapabilityRegistryV1()

    for capability_id in (
        "CAN_START_PREDICTIVE_TRIAL",
        "CAN_ACCESS_PERFORMANCE",
        "CAN_RUN_FINAL_TEST",
        "CAN_RUN_PROSPECTIVE",
        "CAN_CREATE_REAL_ORDER",
    ):
        capability = registry.get(capability_id)
        assert capability is not None
        assert capability.automatic_execution_allowed is False
        assert capability.outcome_blind_compatible is False


def test_capability_registry_does_not_authorize_an_unlisted_action() -> None:
    registry = AgentCapabilityRegistryV1()
    action = ResearchActionV1.create(
        action_type="START_PREDICTIVE_TRIAL",
        objective_id="OBJECTIVE_CAPABILITY_V1",
        required_capabilities=("CAN_START_PREDICTIVE_TRIAL",),
    )

    supported, missing = registry.supports(action)

    assert supported is True
    assert missing == ()
    assert registry.get("CAN_START_PREDICTIVE_TRIAL").automatic_execution_allowed is False
