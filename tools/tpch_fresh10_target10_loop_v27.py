#!/usr/bin/env python3
"""Resume TPC-H target10 and re-explore on insufficient stable regimes.

This controller is a fail-closed successor to v26.  It does not relax the
resource-stability thresholds.  When a clean probe plus clean exact repeat
contains fewer than three stable exact-plan regimes, it records a zero-call
builder failure and starts the next autonomous acquisition cycle.
"""

from __future__ import annotations

import copy
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import time
from typing import Any, Dict, Mapping


V26_PATH = Path("/tmp/tpch_fresh10_target10_loop_v26.py")
V26_SHA256 = "73cfcdd2eb60d199c5b2650eb51f2b19bd4b4d845b7f6fcd9714ef2c48757976"
RECOVERY_STATE_SHA256 = (
    "2cdb1e999ac42aba5678ee4af5c654e408d6ec27dabd3286a85691610ed689f5"
)
RECOVERY_ARTIFACT_SHA256 = {
    "probe_top5.a.json": "5ec20006b811716764eaa4031e37db416a953e62cf664fdc9a9299c851472661",
    "diagnostic_probe/diagnostic_state.json": (
        "9c3b8832e91d78aa06b15d5c2c0d03d1e5caa540b23a39d9eb99e8d0c8dcddfe"
    ),
    "exact_repeat_top5.a.json": (
        "18d68555399ac153c8826e44216ad46171bb068ff92da132f4b1810cbdcedbad"
    ),
    "diagnostic_exact_repeat/diagnostic_state.json": (
        "4a90bd6243b3a5aac32b0342d581b36d510a10927edfd9b08de6a08f74582184"
    ),
    "robust_builder_a.log": (
        "57dafcdbf5d99c323f2a9e956f0ce61c00bb844be875739662b0dc9145ab2b97"
    ),
}
INSUFFICIENT_STABLE_REGIMES = (
    "ValueError: fewer than three resource-stable exact-plan regimes"
)


def import_v26() -> Any:
    specification = importlib.util.spec_from_file_location("tpch_target10_v26", V26_PATH)
    if specification is None or specification.loader is None:
        raise RuntimeError("cannot load sealed TPC-H v26 controller")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def verify_dependencies(v26: Any) -> None:
    v26.verify(V26_PATH, V26_SHA256)
    for name, path in (
        ("common", v26.COMMON_PATH),
        ("successor_builder", v26.SUCCESSOR_BUILDER),
        ("exact_repeat_builder", v26.EXACT_REPEAT_BUILDER),
        ("robust_builder", v26.ROBUST_BUILDER),
        ("analyzer", v26.ANALYZER),
        ("expansion", v26.EXPANSION),
    ):
        v26.verify(path, v26.EXPECTED[name])


def clean_diagnostic(v26: Any, common: Any, root: Path, accepted: int) -> None:
    state_path = root / "diagnostic_state.json"
    state = v26.load(state_path)
    common.validate_clean_diagnostic(state, accepted)
    if not (
        state.get("status") == "complete"
        and int(state.get("physical_call_count", -1)) == v26.CAP
        and int(state.get("valid_replay_count", -1)) == v26.CAP
        and int(state.get("technical_failure_count", -1)) == 0
        and state.get("baseline_restored") is True
    ):
        raise RuntimeError(f"diagnostic is not clean5: {root}")


def insufficient_stable_failure(cycle_root: Path) -> bool:
    log = cycle_root / "robust_builder_a.log"
    if not log.is_file() or INSUFFICIENT_STABLE_REGIMES not in log.read_text(
        encoding="utf-8", errors="replace"
    ):
        return False
    forbidden = (
        cycle_root / "robust_top5.a.json",
        cycle_root / "robust_top5.b.json",
        cycle_root / "diagnostic_robust_repeat",
        cycle_root / "formal_package",
        cycle_root / "formal_run",
    )
    return not any(path.exists() for path in forbidden)


