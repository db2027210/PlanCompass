#!/usr/bin/env python3
"""Fail-closed recovery of TPC-H v41 cycle004 after EXPLAIN-budget exit.

Recovery is deliberately split in two.  ``--apply-recovery-only`` verifies and
quarantines the zero-workload partial cycle, then stops.  ``--resume`` requires
the SHA of that recovered controller state before it can run the unchanged v41
diagnostic/formal state machine with the checkpointed expansion hook.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Any, Dict, Iterable, Mapping


V41_CONTROLLER = Path("/tmp/tpch_v41_preformal_handoff.py")
V32_CHECKPOINTED = Path(
    "/tmp/tpch_fresh10_target10_loop_v32_v7_checkpointed.py"
)
EXPANDER_CHECKPOINTED = Path(
    "/tmp/tpch_exact_regime_neighbor_expand_v6_checkpointed.py"
)
STABILITY_ACQUISITION_BUILDER = Path(
    "/tmp/build_tpch_autonomous_successor_top5_v10_stability_acquisition.py"
)
STABILITY_DIAGNOSTIC_RUNNER = Path(
    "/tmp/run_tpch_round29_diagnostic_exact_plan_regime_batch_v43_stability.py"
)
EXPECTED = {
    V41_CONTROLLER: (
        "bb81fde56677fc2c19b1399e708005e3b8e950169643c5fe777da94e47b36a41"
    ),
    V32_CHECKPOINTED: (
        "f5c735de117c129eed442cbbb7caecc07ba34fe6c68d23eb04a56ef654ffeb61"
    ),
    EXPANDER_CHECKPOINTED: (
        "8e16189de0f7677792dcd6c8b5e1ab3fc8015e5242497efa385957b89ae70b9c"
    ),
    STABILITY_ACQUISITION_BUILDER: (
        "da43bfb66ee99f60702685d4b39018ab1ea9d365d3210009c85b6afb6612fc9b"
    ),
    STABILITY_DIAGNOSTIC_RUNNER: (
        "6cf1b0bcf87611a0f18c4ffc65d8d6b246d0e29cd81935b7f42009b4df603074"
    ),
}
RECOVERY_CONTRACT = "tpch_v42_cycle004_zero_workload_recovery_v1"


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
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def import_module(name: str, path: Path) -> Any:
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot import {name}: {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def verified_v41() -> Any:
    for path, expected in EXPECTED.items():
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"v42 dependency SHA mismatch: {path}")
    v41 = import_module("tpch_v42_v41", V41_CONTROLLER)
    v41.V32 = V32_CHECKPOINTED
    v41.EXPANDER = EXPANDER_CHECKPOINTED
    v41.BUILDER = STABILITY_ACQUISITION_BUILDER
    v41.DIAGNOSTIC_RUNNER = STABILITY_DIAGNOSTIC_RUNNER
    v41.EXPECTED[EXPANDER_CHECKPOINTED] = EXPECTED[EXPANDER_CHECKPOINTED]
    v41.EXPECTED[STABILITY_ACQUISITION_BUILDER] = EXPECTED[
        STABILITY_ACQUISITION_BUILDER
    ]
    v41.EXPECTED[STABILITY_DIAGNOSTIC_RUNNER] = EXPECTED[
        STABILITY_DIAGNOSTIC_RUNNER
    ]
    return v41


def validate_formal_authority(path: Path, expected_sha: str) -> Dict[str, Any]:
    if not path.is_file() or sha256(path) != expected_sha:
        raise RuntimeError("formal authority SHA mismatch")
    state = load(path)
    if not (
        state.get("schema_version") == "tpch_exact_plan_regime_formal_state_v1"
        and state.get("status") == "complete"
        and state.get("baseline_restored") is True
        and int(state.get("accepted_count", -1)) == 1
        and int(state.get("physical_call_count", -1)) == 5
        and int(state.get("valid_replay_count", -1)) == 5
        and int(state.get("technical_failure_count", -1)) == 0
    ):
        raise RuntimeError("formal authority is not clean accepted1 5/5/0")
    return state


def current_paths(v41: Any) -> Dict[str, Path]:
    return {
        "state": v41.V41_LOOP / "controller_state.json",
        "lock": v41.V41_LOOP / "controller.lock",
        "cycle": v41.V41_LOOP / "cycle_004",
        "counter": v41.V41_COUNTER,
        "handoff": v41.V41_ROOT / "preformal_handoff_state.json",
        "reuse_audit": v41.V41_ROOT / "v40_diagnostic_reuse_audit.json",
        "recovery_audit": v41.V41_LOOP / "v42_cycle004_recovery_audit.json",
    }


def validate_counter(path: Path, expected_sha: str) -> None:
    if not (
        path.is_file()
        and sha256(path) == expected_sha
        and path.read_text(encoding="utf-8").strip() == "1"
    ):
        raise RuntimeError("accepted counter is not the sealed value 1")


def validate_failed_boundary(
    v41: Any,
    paths: Mapping[str, Path],
    expected_state_sha: str,
    expected_formal_sha: str,
    expected_counter_sha: str,
) -> Dict[str, Any]:
    state_path = paths["state"]
    if not state_path.is_file() or sha256(state_path) != expected_state_sha:
        raise RuntimeError("controller_state SHA mismatch")
    state = load(state_path)
    formal_state = Path(str(state.get("resume_state") or ""))
    if not (
        state.get("schema_version") == "tpch_fresh10_target10_loop_v26"
        and state.get("status") == "preparing_cycle"
        and int(state.get("accepted_count", -1)) == 1
        and int(state.get("accepted_target", -1)) == 10
        and int(state.get("next_cycle", -1)) == 4
        and state.get("cycles") == []
    ):
        raise RuntimeError("controller is not at the exact cycle004 boundary")
    validate_counter(paths["counter"], expected_counter_sha)
    validate_formal_authority(formal_state, expected_formal_sha)

    handoff = load(paths["handoff"])
    formal_run = Path(str(handoff.get("adopted_formal_run") or ""))
    if not (
        handoff.get("adopted_formal_started") is True
        and handoff.get("adopted_formal_clean") is True
        and int(handoff.get("accepted_count", -1)) == 1
        and formal_run / "formal_state.json" == formal_state
    ):
        raise RuntimeError("v41 handoff/formal authority binding mismatch")

    cycle = paths["cycle"]
    if not cycle.is_dir():
        raise RuntimeError("failed cycle004 directory is missing")
    allowed_files = {
        "config.a.json",
        "config.b.json",
        "dynamic_expansion_v32/expand_a.log",
    }
    observed_files = {
        str(path.relative_to(cycle)) for path in cycle.rglob("*") if path.is_file()
    }
    if observed_files != allowed_files:
        raise RuntimeError(
            "cycle004 is not the exact zero-workload budget-failure shape: "
            f"{sorted(observed_files)}"
        )
    if (cycle / "config.a.json").read_bytes() != (
        cycle / "config.b.json"
    ).read_bytes():
        raise RuntimeError("cycle004 deterministic configs differ")
    failure_log = cycle / "dynamic_expansion_v32/expand_a.log"
    if "candidate-estimation budget exhausted" not in failure_log.read_text(
        encoding="utf-8"
    ):
        raise RuntimeError("cycle004 does not contain the sealed budget failure")
    forbidden_directories = [
        path
        for path in cycle.rglob("*")
        if path.is_dir()
        and (
            path.name.startswith("attempt_")
            or path.name.startswith("diagnostic_")
            or path.name.startswith("formal_")
        )
    ]
    if forbidden_directories:
        raise RuntimeError("cycle004 contains replay/workload directories")
    return {
        "state": state,
        "controller_state_sha256": expected_state_sha,
        "formal_state": formal_state,
        "formal_run": formal_run,
        "observed_files": sorted(observed_files),
    }


def preflight_report(
    v41: Any,
    boundary: Mapping[str, Any],
    paths: Mapping[str, Path],
) -> Dict[str, Any]:
    return {
        "contract": RECOVERY_CONTRACT,
        "status": "audit_passed_ready_to_apply",
        "accepted_count_before": 1,
        "accepted_count_after": 1,
        "formal_state": str(boundary["formal_state"]),
        "formal_state_sha256": sha256(boundary["formal_state"]),
        "controller_state_sha256": boundary["controller_state_sha256"],
        "accepted_counter_sha256": sha256(paths["counter"]),
        "cycle": 4,
        "failed_cycle_files": boundary["observed_files"],
        "physical_workload_calls_in_failed_cycle": 0,
        "formal_state_mutated": False,
        "accepted_counter_mutated": False,
        "manual_candidate_selection": False,
        "next_algorithm": "checkpointed_bounded_expansion_v42",
    }


def apply_recovery(
    v41: Any,
    paths: Mapping[str, Path],
    boundary: Mapping[str, Any],
) -> Dict[str, Any]:
    if paths["recovery_audit"].exists():
        raise RuntimeError("v42 recovery audit already exists; refuse reapply")
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    state_backup = paths["state"].with_name(
        f"controller_state.pre_v42.{timestamp}.json"
    )
    quarantine = paths["cycle"].with_name(
        f"cycle_004.pre_v42_budget_exhausted.{timestamp}"
    )
    if state_backup.exists() or quarantine.exists():
        raise RuntimeError("v42 recovery backup target already exists")
    shutil.copy2(paths["state"], state_backup)
    os.replace(paths["cycle"], quarantine)
    recovered_state = dict(boundary["state"])
    recovered_state["status"] = "restart_ready_v42"
    recovered_state["v42_recovery"] = {
        "contract": RECOVERY_CONTRACT,
        "state_backup": str(state_backup),
        "state_backup_sha256": sha256(state_backup),
        "quarantined_cycle": str(quarantine),
        "quarantined_cycle_has_workload_calls": False,
        "formal_state_sha256": sha256(boundary["formal_state"]),
        "accepted_counter_sha256": sha256(paths["counter"]),
    }
    atomic_json(paths["state"], recovered_state)
    audit = {
        **preflight_report(v41, boundary, paths),
        "status": "recovery_applied_zero_call",
        "state_backup": str(state_backup),
        "state_backup_sha256": sha256(state_backup),
        "quarantined_cycle": str(quarantine),
        "controller_state_sha256_after": sha256(paths["state"]),
        "new_explain_calls": 0,
        "new_physical_workload_calls": 0,
    }
    atomic_json(paths["recovery_audit"], audit)
    audit["recovery_audit_sha256"] = sha256(paths["recovery_audit"])
    return audit


def executed_history(v41: Any, paths: Mapping[str, Path]) -> Iterable[Path]:
    audit = load(paths["reuse_audit"])
    for item in audit.get("v40_executed_selections_added_to_history") or []:
        path = Path(str(item.get("path") or ""))
        if not path.is_file() or sha256(path) != item.get("sha256"):
            raise RuntimeError(f"executed-history binding mismatch: {path}")
        yield path
    adopted = v41.ADOPTION_ROOT / "a/selection.json"
    if not adopted.is_file():
        raise RuntimeError("adopted v41 selection is missing")
    yield adopted


def run_loop(
    v41: Any,
    paths: Mapping[str, Path],
    state: Dict[str, Any],
    *,
    resume_existing_cycle004: bool = False,
) -> int:
    formal_state = Path(str(state["resume_state"]))
    handoff = load(paths["handoff"])
    formal_run = Path(str(handoff["adopted_formal_run"]))
    previous_selection = v41.ADOPTION_ROOT / "a/selection.json"
    previous_run = previous_selection.parent
    history = list(executed_history(v41, paths))
    v26, common = v41.configure_controller(
        formal_state,
        previous_selection,
        previous_run,
        history,
        formal_run,
    )
    environment = common.runtime_environment()
    accepted = 1
    resume_state = formal_state
    for cycle in range(4, 4 + v26.MAX_CYCLES):
        if accepted >= v26.TARGET:
            state["status"] = "target_reached"
            v26.atomic_json(paths["state"], state)
            return 0
        cycle_root = v26.LOOP_ROOT / f"cycle_{cycle:03d}"
        cycle_root.mkdir(
            mode=0o700,
            exist_ok=resume_existing_cycle004 and cycle == 4,
        )
        resume_existing_cycle004 = False
        state["status"] = "preparing_cycle"
        state["next_cycle"] = cycle
        v26.atomic_json(paths["state"], state)
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
            "v42_checkpointed_expansion": True,
        }
        previous_selection = robust
        previous_run = robust_run
        if analysis.get("passed") is not True:
            cycle_audit["formal_started"] = False
            cycle_audit["accepted_after"] = accepted
            state["cycles"].append(cycle_audit)
            state["next_cycle"] = cycle + 1
            v26.atomic_json(paths["state"], state)
            continue
        formal_selection, gate, next_formal_run = v26.promote_formal(
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
                next_formal_run,
                config,
                resume_state,
                environment,
            )
        except RuntimeError as exc:
            formal_error = str(exc)
        accepted_after, next_state, clean = v26.validate_formal_completion(
            next_formal_run,
            accepted,
            allow_wait=False,
        )
        cycle_audit.update(
            {
                "formal_started": True,
                "formal_selection_sha256": v26.sha256(formal_selection),
                "formal_gate_sha256": v26.sha256(gate),
                "formal_clean": clean,
                "formal_error": formal_error,
                "accepted_after": accepted_after,
                "formal_state_sha256": v26.sha256(next_state),
            }
        )
        state["cycles"].append(cycle_audit)
        if clean:
            accepted = accepted_after
            resume_state = next_state
            state["accepted_count"] = accepted
            state["resume_state"] = str(resume_state)
        elif accepted_after != accepted:
            raise RuntimeError("technical formal batch changed accepted count")
        state["next_cycle"] = cycle + 1
        v26.atomic_json(paths["state"], state)
    state["status"] = "blocked_max_cycles"
    v26.atomic_json(paths["state"], state)
    raise RuntimeError("TPC-H target10 loop exhausted maximum cycles")


def resume(
    v41: Any,
    paths: Mapping[str, Path],
    expected_state_sha: str,
    expected_formal_sha: str,
    expected_counter_sha: str,
) -> int:
    if not paths["state"].is_file() or sha256(paths["state"]) != expected_state_sha:
        raise RuntimeError("recovered controller_state SHA mismatch")
    state = load(paths["state"])
    recovery = state.get("v42_recovery") or {}
    if not (
        int(state.get("accepted_count", -1)) == 1
        and int(state.get("next_cycle", -1)) == 4
        and recovery.get("contract") == RECOVERY_CONTRACT
        and paths["recovery_audit"].is_file()
    ):
        raise RuntimeError("controller is not at the sealed v42 restart boundary")
    status = state.get("status")
    cycle_exists = paths["cycle"].exists()
    resume_existing_cycle004 = False
    if status == "restart_ready_v42" and not cycle_exists:
        pass
    elif status == "preparing_cycle" and cycle_exists:
        allowed_root_files = {
            "config.a.json",
            "config.b.json",
            "probe_top5.a.json",
            "probe_top5.b.json",
            "probe_builder_a.log",
            "probe_builder_b.log",
        }
        forbidden = []
        unexpected = []
        for path in paths["cycle"].rglob("*"):
            relative = str(path.relative_to(paths["cycle"]))
            if path.is_dir() and (
                path.name.startswith("attempt_")
                or path.name.startswith("diagnostic_")
                or path.name.startswith("formal_")
            ):
                forbidden.append(relative)
            if path.is_file() and not (
                relative in allowed_root_files
                or relative.startswith("dynamic_expansion_v32/")
            ):
                unexpected.append(relative)
        if forbidden or unexpected:
            raise RuntimeError(
                "cycle004 restart is not a zero-workload preworkload boundary: "
                f"forbidden={sorted(forbidden)} unexpected={sorted(unexpected)}"
            )
        config_a = paths["cycle"] / "config.a.json"
        config_b = paths["cycle"] / "config.b.json"
        if not (
            config_a.is_file()
            and config_b.is_file()
            and config_a.read_bytes() == config_b.read_bytes()
        ):
            raise RuntimeError("resumable cycle004 lacks deterministic configs")
        resume_existing_cycle004 = True
    else:
        raise RuntimeError(
            "controller status/cycle shape is not resumable: "
            f"status={status} cycle_exists={cycle_exists}"
        )
    formal_state = Path(str(state.get("resume_state") or ""))
    validate_counter(paths["counter"], expected_counter_sha)
    validate_formal_authority(formal_state, expected_formal_sha)
    if recovery.get("formal_state_sha256") != expected_formal_sha:
        raise RuntimeError("recovery/formal SHA binding mismatch")
    return run_loop(
        v41,
        paths,
        state,
        resume_existing_cycle004=resume_existing_cycle004,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--apply-recovery-only", action="store_true")
    mode.add_argument("--resume", action="store_true")
    parser.add_argument("--expected-controller-state-sha256", required=True)
    parser.add_argument("--expected-formal-state-sha256", required=True)
    parser.add_argument("--expected-counter-sha256", required=True)
    args = parser.parse_args()

    v41 = verified_v41()
    paths = current_paths(v41)
    paths["lock"].parent.mkdir(parents=True, exist_ok=True)
    with paths["lock"].open("w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.resume:
            return resume(
                v41,
                paths,
                args.expected_controller_state_sha256,
                args.expected_formal_state_sha256,
                args.expected_counter_sha256,
            )
        boundary = validate_failed_boundary(
            v41,
            paths,
            args.expected_controller_state_sha256,
            args.expected_formal_state_sha256,
            args.expected_counter_sha256,
        )
        if args.preflight_only:
            print(json.dumps(preflight_report(v41, boundary, paths), sort_keys=True))
            return 0
        result = apply_recovery(v41, paths, boundary)
        print(json.dumps(result, sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
