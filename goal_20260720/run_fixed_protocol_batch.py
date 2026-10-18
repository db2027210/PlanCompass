#!/usr/bin/env python3
"""Execute every planned formal repeat exactly once and preserve failures.

The runner deliberately has no retry or pass-based early exit.  A protocol file
defines a fixed ``attempt_limit`` and the common arguments for the existing
outer-binary / inner-estimated-grid runner.  The resulting batch manifest and
summary make the distinction between an optimizer failure and a process error
auditable without silently replacing either one.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_protocol(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("protocol must be a JSON object")
    required = {"protocol_id", "attempt_limit", "output_dir", "runner_args"}
    missing = sorted(required - set(value))
    if missing:
        raise ValueError(f"protocol is missing keys: {missing}")
    if not isinstance(value["protocol_id"], str) or not value["protocol_id"].strip():
        raise ValueError("protocol_id must be a non-empty string")
    if not isinstance(value["attempt_limit"], int) or value["attempt_limit"] < 1:
        raise ValueError("attempt_limit must be a positive integer")
    if not isinstance(value["runner_args"], list) or not all(
        isinstance(item, str) for item in value["runner_args"]
    ):
        raise ValueError("runner_args must be a list of strings")
    if "python_executable" in value and not isinstance(value["python_executable"], str):
        raise ValueError("python_executable must be a string when supplied")
    return value


def result_summary(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        return None
    audit = value.get("audit") if isinstance(value.get("audit"), dict) else {}
    acceptance = audit.get("acceptance_metrics") if isinstance(audit.get("acceptance_metrics"), dict) else {}
    result = value.get("result") if isinstance(value.get("result"), dict) else {}
    return {
        "result_path": str(path),
        "final_acceptance_met": acceptance.get("final_acceptance_met"),
        "bad_config": result.get("bad_config"),
        "qps_before": result.get("qps_before"),
        "qps_after": result.get("qps_after"),
        "delta_bottleneck_util_pp": acceptance.get("delta_bottleneck_util_pp"),
        "cpu_policy_met": acceptance.get("non_target_cpu_policy_met"),
    }


def run(protocol_path: Path, *, resume_existing: bool = False) -> Path:
    protocol = load_protocol(protocol_path)
    output = Path(protocol["output_dir"])
    if not output.is_absolute():
        output = (ROOT / output).resolve()
    summary_path = output / "batch_summary.json"
    if output.exists() and not resume_existing:
        raise FileExistsError(
            f"fixed protocol output already exists: {output}; refusing to overwrite or retry"
        )
    if output.exists():
        if summary_path.is_file():
            return summary_path
        snapshot_path = output / "protocol.json"
        manifest_path = output / "batch_manifest.json"
        if not snapshot_path.is_file() or not manifest_path.is_file():
            raise FileNotFoundError(
                f"cannot resume {output}: protocol.json or batch_manifest.json is missing"
            )
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        for key in ("protocol_id", "attempt_limit", "exp_id", "runner_args"):
            if snapshot.get(key) != protocol.get(key):
                raise ValueError(f"cannot resume {output}: protocol field changed: {key}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        attempts = manifest.get("attempts")
        if not isinstance(attempts, list):
            raise ValueError(f"cannot resume {output}: manifest attempts is not a list")
        completed_indexes = [row.get("attempt_index") for row in attempts if isinstance(row, dict)]
        expected_indexes = list(range(1, len(attempts) + 1))
        if completed_indexes != expected_indexes:
            raise ValueError(
                f"cannot resume {output}: completed attempts are not contiguous: {completed_indexes}"
            )
        if len(attempts) >= int(protocol["attempt_limit"]):
            raise ValueError(
                f"cannot resume {output}: all fixed attempts are recorded but summary is missing"
            )
        manifest["resumed_at_utc"] = utc_now()
        manifest["resume_policy"] = (
            "continue_missing_fixed_attempts_only; interrupted directories are preserved"
        )
        write_json(manifest_path, manifest)
    else:
        output.mkdir(parents=True, exist_ok=False)
        write_json(output / "protocol.json", protocol)
        manifest = {
            "protocol_id": protocol["protocol_id"],
            "protocol_source": str(protocol_path.resolve()),
            "started_at_utc": utc_now(),
            "attempt_limit": protocol["attempt_limit"],
            "retry_policy": "disabled_fixed_attempt_count",
            "attempts": [],
        }
        write_json(output / "batch_manifest.json", manifest)

    runner = ROOT / "goal_20260718" / "mysql_double_loop_grid_runner.py"
    python_executable = Path(protocol.get("python_executable", sys.executable)).resolve()
    if not python_executable.is_file():
        raise FileNotFoundError(f"configured Python interpreter does not exist: {python_executable}")
    first_missing_attempt = len(manifest["attempts"]) + 1
    for attempt_index in range(first_missing_attempt, int(protocol["attempt_limit"]) + 1):
        attempt_dir = output / f"attempt_{attempt_index:02d}"
        if attempt_dir.exists():
            interrupted_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            preserved = output / f"attempt_{attempt_index:02d}_interrupted_{interrupted_at}"
            if preserved.exists():
                raise FileExistsError(f"interrupted-attempt preservation target exists: {preserved}")
            attempt_dir.replace(preserved)
        attempt_dir.mkdir(parents=True, exist_ok=False)
        command = [
            str(python_executable),
            str(runner),
            *protocol["runner_args"],
            "--repeat-id", str(attempt_index),
            "--output-dir", str(attempt_dir),
            "--protocol-id", str(protocol["protocol_id"]),
            "--attempt-index", str(attempt_index),
            "--attempt-limit", str(protocol["attempt_limit"]),
        ]
        record: dict[str, Any] = {
            "attempt_index": attempt_index,
            "started_at_utc": utc_now(),
            "command": command,
            "stdout_log": str(attempt_dir / "stdout.log"),
            "stderr_log": str(attempt_dir / "stderr.log"),
        }
        with (attempt_dir / "stdout.log").open("w", encoding="utf-8") as stdout, (
            attempt_dir / "stderr.log"
        ).open("w", encoding="utf-8") as stderr:
            completed = subprocess.run(command, stdout=stdout, stderr=stderr, check=False)
        record["finished_at_utc"] = utc_now()
        record["exit_code"] = completed.returncode
        record["process_failed"] = completed.returncode != 0
        expected_result = attempt_dir / f"{protocol['exp_id']}_r{attempt_index}.json"
        record["formal_result"] = result_summary(expected_result)
        record["failure_log"] = (
            str(attempt_dir / "run_failure.json")
            if (attempt_dir / "run_failure.json").is_file() else None
        )
        manifest["attempts"].append(record)
        write_json(output / "batch_manifest.json", manifest)

    passed = sum(
        row.get("formal_result", {}).get("final_acceptance_met") is True
        for row in manifest["attempts"] if isinstance(row.get("formal_result"), dict)
    )
    summary = {
        **manifest,
        "finished_at_utc": utc_now(),
        "passed_attempts": passed,
        "required_passes_for_majority": int(protocol["attempt_limit"]) // 2 + 1,
        "majority_passed": passed >= int(protocol["attempt_limit"]) // 2 + 1,
    }
    write_json(output / "batch_summary.json", summary)
    return output / "batch_summary.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument(
        "--resume-existing",
        action="store_true",
        help=(
            "resume only missing fixed attempts from a matching incomplete batch; "
            "never rerun recorded attempts"
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    print(
        run(args.protocol.resolve(), resume_existing=args.resume_existing),
        flush=True,
    )
