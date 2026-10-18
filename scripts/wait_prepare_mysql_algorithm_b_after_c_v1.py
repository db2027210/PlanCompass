#!/usr/bin/env python3
"""Wait for the active Algorithm C run and seal its Algorithm B handoff.

This process performs no workload or Docker mutation.  It only waits for a
formally completed Algorithm C controller, rebuilds a final evidence
checkpoint, and emits the C-derived Algorithm B binary-interval release.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_outer_checkpoint_v4 as checkpointmod
from scripts import build_mysql_algorithm_b_from_algorithm_c_release_v1 as handoff


SCHEMA = "mysql_algorithm_c_to_b_auto_handoff_result_v1"
SCENES = list(handoff.SCENES)


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def append(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n"
        )
        stream.flush()
        os.fsync(stream.fileno())


def bound(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular non-symlink evidence required: {path}")
    return {"path": str(path), "sha256": sha(path)}


def validate_launch_result(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink():
        raise RuntimeError("Algorithm C launch result must be a regular file")
    result = load(path)
    if result.get("status") == "TIMED_OUT":
        raise RuntimeError("Algorithm C prestart waiter timed out")
    if result.get("status") != "STARTED":
        raise RuntimeError("Algorithm C launch result status differs")
    receipt_path = Path(str(result.get("launch_receipt_path") or "")).resolve()
    receipt = load(receipt_path)
    run_state_path = Path(str(result.get("run_state_path") or "")).resolve()
    if (
        receipt.get("status") != "STARTED"
        or receipt.get("execution_class") != "FORMAL_MYSQL_ONLY"
        or receipt.get("formal_scope") != "mysql_only"
        or receipt.get("cross_engine_comparison_prohibited") is not True
        or receipt.get("formal_evidence_eligible") is not True
        or receipt.get("accepted_state_credit_prohibited") is not False
        or receipt.get("active_scenes") != SCENES
        or Path(str(receipt.get("run_state_path") or "")).resolve()
        != run_state_path
        or result.get("systemd_unit") != receipt.get("systemd_unit")
    ):
        raise RuntimeError("Algorithm C launch receipt authority differs")
    return {
        "result": result,
        "receipt_path": receipt_path,
        "receipt": receipt,
        "run_state_path": run_state_path,
    }


def prepare(run_root: Path, output_root: Path) -> dict[str, Any]:
    checkpoint_path = output_root / "algorithm_c_final_checkpoint/prepared_binding.json"
    if checkpoint_path.exists():
        checkpoint = checkpointmod.verify_checkpoint(checkpoint_path)
    else:
        checkpoint = checkpointmod.build_checkpoint(run_root)
        atomic(checkpoint_path, checkpoint)
        checkpoint = checkpointmod.verify_checkpoint(checkpoint_path)
    release_path = output_root / "algorithm_b_from_c_interval_release.json"
    if release_path.exists():
        release = handoff.verify_release(release_path)
    else:
        release = handoff.build_release(checkpoint_path)
        handoff._atomic(release_path, release)
        release = handoff.verify_release(release_path)
    return {
        "checkpoint_path": checkpoint_path,
        "checkpoint": checkpoint,
        "release_path": release_path,
        "release": release,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--algorithm-c-launch-result", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--maximum-wait-seconds", type=float, default=72 * 3600)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    args = parser.parse_args()
    if (
        not math_is_positive(args.maximum_wait_seconds)
        or not math_is_positive(args.poll_seconds)
        or re.fullmatch(r"[A-Za-z0-9_.-]{8,180}", args.output_root.name) is None
    ):
        parser.error("positive wait/poll and safe output root are required")
    output_root = args.output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_path = output_root / "auto_handoff.jsonl"
    result_path = output_root / "auto_handoff_result.json"
    if result_path.exists():
        result = load(result_path)
        if result.get("status") == "PREPARED_FOR_ALGORITHM_B":
            handoff.verify_release(Path(result["algorithm_b_interval_release"]["path"]))
            return 0
        raise RuntimeError("existing terminal auto-handoff result is not successful")
    append(log_path, {
        "event": "auto_handoff_wait_started",
        "captured_at": now(),
        "maximum_wait_seconds": args.maximum_wait_seconds,
        "poll_seconds": args.poll_seconds,
        "workload_mutation": False,
        "docker_mutation": False,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
    })
    started = time.monotonic()
    launch: dict[str, Any] | None = None
    last_observed: tuple[str, str] | None = None
    while time.monotonic() - started < args.maximum_wait_seconds:
        if launch is None:
            launch = validate_launch_result(
                args.algorithm_c_launch_result.expanduser().resolve()
            )
            if launch is None:
                time.sleep(args.poll_seconds)
                continue
            append(log_path, {
                "event": "algorithm_c_launch_bound",
                "captured_at": now(),
                "launch_result": bound(args.algorithm_c_launch_result),
                "launch_receipt": bound(launch["receipt_path"]),
                "run_state_path": str(launch["run_state_path"]),
                "systemd_unit": launch["result"]["systemd_unit"],
            })
        state_path = launch["run_state_path"]
        if not state_path.exists():
            time.sleep(args.poll_seconds)
            continue
        state = load(state_path)
        observed = (str(state.get("status")), str(state.get("stage")))
        if observed != last_observed:
            append(log_path, {
                "event": "algorithm_c_state_changed",
                "captured_at": now(),
                "status": observed[0],
                "stage": observed[1],
                "outer_attempt": state.get("outer_attempt"),
                "accepted_rounds": state.get("accepted_rounds"),
                "interval_stable_rounds": state.get("interval_stable_rounds"),
                "converged": state.get("converged"),
            })
            last_observed = observed
        if state.get("status") in {"FAILED_CLOSED", "NON_CONVERGED"}:
            failure = {
                "schema_version": SCHEMA,
                "status": "ALGORITHM_C_NOT_COMPLETE_FAIL_CLOSED",
                "captured_at": now(),
                "algorithm_c_state": bound(state_path),
                "algorithm_c_status": state.get("status"),
                "algorithm_c_stage": state.get("stage"),
                "algorithm_c_error": state.get("error"),
                "algorithm_b_launch_performed": False,
                "workload_mutation": False,
                "docker_mutation": False,
            }
            atomic(result_path, failure)
            return 4
        if state.get("status") != "COMPLETE" or state.get("stage") != "FINISHED":
            time.sleep(args.poll_seconds)
            continue
        try:
            prepared = prepare(state_path.parent, output_root)
        except RuntimeError as error:
            # The controller state becomes terminal slightly before its
            # companion watchdog writes the terminal success row.
            if "runtime interference watchdog" in str(error) or "non-interference" in str(error):
                time.sleep(args.poll_seconds)
                continue
            raise
        result = {
            "schema_version": SCHEMA,
            "status": "PREPARED_FOR_ALGORITHM_B",
            "captured_at": now(),
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "algorithm_c_state": bound(state_path),
            "algorithm_c_final_checkpoint": bound(prepared["checkpoint_path"]),
            "algorithm_b_interval_release": bound(prepared["release_path"]),
            "algorithm_b_release_sha256": prepared["release"]["release_sha256"],
            "algorithm_b_launch_performed": False,
            "algorithm_b_fresh_runtime_preflight_required": True,
            "algorithm_b_fresh_baseline_required": True,
            "workload_mutation": False,
            "docker_mutation": False,
        }
        atomic(result_path, result)
        append(log_path, {
            "event": "algorithm_b_handoff_prepared",
            "captured_at": now(),
            "result": bound(result_path),
        })
        return 0
    timeout = {
        "schema_version": SCHEMA,
        "status": "TIMED_OUT",
        "captured_at": now(),
        "waited_seconds": time.monotonic() - started,
        "algorithm_b_launch_performed": False,
        "workload_mutation": False,
        "docker_mutation": False,
    }
    atomic(result_path, timeout)
    return 5


def math_is_positive(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number > 0.0 and number < float("inf")


if __name__ == "__main__":
    raise SystemExit(main())
