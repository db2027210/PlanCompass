#!/usr/bin/env python3
"""Restore PostgreSQL baseline and quarantine an interrupted TPC-H batch."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Mapping


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=str(path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def import_runner(path: Path) -> Any:
    specification = importlib.util.spec_from_file_location(
        "tpch_interrupted_batch_runner", path
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot import TPC-H runner: {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def parse_verification(raw: str) -> Dict[str, float]:
    result: Dict[str, float] = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        name, separator, value = line.partition("=")
        if not separator:
            raise RuntimeError(f"invalid PostgreSQL verification line: {line}")
        result[name] = float(value)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--loop-root", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--expected-runner-sha256", required=True)
    parser.add_argument("--expected-state-sha256", required=True)
    parser.add_argument("--expected-selection-sha256", required=True)
    parser.add_argument("--expected-diagnostic-state-sha256", required=True)
    parser.add_argument("--expected-events-sha256", required=True)
    parser.add_argument("--expected-interrupted-pid", type=int, required=True)
    parser.add_argument("--cycle", type=int, required=True)
    parser.add_argument("--quarantine-name", required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--database", required=True)
    args = parser.parse_args()

    loop_root = args.loop_root.resolve()
    state_path = loop_root / "controller_state.json"
    cycle_root = loop_root / f"cycle_{args.cycle:03d}"
    diagnostic_root = cycle_root / "diagnostic_probe"
    selection_a = cycle_root / "probe_top5.a.json"
    selection_b = cycle_root / "probe_top5.b.json"
    diagnostic_state_path = diagnostic_root / "diagnostic_state.json"
    events_path = diagnostic_root / "events.jsonl"
    summary_path = diagnostic_root / "diagnostic_summary.json"
    quarantine_root = loop_root / args.quarantine_name

    if Path(f"/proc/{args.expected_interrupted_pid}").exists():
        raise RuntimeError("interrupted diagnostic PID is still alive")
    if quarantine_root.exists():
        raise RuntimeError(f"quarantine already exists: {quarantine_root}")
    if not cycle_root.is_dir() or summary_path.exists():
        raise RuntimeError("cycle is missing or already has a completed summary")
    expected_files = (
        (state_path, args.expected_state_sha256),
        (args.runner, args.expected_runner_sha256),
        (selection_a, args.expected_selection_sha256),
        (selection_b, args.expected_selection_sha256),
        (diagnostic_state_path, args.expected_diagnostic_state_sha256),
        (events_path, args.expected_events_sha256),
    )
    for path, expected in expected_files:
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"SHA mismatch: {path}")

    controller = load(state_path)
    diagnostic = load(diagnostic_state_path)
    if not (
        controller.get("status") == "preparing_cycle"
        and int(controller.get("next_cycle", -1)) == args.cycle
        and int(controller.get("accepted_count", -1)) == 0
        and int((controller.get("cycles") or [])[-1].get("cycle", -1))
        == args.cycle - 1
        and diagnostic.get("status") == "running"
        and diagnostic.get("diagnostic_only") is True
        and diagnostic.get("formal_state_mutation_allowed") is False
        and diagnostic.get("accepted_update_allowed") is False
        and diagnostic.get("incumbent_update_allowed") is False
        and diagnostic.get("range_update_allowed") is False
        and diagnostic.get("gradient_update_allowed") is False
        and int(diagnostic.get("physical_call_count", -1)) == 3
        and int(diagnostic.get("valid_replay_count", -1)) == 2
        and int(diagnostic.get("technical_failure_count", -1)) == 0
        and int(diagnostic.get("current_rank", -1)) == 3
        and int(diagnostic.get("pid", -1)) == args.expected_interrupted_pid
        and int(diagnostic.get("accepted_count", -1)) == 0
    ):
        raise RuntimeError("observed partial-cycle state does not match contract")

    event_lines = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    completed_ranks = {
        int(item["rank"])
        for item in event_lines
        if item.get("event") == "tpch_diagnostic_real_replay_complete"
    }
    started_ranks = {
        int(item["rank"])
        for item in event_lines
        if item.get("event") == "tpch_diagnostic_real_replay_started"
    }
    if completed_ranks != {1, 2} or started_ranks != {1, 2, 3}:
        raise RuntimeError("event ledger does not prove interrupted rank 3")

    state_before = sha256(state_path)
    backup = loop_root / (
        f"controller_state.pre_partial_cycle{args.cycle:03d}_quarantine.json"
    )
    if backup.exists():
        raise RuntimeError(f"controller-state backup already exists: {backup}")
    shutil.copy2(state_path, backup)
    if sha256(backup) != state_before:
        raise RuntimeError("controller-state backup SHA mismatch")

    runner = import_runner(args.runner)
    baseline_params = dict(runner.BASELINE_PARAMS)
    verification_raw = runner.apply_params(
        args.container, args.database, baseline_params
    )
    verified = parse_verification(verification_raw)
    if set(verified) != set(baseline_params):
        raise RuntimeError("PostgreSQL baseline verification is incomplete")
    for name, expected in baseline_params.items():
        if abs(float(verified[name]) - float(expected)) > 1e-12:
            raise RuntimeError(
                f"PostgreSQL baseline mismatch: {name}={verified[name]}"
            )

    os.replace(cycle_root, quarantine_root)
    state_after = sha256(state_path)
    if state_after != state_before:
        raise RuntimeError("controller state changed during partial quarantine")

    audit = {
        "contract": "tpch_interrupted_diagnostic_boundary_recovery_v1",
        "status": "quarantined",
        "cycle": args.cycle,
        "source_cycle_root": str(cycle_root),
        "quarantine_root": str(quarantine_root),
        "controller_state_backup": str(backup),
        "controller_state_sha256_before": state_before,
        "controller_state_sha256_after": state_after,
        "controller_state_mutated": False,
        "selection_sha256": args.expected_selection_sha256,
        "diagnostic_state_sha256": args.expected_diagnostic_state_sha256,
        "events_sha256": args.expected_events_sha256,
        "physical_workload_calls": 3,
        "valid_replays": 2,
        "technical_failures": 1,
        "interrupted_rank": 3,
        "technical_failure_reason": "started_without_complete_after_controller_handoff",
        "remaining_ranks_executed": False,
        "accepted_count": 0,
        "accepted_update_applied": False,
        "incumbent_update_applied": False,
        "range_update_applied": False,
        "gradient_update_applied": False,
        "baseline_restored": True,
        "baseline_params": baseline_params,
        "baseline_verification": verified,
        "qps_used_for_inner_scoring": False,
        "new_workload_calls_by_recovery": 0,
        "recovery_epoch": time.time(),
    }
    audit_path = quarantine_root / "interruption_recovery_audit.json"
    atomic_json(audit_path, audit)
    pointer = {
        "contract": audit["contract"],
        "audit_path": str(audit_path),
        "audit_sha256": sha256(audit_path),
        "physical_workload_calls": 3,
        "valid_replays": 2,
        "technical_failures": 1,
        "baseline_restored": True,
        "controller_state_sha256": state_after,
    }
    atomic_json(
        loop_root / f"cycle_{args.cycle:03d}_interruption_recovery.json",
        pointer,
    )
    print(json.dumps(pointer, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
