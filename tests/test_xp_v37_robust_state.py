#!/usr/bin/env python3
"""Offline regression tests for the XP v37 formal-state contract."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace


PROJECT = Path("/home/anonymous/program/resource_adaptive")
BUNDLE = Path("/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine")
FORMAL_SELECTION = PROJECT / (
    "history/xp_final_algorithm_fresh10_v26_20260731T1935/"
    "batches/cont038/selection.json"
)
PROBE_SELECTION = PROJECT / (
    "history/xp_final_algorithm_fresh10_v36_20260803/"
    "batches/cont001/selection.json"
)
PROBE_REPLAY = PROJECT / (
    "history/xp_final_algorithm_fresh10_v36_20260803/"
    "batches/cont001/replay_summary.json"
)
if str(PROJECT / "tools") not in sys.path:
    sys.path.insert(0, str(PROJECT / "tools"))
if str(BUNDLE) not in sys.path:
    sys.path.insert(0, str(BUNDLE))

import xp_unattended_adapters as adapter  # type: ignore
import xp_v37_robust_state_patch as v37


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def assert_true(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def test_real_sealed_handoff_and_acceptance() -> None:
    selection = load(FORMAL_SELECTION)
    capability = load(BUNDLE / "authoritative_round9/config.json")["capability"]
    with tempfile.TemporaryDirectory(prefix="xp_v37_test_") as raw:
        batch_dir = Path(raw)
        selection["preformal_robust_handoff"] = v37.build_preformal_handoff(
            adapter, selection, batch_dir, capability
        )
        handoff = v37.validate_preformal_handoff(selection)
        records = {
            item["fingerprint_id"]: item for item in handoff["candidates"]
        }
        successful = []
        ledger = []
        for candidate in selection["top5"]:
            fingerprint_id = candidate["three_level_fingerprint"]["id"]
            second = records[fingerprint_id]["observations"][1]
            row = {
                "fingerprint_id": fingerprint_id,
                "selection_role": candidate["selection_role"],
                "candidate_params": copy.deepcopy(candidate["candidate_params"]),
                "actual_score": second["actual_score"],
                "actual_resource": copy.deepcopy(second["actual_resource"]),
                "physical_call": True,
                "technical_failure": False,
                "rejected": False,
            }
            successful.append(row)
            ledger.append(copy.deepcopy(row))
        ranges = {
            name: {"min_value": 0.0, "max_value": 10000.0}
            for name in selection["top5"][0]["candidate_params"]
        }
        summary = {"successful": successful, "ledger": ledger}
        env = {
            "XP_INCUMBENT_SCORE": "-100",
            "XP_INCUMBENT_PARAMS_JSON": json.dumps(
                selection["top5"][0]["candidate_params"]
            ),
            "XP_PARAMETER_RANGES_JSON": json.dumps(ranges),
            "XP_ACCEPTED_REMAINING": "1",
        }
        v37.promote_acceptance_v37(adapter, summary, selection, env)
        accepted = [item for item in successful if item.get("accepted") is True]
        probes = [
            item
            for item in successful
            if item["selection_role"] == "maximin_uncertain_probe"
        ]
        assert_true(bool(accepted), "expected at least one trusted acceptance")
        assert_true(
            all(item.get("accepted") is False for item in probes),
            "probe was accepted",
        )
        assert_true(
            all(item.get("state_patch") == {} for item in probes),
            "probe mutated formal state",
        )
        for item in accepted:
            incumbent = item["state_patch"]["incumbent"]
            score = adapter.replay_driver.actual_score(
                incumbent["actual_utilization"], capability
            )
            assert_true(
                abs(score - incumbent["score"]) <= 1e-12,
                "stored incumbent resource and score are not aligned",
            )
            assert_true(
                item["score_median_improvement_gate"] is True
                and item["resource_recomputed_score_improvement_gate"] is True,
                "accepted point bypassed a robust gate",
            )


def test_missing_handoff_fails_before_runner() -> None:
    calls = []

    def original_run(*args, **kwargs):
        calls.append((args, kwargs))
        return {}

    fake = SimpleNamespace(
        generate_selection=lambda *args, **kwargs: {"batch_mode": "diagnostic_validation"},
        run_batch=original_run,
        _promote_continuation_acceptance=lambda *args, **kwargs: {},
        _score_noise_bound=lambda *args, **kwargs: 1.0,
    )
    v37.install(fake)
    try:
        fake.run_batch(None, {"batch_mode": "formal"}, Path("/tmp/no-call"), {})
    except RuntimeError as exc:
        assert_true("lacks sealed handoff" in str(exc), "unexpected fail-close reason")
    else:
        raise AssertionError("formal replay proceeded without handoff")
    assert_true(not calls, "underlying workload runner was called")


def test_changed_handoff_fails_closed() -> None:
    selection = load(FORMAL_SELECTION)
    capability = load(BUNDLE / "authoritative_round9/config.json")["capability"]
    with tempfile.TemporaryDirectory(prefix="xp_v37_tamper_") as raw:
        selection["preformal_robust_handoff"] = v37.build_preformal_handoff(
            adapter, selection, Path(raw), capability
        )
        handoff_path = Path(selection["preformal_robust_handoff"]["path"])
        with handoff_path.open("a", encoding="utf-8") as handle:
            handle.write(" ")
        try:
            v37.validate_preformal_handoff(selection)
        except RuntimeError as exc:
            assert_true("SHA mismatch" in str(exc), "unexpected tamper failure")
        else:
            raise AssertionError("tampered handoff was accepted")


def test_probe5_acquires_local_noise_without_seed_bias() -> None:
    calls = []

    def old_bound(selection_path: Path, replay_path: Path) -> float:
        calls.append((selection_path, replay_path))
        return 22.107605849570696

    bound = v37.score_noise_bound_v37(old_bound, PROBE_SELECTION, PROBE_REPLAY)
    assert_true(bound == 0.0, "probe5 acquisition retained heterogeneous seed bias")
    assert_true(not calls, "old cross-regime noise estimator was invoked")
    repeat_bound = v37.score_noise_bound_v37(
        old_bound,
        FORMAL_SELECTION,
        Path(
            "history/xp_final_algorithm_fresh10_v26_20260731T1935/"
            "batches/cont038/replay_summary.json"
        ),
    )
    assert_true(repeat_bound == 22.107605849570696, "repeat gate was relaxed")
    assert_true(len(calls) == 1, "same-point repeat did not use the hard estimator")


def test_exact_repeat_selection_contract_is_registered() -> None:
    import xp_round9_serial_replay as serial_replay  # type: ignore

    fake = SimpleNamespace(
        generate_selection=lambda *args, **kwargs: {"batch_mode": "diagnostic_validation"},
        run_batch=lambda *args, **kwargs: {},
        _promote_continuation_acceptance=lambda *args, **kwargs: {},
        _score_noise_bound=lambda *args, **kwargs: 1.0,
    )
    v37.install(fake)
    assert_true(
        v37.EXACT_REPEAT_SELECTION_CONTRACT
        in serial_replay.SUPPORTED_SELECTION_CONTRACTS,
        "sealed exact-point repeat contract was not registered with the serial runner",
    )


def main() -> int:
    tests = [
        test_real_sealed_handoff_and_acceptance,
        test_missing_handoff_fails_before_runner,
        test_changed_handoff_fails_closed,
        test_probe5_acquires_local_noise_without_seed_bias,
        test_exact_repeat_selection_contract_is_registered,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}", flush=True)
    print(f"PASS {len(tests)}/{len(tests)} XP v37 offline tests", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
