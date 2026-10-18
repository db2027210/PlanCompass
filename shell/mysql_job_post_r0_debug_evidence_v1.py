#!/usr/bin/env python3
"""Fail-closed producer for one JOB debug round (zero accepted credit)."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA = "mysql_job_distributed_v2_debug_round_evidence_v1"
FORMULA = "g_j = sum_r(dScore/dU_r * T[j,r] * confidence)"
PARAMETERS = (
    "io_block_read_cost",
    "memory_block_read_cost",
    "row_evaluate_cost",
    "memory_temptable_row_cost",
)
GRADIENT_FIELDS = {
    "feature_ratio", "predicted_resource", "utilization", "dScore/dU", "T",
    "confidence", "g_j", "eta", "delta_param", "candidate_score",
}


class EvidenceError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceError(f"{label} is not numeric")
    result = float(value)
    if not math.isfinite(result):
        raise EvidenceError(f"{label} is not finite")
    return result


def _expected_subsets() -> set[tuple[str, ...]]:
    return {
        tuple(combo)
        for size in range(1, len(PARAMETERS) + 1)
        for combo in itertools.combinations(PARAMETERS, size)
    }


def build_evidence(
    raw: Mapping[str, Any], fresh_r0: Mapping[str, Any]
) -> dict[str, Any]:
    if (
        fresh_r0.get("schema_version") != "mysql_job_distributed_v2_fresh_r0_evidence_v1"
        or fresh_r0.get("status") != "PASS_FRESH_R0"
        or fresh_r0.get("accepted_rounds_credit") != 0
        or fresh_r0.get("accepted_round_authorized") is not False
        or fresh_r0.get("candidate_credit") != 0
        or fresh_r0.get("top5_replay_credit") != 0
    ):
        raise EvidenceError("fresh R0 is not a zero-credit PASS")
    if (
        raw.get("schema_version") != "mysql_job_post_r0_raw_debug_round_v1"
        or raw.get("status") != "PASS_RAW_DEBUG_ROUND"
        or raw.get("attempt_token") != fresh_r0.get("attempt_token")
        or raw.get("planner_identity") != fresh_r0.get("planner_identity")
        or raw.get("algorithm_formula") != FORMULA
        or raw.get("qps_role") != "outer_acceptance_after_real_replay_only"
        or raw.get("accepted_rounds_credit") != 0
        or raw.get("accepted_round_authorized") is not False
    ):
        raise EvidenceError("raw debug round identity or policy differs")
    events = raw.get("gradient_events")
    ranking = raw.get("global_candidate_ranking")
    replays = raw.get("real_replay_top5")
    if not isinstance(events, list) or len(events) != 15:
        raise EvidenceError("exactly 15 gradient events are required")
    if not isinstance(ranking, list) or len(ranking) != 15:
        raise EvidenceError("exactly 15 global ranking rows are required")
    if not isinstance(replays, list) or len(replays) != 5:
        raise EvidenceError("exactly five real replays are required")

    seen_ids: set[str] = set()
    seen_subsets: set[tuple[str, ...]] = set()
    normalized_events: list[dict[str, Any]] = []
    for index, row in enumerate(events, 1):
        if not isinstance(row, Mapping) or not GRADIENT_FIELDS.issubset(row):
            raise EvidenceError(f"gradient event {index} lacks raw-gradient fields")
        subset_id = row.get("subset_id")
        members = row.get("subset_parameters")
        if not isinstance(subset_id, str) or not subset_id or subset_id in seen_ids:
            raise EvidenceError("subset ids must be unique nonempty strings")
        if not isinstance(members, list) or not members:
            raise EvidenceError("subset_parameters are required")
        member_tuple = tuple(name for name in PARAMETERS if name in members)
        if len(member_tuple) != len(members) or set(members) - set(PARAMETERS):
            raise EvidenceError("subset_parameters differ from frozen four parameters")
        for field in ("confidence", "g_j", "eta", "delta_param", "candidate_score"):
            _finite(row[field], f"event {index} {field}")
        for field in ("feature_ratio", "predicted_resource", "utilization", "dScore/dU", "T"):
            if not isinstance(row[field], Mapping) or not row[field]:
                raise EvidenceError(f"event {index} {field} is incomplete")
        seen_ids.add(subset_id)
        seen_subsets.add(member_tuple)
        normalized_events.append(dict(row))
    if seen_subsets != _expected_subsets():
        raise EvidenceError("events do not cover all 15 nonempty subsets")

    event_by_id = {row["subset_id"]: row for row in normalized_events}
    previous = math.inf
    normalized_ranking: list[dict[str, Any]] = []
    for rank, row in enumerate(ranking, 1):
        if not isinstance(row, Mapping) or set(row) != {"rank", "subset_id", "candidate_score"}:
            raise EvidenceError("ranking rows must have exactly rank/subset_id/candidate_score")
        score = _finite(row.get("candidate_score"), f"ranking {rank} score")
        if row.get("rank") != rank or row.get("subset_id") not in event_by_id:
            raise EvidenceError("global ranking identity differs")
        if score > previous or not math.isclose(
            score, float(event_by_id[row["subset_id"]]["candidate_score"]), rel_tol=0, abs_tol=1e-12
        ):
            raise EvidenceError("global ranking is not the frozen final-score order")
        previous = score
        normalized_ranking.append(dict(row))

    for rank, replay in enumerate(replays, 1):
        fingerprint = replay.get("fingerprint") if isinstance(replay, Mapping) else None
        if (
            not isinstance(replay, Mapping)
            or replay.get("rank") != rank
            or replay.get("subset_id") != normalized_ranking[rank - 1]["subset_id"]
            or replay.get("status") != "PASS_REAL_REPLAY"
            or not isinstance(fingerprint, Mapping)
            or not all(fingerprint.get(level) for level in ("l1", "l2", "l3"))
            or replay.get("qps_used_in_candidate_score") is not False
        ):
            raise EvidenceError("fixed Top-5 replay or L1/L2/L3 fingerprint is incomplete")

    return {
        "schema_version": SCHEMA,
        "status": "PASS_DEBUG_ROUND",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "attempt_token": fresh_r0["attempt_token"],
        "planner_identity": fresh_r0["planner_identity"],
        "completed_nonempty_subsets": 15,
        "sealed_top5_ranks": [1, 2, 3, 4, 5],
        "replayed_top5_ranks": [1, 2, 3, 4, 5],
        "fixed_top5_only": True,
        "gradient_event_schema_complete": True,
        "fingerprint_l1_l2_l3_complete": True,
        "accepted_rounds_credit": 0,
        "accepted_round_authorized": False,
        "algorithm_formula": FORMULA,
        "parameter_count": 4,
        "candidate_count": 15,
        "qps_role": "outer_acceptance_after_real_replay_only",
        "gradient_events": normalized_events,
        "global_candidate_ranking": normalized_ranking,
        "real_replay_top5": [dict(row) for row in replays],
        "fresh_r0_sha256": hashlib.sha256(canonical(fresh_r0)).hexdigest(),
    }


def _load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise EvidenceError(f"invalid input: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    sidecar = Path(str(path) + ".sha256")
    if sidecar.is_symlink() or not sidecar.is_file() or sidecar.read_text().strip() != hashlib.sha256(canonical(value)).hexdigest():
        raise EvidenceError(f"sidecar differs: {path}")
    return value


def _write(path: Path, value: Mapping[str, Any]) -> None:
    sidecar = Path(str(path) + ".sha256")
    if path.exists() or path.is_symlink() or sidecar.exists() or sidecar.is_symlink():
        raise EvidenceError("refusing to overwrite evidence")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
    path.write_text(payload, encoding="utf-8")
    sidecar.write_text(hashlib.sha256(canonical(value)).hexdigest() + "\n", encoding="utf-8")
    os.chmod(path, 0o444)
    os.chmod(sidecar, 0o444)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-debug-round", type=Path, required=True)
    parser.add_argument("--fresh-r0", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    _write(args.output, build_evidence(_load(args.raw_debug_round), _load(args.fresh_r0)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
