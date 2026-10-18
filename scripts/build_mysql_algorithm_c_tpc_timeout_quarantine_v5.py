#!/usr/bin/env python3
"""Accumulate the third TPC-H timeout wave without granting state credit."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v17 as checkpoint_v17
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


SCENE = "tpch_sf4_memory"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bound(path: Path) -> dict[str, str]:
    path = path.resolve()
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular evidence required: {path}")
    return {"path": str(path), "sha256": sha(path)}


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def validate_timeout_batch(
    path: Path, expected: dict[str, tuple[int, dict[str, Any]]]
) -> list[dict[str, Any]]:
    batch = load(path)
    measurements = batch.get("measurements") or {}
    failures = batch.get("failures") or []
    if batch.get("status") != "FAILED_CLOSED" or set(measurements) != set(expected):
        raise RuntimeError(f"TPC-H timeout batch differs: {path}")
    timed_out = {
        str(row.get("task")) for row in failures
        if str(row.get("task")) in expected
        and str(row.get("error") or "").startswith("TimeoutExpired:")
        and "timed out after 900 seconds" in str(row.get("error") or "")
    }
    skipped = {
        str(row.get("task")) for row in failures
        if str(row.get("error") or "") == "skipped_after_invalid_workload"
    }
    if timed_out != set(expected) or skipped != {
        f"{worker}_plan_capture" for worker in expected
    }:
        raise RuntimeError(f"TPC-H timeout failure set differs: {path}")
    rows = []
    for worker, (rank, params) in expected.items():
        measured = measurements[worker]
        if (
            int(measured.get("rank") or 0) != rank
            or measured.get("scene") != SCENE
            or measured.get("hard_gate_passed") is not False
            or measured.get("coverage_complete") is not False
            or alg.optimizer_params_key(measured.get("params") or {})
            != alg.optimizer_params_key(params)
        ):
            raise RuntimeError(f"TPC-H timeout measurement differs: {path}: {worker}")
        rows.append({
            "rank": rank,
            "optimized_params": copy.deepcopy(params),
            "optimizer_params_key": alg.optimizer_params_key(params),
            "classification": "TECHNICAL_TPCH_FULL_SUITE_TIMEOUT_EXCLUDED",
            "timeout_seconds": 900,
            "full_suite_coverage_credit": False,
            "accepted_state_credit_increment": 0,
            "rejected_outer_attempt_increment": 0,
            "interval_convergence_mutation": False,
            "replay_history_mutation": False,
            "pruning_evidence_eligible": False,
            "formal_state_mutation": False,
            "source_batch": bound(path),
        })
    return rows


def validate_pass_batch(
    path: Path, expected: dict[str, tuple[int, dict[str, Any]]]
) -> list[dict[str, Any]]:
    batch = load(path)
    measurements = batch.get("measurements") or {}
    if (
        batch.get("status") != "PASS"
        or batch.get("failures") not in ([], None)
        or set(measurements) != set(expected)
    ):
        raise RuntimeError("TPC-H valid replay batch differs")
    rows = []
    for worker, (rank, params) in expected.items():
        measured = measurements[worker]
        if (
            int(measured.get("rank") or 0) != rank
            or measured.get("scene") != SCENE
            or measured.get("hard_gate_passed") is not True
            or measured.get("coverage_complete") is not True
            or float(measured.get("db_qps") or 0.0) <= 0.0
            or alg.optimizer_params_key(measured.get("params") or {})
            != alg.optimizer_params_key(params)
        ):
            raise RuntimeError("TPC-H valid replay measurement differs")
        rows.append({
            "rank": rank,
            "optimized_params": copy.deepcopy(params),
            "optimizer_params_key": alg.optimizer_params_key(params),
            "classification": "VALID_REPLAY_OUTER_ABORTED_BEFORE_DECISION",
            "full_suite_coverage_credit": True,
            "accepted_state_credit_increment": 0,
            "rejected_outer_attempt_increment": 0,
            "interval_convergence_mutation": False,
            "replay_history_mutation": False,
            "pruning_evidence_eligible": False,
            "formal_state_mutation": False,
            "source_batch": bound(path),
        })
    return rows


def validate_mixed_batch(
    path: Path, *, timeout_worker: str, timeout_rank: int,
    timeout_params: dict[str, Any], valid_worker: str, valid_rank: int,
    valid_params: dict[str, Any],
) -> list[dict[str, Any]]:
    """Validate one failed physical wave containing one valid sibling replay."""
    batch = load(path)
    measurements = batch.get("measurements") or {}
    failures = batch.get("failures") or []
    if (
        batch.get("status") != "FAILED_CLOSED"
        or set(measurements) != {timeout_worker, valid_worker}
    ):
        raise RuntimeError("TPC-H mixed timeout batch differs")
    timed_out = {
        str(row.get("task")) for row in failures
        if str(row.get("error") or "").startswith("TimeoutExpired:")
        and "timed out after 900 seconds" in str(row.get("error") or "")
    }
    skipped = {
        str(row.get("task")) for row in failures
        if str(row.get("error") or "") == "skipped_after_invalid_workload"
    }
    if timed_out != {timeout_worker} or skipped != {
        f"{timeout_worker}_plan_capture"
    }:
        raise RuntimeError("TPC-H mixed timeout failure set differs")
    timed = measurements[timeout_worker]
    valid = measurements[valid_worker]
    if (
        int(timed.get("rank") or 0) != timeout_rank
        or timed.get("scene") != SCENE
        or timed.get("hard_gate_passed") is not False
        or timed.get("coverage_complete") is not False
        or alg.optimizer_params_key(timed.get("params") or {})
        != alg.optimizer_params_key(timeout_params)
        or int(valid.get("rank") or 0) != valid_rank
        or valid.get("scene") != SCENE
        or valid.get("hard_gate_passed") is not True
        or valid.get("coverage_complete") is not True
        or float(valid.get("db_qps") or 0.0) <= 0.0
        or alg.optimizer_params_key(valid.get("params") or {})
        != alg.optimizer_params_key(valid_params)
    ):
        raise RuntimeError("TPC-H mixed timeout measurements differ")
    timeout_key = alg.optimizer_params_key(timeout_params)
    valid_key = alg.optimizer_params_key(valid_params)
    return [
        {
            "rank": timeout_rank,
            "optimized_params": copy.deepcopy(timeout_params),
            "optimizer_params_key": timeout_key,
            "classification": "TECHNICAL_TPCH_FULL_SUITE_TIMEOUT_EXCLUDED",
            "timeout_seconds": 900,
            "full_suite_coverage_credit": False,
            "accepted_state_credit_increment": 0,
            "rejected_outer_attempt_increment": 0,
            "interval_convergence_mutation": False,
            "replay_history_mutation": False,
            "pruning_evidence_eligible": False,
            "formal_state_mutation": False,
            "source_batch": bound(path),
        },
        {
            "rank": valid_rank,
            "optimized_params": copy.deepcopy(valid_params),
            "optimizer_params_key": valid_key,
            "classification": "VALID_REPLAY_OUTER_ABORTED_BEFORE_DECISION",
            "full_suite_coverage_credit": True,
            "accepted_state_credit_increment": 0,
            "rejected_outer_attempt_increment": 0,
            "interval_convergence_mutation": False,
            "replay_history_mutation": False,
            "pruning_evidence_eligible": False,
            "formal_state_mutation": False,
            "source_batch": bound(path),
        },
    ]


def build(
    checkpoint_path: Path, inherited_path: Path, source_root: Path
) -> dict[str, Any]:
    checkpoint_path = checkpoint_path.resolve()
    inherited_path = inherited_path.resolve()
    source_root = source_root.resolve()
    checkpoint = checkpoint_v17.verify_checkpoint(checkpoint_path)
    inherited = load(inherited_path)
    state_path = source_root / "run_state.json"
    state = load(state_path)
    outer_root = source_root / "outer_011"
    ranking_path = outer_root / "predicted_rankings.json"
    ranking = load(ranking_path)
    selected = (ranking.get("selected_top_k") or {}).get(SCENE) or []
    prior_rows = (inherited.get("rows_by_scene") or {}).get(SCENE) or []
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
        or len(prior_rows) != 15
        or len(prior_all) != 15
        or len(prior_soft) != 2
        or len(prior_permanent) != 13
        or set(prior_permanent).intersection(prior_soft)
        or set(prior_permanent) | set(prior_soft) != set(prior_all)
        or state.get("status") != "FAILED_CLOSED"
        or state.get("error")
        != "RuntimeError: Algorithm C candidate batch incomplete or technically invalid"
        or int(state.get("outer_attempt") or -1) != 10
        or int(ranking.get("outer_attempt") or 0) != 11
        or len(selected) != 5
        or (outer_root / f"{SCENE}.decision.json").exists()
    ):
        raise RuntimeError("TPC-H accumulated quarantine authority differs")
    for name, raw in dict(inherited.get("evidence") or {}).items():
        path = Path(str((raw or {}).get("path") or "")).resolve()
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"inherited quarantine evidence differs: {name}")
    by_rank = {
        int(row["rank"]): dict(row["optimized_params"]) for row in selected
    }
    if set(by_rank) != set(range(1, 6)):
        raise RuntimeError("TPC-H selected Top-5 ranks differ")
    new_rows = []
    new_rows.extend(validate_pass_batch(
        outer_root / "top5_batch_1/batch_result.json",
        {"tpch_w1": (1, by_rank[1]), "tpch_w2": (2, by_rank[2])},
    ))
    new_rows.extend(validate_pass_batch(
        outer_root / "top5_batch_2/batch_result.json",
        {"tpch_w1": (3, by_rank[3]), "tpch_w2": (4, by_rank[4])},
    ))
    new_rows.extend(validate_timeout_batch(
        outer_root / "top5_batch_3/batch_result.json",
        {"tpch_w1": (5, by_rank[5])},
    ))
    new_rows.sort(key=lambda row: int(row["rank"]))
    new_keys = [str(row["optimizer_params_key"]) for row in new_rows]
    if len(set(new_keys)) != 5 or set(new_keys).intersection(prior_all):
        raise RuntimeError("TPC-H new replay identities differ")
    scenes = tuple((inherited.get("rows_by_scene") or {}).keys())
    rows_by_scene = copy.deepcopy(inherited["rows_by_scene"])
    all_by_scene = copy.deepcopy(inherited["quarantined_param_keys_by_scene"])
    soft_by_scene = copy.deepcopy(inherited["soft_replayed_param_keys_by_scene"])
    permanent_by_scene = copy.deepcopy(
        inherited["permanent_invalid_param_keys_by_scene"]
    )
    rows_by_scene[SCENE] = list(prior_rows) + new_rows
    all_by_scene[SCENE] = prior_all + new_keys
    new_permanent = [
        str(row["optimizer_params_key"]) for row in new_rows
        if row["classification"]
        == "TECHNICAL_TPCH_FULL_SUITE_TIMEOUT_EXCLUDED"
    ]
    new_soft = [
        str(row["optimizer_params_key"]) for row in new_rows
        if row["classification"] == "VALID_REPLAY_OUTER_ABORTED_BEFORE_DECISION"
    ]
    permanent_by_scene[SCENE] = prior_permanent + new_permanent
    soft_by_scene[SCENE] = prior_soft + new_soft
    if (
        len(set(all_by_scene[SCENE])) != 20
        or len(set(permanent_by_scene[SCENE])) != 14
        or len(set(soft_by_scene[SCENE])) != 6
        or set(permanent_by_scene[SCENE]).intersection(soft_by_scene[SCENE])
        or set(permanent_by_scene[SCENE]) | set(soft_by_scene[SCENE])
        != set(all_by_scene[SCENE])
    ):
        raise RuntimeError("TPC-H accumulated replay tiers differ")
    evidence = copy.deepcopy(inherited.get("evidence") or {})
    evidence.update({
        "checkpoint": bound(checkpoint_path),
        "inherited_quarantine": bound(inherited_path),
        "outer_11_failed_source_state": bound(state_path),
        "outer_11_predicted_rankings": bound(ranking_path),
        "outer_11_batch_1": bound(outer_root / "top5_batch_1/batch_result.json"),
        "outer_11_batch_2": bound(outer_root / "top5_batch_2/batch_result.json"),
        "outer_11_batch_3": bound(outer_root / "top5_batch_3/batch_result.json"),
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
        "initialization": "fourteen_tpch_timeouts_plus_six_valid_zero_credit_replays",
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
    document = build(args.checkpoint, args.inherited_quarantine, args.source_root)
    args.output_dir.mkdir(parents=True, mode=0o700)
    output = args.output_dir / "replay_quarantine.json"
    atomic(output, document)
    print(json.dumps({
        "status": "PASS", "output": str(output),
        "permanent_invalid_candidates": 14,
        "soft_replayed_candidates": 6,
        "sha256": sha(output),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
