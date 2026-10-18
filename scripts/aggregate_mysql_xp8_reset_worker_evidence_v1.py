#!/usr/bin/env python3
"""Seal eight successful reset_worker rows into one recovery binding."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", choices=("hybench_xp_cpu", "hybench_xp_io"), required=True)
    parser.add_argument("--worker-root", type=Path, required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prefix = "xp_cpu" if args.scene.endswith("cpu") else "xp_io"
    rows = []
    for index in range(1, 9):
        path = args.worker_root.resolve() / f"{prefix}_w{index}/reset_worker.json"
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"reset worker evidence absent: {path}")
        row = json.loads(path.read_text(encoding="utf-8"))
        if row.get("worker_id") != f"{prefix}_w{index}" or row.get("ready") is not True:
            raise RuntimeError(f"reset worker evidence differs: {path}")
        rows.append(row)
    freeze = args.freeze.resolve()
    document = {
        "schema_version": "mysql_xp8_recovered_reset_binding_v1",
        "status": "PASS", "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "scene": args.scene, "freeze_path": str(freeze), "freeze_sha256": sha(freeze),
        "workers": rows, "recovered_single_stalled_worker": True,
        "completed_candidate_measurement_credit": 0,
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    output.with_name(output.name + ".sha256").write_text(f"{sha(output)}  {output.name}\n", encoding="ascii")
    print(json.dumps({"status": "PASS", "output": str(output), "workers": len(rows)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
