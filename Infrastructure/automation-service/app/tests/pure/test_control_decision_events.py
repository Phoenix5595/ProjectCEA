from datetime import UTC, datetime

from app.automation.rules_engine import RulesEngine


class RecordingSink:
    def __init__(self) -> None:
        self.events = []

    def emit_nowait(self, event) -> None:
        self.events.append(event)


class AlwaysActiveScheduler:
    def is_schedule_active(self, location, cluster, device_name, current_time):
        return True, 7


def test_rule_match_emits_rule_and_schedule_context() -> None:
    # Given: one active rule and a recording operational-event sink
    sink = RecordingSink()
    engine = RulesEngine(
        [
            {
                "id": 42,
                "location": "Flower Room",
                "cluster": "main",
                "action_device": "heater-1",
                "action_state": 1,
                "condition_sensor": "temperature",
                "condition_operator": "<",
                "condition_value": 20.0,
                "schedule_id": 7,
            }
        ],
        AlwaysActiveScheduler(),
        event_sink=sink,
    )

    # When: the rule condition is met
    result = engine.evaluate(
        "Flower Room",
        "main",
        {"temperature": 19.0},
        datetime(2026, 9, 1, tzinfo=UTC),
    )

    # Then: the decision and its causal identifiers are emitted
    assert result == ("heater-1", 1, 42)
    assert [event.event_type for event in sink.events] == ["control.rule_matched"]
    assert sink.events[0].reason_code == "control.rule_matched"
    assert sink.events[0].reason_text == "Rule 42 matched under schedule 7"
