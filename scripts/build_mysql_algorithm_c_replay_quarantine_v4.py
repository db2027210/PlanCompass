#!/usr/bin/env python3
"""Accumulate v6 pass/timeout evidence into tiered replay quarantine v4."""

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
PERMANENT_TIMEOUT_SCENES = {
    "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io",
}


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
    parser.add_argument("--pass-batch", type=Path, required=True)
    parser.add_argument("--failed-batch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = {
        name: getattr(args, name).expanduser().resolve()
        for name in (
            "prior_quarantine", "failed_state", "predicted_rankings",
            "pass_batch", "failed_batch",
        )
    }
    prior = load(paths["prior_quarantine"])
    state = load(paths["failed_state"])
    rankings = load(paths["predicted_rankings"])
    passed = load(paths["pass_batch"])
    failed = load(paths["failed_batch"])
    selected = rankings.get("selected_top_k") or {}
    prior_rows = prior.get("rows_by_scene") or {}
    prior_all = prior.get("quarantined_param_keys_by_scene") or {}
    prior_soft = prior.get("soft_replayed_param_keys_by_scene") or {}
    prior_permanent = prior.get("permanent_invalid_param_keys_by_scene") or {}
    timeout_tasks = {
        str(row.get("task")) for row in (failed.get("failures") or [])
        if "timed out after 900 seconds" in str(row.get("error") or "")
    }
    expected_timeout_tasks = {
        "pair_1", "pair_2", "xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2",
    }
    if (
        prior.get("schema_version") != "mysql_algorithm_c_replay_quarantine_v3"
        or prior.get("status") != "PASS"
        or prior.get("freeze_sha256") != sha(FREEZE)
        or prior.get("formal_state_mutation") is not False
        or any(set(mapping) != set(SCENES) for mapping in (
            prior_rows, prior_all, prior_soft, prior_permanent,
        ))
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("outer_attempt") != 2
        or state.get("freeze_sha256") != sha(FREEZE)
        or "outer 3 batch 2 failed closed" not in str(state.get("error") or "")
        or rankings.get("outer_attempt") != 3
        or rankings.get("fixed_top_k") != 5
        or set(selected) != set(SCENES)
        or any(len(selected[scene]) != 5 for scene in SCENES)
        or passed.get("status") != "PASS"
        or passed.get("failures") not in ([], None)
        or len(passed.get("measurements") or {}) != 8
        or any(
            row.get("rank") not in (1, 2)
            or row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            for row in (passed.get("measurements") or {}).values()
        )
        or failed.get("status") != "FAILED_CLOSED"
        or float(failed.get("elapsed_seconds") or 0) <= 900.0
        or timeout_tasks != expected_timeout_tasks
        or len(failed.get("measurements") or {}) != 8
        or any(
            row.get("rank") not in (3, 4)
            or row.get("hard_gate_passed") is not False
            or row.get("coverage_complete") is not False
            for row in (failed.get("measurements") or {}).values()
        )
    ):
        raise RuntimeError("v4 replay quarantine authority differs")

    rows_by_scene: dict[str, list[dict[str, Any]]] = {}
    all_by_scene: dict[str, list[str]] = {}
    soft_by_scene: dict[str, list[str]] = {}
    permanent_by_scene: dict[str, list[str]] = {}
    for scene in SCENES:
        rows = [dict(row) for row in prior_rows[scene]]
        all_keys = list(prior_all[scene])
        soft_keys = list(prior_soft[scene])
        permanent_keys = list(prior_permanent[scene])
        if (
            [row.get("optimizer_params_key") for row in rows] != all_keys
            or set(soft_keys).intersection(permanent_keys)
            or set(soft_keys) | set(permanent_keys) != set(all_keys)
        ):
            raise RuntimeError(f"{scene} prior tiered rows differ")
        row_by_key = {str(row["optimizer_params_key"]): row for row in rows}
        for replay_position, selected_row in enumerate(selected[scene][:4], 1):
            params = dict(selected_row.get("optimized_params") or {})
            param_key = key(params)
            permanent = (
                replay_position in (3, 4)
                and scene in PERMANENT_TIMEOUT_SCENES
            )
            observation = {
                "source_run": paths["failed_state"].parent.name,
                "source_outer_attempt": 3,
                "replay_position": replay_position,
                "original_rank": int(selected_row["rank"]),
                "batch": 1 if replay_position <= 2 else 2,
                "outcome": (
                    "FORMAL_WALL_TIMEOUT_INVALID_MEASUREMENT"
                    if permanent
                    else (
                        "VALID_REPLAY_OUTER_ABORTED_BEFORE_DECISION"
                        if replay_position <= 2
                        else "INCOMPLETE_HARD_GATE_OUTER_ABORTED_BEFORE_DECISION"
                    )
                ),
                "formal_state_mutation": False,
            }
            if param_key not in row_by_key:
                row = {
                    "replay_position": len(all_keys) + 1,
                    "source_outer_attempt": 3,
                    "original_rank": int(selected_row["rank"]),
                    "optimized_params": params,
                    "optimizer_params_key": param_key,
                    "outcome": observation["outcome"],
                    "formal_state_mutation": False,
                    "replay_observations": [observation],
                }
                rows.append(row)
                row_by_key[param_key] = row
                all_keys.append(param_key)
                (permanent_keys if permanent else soft_keys).append(param_key)
            else:
                row = row_by_key[param_key]
                row.setdefault("replay_observations", []).append(observation)
                if permanent:
                    if param_key in permanent_keys:
                        pass
                    elif param_key in soft_keys:
                        soft_keys.remove(param_key)
                        permanent_keys.append(param_key)
                        row["outcome"] = "FORMAL_WALL_TIMEOUT_INVALID_MEASUREMENT"
                        row["promoted_from_soft_replay"] = True
                    else:
                        raise RuntimeError(f"{scene} existing replay tier differs")
                elif param_key not in soft_keys:
                    raise RuntimeError(
                        f"{scene} permanently invalid point was replayed"
                    )
        if (
            len(set(all_keys)) != len(all_keys)
            or len(set(soft_keys)) != len(soft_keys)
            or len(set(permanent_keys)) != len(permanent_keys)
            or set(soft_keys).intersection(permanent_keys)
            or set(soft_keys) | set(permanent_keys) != set(all_keys)
            or [row.get("optimizer_params_key") for row in rows] != all_keys
        ):
            raise RuntimeError(f"{scene} v4 replay tiers differ")
        rows_by_scene[scene] = rows
        all_by_scene[scene] = all_keys
        soft_by_scene[scene] = soft_keys
        permanent_by_scene[scene] = permanent_keys

    document = {
        **prior,
        "schema_version": "mysql_algorithm_c_replay_quarantine_v4",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "quarantined_counts_by_scene": {
            scene: len(all_by_scene[scene]) for scene in SCENES
        },
        "soft_replayed_counts_by_scene": {
            scene: len(soft_by_scene[scene]) for scene in SCENES
        },
        "permanent_invalid_counts_by_scene": {
            scene: len(permanent_by_scene[scene]) for scene in SCENES
        },
        "quarantined_param_keys_by_scene": all_by_scene,
        "soft_replayed_param_keys_by_scene": soft_by_scene,
        "permanent_invalid_param_keys_by_scene": permanent_by_scene,
        "cycle_reset_may_clear_soft_replayed": True,
        "cycle_reset_may_clear_permanent_invalid": False,
        "latest_source_run": paths["failed_state"].parent.name,
        "rows_by_scene": rows_by_scene,
        "evidence": {
            **dict(prior.get("evidence") or {}),
            **{
                f"v6_{name}": {"path": str(path), "sha256": sha(path)}
                for name, path in paths.items()
                if name != "prior_quarantine"
            },
            "v3_prior_quarantine": {
                "path": str(paths["prior_quarantine"]),
                "sha256": sha(paths["prior_quarantine"]),
            },
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
        "soft_counts": document["soft_replayed_counts_by_scene"],
        "permanent_counts": document["permanent_invalid_counts_by_scene"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
