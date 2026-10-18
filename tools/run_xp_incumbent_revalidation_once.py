#!/usr/bin/env python3
"""Run exactly one state-neutral XP incumbent protocol revalidation."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shlex
import sys
import tempfile
import time
from typing import Any, Dict, Mapping


REQUIRED_CPUSET = "32-51,288-307"
MYSQL_RESERVED_CPUSET = "184-199,440-455"
ALLOWED_PARAMS = {
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
}
REQUIRED_CANDIDATE_FIELDS = {
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
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
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


def write_event(path: Path, event: Mapping[str, Any]) -> None:
    row = dict(event)
    row.setdefault("timestamp_epoch", time.time())
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def parse_cpuset(value: str) -> set[int]:
    result: set[int] = set()
    for part in value.split(","):
        if "-" in part:
            left, right = map(int, part.split("-", 1))
            result.update(range(left, right + 1))
        else:
            result.add(int(part))
    return result


def enforce_cpuset() -> None:
    required = parse_cpuset(REQUIRED_CPUSET)
    reserved = parse_cpuset(MYSQL_RESERVED_CPUSET)
    if required & reserved:
        raise RuntimeError("XP and MySQL CPU sets overlap")
    os.sched_setaffinity(0, required)
    if os.sched_getaffinity(0) != required:
        raise RuntimeError("XP process affinity verification failed")


def load_module(path: Path) -> Any:
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("xp_revalidation_driver", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load legacy replay driver: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def same_params(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return bool(
        set(left) == set(right) == ALLOWED_PARAMS
        and all(
            math.isclose(
                float(left[name]),
                float(right[name]),
                rel_tol=1e-10,
                abs_tol=1e-12,
            )
            for name in ALLOWED_PARAMS
        )
    )


def pg_setting_precision_match(
    actual: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> bool:
    return bool(
        set(actual) == set(expected) == ALLOWED_PARAMS
        and all(
            math.isclose(
                float(actual[name]),
                float(expected[name]),
                rel_tol=1e-5,
                abs_tol=1e-8,
            )
            for name in ALLOWED_PARAMS
        )
    )


def diagnostic_workload_command(command: str) -> str:
    words = shlex.split(command)
    guard = next(
        (
            index
            for index, word in enumerate(words)
            if word.endswith("xp_candidate_exclusion_guard.py")
        ),
        None,
    )
    if guard is None:
        return command
    inner = words[guard + 1 :]
    if not inner:
        raise RuntimeError("formal exclusion guard has no workload command")
    return shlex.join(inner)


def validate_inputs(
    envelope: Mapping[str, Any],
    accepted: Mapping[str, Any],
    formal: Mapping[str, Any],
    config: Mapping[str, Any],
) -> Dict[str, Any]:
    candidate = envelope.get("candidate")
    accepted_params = (accepted.get("accepted_event") or {}).get("params")
    if (
        envelope.get("contract")
        != "xp_incumbent_protocol_revalidation_candidate_v1"
        or envelope.get("diagnostic_only") is not True
        or int(envelope.get("maximum_physical_workload_invocations", -1)) != 1
        or any(
            envelope.get(field) is not False
            for field in (
                "accepted_update_applied",
                "incumbent_update_applied",
                "range_update_applied",
                "gradient_update_applied",
            )
        )
        or envelope.get("qps_used_for_inner_scoring") is not False
        or not isinstance(candidate, dict)
        or not isinstance(accepted_params, dict)
    ):
        raise RuntimeError("XP incumbent revalidation envelope mismatch")
    if (
        candidate.get("selection_role") != "incumbent_protocol_revalidation"
        or candidate.get("diagnostic_only") is not True
        or candidate.get("boundary_corner") is not False
        or candidate.get("qps_used_for_inner_scoring") is not False
        or any(candidate.get(field) is None for field in REQUIRED_CANDIDATE_FIELDS)
        or not same_params(candidate.get("candidate_params") or {}, accepted_params)
        or not same_params(candidate.get("candidate_params") or {}, formal["incumbent_params"])
        or not math.isclose(
            float(accepted["formal_incumbent_score"]),
            float(formal["incumbent_score"]),
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    ):
        raise RuntimeError("candidate is not the authoritative XP incumbent")
    fingerprint = candidate.get("three_level_fingerprint") or {}
    if (
        fingerprint.get("valid") is not True
        or fingerprint.get("parameter_fallback_used") is not False
        or candidate.get("workload_plan_fingerprint") != fingerprint.get("plan_level")
    ):
        raise RuntimeError("candidate three-level fingerprint mismatch")
    physical = config.get("_physical_resource_contract") or {}
    if (
        physical.get("cpuset_cpus") != REQUIRED_CPUSET
        or set((config.get("initial_params") or {})) != ALLOWED_PARAMS
    ):
        raise RuntimeError("XP physical/baseline contract mismatch")
    return dict(candidate)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--candidate-sha256", required=True)
    parser.add_argument("--accepted-incumbent", required=True, type=Path)
    parser.add_argument("--accepted-incumbent-sha256", required=True)
    parser.add_argument("--formal-state", required=True, type=Path)
    parser.add_argument("--formal-state-sha256", required=True)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--legacy-driver", required=True, type=Path)
    parser.add_argument("--legacy-driver-sha256", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()

    paths_and_hashes = (
        (args.candidate.resolve(), args.candidate_sha256, "candidate"),
        (
            args.accepted_incumbent.resolve(),
            args.accepted_incumbent_sha256,
            "accepted incumbent",
        ),
        (args.formal_state.resolve(), args.formal_state_sha256, "formal state"),
        (args.config.resolve(), args.config_sha256, "config"),
        (
            args.legacy_driver.resolve(),
            args.legacy_driver_sha256,
            "legacy driver",
        ),
    )
    for path, expected, label in paths_and_hashes:
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"{label} SHA mismatch: {path}")

    envelope = load_json(args.candidate)
    accepted = load_json(args.accepted_incumbent)
    formal = load_json(args.formal_state)
    config = load_json(args.config)
    candidate = validate_inputs(envelope, accepted, formal, config)
    enforce_cpuset()
    if args.preflight_only:
        print(
            json.dumps(
                {
                    "status": "ready",
                    "maximum_physical_workload_invocations": 1,
                    "cpuset_cpus": REQUIRED_CPUSET,
                    "mysql_reserved_cpus": MYSQL_RESERVED_CPUSET,
                    "state_neutral": True,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0

    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    events_path = output / "events.jsonl"
    ledger_path = output / "ledger.json"
    summary_path = output / "summary.json"
    ledger: Dict[str, Any] = {
        "contract": "xp_incumbent_protocol_revalidation_ledger_v1",
        "maximum_physical_workload_invocations": 1,
        "real_workload_invocation_count": 0,
        "successful": [],
        "technical_failures": [],
    }
    atomic_json(ledger_path, ledger)

    driver = load_module(args.legacy_driver)
    driver.params_match = pg_setting_precision_match
    baseline_params = {
        name: float(value)
        for name, value in (config.get("initial_params") or {}).items()
    }
    capability = {
        name: float(config["capability"][name])
        for name in ("cpu", "memory", "io")
    }
    recovery_command = config["pre_benchmark_recovery"]["snapshot_restore"][
        "command"
    ]
    warmup_command = config["benchmark"]["validation_warmup_cmd"]
    workload_command = diagnostic_workload_command(
        config["benchmark"]["workload_cmd"]
    )
    successful: list[Dict[str, Any]] = []
    failures: list[Dict[str, Any]] = []
    reserved = False
    baseline_snapshot_restored = False
    baseline_params_restored = False
    restore_error = None

    with (output / "replay.lock").open("w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        write_event(
            events_path,
            {
                "event": "xp_incumbent_revalidation_start",
                "maximum_physical_workload_invocations": 1,
                "candidate_sha256": args.candidate_sha256,
                "formal_state_sha256_before": args.formal_state_sha256,
                "accepted_update_applied": False,
                "incumbent_update_applied": False,
                "range_update_applied": False,
                "gradient_update_applied": False,
                "qps_used_for_inner_scoring": False,
            },
        )
        ledger["real_workload_invocation_count"] = 1
        ledger["reserved_rank"] = 1
        atomic_json(ledger_path, ledger)
        write_event(
            events_path,
            {
                "event": "xp_incumbent_revalidation_physical_slot_reserved",
                "rank": 1,
                "real_workload_invocation_count": 1,
                "workload_invoked": False,
            },
        )
        reserved = True
        try:
            enforce_cpuset()
            recovery = driver.run_command(
                recovery_command, 900, output / "recovery.log"
            )
            if recovery.returncode != 0:
                raise RuntimeError(
                    f"snapshot recovery failed: rc={recovery.returncode}"
                )
            driver.apply_params(candidate["candidate_params"])
            enforce_cpuset()
            warmup = driver.run_command(
                warmup_command, 240, output / "warmup.log"
            )
            if warmup.returncode != 0:
                raise RuntimeError(f"warmup failed: rc={warmup.returncode}")

            write_event(
                events_path,
                {
                    "event": (
                        "xp_incumbent_revalidation_workload_invocation_reserved"
                    ),
                    "rank": 1,
                    "real_workload_invocation_count": 1,
                    "workload_invoked": True,
                },
            )
            enforce_cpuset()
            measured = driver.run_command(
                workload_command, 900, output / "workload.log"
            )
            payload = driver.parse_payload(measured.stdout)
            utilization, protocol, measurement_summary = (
                driver.validate_payload(payload)
            )
            result = {
                "rank": 1,
                "fingerprint_id": candidate[
                    "three_level_fingerprint"
                ]["id"],
                "candidate_params": candidate["candidate_params"],
                "three_level_fingerprint": candidate[
                    "three_level_fingerprint"
                ],
                "workload_plan_fingerprint": candidate[
                    "workload_plan_fingerprint"
                ],
                "predicted_score": candidate["candidate_score"],
                "predicted_resource": candidate["predicted_resource"],
                "actual_score": driver.actual_score(
                    utilization, capability
                ),
                "actual_resource": driver.actual_resource(
                    utilization, capability
                ),
                "actual_utilization": utilization,
                "workload_returncode": measured.returncode,
                "algorithmic_gate_rejection": measured.returncode != 0,
                "measurement_protocol": protocol,
                "measurement_summary_status": measurement_summary.get(
                    "status"
                ),
                "physical_call": True,
                "technical_failure": False,
                "state_commit_eligible": False,
            }
            successful.append(result)
            ledger["successful"] = successful
            atomic_json(ledger_path, ledger)
            write_event(
                events_path,
                {
                    "event": "xp_incumbent_revalidation_complete",
                    **result,
                },
            )
        except Exception as exc:
            failure = {
                "rank": 1,
                "error": f"{type(exc).__name__}: {exc}",
                "physical_call": reserved,
            }
            failures.append(failure)
            ledger["technical_failures"] = failures
            atomic_json(ledger_path, ledger)
            write_event(
                events_path,
                {
                    "event": (
                        "xp_incumbent_revalidation_technical_failure"
                    ),
                    **failure,
                },
            )
        finally:
            try:
                enforce_cpuset()
                final_recovery = driver.run_command(
                    recovery_command, 900, output / "final_recovery.log"
                )
                if final_recovery.returncode != 0:
                    raise RuntimeError(
                        "final snapshot recovery failed: "
                        f"rc={final_recovery.returncode}"
                    )
                baseline_snapshot_restored = True
                readback = driver.apply_params(baseline_params)
                baseline_params_restored = driver.params_match(
                    readback, baseline_params
                )
                if not baseline_params_restored:
                    raise RuntimeError("baseline parameter readback mismatch")
            except Exception as exc:
                restore_error = f"{type(exc).__name__}: {exc}"
            write_event(
                events_path,
                {
                    "event": "xp_incumbent_revalidation_baseline_restore",
                    "baseline_snapshot_restored": (
                        baseline_snapshot_restored
                    ),
                    "baseline_params_restored": baseline_params_restored,
                    "restore_error": restore_error,
                },
            )

    formal_state_unchanged = (
        sha256(args.formal_state) == args.formal_state_sha256
    )
    accepted_incumbent_unchanged = (
        sha256(args.accepted_incumbent)
        == args.accepted_incumbent_sha256
    )
    post_replay_error = None
    if restore_error is not None:
        post_replay_error = f"baseline restore failed: {restore_error}"
    elif not formal_state_unchanged:
        post_replay_error = "formal state SHA changed during diagnostic replay"
    elif not accepted_incumbent_unchanged:
        post_replay_error = (
            "accepted incumbent SHA changed during diagnostic replay"
        )
    if post_replay_error is not None and not failures:
        failures.append(
            {
                "rank": 1,
                "error": post_replay_error,
                "physical_call": True,
            }
        )
        successful = []
        ledger["successful"] = []
        ledger["technical_failures"] = failures
        atomic_json(ledger_path, ledger)
        write_event(
            events_path,
            {
                "event": "xp_incumbent_revalidation_post_replay_failure",
                **failures[-1],
            },
        )
    physical = int(ledger["real_workload_invocation_count"])
    clean = bool(
        physical == 1
        and len(successful) == 1
        and not failures
        and baseline_snapshot_restored
        and baseline_params_restored
        and formal_state_unchanged
        and accepted_incumbent_unchanged
    )
    summary = {
        "contract": "xp_incumbent_protocol_revalidation_summary_v1",
        "status": "complete" if clean else "technical_failure",
        "maximum_physical_workload_invocations": 1,
        "real_workload_invocation_count": physical,
        "valid_replay_count": len(successful),
        "technical_failure_count": len(failures),
        "successful": successful,
        "technical_failures": failures,
        "baseline_snapshot_restored": baseline_snapshot_restored,
        "baseline_params_restored": baseline_params_restored,
        "restore_error": restore_error,
        "formal_state_sha256_before": args.formal_state_sha256,
        "formal_state_sha256_after": sha256(args.formal_state),
        "formal_state_unchanged": formal_state_unchanged,
        "accepted_incumbent_unchanged": accepted_incumbent_unchanged,
        "accepted_update_applied": False,
        "incumbent_update_applied": False,
        "range_update_applied": False,
        "gradient_update_applied": False,
        "qps_used_for_inner_scoring": False,
        "cpuset_cpus": REQUIRED_CPUSET,
        "mysql_reserved_cpus": MYSQL_RESERVED_CPUSET,
    }
    atomic_json(summary_path, summary)
    print(
        json.dumps(
            {
                "status": summary["status"],
                "summary": str(summary_path),
                "physical_calls": physical,
                "valid_replays": len(successful),
                "technical_failures": len(failures),
                "baseline_restored": (
                    baseline_snapshot_restored
                    and baseline_params_restored
                ),
                "state_neutral": (
                    formal_state_unchanged
                    and accepted_incumbent_unchanged
                ),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if clean else 2


if __name__ == "__main__":
    raise SystemExit(main())
