#!/usr/bin/env python3
"""Audit normalized TPC-H B decisions including candidate-local timeouts."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "mysql_tpch_same_worker_client_qps_normalization_v1"
TOP_K = 5


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def close(left: Any, right: Any) -> bool:
    return math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-15)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    root = args.run_root.resolve()
    state = load(root / "run_state.json")
    contract = dict(state.get("same_worker_qps_normalization") or {})
    if (
        contract.get("schema_version") != SCHEMA
        or state.get("outer_acceptance_uses_same_worker_normalized_client_qps")
        is not True
    ):
        raise RuntimeError("same-worker QPS state contract differs")
    if args.require_complete and state.get("status") != "COMPLETE":
        raise RuntimeError("Algorithm B is not complete")

    decisions = sorted(root.glob("outer_*/tpch_sf4_memory.decision.json"))
    audited_rows = 0
    invalid_rows = 0
    accepted = 0
    rejected = 0
    for path in decisions:
        document = load(path)
        rows = document.get("evaluations")
        invalid = document.get("invalid_candidate_attempts") or []
        if not isinstance(rows, list) or not isinstance(invalid, list):
            raise RuntimeError(f"B decision candidate accounting differs: {path}")
        if (
            len(rows) != int(document.get("valid_real_evaluation_count") or 0)
            or len(rows) + len(invalid) != TOP_K
        ):
            raise RuntimeError(f"complete Top-5 accounting required: {path}")
        invalid_ranks: set[int] = set()
        for raw in invalid:
            if not isinstance(raw, Mapping):
                raise RuntimeError(f"invalid candidate row differs: {path}")
            row = dict(raw)
            rank = int(row.get("rank") or 0)
            if (
                rank < 1 or rank > TOP_K or rank in invalid_ranks
                or row.get("classification")
                != "formal_workload_timeout_candidate_infeasible"
                or row.get("accepted_state_credit") is not False
                or row.get("affected_scene_measurement_data_used") is not False
                or "tpch_sf4_memory" not in (row.get("affected_scenes") or [])
                or not any(
                    "timed out after 900 seconds" in str(failure.get("error") or "")
                    for failure in (row.get("failures") or [])
                    if isinstance(failure, Mapping)
                )
            ):
                raise RuntimeError(f"invalid timeout candidate differs: {path}")
            invalid_ranks.add(rank)
            invalid_rows += 1

        by_rank: dict[int, Mapping[str, Any]] = {}
        for raw_row in rows:
            if not isinstance(raw_row, Mapping):
                raise RuntimeError(f"B evaluation row differs: {path}")
            row = dict(raw_row)
            worker_id = str(row.get("worker_id") or "")
            factor = float(row.get("same_worker_qps_normalization_factor") or 0.0)
            raw_qps = float(row.get("raw_worker_client_qps") or 0.0)
            normalized_qps = float(row.get("db_qps") or 0.0)
            worker_baseline = float(
                row.get("same_worker_baseline_client_qps") or 0.0
            )
            rank = int(row.get("rank") or 0)
            if (
                rank < 1 or rank > TOP_K or rank in invalid_ranks or rank in by_rank
                or row.get("same_worker_qps_normalization_applied") is not True
                or row.get("same_worker_qps_normalization_schema_version") != SCHEMA
                or worker_id not in contract["worker_baseline_client_qps"]
                or not close(
                    worker_baseline,
                    contract["worker_baseline_client_qps"][worker_id],
                )
                or not close(
                    factor, contract["worker_to_canonical_factors"][worker_id]
                )
                or not close(normalized_qps, raw_qps * factor)
                or not close(
                    row.get("canonical_baseline_client_qps"),
                    contract["canonical_baseline_client_qps"],
                )
            ):
                raise RuntimeError(f"B normalized QPS formula differs: {path}")
            outer = dict(row.get("outer_decision") or {})
            if not close(outer.get("candidate_db_qps"), normalized_qps):
                raise RuntimeError(f"B decision did not use normalized QPS: {path}")
            expected_accept = normalized_qps > float(outer["incumbent_db_qps"])
            if bool(outer.get("accepted")) != expected_accept:
                raise RuntimeError(f"B strict-progress decision differs: {path}")
            by_rank[rank] = row
            audited_rows += 1
        if set(by_rank) | invalid_ranks != set(range(1, TOP_K + 1)):
            raise RuntimeError(f"B Top-5 rank coverage differs: {path}")
        selected_rank = int(document["selected_outer_candidate_rank"])
        if selected_rank not in by_rank:
            raise RuntimeError(f"B selected an invalid candidate: {path}")
        selected = by_rank[selected_rank]
        if bool(document.get("accepted")) != bool(
            dict(selected["outer_decision"])["accepted"]
        ):
            raise RuntimeError(f"B selected decision differs: {path}")
        if document.get("accepted") is True:
            accepted += 1
        else:
            rejected += 1

    evidence_complete = bool(state.get("status") == "COMPLETE" and decisions)
    result = {
        "status": "PASS" if evidence_complete else "PASS_PARTIAL",
        "run_root": str(root),
        "run_status": state.get("status"),
        "decision_count": len(decisions),
        "evaluation_count": audited_rows,
        "invalid_timeout_candidate_count": invalid_rows,
        "accepted_decisions": accepted,
        "rejected_decisions": rejected,
        "complete_top5_accounting_valid": True,
        "invalid_timeout_credit_excluded": True,
        "same_worker_qps_formula_valid": True,
        "strict_progress_valid": True,
        "evidence_complete": evidence_complete,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
