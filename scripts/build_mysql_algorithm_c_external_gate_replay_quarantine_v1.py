#!/usr/bin/env python3
"""Soft-quarantine completed candidate waves from an external-gate abort."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_mysql_algorithm_c_replay_quarantine_v4 import SCENES, key


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def binding(path: Path) -> dict[str, str]:
    path = path.resolve()
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular evidence required: {path}")
    return {"path": str(path), "sha256": sha(path)}


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists() or path.parent.exists():
        raise RuntimeError(f"fresh output directory required: {path.parent}")
    path.parent.mkdir(parents=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=SCENES, required=True)
    parser.add_argument("--prior-quarantine", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--failed-state", type=Path, required=True)
    parser.add_argument("--predicted-rankings", type=Path, required=True)
    parser.add_argument("--pass-batch", type=Path, action="append", default=[])
    parser.add_argument("--blocked-gate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    scene = args.scene
    paths = {
        "prior_quarantine": args.prior_quarantine.resolve(),
        "checkpoint": args.checkpoint.resolve(),
        "failed_state": args.failed_state.resolve(),
        "predicted_rankings": args.predicted_rankings.resolve(),
        "blocked_gate": args.blocked_gate.resolve(),
    }
    pass_paths = [path.resolve() for path in args.pass_batch]
    prior = load(paths["prior_quarantine"])
    checkpoint = load(paths["checkpoint"])
    state = load(paths["failed_state"])
    ranking = load(paths["predicted_rankings"])
    gate = load(paths["blocked_gate"])
    completed = int(checkpoint.get("completed_outer_attempts") or -1)
    failed_outer = completed + 1
    selected = list((ranking.get("selected_top_k") or {}).get(scene) or [])
    samples = list(gate.get("samples") or [])
    if (
        prior.get("schema_version") != "mysql_algorithm_c_replay_quarantine_v6"
        or prior.get("status") != "PASS"
        or checkpoint.get("schema_version")
        != "mysql_algorithm_c_single_scene_outer_resume_checkpoint_v19"
        or checkpoint.get("scene") != scene
        or checkpoint.get("freeze_sha256") != prior.get("freeze_sha256")
        or state.get("status") != "FAILED_CLOSED"
        or state.get("error") != "RuntimeError: dynamic non-interference gate failed closed"
        or int(state.get("outer_attempt") or -1) != completed
        or list(state.get("active_scenes") or []) != [scene]
        or int(ranking.get("outer_attempt") or -1) != failed_outer
        or list(ranking.get("active_decision_scenes") or []) != [scene]
        or not selected
        or gate.get("status") != "BLOCKED_FAIL_CLOSED"
        or "dynamic_noninterference_window_timeout" not in set(gate.get("errors") or [])
        or not samples
        or any(
            (sample.get("target_local_resource_proof") or {}).get("passed") is not True
            for sample in samples
        )
        or not any(sample.get("blocking_external_workloads") for sample in samples)
    ):
        raise RuntimeError("external-gate replay quarantine authority differs")

    observed: list[dict[str, Any]] = []
    for batch_number, path in enumerate(pass_paths, 1):
        batch = load(path)
        measurements = dict(batch.get("measurements") or {})
        if (
            batch.get("status") != "PASS"
            or batch.get("failures") not in ([], None)
            or not measurements
            or any(
                row.get("scene") != scene
                or row.get("hard_gate_passed") is not True
                or row.get("coverage_complete") is not True
                for row in measurements.values()
            )
        ):
            raise RuntimeError(f"completed aborted batch differs: {path}")
        for row in measurements.values():
            physical_rank = int(row["rank"])
            params = dict(row.get("params") or {})
            matches = [
                selected_row for selected_row in selected
                if params == dict(selected_row.get("optimized_params") or {})
            ]
            if len(matches) != 1:
                raise RuntimeError("aborted measurement/ranking identity differs")
            selected_row = matches[0]
            observed.append({
                "rank": int(selected_row["rank"]),
                "physical_rank": physical_rank,
                "batch": batch_number,
                "params": params,
                "optimizer_params_key": key(params),
            })
    if len({row["optimizer_params_key"] for row in observed}) != len(observed):
        raise RuntimeError("aborted valid replay identities are not unique")

    rows_by_scene = copy.deepcopy(prior["rows_by_scene"])
    all_by_scene = copy.deepcopy(prior["quarantined_param_keys_by_scene"])
    soft_by_scene = copy.deepcopy(prior["soft_replayed_param_keys_by_scene"])
    permanent_by_scene = copy.deepcopy(
        prior["permanent_invalid_param_keys_by_scene"]
    )
    row_by_key = {
        str(row["optimizer_params_key"]): row for row in rows_by_scene[scene]
    }
    for observed_row in observed:
        param_key = observed_row["optimizer_params_key"]
        observation = {
            "source_run": paths["failed_state"].parent.name,
            "source_outer_attempt": failed_outer,
            "original_rank": observed_row["rank"],
            "physical_rank": observed_row["physical_rank"],
            "batch": observed_row["batch"],
            "outcome": "VALID_REPLAY_EXTERNAL_GATE_ABORT_BEFORE_DECISION",
            "formal_state_mutation": False,
        }
        if param_key in permanent_by_scene[scene]:
            raise RuntimeError("permanent-invalid point was remeasured")
        if param_key in row_by_key:
            row_by_key[param_key].setdefault("replay_observations", []).append(
                observation
            )
            continue
        row = {
            "replay_position": len(all_by_scene[scene]) + 1,
            "source_outer_attempt": failed_outer,
            "original_rank": observed_row["rank"],
            "optimized_params": observed_row["params"],
            "optimizer_params_key": param_key,
            "outcome": observation["outcome"],
            "formal_state_mutation": False,
            "replay_observations": [observation],
        }
        rows_by_scene[scene].append(row)
        row_by_key[param_key] = row
        all_by_scene[scene].append(param_key)
        soft_by_scene[scene].append(param_key)

    evidence = copy.deepcopy(prior.get("evidence") or {})
    new_evidence = {
        **paths,
        **{f"pass_batch_{i}": path for i, path in enumerate(pass_paths, 1)},
    }
    prefix = f"{scene}_outer_{failed_outer}_external_gate_abort"
    evidence.update({
        f"{prefix}_{name}": binding(path)
        for name, path in new_evidence.items()
    })
    document = {
        **prior,
        "created_at": datetime.now().astimezone().isoformat(),
        "resume_checkpoint_outer_attempt": completed,
        "quarantined_counts_by_scene": {
            name: len(all_by_scene[name]) for name in SCENES
        },
        "soft_replayed_counts_by_scene": {
            name: len(soft_by_scene[name]) for name in SCENES
        },
        "permanent_invalid_counts_by_scene": {
            name: len(permanent_by_scene[name]) for name in SCENES
        },
        "quarantined_param_keys_by_scene": all_by_scene,
        "soft_replayed_param_keys_by_scene": soft_by_scene,
        "permanent_invalid_param_keys_by_scene": permanent_by_scene,
        "rows_by_scene": rows_by_scene,
        "latest_source_run": paths["failed_state"].parent.name,
        "latest_external_gate_aborted_valid_replays": len(observed),
        "external_gate_abort_state_credit": 0,
        "evidence": evidence,
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": sha(Path(__file__).resolve()),
    }
    atomic(args.output.resolve(), document)
    print(json.dumps({
        "status": "PASS", "output": str(args.output.resolve()),
        "scene": scene, "completed_outer_attempts": completed,
        "new_soft_replays": len(observed),
        "soft_count": len(soft_by_scene[scene]),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
