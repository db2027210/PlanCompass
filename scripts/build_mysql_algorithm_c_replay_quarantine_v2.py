#!/usr/bin/env python3
"""Accumulate exact replay keys from a failed Outer-3 without state mutation."""

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
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior-quarantine", type=Path, required=True)
    parser.add_argument("--failed-state", type=Path, required=True)
    parser.add_argument("--predicted-rankings", type=Path, required=True)
    parser.add_argument("--pass-batch", type=Path, action="append", required=True)
    parser.add_argument("--failed-batch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = {
        "prior_quarantine": args.prior_quarantine.expanduser().resolve(),
        "failed_state": args.failed_state.expanduser().resolve(),
        "predicted_rankings": args.predicted_rankings.expanduser().resolve(),
        "failed_batch": args.failed_batch.expanduser().resolve(),
    }
    pass_paths = [path.expanduser().resolve() for path in args.pass_batch]
    prior = load(paths["prior_quarantine"])
    state = load(paths["failed_state"])
    rankings = load(paths["predicted_rankings"])
    failed = load(paths["failed_batch"])
    passed = [load(path) for path in pass_paths]
    freeze_sha = sha(FREEZE)
    selected = rankings.get("selected_top_k") or {}
    prior_keys = prior.get("quarantined_param_keys_by_scene") or {}
    timeout_tasks = {
        str(row.get("task")) for row in (failed.get("failures") or [])
        if "timed out after 900 seconds" in str(row.get("error") or "")
    }
    if (
        prior.get("status") != "PASS"
        or prior.get("freeze_sha256") != freeze_sha
        or prior.get("resume_checkpoint_outer_attempt") != 2
        or prior.get("formal_state_mutation") is not False
        or set(prior_keys) != set(SCENES)
        or any(
            not isinstance(prior_keys[scene], list)
            or len(prior_keys[scene]) < 1
            or len(set(prior_keys[scene])) != len(prior_keys[scene])
            for scene in SCENES
        )
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("outer_attempt") != 2
        or state.get("freeze_sha256") != freeze_sha
        or "outer 3 batch 3 failed closed" not in str(state.get("error") or "")
        or rankings.get("outer_attempt") != 3
        or rankings.get("fixed_top_k") != 5
        or set(selected) != set(SCENES)
        or any(len(selected[scene]) != 5 for scene in SCENES)
        or len(passed) != 2
        or any(
            batch.get("status") != "PASS"
            or len(batch.get("measurements") or {}) != 8
            or any(
                row.get("hard_gate_passed") is not True
                or row.get("coverage_complete") is not True
                for row in (batch.get("measurements") or {}).values()
            )
            for batch in passed
        )
        or failed.get("status") != "FAILED_CLOSED"
        or float(failed.get("elapsed_seconds") or 0) <= 900.0
        or timeout_tasks != {"pair_1", "xp_cpu_w1", "xp_io_w1"}
        or len(failed.get("measurements") or {}) != 4
    ):
        raise RuntimeError("cumulative replay quarantine authority differs")

    rows_by_scene: dict[str, list[dict[str, Any]]] = {}
    keys_by_scene: dict[str, list[str]] = {}
    for scene in SCENES:
        old_rows = list((prior.get("rows_by_scene") or {}).get(scene) or [])
        old_keys = list(prior_keys[scene])
        new_rows = []
        new_keys = []
        for replay_position, row in enumerate(selected[scene], 1):
            params = row.get("optimized_params") or {}
            param_key = key(params)
            new_rows.append({
                "replay_position": len(old_keys) + replay_position,
                "source_outer_attempt": 3,
                "original_rank": int(row["rank"]),
                "optimized_params": params,
                "optimizer_params_key": param_key,
                "outcome": (
                    "VALID_REPLAY_OUTER_ABORTED_BEFORE_DECISION"
                    if replay_position <= 4
                    else "FORMAL_WALL_TIMEOUT_INVALID_MEASUREMENT"
                ),
                "formal_state_mutation": False,
            })
            new_keys.append(param_key)
        combined = old_keys + new_keys
        if len(set(combined)) != len(combined):
            raise RuntimeError(f"{scene} cumulative replay keys overlap")
        rows_by_scene[scene] = old_rows + new_rows
        keys_by_scene[scene] = combined

    evidence_paths = {
        **paths,
        **{f"pass_batch_{index}": path for index, path in enumerate(pass_paths, 1)},
    }
    count = len(next(iter(keys_by_scene.values())))
    if any(len(keys) != count for keys in keys_by_scene.values()):
        raise RuntimeError("cumulative replay quarantine cardinality differs")
    document = {
        "schema_version": "mysql_algorithm_c_replay_quarantine_v2",
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
        "quarantined_per_scene": count,
        "purpose": "avoid exact replay of all candidates executed before failed outer decisions",
        "quarantined_param_keys_by_scene": keys_by_scene,
        "rows_by_scene": rows_by_scene,
        "evidence": {
            name: {"path": str(path), "sha256": sha(path)}
            for name, path in evidence_paths.items()
        },
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": sha(Path(__file__).resolve()),
    }
    output = args.output.expanduser().resolve()
    if output.exists() or output.parent.exists():
        raise RuntimeError(f"fresh output directory required: {output.parent}")
    atomic(output, document)
    print(json.dumps({
        "status": "PASS", "output": str(output),
        "quarantined_per_scene": count,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
