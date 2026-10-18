#!/usr/bin/env python3
"""Finish only TPC-H v40 cycle004 exact and robust diagnostics.

The already completed probe is immutable input.  This continuation builds and
runs one exact repeat (five serial calls), then one robust repeat (five serial
calls), and stops.  It contains no gate analysis or formal replay path; the
separate v41 handoff owns all work after the robust diagnostic is clean.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping


B = Path(
    "/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/"
    "pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/"
    "experience_sgd/attempt48_global_replay_cap5_20260726"
)
V40_ROOT = B / "tpch_final_algorithm_fresh0_v40_retest_compat_20260803"
V40_LOOP = V40_ROOT / "target10_loop_v40"
V40_COUNTER = V40_ROOT / "formal_search_accepted_counter.txt"
CYCLE = V40_LOOP / "cycle_004"
PROBE = CYCLE / "probe_top5.a.json"
PROBE_SECOND = CYCLE / "probe_top5.b.json"
PROBE_RUN = CYCLE / "diagnostic_probe"
EXACT = CYCLE / "exact_repeat_top5.a.json"
EXACT_SECOND = CYCLE / "exact_repeat_top5.b.json"
EXACT_RUN = CYCLE / "diagnostic_exact_repeat"
ROBUST = CYCLE / "robust_top5.a.json"
ROBUST_SECOND = CYCLE / "robust_top5.b.json"
ROBUST_RUN = CYCLE / "diagnostic_robust_repeat"
CONTINUATION_AUDIT = CYCLE / "v42_diagnostic_continuation_audit.json"

V26 = Path("/tmp/tpch_fresh0_target10_loop_v36_base.py")
V32 = Path("/tmp/tpch_fresh10_target10_loop_v32_v6_hierarchical.py")
DIAGNOSTIC_RUNNER = Path(
    "/tmp/run_tpch_round29_diagnostic_exact_plan_regime_batch_v42.py"
)
EXPECTED = {
    V26: "34107e91b4bcfd0b2fe8d2911a0855e1512fc881665f4db3c3cb67618bd055fb",
    V32: "54ac22227f190bfbbc1f2889927cba9500cbe1bd6f4347287d69c3647d6e98b5",
    DIAGNOSTIC_RUNNER: "5f6ad0c1afc5bfe0adbb8f93aef9de2e487c1bae5e648fe9c59c793053e9e248",
}
COMMON = Path("/tmp/tpch_fresh10_robust_validation_v22.py")
COMMON_SHA256 = "e56f3db493201e7aec9b944df67e8b40c5226114a87c85adad7b5def0532e578"
EXACT_BUILDER = Path(
    "/tmp/tpch_fresh0_handover_v19/build_tpch_exact_plan_repeat_validation.py"
)
EXACT_BUILDER_SHA256 = (
    "4f1684e73c9c91f32d94655053a4c6af5a8f8f0d2d2eb8a94363eaa24b48cfe7"
)
ROBUST_BUILDER = Path("/tmp/build_tpch_two_observation_robust_repeat_v3.py")
ROBUST_BUILDER_SHA256 = (
    "b124ef363035606c55bcc16a801c220b50725b18d3cbe8e98fe816fdf83195b5"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def import_module(name: str, path: Path) -> Any:
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot load {name}: {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def verify_dependencies() -> None:
    for path, expected in EXPECTED.items():
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"TPC-H v42 dependency SHA mismatch: {path}")
    if not ROBUST_BUILDER.is_file() or sha256(ROBUST_BUILDER) != ROBUST_BUILDER_SHA256:
        raise RuntimeError("TPC-H v42 robust builder SHA mismatch")
    if not COMMON.is_file() or sha256(COMMON) != COMMON_SHA256:
        raise RuntimeError("TPC-H v42 common controller SHA mismatch")
    if not EXACT_BUILDER.is_file() or sha256(EXACT_BUILDER) != EXACT_BUILDER_SHA256:
        raise RuntimeError("TPC-H v42 exact-repeat builder SHA mismatch")


def clean_diagnostic(
    selection: Path,
    run_root: Path,
    anchor_state: Path,
) -> Dict[str, Any]:
    state_path = run_root / "diagnostic_state.json"
    summary_path = run_root / "diagnostic_summary.json"
    if not state_path.is_file() or not summary_path.is_file():
        raise RuntimeError(f"diagnostic artifacts are incomplete: {run_root}")
    state = load(state_path)
    summary = load(summary_path)
    selection_sha = sha256(selection)
    immutable = (
        "accepted_update_applied",
        "incumbent_update_applied",
        "range_update_applied",
        "gradient_update_applied",
    )
    if not (
        state.get("status") == "complete"
        and state.get("diagnostic_only") is True
        and int(state.get("accepted_count", -1)) == 0
        and int(state.get("physical_call_count", -1)) == 5
        and int(state.get("valid_replay_count", -1)) == 5
        and int(state.get("technical_failure_count", -1)) == 0
        and state.get("baseline_restored") is True
        and state.get("accepted_update_allowed") is False
        and state.get("incumbent_update_allowed") is False
        and state.get("range_update_allowed") is False
        and state.get("gradient_update_allowed") is False
        and state.get("selection_sha256") == selection_sha
        and state.get("resume_state_sha256") == sha256(anchor_state)
        and int(summary.get("accepted_count", -1)) == 0
        and int(summary.get("physical_call_count", -1)) == 5
        and int(summary.get("valid_replay_count", -1)) == 5
        and int(summary.get("technical_failure_count", -1)) == 0
        and summary.get("baseline_restored") is True
        and summary.get("selection_sha256") == selection_sha
        and all(summary.get(field) is False for field in immutable)
    ):
        raise RuntimeError(f"diagnostic is not clean state-neutral 5/5/0: {run_root}")
    return {"state": state, "summary": summary}


def validate_top5(common: Any, path: Path, roles: list[str]) -> None:
    selection = load(path)
    common.validate_selection(selection, roles)
    candidates = selection.get("top5") or []
    identifiers = [
        str((item.get("three_level_fingerprint") or {}).get("id") or "")
        for item in candidates
    ]
    required = (
        "feature_ratio",
        "predicted_resource",
        "utilization",
        "dScore/dU",
        "T",
        "confidence",
        "g_j",
        "eta",
        "delta_param",
        "candidate_score",
    )
    if (
        len(candidates) != 5
        or len(set(identifiers)) != 5
        or any(not value.startswith("three-level-sha256:") for value in identifiers)
        or any(
            item.get("boundary_corner") is not False
            or any(field not in item for field in required)
            for item in candidates
        )
    ):
        raise RuntimeError(f"TPC-H v42 Top-5 contract mismatch: {path}")


def formal_forbidden() -> None:
    forbidden = [
        CYCLE / "entry_gate.json",
        CYCLE / "formal_package",
        CYCLE / "formal_run",
    ]
    if any(path.exists() for path in forbidden):
        raise RuntimeError("TPC-H v42 refuses a cycle that has entered formal")
    for path in V40_LOOP.glob("cycle_*/formal_run/formal_state.json"):
        if int(load(path).get("physical_call_count", 0)) != 0:
            raise RuntimeError("TPC-H v40 already contains a formal physical call")


def preflight(anchor_state: Path, anchor_sha: str) -> Dict[str, Any]:
    verify_dependencies()
    if not anchor_state.is_file() or sha256(anchor_state) != anchor_sha:
        raise RuntimeError("TPC-H v42 accepted0 anchor SHA mismatch")
    anchor = load(anchor_state)
    if not (
        anchor.get("status") == "complete"
        and int(anchor.get("accepted_count", -1)) == 0
        and anchor.get("baseline_restored") is True
        and int(anchor.get("technical_failure_count", -1)) == 0
        and V40_COUNTER.is_file()
        and V40_COUNTER.read_text(encoding="utf-8").strip() == "0"
    ):
        raise RuntimeError("TPC-H v42 accepted0 authority is not clean")
    if not PROBE.is_file() or not PROBE_SECOND.is_file() or PROBE.read_bytes() != PROBE_SECOND.read_bytes():
        raise RuntimeError("TPC-H v42 probe selection pair is missing or nondeterministic")
    probe_evidence = clean_diagnostic(PROBE, PROBE_RUN, anchor_state)
    formal_forbidden()
    controller_state = load(V40_LOOP / "controller_state.json")
    if not (
        int(controller_state.get("accepted_count", -1)) == 0
        and Path(str(controller_state.get("resume_state"))).resolve() == anchor_state
    ):
        raise RuntimeError("TPC-H v42 controller/anchor authority mismatch")
    if CONTINUATION_AUDIT.exists():
        audit = load(CONTINUATION_AUDIT)
        if audit.get("status") != "complete_robust_ready_for_v41":
            raise RuntimeError("TPC-H v42 existing continuation audit is not complete")
    return probe_evidence


def require_absent_or_clean(
    selection: Path,
    second: Path,
    run_root: Path,
    anchor_state: Path,
) -> bool:
    artifacts = (selection, second, run_root)
    if not any(path.exists() for path in artifacts):
        return False
    if not all(path.exists() for path in artifacts):
        raise RuntimeError(f"partial diagnostic phase cannot be retried: {run_root}")
    if selection.read_bytes() != second.read_bytes():
        raise RuntimeError(f"diagnostic selection pair changed: {selection}")
    clean_diagnostic(selection, run_root, anchor_state)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-anchor-state", type=Path, required=True)
    parser.add_argument("--current-anchor-state-sha256", required=True)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    anchor_state = args.current_anchor_state.resolve()
    preflight(anchor_state, args.current_anchor_state_sha256)
    if args.preflight_only:
        print("TPC-H v42 cycle004 diagnostic continuation preflight passed", flush=True)
        return 0

    v26 = import_module("tpch_v42_v26", V26)
    v26.ROOT = V40_ROOT
    v26.LOOP_ROOT = V40_LOOP
    v26.ACCEPTED_COUNTER = V40_COUNTER
    v26.ROBUST_BUILDER = ROBUST_BUILDER
    v26.EXPECTED["robust_builder"] = ROBUST_BUILDER_SHA256
    v26.EXACT_REPEAT_BUILDER = EXACT_BUILDER
    v26.EXPECTED["exact_repeat_builder"] = EXACT_BUILDER_SHA256
    common = v26.import_common()
    common.DIAGNOSTIC_RUNNER = DIAGNOSTIC_RUNNER
    common.EXPECTED["diagnostic_runner"] = EXPECTED[DIAGNOSTIC_RUNNER]
    environment = common.runtime_environment()
    config = CYCLE / "config.a.json"
    config_second = CYCLE / "config.b.json"
    if not config.is_file() or not config_second.is_file() or config.read_bytes() != config_second.read_bytes():
        raise RuntimeError("TPC-H v42 cycle004 dynamic config is incomplete")

    lock_path = CYCLE / "v42_diagnostic_continuation.lock"
    with lock_path.open("w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        preflight(anchor_state, args.current_anchor_state_sha256)
        probe_roles = [
            str(item.get("selection_role") or "")
            for item in (load(PROBE).get("top5") or [])
        ]
        if probe_roles not in (["probe"] * 5, ["trusted"] * 3 + ["probe"] * 2):
            raise RuntimeError(f"TPC-H v42 probe roles are invalid: {probe_roles}")
        validate_top5(common, PROBE, probe_roles)

        exact_complete = require_absent_or_clean(
            EXACT, EXACT_SECOND, EXACT_RUN, anchor_state
        )
        if not exact_complete:
            exact = v26.build_exact_repeat(
                common, CYCLE, PROBE, PROBE_RUN, 0, environment
            )
            if exact.resolve() != EXACT.resolve():
                raise RuntimeError("TPC-H v42 exact builder returned unexpected path")
            validate_top5(common, EXACT, ["trusted"] * 3 + ["probe"] * 2)
            v26.run_clean_diagnostic(
                common,
                EXACT,
                EXACT_RUN,
                config,
                anchor_state,
                0,
                CYCLE / "diagnostic_exact_repeat.log",
                environment,
            )
            clean_diagnostic(EXACT, EXACT_RUN, anchor_state)

        if V40_COUNTER.read_text(encoding="utf-8").strip() != "0" or sha256(anchor_state) != args.current_anchor_state_sha256:
            raise RuntimeError("TPC-H v42 exact repeat mutated accepted/anchor state")
        formal_forbidden()

        robust_complete = require_absent_or_clean(
            ROBUST, ROBUST_SECOND, ROBUST_RUN, anchor_state
        )
        if not robust_complete:
            robust = v26.build_robust_repeat(
                common,
                CYCLE,
                PROBE,
                PROBE_RUN,
                EXACT,
                EXACT_RUN,
                0,
                environment,
            )
            if robust.resolve() != ROBUST.resolve():
                raise RuntimeError("TPC-H v42 robust builder returned unexpected path")
            validate_top5(common, ROBUST, ["trusted"] * 3 + ["probe"] * 2)
            v26.run_clean_diagnostic(
                common,
                ROBUST,
                ROBUST_RUN,
                config,
                anchor_state,
                0,
                CYCLE / "diagnostic_robust_repeat.log",
                environment,
            )
            clean_diagnostic(ROBUST, ROBUST_RUN, anchor_state)

        if V40_COUNTER.read_text(encoding="utf-8").strip() != "0" or sha256(anchor_state) != args.current_anchor_state_sha256:
            raise RuntimeError("TPC-H v42 robust repeat mutated accepted/anchor state")
        formal_forbidden()
        audit = {
            "contract": "tpch_v42_cycle004_diagnostics_only_continuation_v1",
            "status": "complete_robust_ready_for_v41",
            "accepted_count": 0,
            "accepted_target": 10,
            "source_probe_selection_sha256": sha256(PROBE),
            "source_probe_state_sha256": sha256(PROBE_RUN / "diagnostic_state.json"),
            "source_probe_summary_sha256": sha256(PROBE_RUN / "diagnostic_summary.json"),
            "exact_selection_sha256": sha256(EXACT),
            "exact_state_sha256": sha256(EXACT_RUN / "diagnostic_state.json"),
            "exact_summary_sha256": sha256(EXACT_RUN / "diagnostic_summary.json"),
            "robust_selection_sha256": sha256(ROBUST),
            "robust_state_sha256": sha256(ROBUST_RUN / "diagnostic_state.json"),
            "robust_summary_sha256": sha256(ROBUST_RUN / "diagnostic_summary.json"),
            "new_physical_calls": 10,
            "new_valid_replays": 10,
            "technical_failures": 0,
            "strict_serial": True,
            "formal_started": False,
            "formal_physical_calls": 0,
            "manual_candidate_injection": False,
            "anchor_state_sha256_before_after": args.current_anchor_state_sha256,
            "v41_handoff_artifact": str(ROBUST_RUN / "diagnostic_state.json"),
        }
        if CONTINUATION_AUDIT.exists() and load(CONTINUATION_AUDIT) != audit:
            raise RuntimeError("TPC-H v42 continuation audit changed")
        if not CONTINUATION_AUDIT.exists():
            atomic_json(CONTINUATION_AUDIT, audit)
        print(json.dumps(audit, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
