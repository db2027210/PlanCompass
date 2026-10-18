#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import subprocess
import time
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATUS = ROOT / "report" / "bonect" / "latest_robust_ranking_validation_plan" / "validation_status.json"


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        f.write("\n")


def run_command(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        shlex.split(command),
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def command_record(status: Dict[str, Any], *, dry_run: bool) -> Dict[str, Any]:
    next_batch = status.get("next_validation_batch") or {}
    command = str(status.get("next_validation_command") or "")
    return {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "dry_run": dry_run,
        "complete": bool(status.get("complete")),
        "candidate_count": status.get("candidate_count"),
        "ok_count": status.get("ok_count"),
        "failed_count": status.get("failed_count"),
        "pending_count": status.get("pending_count"),
        "next_validation_batch": next_batch,
        "next_validation_command": command,
        "status_refresh_command": status.get("status_refresh_command"),
        "partial_evaluation_command": status.get("partial_evaluation_command"),
        "strict_evaluation_command": status.get("strict_evaluation_command"),
    }


def status_signature(status: Dict[str, Any]) -> Dict[str, Any]:
    next_batch = status.get("next_validation_batch") or {}
    return {
        "complete": bool(status.get("complete")),
        "candidate_count": status.get("candidate_count"),
        "ok_count": status.get("ok_count"),
        "failed_count": status.get("failed_count"),
        "pending_count": status.get("pending_count"),
        "next_validation_command": status.get("next_validation_command"),
        "next_batch_queue_jsonl": next_batch.get("queue_jsonl") if isinstance(next_batch, dict) else None,
        "next_batch_candidate_count": next_batch.get("candidate_count") if isinstance(next_batch, dict) else None,
    }


def strict_evaluation_record(status: Dict[str, Any], *, enabled: bool) -> Dict[str, Any]:
    command = str(status.get("strict_evaluation_command") or "")
    record = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "enabled": enabled,
        "complete": bool(status.get("complete")),
        "strict_evaluation_command": command,
    }
    if not enabled:
        record["action"] = "disabled"
        return record
    if not status.get("complete"):
        record["action"] = "skip_not_complete"
        return record
    if not command:
        record["action"] = "missing_strict_evaluation_command"
        record["returncode"] = 1
        return record
    record["action"] = "execute_strict_evaluation"
    record["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    proc = run_command(command)
    record["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    record["returncode"] = proc.returncode
    record["stdout"] = proc.stdout
    record["stderr"] = proc.stderr
    return record


def orchestrate(args: argparse.Namespace) -> int:
    status_path = Path(args.status_json)
    if not status_path.is_absolute():
        status_path = ROOT / status_path
    status = read_json(status_path)
    records: List[Dict[str, Any]] = []
    exit_code = 0
    max_batches = max(1, int(args.max_batches))

    for _ in range(max_batches):
        status = read_json(status_path)
        record = command_record(status, dry_run=not bool(args.execute))
        records.append(record)
        command = str(record.get("next_validation_command") or "")
        if record.get("complete"):
            record["action"] = "stop_complete"
            break
        if not command:
            record["action"] = "stop_no_next_command"
            exit_code = 1
            break
        if not args.execute:
            record["action"] = "dry_run"
            break

        before_signature = status_signature(status)
        record["status_signature_before"] = before_signature
        record["action"] = "execute"
        record["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S %Z")
        proc = run_command(command)
        record["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S %Z")
        record["returncode"] = proc.returncode
        record["stdout"] = proc.stdout
        record["stderr"] = proc.stderr
        if proc.returncode != 0:
            exit_code = proc.returncode
            break
        after_status = read_json(status_path)
        after_signature = status_signature(after_status)
        record["status_signature_after"] = after_signature
        record["progressed"] = after_signature != before_signature
        if not record["progressed"] and not args.allow_no_progress:
            record["action_after_execute"] = "stop_no_progress"
            record["no_progress_message"] = (
                "The validation command returned 0 but validation_status.json did not change. "
                "Stopping to avoid rerunning the same chunk."
            )
            exit_code = 1
            break

    final_status = read_json(status_path)
    strict_record = strict_evaluation_record(
        final_status,
        enabled=bool(args.strict_after_complete),
    )
    if int(strict_record.get("returncode") or 0) != 0 and exit_code == 0:
        exit_code = int(strict_record.get("returncode") or 1)
    summary = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "status_json": str(status_path),
        "execute": bool(args.execute),
        "allow_no_progress": bool(args.allow_no_progress),
        "max_batches": max_batches,
        "records": records,
        "strict_after_complete": strict_record,
    }
    if args.output_json:
        output_path = Path(args.output_json)
        if not output_path.is_absolute():
            output_path = ROOT / output_path
        write_json(output_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
    return exit_code


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Drive Bonect robust-ranking real-validation chunks from validation_status.json. "
            "Default mode is a dry run; pass --execute to run the next chunk command."
        )
    )
    parser.add_argument("--status-json", default=str(DEFAULT_STATUS))
    parser.add_argument("--max-batches", type=int, default=1)
    parser.add_argument("--output-json", default="")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--allow-no-progress",
        action="store_true",
        help=(
            "Permit an executed command to return 0 without changing validation_status.json. "
            "This is intended for tests or custom commands; generated real-validation queues should not need it."
        ),
    )
    parser.add_argument(
        "--strict-after-complete",
        action="store_true",
        help="Run strict_evaluation_command only after the refreshed status is complete.",
    )
    return parser


def main() -> int:
    return orchestrate(build_arg_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
