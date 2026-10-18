#!/usr/bin/env python3
"""Seal the latest completed XP point reset for an outer-boundary resume."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260811_v15"
    / "four_scene_effective_resource_successor.json"
)
XP_IDS = {"xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def build(source_prepared: Path, xp_reset: Path) -> dict[str, Any]:
    source = load(source_prepared)
    reset = load(xp_reset)
    workers = [dict(row) for row in (reset.get("workers") or [])]
    if (
        source.get("status") != "PREPARED"
        or source.get("freeze_sha256") != sha(FREEZE)
        or reset.get("status") != "PASS"
        or reset.get("formal_scope") != "mysql_only"
        or reset.get("cross_engine_comparison_prohibited") is not True
        or reset.get("freeze_sha256") != sha(FREEZE)
        or len(workers) != 4
        or {row.get("worker_id") for row in workers} != XP_IDS
        or any(row.get("ready") is not True for row in workers)
    ):
        raise RuntimeError("completed XP reset authority differs")
    for row in workers:
        evidence = Path(str(row.get("worker_evidence_path") or "")).resolve()
        if (
            not evidence.is_file()
            or evidence.is_symlink()
            or sha(evidence) != row.get("worker_evidence_sha256")
        ):
            raise RuntimeError(f"{row.get('worker_id')} reset evidence differs")
    workers.sort(key=lambda row: str(row["worker_id"]))
    return {
        "schema_version": "mysql_algorithm_c_boundary_xp_binding_v1",
        "status": "PREPARED",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE),
        "freeze_sha256": sha(FREEZE),
        "predecessor_prepared_path": str(source_prepared),
        "predecessor_prepared_sha256": sha(source_prepared),
        "xp_reset_binding_path": str(xp_reset),
        "xp_reset_binding_sha256": sha(xp_reset),
        "workers": workers,
        "workload_started": False,
        "fresh_r0_complete": False,
        "formal_outer_started": False,
        "accepted_rounds": {
            "job_memory": 0,
            "tpch_sf4_memory": 0,
            "hybench_xp_cpu": 0,
            "hybench_xp_io": 0,
        },
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": sha(Path(__file__).resolve()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-prepared", type=Path, required=True)
    parser.add_argument("--xp-reset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = [path.expanduser().resolve() for path in (args.source_prepared, args.xp_reset)]
    if any(not path.is_file() or path.is_symlink() for path in paths):
        parser.error("source bindings must be regular non-symlink files")
    output = args.output.expanduser().resolve()
    if output.exists() or output.parent.exists():
        raise RuntimeError(f"fresh output directory required: {output.parent}")
    atomic(output, build(paths[0], paths[1]))
    print(json.dumps({"status": "PREPARED", "binding": str(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
