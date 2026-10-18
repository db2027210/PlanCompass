#!/usr/bin/env python3
"""Print one concise status row for every formal double-loop matrix run."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_ROOT = Path("/disk2/resource_adaptive_experiment_matrix_20260718")


def run_id(vm: Dict[str, Any]) -> str:
    return (
        f"{str(vm['exp_id']).lower()}_r{int(vm['repeat_id'])}_"
        f"{vm['benchmark']}_{vm['target']}"
    )


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, PermissionError):
        return False
    return True


def launch_pid(root: Path, launch_id: str) -> Tuple[Optional[int], bool]:
    path = root / "launch" / f"{launch_id}.pid"
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError, OSError):
        return None, False
    return pid, process_alive(pid)


def candidate_attempts(root: Path, identity: str) -> Iterable[Path]:
    formal_root = root / "formal_runs"
    yield from formal_root.glob(identity)
    yield from formal_root.glob(f"{identity}_*")


def choose_attempt(root: Path, identity: str) -> Optional[Path]:
    attempts = [path for path in candidate_attempts(root, identity) if path.is_dir()]
    if not attempts:
        return None
    live: List[Path] = []
    for path in attempts:
        _, alive = launch_pid(root, path.name)
        if alive:
            live.append(path)
    pool = live or attempts
    return max(pool, key=lambda path: (path.stat().st_mtime, path.name))


def count_jsonl_events(path: Path, event: str) -> int:
    count = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("event") == event:
            count += 1
    return count


def status_row(root: Path, vm: Dict[str, Any]) -> Dict[str, Any]:
    identity = run_id(vm)
    attempt = choose_attempt(root, identity)
    base = {
        "run_id": identity,
        "exp_id": vm["exp_id"],
        "repeat_id": vm["repeat_id"],
        "benchmark": vm["benchmark"],
        "target": vm["target"],
        "attempt": attempt.name if attempt else None,
        "running": False,
        "phase": "not_started",
        "real_replays": 0,
        "outer_rounds": 0,
        "baseline_qps": None,
        "baseline_target": None,
        "error": "",
    }
    if attempt is None:
        return base
    _, base["running"] = launch_pid(root, attempt.name)
    scenario_dir = attempt / str(vm["target"])
    validations = scenario_dir / "real_validations"
    base["real_replays"] = len(
        list(validations.glob(f"{vm['target']}_double_loop_*.json"))
    )
    base["outer_rounds"] = count_jsonl_events(
        scenario_dir / "optimizer.jsonl", "double_loop_grid_round"
    )
    baseline = read_json(scenario_dir / "fresh_baseline.json")
    if baseline:
        measurement = baseline.get("measurement") or {}
        base["baseline_qps"] = measurement.get("db_qps")
        base["baseline_target"] = (
            measurement.get(vm["target"]) if vm["target"] != "none" else None
        )
    summary = read_json(scenario_dir / "scenario_summary.json")
    if summary:
        base["phase"] = str(summary.get("status") or "finished")
        base["error"] = str(summary.get("error") or "")
        return base
    gate = read_json(scenario_dir / "fresh_model_quality_gate.json")
    if gate and gate.get("optimizer_started"):
        base["phase"] = "optimizer"
    elif gate:
        base["phase"] = "model_gate"
    elif baseline:
        base["phase"] = "calibration"
    else:
        base["phase"] = "baseline"
    return base


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    manifest = read_json(args.root / "vm_manifest.json")
    if not manifest:
        raise RuntimeError(f"missing matrix manifest under {args.root}")
    rows = [status_row(args.root, vm) for vm in manifest.get("vms") or []]
    if args.json:
        print(json.dumps({"rows": rows}, ensure_ascii=False, indent=2), flush=True)
        return 0
    print(
        "run_id\tphase\trunning\treplays\trounds\tbaseline_qps\tbaseline_target\terror",
        flush=True,
    )
    for row in rows:
        print(
            "\t".join(
                str(value if value is not None else "")
                for value in (
                    row["run_id"],
                    row["phase"],
                    row["running"],
                    row["real_replays"],
                    row["outer_rounds"],
                    row["baseline_qps"],
                    row["baseline_target"],
                    row["error"],
                )
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
