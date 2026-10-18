from __future__ import annotations

import pytest

import formal_mysql_campaign_v2_20260721 as campaign


class FakeContinuousSession:
    def __init__(self, stable_at: int | None, *, source: str = "job_completion_events") -> None:
        self.stable_at = stable_at
        self.source = source
        self.starts: list[tuple[int, int]] = []
        self.waits: list[int] = []
        self.stops: list[int] = []

    def start(self, *, warmup_seconds: int, maximum_measurement_seconds: int) -> None:
        self.starts.append((warmup_seconds, maximum_measurement_seconds))

    def wait_until_measurement(self, measurement_seconds: int) -> None:
        self.waits.append(measurement_seconds)

    def observation(self, measurement_seconds: int):
        return {
            "measurement_seconds": measurement_seconds,
            "throughput_evidence": {"reliable": True, "source": self.source},
            "stability": {
                "stable": self.stable_at is not None and measurement_seconds >= self.stable_at,
                "decision_window_seconds": 180,
            },
        }

    def stop(self, measurement_seconds: int):
        self.stops.append(measurement_seconds)
        return {"sampling": {"measurement_seconds": measurement_seconds}}


def test_continuous_measurement_extends_exactly_thirty_seconds_until_stable() -> None:
    session = FakeContinuousSession(stable_at=240)

    result = campaign._run_continuous_measurement(session)

    assert session.starts == [(60, 600)]
    assert session.waits == [180, 210, 240]
    assert session.stops == [240]
    assert result["rolling_measurement_control"]["selected_measurement_seconds"] == 240
    assert result["rolling_measurement_control"]["decision_window_seconds"] == 180


def test_continuous_measurement_uses_one_session_through_maximum() -> None:
    session = FakeContinuousSession(stable_at=None)

    result = campaign._run_continuous_measurement(session)

    assert session.starts == [(60, 600)]
    assert session.waits == list(range(180, 601, 30))
    assert session.stops == [600]
    assert result["rolling_measurement_control"]["stop_reason"] == (
        "maximum_measurement_seconds_reached_unstable"
    )


def test_questions_counter_is_rejected_as_workload_throughput() -> None:
    session = FakeContinuousSession(stable_at=180, source="performance_schema.questions")

    with pytest.raises(campaign.GateError, match="Questions is an activity counter"):
        campaign._run_continuous_measurement(session)

    assert session.starts == [(60, 600)]
    assert session.waits == [180]
    assert session.stops == []


def test_wrong_decision_window_fails_closed() -> None:
    session = FakeContinuousSession(stable_at=180)
    original = session.observation

    def wrong_window(measurement_seconds: int):
        value = original(measurement_seconds)
        value["stability"]["decision_window_seconds"] = 210
        return value

    session.observation = wrong_window  # type: ignore[method-assign]
    with pytest.raises(campaign.GateError, match="latest 180-second window"):
        campaign._run_continuous_measurement(session)