def reexploration_audit(
    v26: Any,
    cycle: int,
    accepted: int,
    cycle_root: Path,
    probe: Path,
    repeat: Path,
) -> Dict[str, Any]:
    return {
        "cycle": cycle,
        "status": "reexplore_insufficient_resource_stable_exact_plan_regimes",
        "accepted_before": accepted,
        "accepted_after": accepted,
        "probe_selection_sha256": v26.sha256(probe),
        "repeat_selection_sha256": v26.sha256(repeat),
        "probe_physical_calls": v26.CAP,
        "probe_valid_replays": v26.CAP,
        "repeat_physical_calls": v26.CAP,
        "repeat_valid_replays": v26.CAP,
        "technical_failures": 0,
        "builder_failure_workload_calls_added": 0,
        "robust_builder_log_sha256": v26.sha256(cycle_root / "robust_builder_a.log"),
        "formal_started": False,
        "thresholds_relaxed": False,
    }


def recover_cycle004(v26: Any, common: Any) -> tuple[Dict[str, Any], int, Path, Path, Path, int]:
    state_path = v26.LOOP_ROOT / "controller_state.json"
    if v26.sha256(state_path) != RECOVERY_STATE_SHA256:
        raise RuntimeError("TPC-H v27 recovery state SHA mismatch")
    cycle_root = v26.LOOP_ROOT / "cycle_004"
    for relative, expected in RECOVERY_ARTIFACT_SHA256.items():
        v26.verify(cycle_root / relative, expected)
    state = v26.load(state_path)
    accepted = v26.accepted_counter()
    expected_resume = v26.ROOT / "formal_batch002" / "formal_state.json"
    if not (
        state.get("schema_version") == "tpch_fresh10_target10_loop_v26"
        and state.get("status") == "preparing_cycle"
        and int(state.get("accepted_count", -1)) == 0
        and int(state.get("accepted_target", -1)) == v26.TARGET
        and int(state.get("historical_accepted_excluded", -1)) == 1
        and int(state.get("next_cycle", -1)) == 4
        and state.get("cycles") == []
        and Path(str(state.get("resume_state"))) == expected_resume
        and accepted == 0
    ):
        raise RuntimeError("TPC-H v27 recovery state does not match cycle004 boundary")
    clean_diagnostic(v26, common, cycle_root / "diagnostic_probe", accepted)
    clean_diagnostic(v26, common, cycle_root / "diagnostic_exact_repeat", accepted)
    if not insufficient_stable_failure(cycle_root):
        raise RuntimeError("TPC-H cycle004 is not the sealed stable-regime failure")

    backup = v26.LOOP_ROOT / "controller_state.pre_v27_cycle004.json"
    if backup.exists():
        raise RuntimeError("TPC-H v27 recovery backup already exists")
    atomic_json(backup, state)
    if v26.sha256(backup) != RECOVERY_STATE_SHA256:
        raise RuntimeError("TPC-H v27 recovery backup SHA mismatch")

    probe = cycle_root / "probe_top5.a.json"
    repeat = cycle_root / "exact_repeat_top5.a.json"
    recovered = copy.deepcopy(state)
    recovered["schema_version"] = "tpch_fresh10_target10_loop_v27"
    recovered["status"] = "reexploring_after_insufficient_stable_regimes"
    recovered["controller_version"] = 27
    recovered["cycles"].append(
        reexploration_audit(v26, 4, accepted, cycle_root, probe, repeat)
    )
    recovered["next_cycle"] = 5
    atomic_json(state_path, recovered)
    return (
        recovered,
        accepted,
        expected_resume,
        repeat,
        cycle_root / "diagnostic_exact_repeat",
        5,
    )


