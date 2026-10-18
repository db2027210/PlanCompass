#!/usr/bin/env python3
"""Seal replayed candidates from a failed outer without mutating formal state."""

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
SCENES = (
    "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular non-symlink JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def key(params: Mapping[str, Any]) -> str:
    return json.dumps(
        {name: float(params[name]) for name in sorted(params)},
        sort_keys=True, separators=(",", ":"),
    )


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=False)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failed-state", type=Path, required=True)
    parser.add_argument("--predicted-rankings", type=Path, required=True)
    parser.add_argument("--pass-batch", type=Path, required=True)
    parser.add_argument("--failed-batch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = {
        name: path.expanduser().resolve()
        for name, path in {
            "failed_state": args.failed_state,
            "predicted_rankings": args.predicted_rankings,
            "pass_batch": args.pass_batch,
            "failed_batch": args.failed_batch,
        }.items()
    }
    state = load(paths["failed_state"])
    rankings = load(paths["predicted_rankings"])
    pass_batch = load(paths["pass_batch"])
    failed_batch = load(paths["failed_batch"])
    freeze_sha = sha(FREEZE)
    failures = failed_batch.get("failures") or []
    timeout_tasks = {
        str(row.get("task")) for row in failures
        if "timed out after 900 seconds" in str(row.get("error") or "")
    }
    selected = rankings.get("selected_top_k") or {}
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("outer_attempt") != 2
        or state.get("freeze_sha256") != freeze_sha
        or rankings.get("outer_attempt") != 3
        or rankings.get("fixed_top_k") != 5
        or set(selected) != set(SCENES)
        or any(len(selected[scene]) != 5 for scene in SCENES)
        or pass_batch.get("status") != "PASS"
        or len(pass_batch.get("measurements") or {}) != 8
        or failed_batch.get("status") != "FAILED_CLOSED"
        or float(failed_batch.get("elapsed_seconds") or 0) <= 900.0
        or timeout_tasks
        != {"pair_1", "pair_2", "xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"}
    ):
        raise RuntimeError("failed-outer replay quarantine authority differs")
    rows_by_scene: dict[str, list[dict[str, Any]]] = {}
    keys_by_scene: dict[str, list[str]] = {}
    for scene in SCENES:
        rows = []
        keys = []
        for replay_position, row in enumerate(selected[scene][:4], 1):
            params = row.get("optimized_params") or {}
            param_key = key(params)
            rows.append({
                "replay_position": replay_position,
                "original_rank": int(row["rank"]),
                "optimized_params": params,
                "optimizer_params_key": param_key,
                "outcome": (
                    "VALID_REPLAY_OUTER_ABORTED_BEFORE_DECISION"
                    if replay_position <= 2
                    else "FORMAL_WALL_TIMEOUT_INVALID_MEASUREMENT"
                ),
                "formal_state_mutation": False,
            })
            keys.append(param_key)
        if len(set(keys)) != 4:
            raise RuntimeError(f"{scene} replay quarantine keys are not distinct")
        rows_by_scene[scene] = rows
        keys_by_scene[scene] = keys
    output = args.output.expanduser().resolve()
    if output.exists() or output.parent.exists():
        raise RuntimeError(f"fresh output directory required: {output.parent}")
    document = {
        "schema_version": "mysql_algorithm_c_replay_quarantine_v1",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE),
        "freeze_sha256": freeze_sha,
        "source_outer_attempt": 3,
        "resume_checkpoint_outer_attempt": 2,
        "formal_state_mutation": False,
        "accepted_rounds_mutation": False,
        "rejected_rounds_mutation": False,
        "interval_convergence_mutation": False,
        "purpose": "avoid exact physical replay of candidates already executed before failed outer decision",
        "quarantined_param_keys_by_scene": keys_by_scene,
        "rows_by_scene": rows_by_scene,
        "evidence": {
            name: {"path": str(path), "sha256": sha(path)}
            for name, path in paths.items()
        },
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": sha(Path(__file__).resolve()),
    }
    atomic(output, document)
    print(json.dumps({
        "status": "PASS", "output": str(output),
        "quarantined_per_scene": 4,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
