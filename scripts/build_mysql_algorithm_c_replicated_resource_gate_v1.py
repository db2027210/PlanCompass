#!/usr/bin/env python3
"""Build a fail-closed mean resource gate from complete same-freeze baselines."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
from statistics import fmean
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TARGETS = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_paper_baseline_20260920_v121/"
    "baseline_targets.json"
)
METRICS = ("cpu", "memory", "io")


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
    parser.add_argument("--scene", required=True)
    parser.add_argument("--run", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if len(args.run) < 2:
        parser.error("at least two complete baseline runs are required")
    output = args.output.expanduser().resolve()
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh replicated gate output required: {output}")
    targets = load(TARGETS)
    target = dict((targets.get("scenes") or {}).get(args.scene) or {})
    if set(target) != set(METRICS):
        raise RuntimeError("paper target scene differs")
    rows = []
    freeze_sha: str | None = None
    for raw_run in args.run:
        run = raw_run.expanduser().resolve()
        state_path = run / "run_state.json"
        gate_path = run / "paper_baseline_resource_gate.json"
        batch_path = run / "formal_incumbent_baseline/batch_result.json"
        if not all(path.is_file() and not path.is_symlink()
                   for path in (state_path, gate_path, batch_path)):
            raise RuntimeError(f"regular complete baseline artifacts required: {run}")
        state, gate, batch = map(load, (state_path, gate_path, batch_path))
        current_freeze = str(state.get("freeze_sha256") or "")
        if freeze_sha is None:
            freeze_sha = current_freeze
        if (
            not current_freeze or current_freeze != freeze_sha
            or state.get("status") not in {"COMPLETE", "FAILED_CLOSED"}
            or int((state.get("accepted_rounds") or {}).get(args.scene, 0)) != 0
            or batch.get("status") != "PASS"
            or batch.get("scene") != args.scene
            or batch.get("formal_scope") != "mysql_only"
            or batch.get("cross_engine_comparison_prohibited") is not True
            or gate.get("formal_scope") != "mysql_only"
            or gate.get("cross_engine_comparison_prohibited") is not True
            or gate.get("target_sha256") != sha(TARGETS)
            or args.scene not in (gate.get("scenes") or {})
        ):
            raise RuntimeError(f"replicated baseline lineage differs: {run}")
        scene_gate = dict(gate["scenes"][args.scene])
        if set(scene_gate) != set(METRICS):
            raise RuntimeError(f"replicated resource metrics differ: {run}")
        rows.append({
            "run": str(run),
            "run_state_path": str(state_path),
            "run_state_sha256": sha(state_path),
            "batch_result_path": str(batch_path),
            "batch_result_sha256": sha(batch_path),
            "single_run_gate_path": str(gate_path),
            "single_run_gate_sha256": sha(gate_path),
            "single_run_gate_status": gate.get("status"),
            "observed_percent": {
                metric: float(scene_gate[metric]["observed_percent"])
                for metric in METRICS
            },
        })
    aggregate = {}
    failures = []
    for metric in METRICS:
        values = [row["observed_percent"][metric] for row in rows]
        observed = fmean(values)
        target_percent = float(target[metric]) * 100.0
        delta = observed - target_percent
        passed = abs(delta) <= 1.0
        aggregate[metric] = {
            "replicate_observed_percent": values,
            "replicate_count": len(values),
            "mean_observed_percent": observed,
            "target_percent": target_percent,
            "delta_percentage_points": delta,
            "passed": passed,
            "replicate_range_percentage_points": max(values) - min(values),
        }
        if not passed:
            failures.append(f"{args.scene}.{metric}: {delta:+.4f} percentage points")
    value = {
        "schema_version": "mysql_algorithm_c_replicated_paper_baseline_gate_v1",
        "status": "PASS" if not failures else "FAILED_CLOSED",
        "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": args.scene,
        "freeze_sha256": freeze_sha,
        "aggregation": "equal_weight_arithmetic_mean_of_complete_baseline_runs",
        "tolerance_percentage_points": 1.0,
        "individual_run_failures_are_retained": True,
        "replicate_spread_is_reported_not_hidden": True,
        "paper_targets_path": str(TARGETS),
        "paper_targets_sha256": sha(TARGETS),
        "replicates": rows,
        "resources": aggregate,
        "failures": failures,
        "candidate_search_authorized": not failures,
    }
    atomic(output, value)
    print(json.dumps({"status": value["status"], "path": str(output), "sha256": sha(output)}))
    return 0 if not failures else 4


if __name__ == "__main__":
    raise SystemExit(main())
