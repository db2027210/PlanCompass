#!/usr/bin/env python3
"""Accumulate one mixed TPC-H timeout wave without formal state credit."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v19 as checkpoint_v19
from scripts import build_mysql_algorithm_c_tpc_timeout_quarantine_v6 as v6
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


SCENE = v6.SCENE
load = v6.load
sha = v6.sha
bound = v6.bound
atomic = v6.atomic


def build(
    checkpoint_path: Path, inherited_path: Path, source_root: Path
) -> dict[str, Any]:
    checkpoint_path = checkpoint_path.resolve()
    inherited_path = inherited_path.resolve()
    source_root = source_root.resolve()
    checkpoint = checkpoint_v19.verify_checkpoint(checkpoint_path)
    inherited = load(inherited_path)
    state_path = source_root / "run_state.json"
    state = load(state_path)
    outer_root = source_root / "outer_011"
    ranking_path = outer_root / "predicted_rankings.json"
    ranking = load(ranking_path)
    batch_path = outer_root / "top5_batch_1/batch_result.json"
    gate_path = outer_root / "top5_batch_1_dynamic_noninterference_gate.json"
    gate = load(gate_path)
    selected = (ranking.get("selected_top_k") or {}).get(SCENE) or []
    prior_rows = list((inherited.get("rows_by_scene") or {}).get(SCENE) or [])
    prior_all = list(
        (inherited.get("quarantined_param_keys_by_scene") or {}).get(SCENE) or []
    )
    prior_soft = list(
        (inherited.get("soft_replayed_param_keys_by_scene") or {}).get(SCENE) or []
    )
    prior_permanent = list(
        (inherited.get("permanent_invalid_param_keys_by_scene") or {}).get(SCENE) or []
    )
    if (
        checkpoint.get("scene") != SCENE
        or int(checkpoint.get("completed_outer_attempts") or 0) != 10
        or inherited.get("schema_version") != "mysql_algorithm_c_replay_quarantine_v6"
        or inherited.get("status") != "PASS"
        or int(inherited.get("resume_checkpoint_outer_attempt") or 0) != 10
        or inherited.get("formal_state_mutation") is not False
        or len(prior_rows) != 25
        or len(prior_all) != 25
        or len(prior_soft) != 8
        or len(prior_permanent) != 17
        or set(prior_permanent).intersection(prior_soft)
        or set(prior_permanent) | set(prior_soft) != set(prior_all)
        or state.get("status") != "FAILED_CLOSED"
        or state.get("error")
        != "RuntimeError: dynamic non-interference gate failed closed"
        or int(state.get("outer_attempt") or -1) != 10
        or int(ranking.get("outer_attempt") or 0) != 11
        or len(selected) != 5
        or (outer_root / f"{SCENE}.decision.json").exists()
        or gate.get("status") != "PASS"
        or gate.get("errors") not in ([], None)
        or gate.get("external_process_control_or_stop_performed") is not False
        or gate.get("postgresql_process_control_or_stop_performed") is not False
        or gate.get("cross_scene_completion_dependency") is not False
    ):
        raise RuntimeError("TPC-H fifth timeout quarantine authority differs")
    for name, raw in dict(inherited.get("evidence") or {}).items():
        path = Path(str((raw or {}).get("path") or "")).resolve()
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"inherited quarantine evidence differs: {name}")

    by_rank = {int(row["rank"]): dict(row["optimized_params"]) for row in selected}
    if set(by_rank) != set(range(1, 6)):
        raise RuntimeError("TPC-H selected Top-5 ranks differ")
    new_rows = v6.validate_mixed_batch(
        batch_path,
        timeout_worker="tpch_w1",
        timeout_rank=1,
        timeout_params=by_rank[1],
        valid_worker="tpch_w2",
        valid_rank=2,
        valid_params=by_rank[2],
    )
    new_rows.sort(key=lambda row: int(row["rank"]))
    new_keys = [str(row["optimizer_params_key"]) for row in new_rows]
    if len(set(new_keys)) != 2 or set(new_keys).intersection(prior_all):
        raise RuntimeError("TPC-H fifth timeout replay identities differ")

    scenes = tuple((inherited.get("rows_by_scene") or {}).keys())
    rows_by_scene = copy.deepcopy(inherited["rows_by_scene"])
    all_by_scene = copy.deepcopy(inherited["quarantined_param_keys_by_scene"])
    soft_by_scene = copy.deepcopy(inherited["soft_replayed_param_keys_by_scene"])
    permanent_by_scene = copy.deepcopy(
        inherited["permanent_invalid_param_keys_by_scene"]
    )
    rows_by_scene[SCENE] = prior_rows + new_rows
    all_by_scene[SCENE] = prior_all + new_keys
    permanent_by_scene[SCENE] = prior_permanent + [new_keys[0]]
    soft_by_scene[SCENE] = prior_soft + [new_keys[1]]
    if (
        len(set(all_by_scene[SCENE])) != 27
        or len(set(permanent_by_scene[SCENE])) != 18
        or len(set(soft_by_scene[SCENE])) != 9
        or set(permanent_by_scene[SCENE]).intersection(soft_by_scene[SCENE])
        or set(permanent_by_scene[SCENE]) | set(soft_by_scene[SCENE])
        != set(all_by_scene[SCENE])
    ):
        raise RuntimeError("TPC-H fifth timeout replay tiers differ")

    evidence = copy.deepcopy(inherited.get("evidence") or {})
    evidence.update({
        "checkpoint": bound(checkpoint_path),
        "inherited_quarantine": bound(inherited_path),
        "fifth_outer_11_failed_source_state": bound(state_path),
        "fifth_outer_11_predicted_rankings": bound(ranking_path),
        "fifth_outer_11_batch_1": bound(batch_path),
        "fifth_outer_11_batch_1_gate": bound(gate_path),
    })
    document = copy.deepcopy(inherited)
    document.update({
        "created_at": datetime.now().astimezone().isoformat(),
        "resume_checkpoint_outer_attempt": 10,
        "formal_state_mutation": False,
        "accepted_rounds_mutation": False,
        "rejected_rounds_mutation": False,
        "interval_convergence_mutation": False,
        "quarantined_counts_by_scene": {
            scene: len(all_by_scene[scene]) for scene in scenes
        },
        "soft_replayed_counts_by_scene": {
            scene: len(soft_by_scene[scene]) for scene in scenes
        },
        "permanent_invalid_counts_by_scene": {
            scene: len(permanent_by_scene[scene]) for scene in scenes
        },
        "quarantined_param_keys_by_scene": all_by_scene,
        "soft_replayed_param_keys_by_scene": soft_by_scene,
        "permanent_invalid_param_keys_by_scene": permanent_by_scene,
        "rows_by_scene": rows_by_scene,
        "cycle_reset_may_clear_soft_replayed": True,
        "cycle_reset_may_clear_permanent_invalid": False,
        "initialization": "eighteen_tpch_timeouts_plus_nine_valid_zero_credit_replays",
        "evidence": evidence,
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": sha(Path(__file__).resolve()),
    })
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--inherited-quarantine", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.output_dir.name) is None:
        parser.error("unsafe output directory")
    if args.output_dir.exists() or args.output_dir.is_symlink():
        raise RuntimeError("fresh quarantine output required")
    document = build(
        args.checkpoint, args.inherited_quarantine, args.source_root
    )
    args.output_dir.mkdir(parents=True, mode=0o700)
    output = args.output_dir / "replay_quarantine.json"
    atomic(output, document)
    print(json.dumps({
        "status": "PASS",
        "output": str(output),
        "permanent_invalid_candidates": 18,
        "soft_replayed_candidates": 9,
        "sha256": sha(output),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
