from home_assist_agent.events.models import (
    DerivedDeviceIntent,
    EventRequest,
    HouseholdContextEntry,
)


class NoopAutomationRuleEngine:
    async def evaluate(
        self,
        event: EventRequest,
        context: HouseholdContextEntry,
    ) -> DerivedDeviceIntent | None:
        return None
