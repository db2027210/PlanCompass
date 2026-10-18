#!/usr/bin/env python3
"""Bind four PASS v145 baselines into one immutable formal R0 set."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = ROOT / (
    "config/formal/frozen_mysql_four_scene_two_worker_20260923_v31/"
    "four_scene_effective_resource_successor.json"
)
SCENES = {
    "job_memory": "job",
    "tpch_sf4_memory": "tpch",
    "hybench_xp_cpu": "xp_cpu",
    "hybench_xp_io": "xp_io",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
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
    parser.add_argument("--baseline-prefix", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    for value in (args.baseline_prefix, args.run_id):
        if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", value) is None:
            parser.error("unsafe run identifier")
    output = HISTORY / args.run_id / "formal_r0_set.json"
    if output.parent.exists() or output.parent.is_symlink():
        raise RuntimeError(f"fresh formal R0 output required: {output.parent}")
    freeze_sha = sha(FREEZE)
    rows: dict[str, Any] = {}
    for scene, suffix in SCENES.items():
        run = HISTORY / f"{args.baseline_prefix}_{suffix}"
        state_path = run / "run_state.json"
        gate_path = run / "paper_baseline_resource_gate.json"
        batch_path = run / "formal_incumbent_baseline/batch_result.json"
        if not all(path.is_file() and not path.is_symlink()
                   for path in (state_path, gate_path, batch_path)):
            raise RuntimeError(f"regular baseline artifacts required: {scene}")
        state, gate, batch = map(load, (state_path, gate_path, batch_path))
        if (
            state.get("status") != "COMPLETE"
            or state.get("freeze_sha256") != freeze_sha
            or gate.get("status") != "PASS"
            or gate.get("candidate_search_authorized") is not True
            or batch.get("status") != "PASS"
            or batch.get("scene") != scene
            or batch.get("formal_scope") != "mysql_only"
            or batch.get("cross_engine_comparison_prohibited") is not True
        ):
            raise RuntimeError(f"formal baseline differs: {scene}")
        rows[scene] = {
            "batch_result_path": str(batch_path.resolve()),
            "batch_result_sha256": sha(batch_path),
            "paper_gate_path": str(gate_path.resolve()),
            "paper_gate_sha256": sha(gate_path),
            "run_state_path": str(state_path.resolve()),
            "run_state_sha256": sha(state_path),
        }
    atomic(output, {
        "schema_version": "mysql_algorithm_c_scene_independent_formal_r0_set_v2",
        "status": "PASS",
        "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE.resolve()),
        "freeze_sha256": freeze_sha,
        "scenes": rows,
    })
    print(json.dumps({"status": "PASS", "path": str(output), "sha256": sha(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