def main() -> int:
    v26 = import_v26()
    verify_dependencies(v26)
    common = v26.import_common()
    environment = common.runtime_environment()
    state_path = v26.LOOP_ROOT / "controller_state.json"
    lock_path = v26.LOOP_ROOT / "controller.lock"
    with lock_path.open("w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        state, accepted, resume_state, previous_selection, previous_run, start_cycle = (
            recover_cycle004(v26, common)
        )

        for cycle in range(start_cycle, start_cycle + v26.MAX_CYCLES):
            if accepted >= v26.TARGET:
                state["status"] = "target_reached"
                atomic_json(state_path, state)
                return 0
            cycle_root = v26.LOOP_ROOT / f"cycle_{cycle:03d}"
            cycle_root.mkdir(mode=0o700)
            state["status"] = "preparing_cycle"
            state["next_cycle"] = cycle
            atomic_json(state_path, state)
            config = v26.dynamic_config(common, cycle_root, accepted)
            probe = v26.build_probe(
                common,
                cycle_root,
                previous_selection,
                previous_run,
                resume_state,
                accepted,
                environment,
            )
            probe_run = cycle_root / "diagnostic_probe"
            v26.run_clean_diagnostic(
                common,
                probe,
                probe_run,
                config,
                resume_state,
                accepted,
                cycle_root / "diagnostic_probe.log",
                environment,
            )
            repeat = v26.build_exact_repeat(
                common, cycle_root, probe, probe_run, accepted, environment
            )
            repeat_run = cycle_root / "diagnostic_exact_repeat"
            v26.run_clean_diagnostic(
                common,
                repeat,
                repeat_run,
                config,
                resume_state,
                accepted,
                cycle_root / "diagnostic_exact_repeat.log",
                environment,
            )
            try:
                robust = v26.build_robust_repeat(
                    common,
                    cycle_root,
                    probe,
                    probe_run,
                    repeat,
                    repeat_run,
                    accepted,
                    environment,
                )
            except RuntimeError:
                if not insufficient_stable_failure(cycle_root):
                    raise
                cycle_audit = reexploration_audit(
                    v26, cycle, accepted, cycle_root, probe, repeat
                )
                state["cycles"].append(cycle_audit)
                state["status"] = cycle_audit["status"]
                state["next_cycle"] = cycle + 1
                previous_selection = repeat
                previous_run = repeat_run
                atomic_json(state_path, state)
                continue

            robust_run = cycle_root / "diagnostic_robust_repeat"
            v26.run_clean_diagnostic(
                common,
                robust,
                robust_run,
                config,
                resume_state,
                accepted,
                cycle_root / "diagnostic_robust_repeat.log",
                environment,
            )
            analysis_path = v26.analyze_gate(
                common, cycle_root, robust, robust_run, environment
            )
            analysis = v26.load(analysis_path)
            cycle_audit: Dict[str, Any] = {
                "cycle": cycle,
                "accepted_before": accepted,
                "probe_selection_sha256": v26.sha256(probe),
                "repeat_selection_sha256": v26.sha256(repeat),
                "robust_selection_sha256": v26.sha256(robust),
                "entry_gate_sha256": v26.sha256(analysis_path),
                "entry_gate_passed": analysis.get("passed") is True,
            }
            previous_selection = robust
            previous_run = robust_run
            if analysis.get("passed") is not True:
                cycle_audit["formal_started"] = False
                cycle_audit["accepted_after"] = accepted
                state["cycles"].append(cycle_audit)
                state["next_cycle"] = cycle + 1
                atomic_json(state_path, state)
                continue

            formal_selection, gate, formal_run = v26.promote_formal(
                common,
                cycle_root,
                robust,
                robust_run,
                analysis_path,
                accepted,
                config,
            )
            formal_error = None
            try:
                v26.run_formal(
                    common,
                    formal_selection,
                    gate,
                    formal_run,
                    config,
                    resume_state,
                    environment,
                )
            except RuntimeError as exc:
                formal_error = str(exc)
            accepted_after, formal_state, formal_clean = v26.validate_formal_completion(
                formal_run,
                accepted,
                allow_wait=False,
            )
            cycle_audit.update(
                {
                    "formal_started": True,
                    "formal_selection_sha256": v26.sha256(formal_selection),
                    "formal_gate_sha256": v26.sha256(gate),
                    "formal_clean": formal_clean,
                    "formal_error": formal_error,
                    "accepted_after": accepted_after,
                    "formal_state_sha256": v26.sha256(formal_state),
                }
            )
            state["cycles"].append(cycle_audit)
            if formal_clean:
                accepted = accepted_after
                resume_state = formal_state
                state["accepted_count"] = accepted
                state["resume_state"] = str(resume_state)
            elif accepted_after != accepted:
                raise RuntimeError("technical formal batch changed accepted count")
            state["next_cycle"] = cycle + 1
            atomic_json(state_path, state)

        state["status"] = "blocked_max_cycles"
        atomic_json(state_path, state)
        raise RuntimeError("TPC-H target10 loop exhausted maximum cycles")


if __name__ == "__main__":
    raise SystemExit(main())
