#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CAMPAIGN_DIR = (
    ROOT / "report" / "bonect" / "phase2_vm_teacher_neighborhood_v80_20260711"
)
TRAINER = ROOT / "shell" / "train_bonect_phase2_vm_teacher_calibrators.py"


def now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    return value if isinstance(value, dict) else {}


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def failure_events(path: Path) -> list[Dict[str, Any]]:
    failures = []
    for record in iter_jsonl(path):
        event = str(record.get("event") or "")
        if event.endswith("_failed") or event.endswith("_gate_failed"):
            failures.append(record)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Wait for VM teacher-neighborhood labels, then run the estimator gate."
    )
    parser.add_argument("--campaign-dir", default=str(DEFAULT_CAMPAIGN_DIR))
    parser.add_argument("--poll-sec", type=float, default=60.0)
    parser.add_argument("--timeout-sec", type=float, default=0.0)
    args = parser.parse_args()

    campaign_dir = Path(args.campaign_dir).expanduser().resolve()
    summary_path = campaign_dir / "summary.json"
    state_path = campaign_dir / "state.jsonl"
    gate_state_path = campaign_dir / "estimator_gate_state.jsonl"
    started = time.monotonic()
    next_log = 0.0
    while True:
        failures = failure_events(state_path)
        if failures:
            record = {
                "event": "estimator_gate_blocked_by_sampling_failure",
                "timestamp": now_text(),
                "failure_count": len(failures),
                "latest_failure": failures[-1],
            }
            append_jsonl(gate_state_path, record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
            return 3
        if summary_path.is_file() and read_json(summary_path).get("all_complete") is True:
            break
        elapsed = time.monotonic() - started
        if args.timeout_sec > 0 and elapsed >= args.timeout_sec:
            record = {
                "event": "estimator_gate_wait_timeout",
                "timestamp": now_text(),
                "elapsed_sec": elapsed,
            }
            append_jsonl(gate_state_path, record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
            return 4
        if time.monotonic() >= next_log:
            record = {
                "event": "waiting_for_teacher_neighborhood_samples",
                "timestamp": now_text(),
                "elapsed_sec": elapsed,
                "summary_path": str(summary_path),
            }
            append_jsonl(gate_state_path, record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
            next_log = time.monotonic() + 300.0
        time.sleep(max(1.0, args.poll_sec))

    start_record = {
        "event": "estimator_gate_start",
        "timestamp": now_text(),
        "campaign_dir": str(campaign_dir),
    }
    append_jsonl(gate_state_path, start_record)
    print(json.dumps(start_record, ensure_ascii=False), flush=True)
    proc = subprocess.run(
        [
            sys.executable,
            str(TRAINER),
            "--campaign-dir",
            str(campaign_dir),
        ],
        cwd=str(ROOT),
        check=False,
    )
    record = {
        "event": (
            "estimator_gate_complete"
            if proc.returncode == 0
            else "estimator_gate_failed"
        ),
        "timestamp": now_text(),
        "returncode": proc.returncode,
        "training_summary": str(campaign_dir / "estimator_training_summary.json"),
    }
    append_jsonl(gate_state_path, record)
    print(json.dumps(record, ensure_ascii=False), flush=True)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
