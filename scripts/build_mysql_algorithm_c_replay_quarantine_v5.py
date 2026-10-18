#!/usr/bin/env python3
"""Accumulate an aborted outer into the tiered v4 replay quarantine."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from build_mysql_algorithm_c_replay_quarantine_v4 import (
    FREEZE, PERMANENT_TIMEOUT_SCENES, SCENES, key, load, sha,
)


def _atomic(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists() or path.parent.exists():
        raise RuntimeError(f"fresh output directory required: {path.parent}")
    path.parent.mkdir(parents=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def _timeout_scenes(failures: list[Mapping[str, Any]]) -> set[str]:
    result: set[str] = set()
    for row in failures:
        error = str(row.get("error") or "")
        task = str(row.get("task") or "")
        if "timed out after 900 seconds" not in error:
            continue
        if task.startswith("xp_cpu_"):
            result.add("hybench_xp_cpu")
        elif task.startswith("xp_io_"):
            result.add("hybench_xp_io")
        elif task.startswith("pair_") and "benchbase_tpch.xml" in error:
            result.add("tpch_sf4_memory")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior-quarantine", type=Path, required=True)
    parser.add_argument("--failed-state", type=Path, required=True)
    parser.add_argument("--predicted-rankings", type=Path, required=True)
    parser.add_argument("--pass-batch", type=Path, action="append", default=[])
    parser.add_argument("--failed-batch", type=Path)
    parser.add_argument("--interrupted-reset", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prior_path = args.prior_quarantine.resolve()
    state_path = args.failed_state.resolve()
    ranking_path = args.predicted_rankings.resolve()
    failed_path = args.failed_batch.resolve() if args.failed_batch else None
    pass_paths = [path.resolve() for path in args.pass_batch]
    prior, state = load(prior_path), load(state_path)
    rankings = load(ranking_path)
    failed = load(failed_path) if failed_path is not None else None
    passed_batches = [load(path) for path in pass_paths]
    selected = rankings.get("selected_top_k") or {}
    failure_match = re.search(
        r"outer (\d+) batch ([123]) failed closed",
        str(state.get("error") or ""),
    )
    if args.interrupted_reset:
        incomplete = [
            path.parent for path in state_path.parent.glob(
                "xp_resets/outer_*_batch_*/pre_reset_session_drain.json"
            )
            if not (path.parent / "reset_binding.json").exists()
        ]
        if len(incomplete) != 1:
            raise RuntimeError("interrupted reset evidence cardinality differs")
        token = re.fullmatch(r"outer_(\d{3})_batch_([123])", incomplete[0].name)
        if token is None:
            raise RuntimeError("interrupted reset token differs")
        failed_outer, failed_batch_number = map(int, token.groups())
    else:
        if failure_match is None:
            raise RuntimeError("failed state does not identify an aborted outer batch")
        failed_outer, failed_batch_number = map(int, failure_match.groups())
    completed_outer = failed_outer - 1
    timeout_scenes = set() if failed is None else _timeout_scenes(
        list(failed.get("failures") or [])
    )
    prior_rows = prior.get("rows_by_scene") or {}
    prior_all = prior.get("quarantined_param_keys_by_scene") or {}
    prior_soft = prior.get("soft_replayed_param_keys_by_scene") or {}
    prior_permanent = prior.get("permanent_invalid_param_keys_by_scene") or {}
    if (
        prior.get("schema_version") != "mysql_algorithm_c_replay_quarantine_v4"
        or prior.get("status") != "PASS"
        or prior.get("freeze_sha256") != sha(FREEZE)
        or prior.get("formal_state_mutation") is not False
        or any(set(mapping) != set(SCENES) for mapping in (
            prior_rows, prior_all, prior_soft, prior_permanent,
        ))
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or int(state.get("outer_attempt") or -1) != completed_outer
        or state.get("freeze_sha256") != sha(FREEZE)
        or int(rankings.get("outer_attempt") or -1) != failed_outer
        or rankings.get("fixed_top_k") != 5
        or set(selected) != set(SCENES)
        or any(len(selected[scene]) != 5 for scene in SCENES)
        or failed_batch_number != len(pass_paths) + 1
    ):
        raise RuntimeError("aborted-outer replay quarantine authority differs")
    if args.interrupted_reset:
        if (
            failed is not None
            or "credentials/benchmark.cnf" not in str(state.get("error") or "")
            or "timed out after 30 seconds" not in str(state.get("error") or "")
            or timeout_scenes
        ):
            raise RuntimeError("interrupted-reset replay authority differs")
    elif (
        failed is None
        or failed.get("status") != "FAILED_CLOSED"
        or float(failed.get("elapsed_seconds") or 0) <= 900.0
        or timeout_scenes != PERMANENT_TIMEOUT_SCENES
    ):
        raise RuntimeError("timeout replay authority differs")
    observed_pass_ranks: dict[str, set[int]] = {scene: set() for scene in SCENES}
    for batch in passed_batches:
        measurements = batch.get("measurements") or {}
        if (
            batch.get("status") != "PASS"
            or batch.get("failures") not in ([], None)
            or any(
                row.get("hard_gate_passed") is not True
                or row.get("coverage_complete") is not True
                for row in measurements.values()
            )
        ):
            raise RuntimeError("passing replay batch authority differs")
        for row in measurements.values():
            observed_pass_ranks[str(row["scene"])].add(int(row["rank"]))
    expected_pass_ranks = set(range(1, 2 * len(pass_paths) + 1))
    if any(observed_pass_ranks[scene] != expected_pass_ranks for scene in SCENES):
        raise RuntimeError("passing replay rank coverage differs")
    failed_measurements = (failed or {}).get("measurements") or {}
    failed_rank_start = 2 * len(pass_paths) + 1
    expected_failed_ranks = (
        set() if args.interrupted_reset
        else set(range(failed_rank_start, min(5, failed_rank_start + 1) + 1))
    )
    observed_failed_ranks: dict[str, set[int]] = {scene: set() for scene in SCENES}
    for row in failed_measurements.values():
        observed_failed_ranks[str(row["scene"])].add(int(row["rank"]))
        if row.get("hard_gate_passed") is not False or row.get("coverage_complete") is not False:
            raise RuntimeError("failed replay hard gate differs")
    if any(observed_failed_ranks[scene] != expected_failed_ranks for scene in SCENES):
        raise RuntimeError("failed replay rank coverage differs")

    rows_by_scene: dict[str, list[dict[str, Any]]] = {}
    all_by_scene: dict[str, list[str]] = {}
    soft_by_scene: dict[str, list[str]] = {}
    permanent_by_scene: dict[str, list[str]] = {}
    for scene in SCENES:
        rows = [dict(row) for row in prior_rows[scene]]
        all_keys, soft_keys, permanent_keys = (
            list(prior_all[scene]), list(prior_soft[scene]),
            list(prior_permanent[scene]),
        )
        row_by_key = {str(row["optimizer_params_key"]): row for row in rows}
        attempted_positions = expected_pass_ranks | expected_failed_ranks
        for position, selected_row in enumerate(selected[scene], 1):
            if position not in attempted_positions:
                continue
            params = dict(selected_row.get("optimized_params") or {})
            param_key = key(params)
            permanent = position in expected_failed_ranks and scene in timeout_scenes
            outcome = (
                "FORMAL_WALL_TIMEOUT_INVALID_MEASUREMENT" if permanent
                else "VALID_REPLAY_OUTER_ABORTED_BEFORE_DECISION"
                if position in expected_pass_ranks
                else "INCOMPLETE_HARD_GATE_OUTER_ABORTED_BEFORE_DECISION"
            )
            observation = {
                "source_run": state_path.parent.name,
                "source_outer_attempt": failed_outer,
                "replay_position": position,
                "original_rank": int(selected_row["rank"]),
                "batch": (position + 1) // 2,
                "outcome": outcome,
                "formal_state_mutation": False,
            }
            if param_key in permanent_keys:
                raise RuntimeError(f"{scene} permanently invalid point was selected")
            if param_key not in row_by_key:
                row = {
                    "replay_position": len(all_keys) + 1,
                    "source_outer_attempt": failed_outer,
                    "original_rank": int(selected_row["rank"]),
                    "optimized_params": params,
                    "optimizer_params_key": param_key,
                    "outcome": outcome,
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
                if permanent and param_key in soft_keys:
                    soft_keys.remove(param_key)
                    permanent_keys.append(param_key)
                    row["outcome"] = outcome
                    row["promoted_from_soft_replay"] = True
            if param_key not in (permanent_keys if permanent else soft_keys):
                raise RuntimeError(f"{scene} replay tier assignment differs")
        if (
            len(set(all_keys)) != len(all_keys)
            or set(soft_keys).intersection(permanent_keys)
            or set(soft_keys) | set(permanent_keys) != set(all_keys)
            or [row.get("optimizer_params_key") for row in rows] != all_keys
        ):
            raise RuntimeError(f"{scene} replay tier integrity differs")
        rows_by_scene[scene] = rows
        all_by_scene[scene] = all_keys
        soft_by_scene[scene] = soft_keys
        permanent_by_scene[scene] = permanent_keys

    evidence = dict(prior.get("evidence") or {})
    prefix = f"outer_{failed_outer}_aborted"
    evidence_paths = {
        "prior_quarantine": prior_path, "failed_state": state_path,
        "predicted_rankings": ranking_path,
        **{f"pass_batch_{i}": path for i, path in enumerate(pass_paths, 1)},
    }
    if failed_path is not None:
        evidence_paths["failed_batch"] = failed_path
    for name, path in evidence_paths.items():
        evidence[f"{prefix}_{name}"] = {"path": str(path), "sha256": sha(path)}
    document = {
        **prior,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "resume_checkpoint_outer_attempt": completed_outer,
        "quarantined_counts_by_scene": {scene: len(all_by_scene[scene]) for scene in SCENES},
        "soft_replayed_counts_by_scene": {scene: len(soft_by_scene[scene]) for scene in SCENES},
        "permanent_invalid_counts_by_scene": {scene: len(permanent_by_scene[scene]) for scene in SCENES},
        "quarantined_param_keys_by_scene": all_by_scene,
        "soft_replayed_param_keys_by_scene": soft_by_scene,
        "permanent_invalid_param_keys_by_scene": permanent_by_scene,
        "latest_source_run": state_path.parent.name,
        "rows_by_scene": rows_by_scene,
        "evidence": evidence,
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": sha(Path(__file__).resolve()),
    }
    _atomic(args.output.resolve(), document)
    print(json.dumps({
        "status": "PASS", "output": str(args.output.resolve()),
        "soft_counts": document["soft_replayed_counts_by_scene"],
        "permanent_counts": document["permanent_invalid_counts_by_scene"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
