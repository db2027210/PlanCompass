import json
from pathlib import Path

from formal_stability import FrozenStabilityEvaluator, StabilitySample


PROTOCOL = (
    Path(__file__).parents[1]
    / "history/unified_formal/six_session_20260721_v3/shared/frozen_config/measurement_protocol.json"
)


def _evaluator():
    return FrozenStabilityEvaluator(json.loads(PROTOCOL.read_text(encoding="utf-8")))


def _samples(seconds=180, *, memory_jitter=0.001, xp=False):
    return [
        StabilitySample(
            elapsed_s=float(second),
            cpu=0.5 + (0.001 if second % 2 else -0.001),
            memory=0.9 + (memory_jitter if second % 2 else -memory_jitter),
            io=0.04 + (0.002 if second % 2 else -0.002),
            qps=None if xp else 10.0 + (0.1 if second % 2 else -0.1),
            xap_qps=(5.0 + (0.1 if second % 2 else -0.1)) if xp else None,
            xtp_tps=(50.0 + (1.0 if second % 2 else -1.0)) if xp else None,
        )
        for second in range(1, seconds + 1)
    ]


def test_stable_normal_measurement_may_stop_at_180_seconds():
    decision = _evaluator().evaluate(_samples(), workload_is_xp=False, elapsed_s=180)
    assert decision.stable
    assert decision.may_stop
    assert decision.consecutive_stable_windows == 6
    assert [window.index for window in decision.windows] == [1, 2, 3, 4, 5, 6]
    assert decision.windows[0].start_s == 0
    assert decision.windows[-1].end_s == 180


def test_qps_instability_is_audit_only_not_resource_stability_gate():
    samples = _samples()
    for index, sample in enumerate(samples):
        object.__setattr__(sample, "qps", 0.0 if index % 2 else 100.0)
    decision = _evaluator().evaluate(samples, workload_is_xp=False, elapsed_s=180)
    assert decision.stable
    assert decision.windows[-1].metrics["qps"]["stable"] is False
    assert decision.windows[-1].metrics["qps"]["decision_role"] == "audit_only"
    assert "qps_unstable" not in decision.windows[-1].failures


def test_missing_qps_is_audit_only_not_resource_stability_gate():
    samples = _samples()
    for sample in samples:
        object.__setattr__(sample, "qps", None)
    decision = _evaluator().evaluate(samples, workload_is_xp=False, elapsed_s=180)
    assert decision.stable
    assert decision.windows[-1].metrics["qps"]["stable"] is False
    assert decision.windows[-1].metrics["qps"]["decision_role"] == "audit_only"
    assert "qps_missing" not in decision.windows[-1].failures


def test_memory_uses_stricter_three_percent_cv():
    samples = _samples(memory_jitter=0.04)
    decision = _evaluator().evaluate(samples, workload_is_xp=False, elapsed_s=180)
    assert not decision.stable
    assert "memory_unstable" in decision.windows[-1].failures


def test_low_utilization_resource_uses_absolute_range():
    decision = _evaluator().evaluate(_samples(), workload_is_xp=False, elapsed_s=180)
    assert decision.windows[-1].metrics["io"]["rule"] == "low_utilization_absolute_range"
    assert decision.windows[-1].metrics["io"]["stable"]


def test_xp_streams_are_checked_independently():
    decision = _evaluator().evaluate(_samples(xp=True), workload_is_xp=True, elapsed_s=180)
    assert decision.stable
    assert "xap_qps" in decision.windows[-1].metrics
    assert "xtp_tps" in decision.windows[-1].metrics


def test_missing_samples_fail_coverage():
    samples = [sample for index, sample in enumerate(_samples()) if index % 3]
    decision = _evaluator().evaluate(samples, workload_is_xp=False, elapsed_s=180)
    assert not decision.stable
    assert decision.sample_coverage < 0.9


def test_unstable_measurement_terminates_at_600_without_passing():
    samples = _samples(seconds=600, memory_jitter=0.04)
    decision = _evaluator().evaluate(samples, workload_is_xp=False, elapsed_s=600)
    assert not decision.stable
    assert decision.terminal_at_maximum
    assert decision.stop_reason == "maximum_measurement_seconds_reached_unstable"


def test_early_unstable_window_is_audit_only_after_latest_six_are_stable():
    samples = _samples(seconds=210)
    samples[:30] = _samples(seconds=30, memory_jitter=0.08)
    decision = _evaluator().evaluate(samples, workload_is_xp=False, elapsed_s=210)
    assert not decision.windows[0].stable
    assert decision.stable
    assert decision.consecutive_stable_windows == 6
    assert decision.sample_coverage == 1.0


def test_early_missing_samples_do_not_reduce_latest_180_second_coverage():
    samples = _samples(seconds=210)[30:]
    decision = _evaluator().evaluate(samples, workload_is_xp=False, elapsed_s=210)
    assert decision.stable
    assert decision.sample_coverage == 1.0


def test_unstable_at_180_extends_in_30_second_steps_until_latest_six_pass():
    samples = _samples(seconds=240)
    unstable = _samples(seconds=60, memory_jitter=0.08)
    samples[:60] = unstable
    at_180 = _evaluator().evaluate(samples[:180], workload_is_xp=False, elapsed_s=180)
    at_210 = _evaluator().evaluate(samples[:210], workload_is_xp=False, elapsed_s=210)
    at_240 = _evaluator().evaluate(samples, workload_is_xp=False, elapsed_s=240)
    assert not at_180.stable
    assert not at_210.stable
    assert at_240.stable
    assert at_240.consecutive_stable_windows == 6


def test_window_mean_protocol_ignores_one_second_burstiness_when_six_means_stable():
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    protocol["stability"]["calculation"] = "cv_across_latest_six_window_means"
    evaluator = FrozenStabilityEvaluator(protocol)
    samples = _samples(seconds=180)
    for index, sample in enumerate(samples):
        object.__setattr__(sample, "cpu", 0.8 if index % 2 else 0.2)
    decision = evaluator.evaluate(samples, workload_is_xp=False, elapsed_s=180)
    assert decision.stable
    assert decision.stop_reason == "latest_six_window_means_stable"
    assert decision.consecutive_stable_windows == 6
