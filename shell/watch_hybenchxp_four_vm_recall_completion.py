#!/usr/bin/env python3
"""Run the strict four-VM completion audit once convergence is durable."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional


TARGETS = ("cpu", "memory", "io", "none")


def load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o644)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def converged_final_artifacts(run_root: Path) -> Dict[str, bool]:
    result: Dict[str, bool] = {}
    for target in TARGETS:
        value = load_json(run_root / target / f"final_for_{target}.json") or {}
        result[target] = bool(
            value.get("converged") is True and value.get("stop_reason") == "epsilon_converged"
        )
    return result


def guard_complete(run_root: Path) -> bool:
    value = load_json(run_root / "host_disk_isolation_status.json") or {}
    return bool(
        value.get("event") == "host_disk_guard_complete"
        and value.get("status") == "ok"
        and value.get("all_converged") is True
    )


def invalidation_markers(run_root: Path) -> List[str]:
    return sorted(path.name for path in run_root.glob("INVALIDATED_*.md") if path.is_file())


def status_payload(run_root: Path, *, event: str, **extra: Any) -> Dict[str, Any]:
    return {
        "event": event,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "run_root": str(run_root),
        "final_artifacts_converged": converged_final_artifacts(run_root),
        "guard_complete": guard_complete(run_root),
        "invalidation_markers": invalidation_markers(run_root),
        **extra,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--interval-sec", type=float, default=30.0)
    parser.add_argument("--retry-sec", type=float, default=60.0)
    parser.add_argument(
        "--audit-script",
        type=Path,
        default=Path(__file__).with_name("audit_hybenchxp_four_vm_recall_completion.py"),
    )
    args = parser.parse_args()

    run_root = args.run_root.resolve()
    audit_script = args.audit_script.resolve()
    interval = max(5.0, args.interval_sec)
    retry = max(5.0, args.retry_sec)
    status_path = run_root / "completion_audit_watcher_status.json"
    history_path = run_root / "completion_audit_watcher_history.jsonl"

    start = status_payload(
        run_root,
        event="completion_audit_watcher_start",
        audit_script=str(audit_script),
        interval_sec=interval,
        retry_sec=retry,
    )
    atomic_write(status_path, json.dumps(start, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    append_jsonl(history_path, start)

    while True:
        markers = invalidation_markers(run_root)
        if markers:
            payload = status_payload(
                run_root,
                event="completion_audit_watcher_invalidated",
                audit_script=str(audit_script),
            )
            atomic_write(status_path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            append_jsonl(history_path, payload)
            return 2

        finals = converged_final_artifacts(run_root)
        if all(finals.values()) and guard_complete(run_root):
            process = subprocess.run(
                [sys.executable, str(audit_script), str(run_root)],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            payload = status_payload(
                run_root,
                event=(
                    "completion_audit_watcher_complete"
                    if process.returncode == 0
                    else "completion_audit_watcher_retry"
                ),
                audit_script=str(audit_script),
                audit_returncode=process.returncode,
                audit_stdout=process.stdout,
                audit_stderr=process.stderr,
            )
            atomic_write(status_path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            append_jsonl(history_path, payload)
            if process.returncode == 0:
                return 0
            time.sleep(retry)
            continue

        payload = status_payload(
            run_root,
            event="completion_audit_watcher_waiting",
            audit_script=str(audit_script),
            interval_sec=interval,
        )
        atomic_write(status_path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
