#!/usr/bin/env python3
"""Bind a delayed formal Algorithm C launch receipt into a launch result."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SCENES = [
    "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io",
]


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--maximum-wait-seconds", type=float, default=7200)
    parser.add_argument("--poll-seconds", type=float, default=2)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,180}", args.run_id) is None:
        parser.error("safe run id required")
    if args.maximum_wait_seconds <= 0 or args.poll_seconds <= 0:
        parser.error("positive wait and poll intervals required")

    run_root = (
        ROOT / "history/mysql_four_scene_20260809" / args.run_id
    ).resolve()
    receipt_path = run_root / "launch_receipt.json"
    output = args.output.expanduser().resolve()
    started = time.monotonic()
    while time.monotonic() - started < args.maximum_wait_seconds:
        if not receipt_path.exists():
            time.sleep(args.poll_seconds)
            continue
        if receipt_path.is_symlink() or not receipt_path.is_file():
            raise RuntimeError("regular non-symlink launch receipt required")
        receipt = load(receipt_path)
        run_state_path = Path(str(receipt.get("run_state_path") or "")).resolve()
        if (
            receipt.get("status") != "STARTED"
            or receipt.get("execution_class") != "FORMAL_MYSQL_ONLY"
            or receipt.get("formal_scope") != "mysql_only"
            or receipt.get("cross_engine_comparison_prohibited") is not True
            or receipt.get("formal_evidence_eligible") is not True
            or receipt.get("accepted_state_credit_prohibited") is not False
            or receipt.get("active_scenes") != SCENES
            or run_state_path.parent != run_root
            or (
                receipt.get("run_id") is not None
                and receipt.get("run_id") != args.run_id
            )
        ):
            raise RuntimeError("formal Algorithm C launch receipt differs")
        result = {
            "status": "STARTED",
            "captured_at": now(),
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "run_id": args.run_id,
            "systemd_unit": receipt["systemd_unit"],
            "run_state_path": str(run_state_path),
            "launch_receipt_path": str(receipt_path),
            "launch_receipt_sha256": sha(receipt_path),
            "docker_mutation": False,
            "external_process_mutation": False,
        }
        atomic(output, result)
        return 0
    atomic(output, {
        "status": "TIMED_OUT",
        "captured_at": now(),
        "run_id": args.run_id,
        "waited_seconds": time.monotonic() - started,
        "docker_mutation": False,
        "external_process_mutation": False,
    })
    return 5


if __name__ == "__main__":
    raise SystemExit(main())
