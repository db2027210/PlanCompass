#!/usr/bin/env python3
"""Zero-call recovery of the TPC-H v51 cycle-6 runner-compatibility stop."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from typing import Any


CAMPAIGN = Path(
    "/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/"
    "pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/"
    "experience_sgd/attempt48_global_replay_cap5_20260726/"
    "tpch_final_algorithm_fresh0_v41_robust4_handoff_20260803"
)
LOOP = CAMPAIGN / "target10_loop_v41"
STATE = LOOP / "controller_state.json"
COUNTER = CAMPAIGN / "formal_search_accepted_counter.txt"
REMOTE_COMPATIBILITY = Path(
    "/tmp/run_tpch_round29_diagnostic_exact_plan_regime_batch_"
    "v52_safe_unknown.py"
)
LOCAL_COMPATIBILITY = Path(__file__).with_name(REMOTE_COMPATIBILITY.name)
EXPECTED_COMPATIBILITY_SHA256 = (
    "afa8d50a0ec592102a1d191da9e6ebe223fff02284d63c7782b0dffcd4935495"
)
CONTRACT = "tpch_v52_cycle6_preworkload_runner_compatibility_recovery_v1"
FAILURE_PHRASE = "selection contract mismatch"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"TPC-H v52 expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def import_compatibility() -> Any:
    path = (
        REMOTE_COMPATIBILITY
        if REMOTE_COMPATIBILITY.is_file()
        else LOCAL_COMPATIBILITY
    )
    if not path.is_file() or sha256(path) != EXPECTED_COMPATIBILITY_SHA256:
        raise RuntimeError("TPC-H v52 compatibility runner SHA mismatch")
    specification = importlib.util.spec_from_file_location(
        "tpch_v52_recovery_compatibility", path
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot import compatibility shim: {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def validate_boundary(args: argparse.Namespace) -> dict[str, Any]:
    if not STATE.is_file() or sha256(STATE) != args.expected_controller_state_sha256:
        raise RuntimeError("TPC-H v52 controller-state SHA mismatch")
    state = load(STATE)
    formal = Path(str(state.get("resume_state") or ""))
    if not (
        state.get("schema_version") == "tpch_fresh10_target10_loop_v26"
        and state.get("status") == "preparing_cycle"
        and int(state.get("accepted_count", -1)) == 1
        and int(state.get("accepted_target", -1)) == 10
        and int(state.get("next_cycle", -1)) == args.expected_cycle
        and formal.is_file()
        and sha256(formal) == args.expected_formal_state_sha256
        and COUNTER.is_file()
        and sha256(COUNTER) == args.expected_counter_sha256
        and COUNTER.read_text(encoding="utf-8").strip() == "1"
    ):
        raise RuntimeError("TPC-H v52 formal boundary changed")

    cycle_root = LOOP / f"cycle_{args.expected_cycle:03d}"
    first = cycle_root / "probe_top5.a.json"
    second = cycle_root / "probe_top5.b.json"
    failed_output = cycle_root / "diagnostic_probe"
    failure_log = cycle_root / "diagnostic_probe.log"
    if not (
        first.is_file()
        and second.is_file()
        and first.read_bytes() == second.read_bytes()
        and sha256(first) == args.expected_probe_sha256
        and failed_output.is_dir()
        and not any(failed_output.iterdir())
        and failure_log.is_file()
        and sha256(failure_log) == args.expected_failure_log_sha256
        and FAILURE_PHRASE in failure_log.read_text(encoding="utf-8")
        and not (cycle_root / "diagnostic_summary.json").exists()
        and not (cycle_root / "formal_package").exists()
        and not (cycle_root / "formal_run").exists()
    ):
        raise RuntimeError("TPC-H v52 is not at the exact zero-call runner stop")

    forbidden = (
        "physical_call_count",
        "workload invocation",
        "tpch_diagnostic_exact_plan_regime_batch_start",
    )
    failure_text = failure_log.read_text(encoding="utf-8").lower()
    if any(value.lower() in failure_text for value in forbidden):
        raise RuntimeError("TPC-H v52 failure log crossed the workload boundary")

    selection = load(first)
    compatibility = import_compatibility()
    compatibility.validate_safe_unknown_selection(selection)
    return {
        "contract": CONTRACT,
        "status": "ready_for_zero_call_recovery",
        "cycle": args.expected_cycle,
        "accepted_count": 1,
        "controller_state_sha256": args.expected_controller_state_sha256,
        "formal_state": str(formal.resolve()),
        "formal_state_sha256": args.expected_formal_state_sha256,
        "accepted_counter_sha256": args.expected_counter_sha256,
        "probe_selection": str(first.resolve()),
        "probe_selection_sha256": args.expected_probe_sha256,
        "failure_log": str(failure_log.resolve()),
        "failure_log_sha256": args.expected_failure_log_sha256,
        "failed_output": str(failed_output.resolve()),
        "probe_pair_reused": True,
        "explain_rebuilt": False,
        "workload_calls_added": 0,
        "formal_state_mutated": False,
        "accepted_counter_mutated": False,
    }


def apply_recovery(args: argparse.Namespace, boundary: dict[str, Any]) -> dict[str, Any]:
    cycle_root = LOOP / f"cycle_{args.expected_cycle:03d}"
    failed_output = cycle_root / "diagnostic_probe"
    failure_log = cycle_root / "diagnostic_probe.log"
    quarantine = LOOP / f"v52_cycle_{args.expected_cycle:03d}_preworkload_quarantine"
    if quarantine.exists():
        raise RuntimeError(f"TPC-H v52 quarantine already exists: {quarantine}")
    quarantine.mkdir(mode=0o700)
    os.replace(failure_log, quarantine / failure_log.name)
    os.replace(failed_output, quarantine / "diagnostic_probe.empty")

    audit = {
        **boundary,
        "status": "zero_call_recovery_applied",
        "quarantine": str(quarantine.resolve()),
        "failure_log_quarantined": True,
        "empty_output_quarantined": True,
        "selection_rebuilt": False,
        "selection_bytes_changed": False,
    }
    atomic_json(quarantine / "recovery_audit.json", audit)
    if not (
        sha256(STATE) == args.expected_controller_state_sha256
        and sha256(Path(boundary["formal_state"]))
        == args.expected_formal_state_sha256
        and sha256(COUNTER) == args.expected_counter_sha256
        and sha256(Path(boundary["probe_selection"])) == args.expected_probe_sha256
        and not failure_log.exists()
        and not failed_output.exists()
    ):
        raise RuntimeError("TPC-H v52 recovery changed a sealed authority")
    return audit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preflight", "apply"))
    parser.add_argument("--expected-controller-state-sha256", required=True)
    parser.add_argument("--expected-formal-state-sha256", required=True)
    parser.add_argument("--expected-counter-sha256", required=True)
    parser.add_argument("--expected-cycle", type=int, required=True)
    parser.add_argument("--expected-probe-sha256", required=True)
    parser.add_argument("--expected-failure-log-sha256", required=True)
    args = parser.parse_args()
    for value in (
        args.expected_controller_state_sha256,
        args.expected_formal_state_sha256,
        args.expected_counter_sha256,
        args.expected_probe_sha256,
        args.expected_failure_log_sha256,
    ):
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise RuntimeError("TPC-H v52 expected SHA256 must be lowercase hexadecimal")
    if args.expected_cycle <= 0:
        raise RuntimeError("TPC-H v52 expected cycle must be positive")
    return args


def main() -> int:
    args = parse_args()
    lock_path = LOOP / "controller.lock"
    if not lock_path.parent.is_dir():
        raise RuntimeError("TPC-H v52 controller-lock parent is missing")
    with lock_path.open("w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        boundary = validate_boundary(args)
        result = (
            boundary
            if args.mode == "preflight"
            else apply_recovery(args, boundary)
        )
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
