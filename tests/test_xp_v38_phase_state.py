#!/usr/bin/env python3
"""Offline tests for XP v38 phase-aware state and selection gate."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace


PROJECT = Path("/home/anonymous/program/resource_adaptive")
if str(PROJECT / "tools") not in sys.path:
    sys.path.insert(0, str(PROJECT / "tools"))

import xp_v38_phase_state_patch as v38


def state() -> dict:
    return {
        "status": "running",
        "accepted_count": 0,
        "accepted_fingerprints": [],
        "incumbent": {"params": {"p": 1.0}, "score": 1.0},
        "incumbent_score": 1.0,
        "incumbent_params": {"p": 1.0},
        "parameter_ranges": {"p": {"min_value": 1.0, "max_value": 1.0}},
        "range_state": {"p": {"min_value": 1.0, "max_value": 1.0}},
        "gradient_state": {"update_count": 0},
    }


class MemoryStore:
    def __init__(self, value: dict) -> None:
        self.value = copy.deepcopy(value)

    def load(self) -> dict:
        return copy.deepcopy(self.value)

    def commit(self, value: dict) -> None:
        self.value = copy.deepcopy(value)


class CaptureController:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


def candidate(rank: int, role: str) -> dict:
    return {
        "rank": rank,
        "selection_role": role,
        "boundary_corner": False,
        "near_corner_excluded_from_noncorner_gate": False,
        "three_level_fingerprint": {"id": f"three-level-sha256:{rank:064x}"},
    }


def ready_selection() -> dict:
    return {
        "batch_mode": "diagnostic_validation",
        "replay_authorized": True,
        "top5": [candidate(index, role) for index, role in enumerate(v38.EXPECTED_ROLES, 1)],
    }


def controller(store: MemoryStore, factory, runner=lambda *args: {}) -> CaptureController:
    continuation = SimpleNamespace(
        UnattendedContinuationController=CaptureController,
        REQUIRED_CPUSET="32-51,288-307",
    )
    v38.install_controller(continuation)
    return continuation.UnattendedContinuationController(
        state_store=store,
        batch_root=Path("/tmp/xp-v38-test"),
        candidate_factory=factory,
        batch_runner=runner,
        baseline_restore=lambda *args: None,
    )


def test_failure_becomes_blocked_preworkload() -> None:
    store = MemoryStore(state())

    def fail(*args):
        raise RuntimeError("offline generator stopped")

    instance = controller(store, fail)
    wrapped = instance.kwargs["candidate_factory"]
    try:
        wrapped("cont003", store.load(), Path(tempfile.mkdtemp()))
    except RuntimeError:
        pass
    else:
        raise AssertionError("offline generator failure was swallowed")
    incoming = state()
    incoming["status"] = "blocked_contract"
    incoming["last_error"] = "offline generator stopped"
    instance.kwargs["state_store"].commit(incoming)
    assert store.value["status"] == "blocked_preworkload"
    assert store.value["active_phase"]["physical_workload_calls"] == 0


def test_ready_gate_and_replay_phase_are_atomic() -> None:
    store = MemoryStore(state())
    calls = []
    instance = controller(
        store,
        lambda *args: ready_selection(),
        runner=lambda *args: calls.append(args) or {"ok": True},
    )
    with tempfile.TemporaryDirectory(prefix="xp_v38_phase_") as raw:
        selected = instance.kwargs["candidate_factory"](
            "cont003", state(), Path(raw)
        )
    assert store.value["status"] == "diagnostic_ready_preworkload"
    assert store.value["accepted_count"] == 0
    assert store.value["gradient_state"]["update_count"] == 0
    assert selected["v38_automatic_reexploration"]["manual_candidate_injection"] is False
    instance.kwargs["batch_runner"](selected, Path("/tmp/xp-v38"), {})
    assert store.value["status"] == "diagnostic_replay_running"
    assert len(calls) == 1


def test_probe5_cannot_bypass_trusted3_probe2_gate() -> None:
    selection = ready_selection()
    for item in selection["top5"]:
        item["selection_role"] = "maximin_uncertain_probe"
    gate = v38._selection_gate(selection)
    assert gate["passed"] is False
    assert gate["checks"]["role_stage_contract"] is False


def test_bounded_probe5_bootstrap_is_diagnostic_only() -> None:
    selection = ready_selection()
    for item in selection["top5"]:
        item["selection_role"] = "maximin_uncertain_probe"
        item["plan_regime_calibration"] = {"unknown_plan_probe_only": True}
    selection["v36_resource_safe_maximin_fallback"] = {
        "contract": "xp_v36_resource_safe_maximin_probe5_fallback_v1",
        "all_candidates_remain_uncertain_probes": True,
        "trusted_promotion_applied": False,
        "resource_envelope_relaxed": False,
        "manual_candidate_injection": False,
    }
    gate = v38._selection_gate(selection)
    assert isinstance(gate, dict)
    assert gate["passed"] is True
    assert gate["selection_stage"] == "discovery_bootstrap_probe5"
    selection["batch_mode"] = "formal"
    formal_gate = v38._selection_gate(selection)
    assert formal_gate["passed"] is False
    assert formal_gate["checks"]["formal_requires_trusted3_probe2"] is False


def test_two_diagnostics_are_followed_by_formal_third() -> None:
    selection_path = PROJECT / (
        "history/xp_final_algorithm_fresh10_v37_20260803/"
        "batches/cont002/selection.json"
    )
    replay_path = PROJECT / (
        "history/xp_final_algorithm_fresh10_v37_20260803/"
        "batches/cont002/replay_summary.json"
    )

    def old_gate(*args):
        return {
            "contract": "xp_fresh0_formal_entry_robust_exact_regime_v4",
            "checks": {
                "five_novel_noncorner_fingerprints": True,
                "trusted3_probe2": True,
                "top5_clean_no_technical_failure": True,
                "trusted_spearman_positive": True,
                "actual_best_in_predicted_top3": True,
                "trusted_resource_no_order_of_magnitude_error": True,
                "score_span_exceeds_replay_noise_bound": True,
                "baseline_restored": True,
                "diagnostic_state_neutral": True,
                "robust_trusted_three_independent_stable_observations": False,
            },
            "passed": False,
        }

    adapter = SimpleNamespace(_diagnostic_entry_gate=old_gate)
    v38.install_adapter(adapter)
    gate = adapter._diagnostic_entry_gate(selection_path, replay_path)
    assert gate["passed"] is True
    assert gate["contract"] == "xp_v38_two_diagnostics_then_formal_third_entry_v1"
    assert gate["checks"][
        "three_trusted_have_two_sealed_diagnostic_observations"
    ] is True
    assert gate["formal_state_mutation_before_third_observation"] is False


def main() -> int:
    tests = [
        test_failure_becomes_blocked_preworkload,
        test_ready_gate_and_replay_phase_are_atomic,
        test_probe5_cannot_bypass_trusted3_probe2_gate,
        test_bounded_probe5_bootstrap_is_diagnostic_only,
        test_two_diagnostics_are_followed_by_formal_third,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}", flush=True)
    print(f"PASS {len(tests)}/{len(tests)} XP v38 phase tests", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
