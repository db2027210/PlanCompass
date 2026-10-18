#!/usr/bin/env python3
"""Revalidate one TPC-H incumbent and migrate only its protocol evidence.

This tool is deliberately fail closed.  It performs exactly one physical
workload invocation, restores the PostgreSQL baseline parameters, and only
then replaces the incumbent resource/score in the pinned formal state.  The
accepted count, incumbent parameters, ranges, and gradient state are never
changed.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from types import ModuleType
from typing import Any, Dict, Mapping


def file_sha256(path: Path) -> str:
    """Return the SHA256 digest for a local file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    """Load a JSON object."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def json_bytes(value: Mapping[str, Any]) -> bytes:
    """Serialize JSON exactly like :func:`atomic_json`."""
    return (
        json.dumps(value, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    """Atomically write and fsync a JSON object."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def verify_file(path: Path, expected_sha256: str, label: str) -> None:
    """Verify one pinned input file."""
    if not path.is_file():
        raise FileNotFoundError(f"{label} is missing: {path}")
    observed = file_sha256(path)
    if observed != expected_sha256:
        raise RuntimeError(
            f"{label} SHA256 mismatch: {observed} != {expected_sha256}"
        )


def import_runner(path: Path) -> ModuleType:
    """Import the pinned diagnostic runner as a helper module."""
    spec = importlib.util.spec_from_file_location(
        "tpch_pinned_diagnostic_runner", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import runner module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def finite_resource(resource: Mapping[str, Any]) -> Dict[str, float]:
    """Validate and normalize a CPU/IO/memory resource dictionary."""
    normalized: Dict[str, float] = {}
    for name in ("cpu", "io", "memory"):
        value = float(resource[name])
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(f"invalid actual resource {name}={value!r}")
        normalized[name] = value
    return normalized


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--formal-state", required=True, type=Path)
    parser.add_argument("--formal-state-sha256", required=True)
    parser.add_argument("--runner-module", required=True, type=Path)
    parser.add_argument("--runner-module-sha256", required=True)
    parser.add_argument("--workload", required=True, type=Path)
    parser.add_argument("--workload-sha256", required=True)
    parser.add_argument("--workload-driver", required=True, type=Path)
    parser.add_argument("--workload-driver-sha256", required=True)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument(
        "--runtime-dependency",
        action="append",
        default=[],
        metavar="PATH::SHA256",
        help=(
            "Pinned workload-driver dependency. May be repeated; every file "
            "is verified before the physical workload slot is reserved."
        ),
    )
    parser.add_argument("--container", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    parser.add_argument("--expected-accepted-count", type=int, default=1)
    parser.add_argument("--armed", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.armed:
        raise RuntimeError("fail closed: --armed is required")
    if args.timeout_seconds < 600 or args.timeout_seconds > 1800:
        raise ValueError("timeout must be between 600 and 1800 seconds")
    if args.output.exists():
        raise FileExistsError(f"output already exists: {args.output}")

    verify_file(
        args.formal_state, args.formal_state_sha256, "formal state"
    )
    verify_file(
        args.runner_module, args.runner_module_sha256, "runner module"
    )
    verify_file(args.workload, args.workload_sha256, "workload wrapper")
    verify_file(
        args.workload_driver,
        args.workload_driver_sha256,
        "workload driver",
    )
    verify_file(args.config, args.config_sha256, "optimizer config")
    runtime_dependencies = []
    for value in args.runtime_dependency:
        try:
            raw_path, expected_sha256 = value.rsplit("::", 1)
        except ValueError as exc:
            raise ValueError(
                "runtime dependency must use PATH::SHA256"
            ) from exc
        path = Path(raw_path).resolve()
        if (
            len(expected_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in expected_sha256
            )
        ):
            raise ValueError(
                f"invalid runtime dependency SHA256: {expected_sha256}"
            )
        verify_file(path, expected_sha256, f"runtime dependency {path.name}")
        runtime_dependencies.append(
            {"path": str(path), "sha256": expected_sha256}
        )

    runner = import_runner(args.runner_module)
    config = load_json(args.config)
    capability = {
        name: float(config["capability"][name])
        for name in ("cpu", "io", "memory")
    }
    formal_before = load_json(args.formal_state)
    if int(formal_before.get("accepted_count", -1)) != (
        args.expected_accepted_count
    ):
        raise RuntimeError("formal accepted count does not match authorization")
    if formal_before.get("status") != "complete":
        raise RuntimeError("formal state is not complete")
    if formal_before.get("baseline_restored") is not True:
        raise RuntimeError("formal state does not attest baseline restoration")
    incumbent_params = copy.deepcopy(formal_before.get("incumbent_params"))
    range_before = copy.deepcopy(formal_before.get("range_state"))
    if not isinstance(incumbent_params, dict) or not incumbent_params:
        raise RuntimeError("formal state has no incumbent parameters")
    if not isinstance(range_before, dict) or not range_before:
        raise RuntimeError("formal state has no range state")

    args.output.mkdir(parents=True)
    lock_path = args.output.parent / ".tpch_protocol_migration.lock"
    result_path = args.output / "incumbent_revalidation_result.json"
    failure_path = args.output / "migration_failure_audit.json"
    prepared_path = args.output / "migration_commit_prepared.json"
    complete_path = args.output / "migration_commit_complete.json"
    run_root = args.output / "physical_call_01"
    state = {
        "schema_version": "tpch_sealed_v4_protocol_migration_state_v1",
        "status": "preflight_complete",
        "physical_call_count": 0,
        "valid_replay_count": 0,
        "technical_failure_count": 0,
        "accepted_count_before": args.expected_accepted_count,
        "accepted_count_after": args.expected_accepted_count,
        "accepted_update_applied": False,
        "range_update_applied": False,
        "gradient_update_applied": False,
        "qps_used_for_inner_scoring": False,
        "baseline_restored": False,
        "formal_state_sha256_before": args.formal_state_sha256,
        "runtime_dependencies": copy.deepcopy(runtime_dependencies),
    }
    atomic_json(args.output / "migration_state.json", state)

    params_applied = False
    baseline_restored = False
    result: Dict[str, Any] | None = None
    with lock_path.open("w", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            # Recheck the authority immediately before touching PostgreSQL.
            verify_file(
                args.formal_state,
                args.formal_state_sha256,
                "formal state before workload",
            )
            verification = runner.apply_params(
                args.container, args.database, incumbent_params
            )
            params_applied = True
            state["status"] = "physical_call_started"
            state["physical_call_count"] = 1
            state["parameter_verification"] = verification
            atomic_json(args.output / "migration_state.json", state)

            returncode, output = runner.run_workload(
                args.workload, run_root, args.timeout_seconds
            )
            payload = runner.extract_payload(output)
            resource, utilization, score = runner.classify_payload(
                payload, returncode, capability
            )
            normalized_resource = finite_resource(resource)
            if not math.isfinite(float(score)):
                raise ValueError("actual score is not finite")
            result = {
                "schema_version": (
                    "tpch_sealed_v4_incumbent_revalidation_result_v1"
                ),
                "technical_failure": False,
                "valid_replay": True,
                "physical_call_count": 1,
                "candidate_params": incumbent_params,
                "actual_resource": normalized_resource,
                "actual_utilization": {
                    name: float(utilization[name])
                    for name in ("cpu", "io", "memory")
                },
                "actual_score": float(score),
                "accepted_count_before": args.expected_accepted_count,
                "accepted_count_after": args.expected_accepted_count,
                "accepted_update_applied": False,
                "range_update_applied": False,
                "gradient_update_applied": False,
                "qps_used_for_inner_scoring": False,
                "workload_wrapper_path": str(args.workload),
                "workload_wrapper_sha256": args.workload_sha256,
                "workload_driver_path": str(args.workload_driver),
                "workload_driver_sha256": args.workload_driver_sha256,
                "runner_module_path": str(args.runner_module),
                "runner_module_sha256": args.runner_module_sha256,
                "runtime_dependencies": copy.deepcopy(
                    runtime_dependencies
                ),
            }
            atomic_json(result_path, result)
            state["status"] = "valid_replay_waiting_for_baseline_restore"
            state["valid_replay_count"] = 1
            atomic_json(args.output / "migration_state.json", state)
        except Exception as exc:
            state["status"] = "technical_failure"
            state["technical_failure_count"] = (
                1 if state["physical_call_count"] == 1 else 0
            )
            state["error"] = f"{type(exc).__name__}: {exc}"
            atomic_json(args.output / "migration_state.json", state)
        finally:
            if params_applied:
                try:
                    baseline_verification = runner.apply_params(
                        args.container, args.database, runner.BASELINE_PARAMS
                    )
                    baseline_restored = True
                    state["baseline_verification"] = baseline_verification
                except Exception as exc:
                    state["baseline_restore_error"] = (
                        f"{type(exc).__name__}: {exc}"
                    )
            state["baseline_restored"] = baseline_restored
            atomic_json(args.output / "migration_state.json", state)

        if result is None or not baseline_restored:
            state["status"] = "failed_closed_no_formal_state_update"
            state["accepted_count_after"] = args.expected_accepted_count
            atomic_json(args.output / "migration_state.json", state)
            atomic_json(
                failure_path,
                {
                    **state,
                    "formal_state_sha256_after": file_sha256(
                        args.formal_state
                    ),
                    "formal_state_mutated": False,
                },
            )
            return 2

        # Recheck the authority after baseline restoration and immediately
        # before creating the backup and atomic migration.
        verify_file(
            args.formal_state,
            args.formal_state_sha256,
            "formal state before migration",
        )
        current = load_json(args.formal_state)
        if current != formal_before:
            raise RuntimeError("formal state changed despite identical SHA")

        timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backup = args.formal_state.with_name(
            f"{args.formal_state.name}.pre_sealed_v4_migration_{timestamp}.bak"
        )
        if backup.exists():
            raise FileExistsError(f"backup already exists: {backup}")
        shutil.copy2(args.formal_state, backup)
        if file_sha256(backup) != args.formal_state_sha256:
            raise RuntimeError("formal state backup SHA256 mismatch")

        result_sha256 = file_sha256(result_path)
        migrated = copy.deepcopy(formal_before)
        migrated["incumbent_resource"] = copy.deepcopy(
            result["actual_resource"]
        )
        migrated["incumbent_score"] = float(result["actual_score"])
        migrated["workload_protocol_migration"] = {
            "schema_version": "tpch_workload_protocol_migration_v1",
            "from_protocol": "mutable_unpinned_workload_driver",
            "to_protocol": "sealed_v4_completiongate",
            "physical_call_count": 1,
            "valid_replay_count": 1,
            "technical_failure_count": 0,
            "baseline_restored": True,
            "accepted_count_preserved": args.expected_accepted_count,
            "accepted_update_applied": False,
            "range_update_applied": False,
            "gradient_update_applied": False,
            "qps_used_for_inner_scoring": False,
            "revalidation_result_path": str(result_path),
            "revalidation_result_sha256": result_sha256,
            "workload_wrapper_sha256": args.workload_sha256,
            "workload_driver_sha256": args.workload_driver_sha256,
            "runner_module_sha256": args.runner_module_sha256,
            "runtime_dependencies": copy.deepcopy(runtime_dependencies),
            "formal_state_sha256_before": args.formal_state_sha256,
            "backup_path": str(backup),
            "backup_sha256": args.formal_state_sha256,
        }

        if migrated["accepted_count"] != formal_before["accepted_count"]:
            raise RuntimeError("accepted count would change")
        if migrated["incumbent_params"] != incumbent_params:
            raise RuntimeError("incumbent parameters would change")
        if migrated["range_state"] != range_before:
            raise RuntimeError("range state would change")

        expected_after_sha256 = hashlib.sha256(json_bytes(migrated)).hexdigest()
        atomic_json(
            prepared_path,
            {
                "schema_version": (
                    "tpch_workload_protocol_migration_prepared_v1"
                ),
                "formal_state_path": str(args.formal_state),
                "formal_state_sha256_before": args.formal_state_sha256,
                "formal_state_sha256_expected_after": expected_after_sha256,
                "backup_path": str(backup),
                "backup_sha256": args.formal_state_sha256,
                "accepted_count_before": formal_before["accepted_count"],
                "accepted_count_after": migrated["accepted_count"],
                "incumbent_params_unchanged": True,
                "range_state_unchanged": True,
                "baseline_restored": True,
                "result_path": str(result_path),
                "result_sha256": result_sha256,
            },
        )
        atomic_json(args.formal_state, migrated)
        observed_after_sha256 = file_sha256(args.formal_state)
        if observed_after_sha256 != expected_after_sha256:
            raise RuntimeError("atomic formal state migration SHA256 mismatch")

        state["status"] = "complete"
        state["formal_state_sha256_after"] = observed_after_sha256
        state["accepted_count_after"] = migrated["accepted_count"]
        state["baseline_restored"] = True
        atomic_json(args.output / "migration_state.json", state)
        atomic_json(
            complete_path,
            {
                "schema_version": (
                    "tpch_workload_protocol_migration_complete_v1"
                ),
                "formal_state_path": str(args.formal_state),
                "formal_state_sha256_before": args.formal_state_sha256,
                "formal_state_sha256_after": observed_after_sha256,
                "backup_path": str(backup),
                "backup_sha256": args.formal_state_sha256,
                "accepted_count_before": formal_before["accepted_count"],
                "accepted_count_after": migrated["accepted_count"],
                "incumbent_params_unchanged": True,
                "range_state_unchanged": True,
                "baseline_restored": True,
                "physical_call_count": 1,
                "valid_replay_count": 1,
                "technical_failure_count": 0,
                "result_path": str(result_path),
                "result_sha256": result_sha256,
            },
        )
    print(
        json.dumps(
            {
                "status": "complete",
                "formal_state_sha256_after": file_sha256(args.formal_state),
                "accepted_count": args.expected_accepted_count,
                "actual_score": result["actual_score"],
                "output": str(args.output),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
