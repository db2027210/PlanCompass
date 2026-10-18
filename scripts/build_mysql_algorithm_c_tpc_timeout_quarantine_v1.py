#!/usr/bin/env python3
"""Quarantine five zero-credit TPC-H candidates that exceeded 900 seconds."""

from __future__ import annotations

import argparse
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

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v13 as checkpoint_v13
from scripts import build_mysql_algorithm_c_empty_quarantine_for_checkpoint_v1 as base
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


SCENE = "tpch_sf4_memory"
SCENES = tuple(base.SCENES)
PRUNING = base.PRUNING


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bound(path: Path) -> dict[str, str]:
    path = path.resolve()
    if not path.is_file() or path.is_symlink():
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


def validate_batch(
    path: Path, expected: dict[str, tuple[int, dict[str, Any]]],
) -> list[dict[str, Any]]:
    batch = load(path)
    measurements = batch.get("measurements") or {}
    failures = batch.get("failures") or []
    expected_workers = set(expected)
    if (
        batch.get("status") != "FAILED_CLOSED"
        or set(measurements) != expected_workers
        or not isinstance(failures, list)
    ):
        raise RuntimeError(f"TPC-H technical-timeout batch differs: {path}")
    timeout_workers = {
        str(row.get("task"))
        for row in failures
        if isinstance(row, Mapping)
        and str(row.get("task")) in expected_workers
        and str(row.get("error") or "").startswith("TimeoutExpired:")
        and "timed out after 900 seconds" in str(row.get("error") or "")
    }
    skipped = {
        str(row.get("task"))
        for row in failures
        if isinstance(row, Mapping)
        and str(row.get("error") or "") == "skipped_after_invalid_workload"
    }
    if timeout_workers != expected_workers or skipped != {
        f"{worker}_plan_capture" for worker in expected_workers
    }:
        raise RuntimeError(f"TPC-H timeout failure set differs: {path}")
    rows = []
    for worker, (rank, params) in expected.items():
        row = measurements[worker]
        if (
            int(row.get("rank") or 0) != rank
            or row.get("scene") != SCENE
            or row.get("hard_gate_passed") is not False
            or row.get("coverage_complete") is not False
            or alg.optimizer_params_key(row.get("params") or {})
            != alg.optimizer_params_key(params)
        ):
            raise RuntimeError(f"TPC-H timeout measurement differs: {path}: {worker}")
        rows.append({
            "rank": rank,
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


def build(checkpoint_path: Path, source_root: Path) -> dict[str, Any]:
    checkpoint_path = checkpoint_path.resolve()
    source_root = source_root.resolve()
    checkpoint = checkpoint_v13.verify_checkpoint(checkpoint_path)
    state_path = source_root / "run_state.json"
    state = load(state_path)
    outer = int(checkpoint["completed_outer_attempts"]) + 1
    outer_root = source_root / f"outer_{outer:03d}"
    ranking_path = outer_root / "predicted_rankings.json"
    ranking = load(ranking_path)
    selected = (ranking.get("selected_top_k") or {}).get(SCENE) or []
    if (
        checkpoint.get("scene") != SCENE
        or outer != 11
        or state.get("status") != "FAILED_CLOSED"
        or state.get("error")
        != "RuntimeError: Algorithm C candidate batch incomplete or technically invalid"
        or int(state.get("outer_attempt") or -1) != 10
        or int(ranking.get("outer_attempt") or 0) != outer
        or len(selected) != 5
        or (outer_root / f"{SCENE}.decision.json").exists()
    ):
        raise RuntimeError("TPC-H technical-timeout quarantine authority differs")
    by_rank = {
        int(row["rank"]): dict(row["optimized_params"])
        for row in selected
    }
    if set(by_rank) != set(range(1, 6)):
        raise RuntimeError("TPC-H selected Top-5 ranks differ")
    rows = []
    rows.extend(validate_batch(
        outer_root / "top5_batch_1/batch_result.json",
        {"tpch_w1": (1, by_rank[1]), "tpch_w2": (2, by_rank[2])},
    ))
    rows.extend(validate_batch(
        outer_root / "top5_batch_2/batch_result.json",
        {"tpch_w1": (3, by_rank[3]), "tpch_w2": (4, by_rank[4])},
    ))
    rows.extend(validate_batch(
        outer_root / "top5_batch_3/batch_result.json",
        {"tpch_w1": (5, by_rank[5])},
    ))
    rows.sort(key=lambda row: int(row["rank"]))
    keys = [str(row["optimizer_params_key"]) for row in rows]
    if len(set(keys)) != 5:
        raise RuntimeError("TPC-H timeout parameter identities differ")
    empty = {scene: [] for scene in SCENES}
    zeros = {scene: 0 for scene in SCENES}
    combined = {scene: list(empty[scene]) for scene in SCENES}
    permanent = {scene: list(empty[scene]) for scene in SCENES}
    rows_by_scene = {scene: [] for scene in SCENES}
    counts = dict(zeros)
    permanent_counts = dict(zeros)
    combined[SCENE] = keys
    permanent[SCENE] = keys
    rows_by_scene[SCENE] = rows
    counts[SCENE] = 5
    permanent_counts[SCENE] = 5
    freeze = Path(str(checkpoint["freeze_path"])).resolve()
    return {
        "schema_version": "mysql_algorithm_c_replay_quarantine_v6",
        "status": "PASS",
        "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_sha256": str(checkpoint["freeze_sha256"]),
        "resume_checkpoint_outer_attempt": int(checkpoint["completed_outer_attempts"]),
        "formal_state_mutation": False,
        "accepted_rounds_mutation": False,
        "rejected_rounds_mutation": False,
        "interval_convergence_mutation": False,
        "quarantined_counts_by_scene": counts,
        "soft_replayed_counts_by_scene": zeros,
        "permanent_invalid_counts_by_scene": permanent_counts,
        "quarantined_param_keys_by_scene": combined,
        "soft_replayed_param_keys_by_scene": empty,
        "permanent_invalid_param_keys_by_scene": permanent,
        "rows_by_scene": rows_by_scene,
        "cycle_reset_may_clear_soft_replayed": True,
        "cycle_reset_may_clear_permanent_invalid": False,
        "initialization": "tpch_full_suite_900s_technical_timeout_exclusion",
        "pre_v18_timeout_and_replay_evidence_imported": False,
        "evidence": {
            "checkpoint": bound(checkpoint_path),
            "resource_freeze": bound(freeze),
            "pruning_contract": bound(PRUNING),
            "failed_source_state": bound(state_path),
            "predicted_rankings": bound(ranking_path),
            "batch_1": bound(outer_root / "top5_batch_1/batch_result.json"),
            "batch_2": bound(outer_root / "top5_batch_2/batch_result.json"),
            "batch_3": bound(outer_root / "top5_batch_3/batch_result.json"),
        },
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": sha(Path(__file__).resolve()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.output_dir.name) is None:
        parser.error("unsafe output directory")
    if args.output_dir.exists() or args.output_dir.is_symlink():
        raise RuntimeError("fresh quarantine output required")
    document = build(args.checkpoint, args.source_root)
    args.output_dir.mkdir(parents=True, mode=0o700)
    output = args.output_dir / "replay_quarantine.json"
    atomic(output, document)
    print(json.dumps({
        "status": "PASS", "output": str(output),
        "permanent_invalid_candidates": 5, "sha256": sha(output),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

