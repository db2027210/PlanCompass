from __future__ import annotations

import formal_mysql_campaign_v2_20260721 as campaign


def _pressure(*, memory: bool = True, io: bool = True):
    return {"memory": {"passed": memory}, "io": {"passed": io}}


def _gate(scenario: str, resources, pressure=None):
    return campaign._baseline_gate(
        scenario,
        resources,
        pressure or _pressure(),
        stable=True,
        correctness=True,
        error_rate=0.0,
        oom=False,
        restarted=False,
    )


def test_r0_requires_target_85_95_and_both_non_targets_30_50() -> None:
    gate = _gate("memory", {"cpu": 0.40, "memory": 0.90, "io": 0.40})
    assert gate["passed"] is True
    assert gate["target_required_range"] == [0.85, 0.95]
    assert gate["non_target_required_range"] == [0.3, 0.5]


def test_r0_rejects_target_above_95() -> None:
    gate = _gate("memory", {"cpu": 0.40, "memory": 0.96, "io": 0.40})
    assert gate["passed"] is False
    assert "target_memory_utilization_above_0.95" in gate["failures"]


def test_r0_rejects_non_target_below_or_above_30_50() -> None:
    low = _gate("memory", {"cpu": 0.29, "memory": 0.90, "io": 0.40})
    high = _gate("memory", {"cpu": 0.40, "memory": 0.90, "io": 0.51})
    assert low["passed"] is False
    assert high["passed"] is False
    assert "non_target_cpu_utilization_outside_0.3_0.5" in low["failures"]
    assert "non_target_io_utilization_outside_0.3_0.5" in high["failures"]


def test_r0_memory_and_io_require_pressure_evidence() -> None:
    memory = _gate(
        "memory", {"cpu": 0.40, "memory": 0.90, "io": 0.40}, _pressure(memory=False)
    )
    io = _gate("io", {"cpu": 0.40, "memory": 0.40, "io": 0.90}, _pressure(io=False))
    assert "memory_pressure_evidence_absent" in memory["failures"]
    assert "io_pressure_evidence_absent" in io["failures"]
