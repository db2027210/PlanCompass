#!/usr/bin/env python3
"""Build a sealed TPC-C exact repeat with heterogeneous point histories.

The complete-batch ordinal describes repetitions of the immutable Top-5 set.
It is deliberately independent from each point's accumulated observation
count: a newly assembled Top-5 may contain points with different amounts of
historical evidence.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Dict, Mapping

try:
    import build_tpcc_exact_plan_repeat_validation_v3 as v3
    import build_tpcc_exact_plan_repeat_validation_v4 as v4
except ModuleNotFoundError:
    from tools import build_tpcc_exact_plan_repeat_validation_v3 as v3
    from tools import build_tpcc_exact_plan_repeat_validation_v4 as v4


REPEAT_CONTRACT = "tpcc_sealed_exact_top5_independent_repeat_v34"


def _completed_source_batch_count(source: Mapping[str, Any]) -> int:
    contract = source.get("validation_repeat_contract")
    if not isinstance(contract, Mapping):
        # A clean discovery/validation ledger is the first complete batch for
        # its exact Top-5 set.
        return 1
    if contract.get("contract") != REPEAT_CONTRACT:
        raise RuntimeError("unsupported prior exact-repeat contract")
    count = int(contract.get("independent_same_group_batch_ordinal", 0))
    if count < 2:
        raise RuntimeError("invalid prior same-group batch ordinal")
    return count


def _point_observation_counts(selection: Mapping[str, Any]) -> list[Dict[str, Any]]:
    rows = []
    for candidate in selection.get("top5") or []:
        count = int((candidate.get("confidence") or {}).get("measurement_count", -1))
        if count < 1:
            raise RuntimeError("exact-repeat point has no accumulated observation")
        params = v4._params(candidate)
        rows.append(
            {
                "rank": int(candidate["rank"]),
                "parameter_sha256": v4._canonical_sha256(params),
                "workload_plan_fingerprint": str(
                    candidate["workload_plan_fingerprint"]
                ),
                "total_exact_observation_count": count,
            }
        )
    if len(rows) != 5:
        raise RuntimeError("exact-repeat observation vector requires five points")
    rows.sort(key=lambda item: item["rank"])
    return rows


def add_repeat_contract(
    output: Dict[str, Any],
    source_selection: Mapping[str, Any],
    *,
    source_selection_path: Path,
    source_selection_sha256: str,
    source_ledger_path: Path,
    source_ledger_sha256: str,
) -> Dict[str, Any]:
    """Seal provenance without assuming equal per-point history lengths."""
    result = copy.deepcopy(output)
    source_identity = v4.exact_top5_identity(source_selection)
    repeat_identity = v4.exact_top5_identity(result)
    if repeat_identity["sha256"] != source_identity["sha256"]:
        raise RuntimeError("repeat builder changed the exact parameter/plan set")

    vector = _point_observation_counts(result)
    counts = [item["total_exact_observation_count"] for item in vector]
    completed_batches = _completed_source_batch_count(source_selection)
    result["validation_repeat_contract"] = {
        "contract": REPEAT_CONTRACT,
        "source_selection_path": str(source_selection_path.resolve()),
        "source_selection_sha256": source_selection_sha256,
        "source_ledger_path": str(source_ledger_path.resolve()),
        "source_ledger_sha256": source_ledger_sha256,
        "source_top5_identity_sha256": source_identity["sha256"],
        "repeat_top5_identity_sha256": repeat_identity["sha256"],
        "prior_complete_same_group_batch_count": completed_batches,
        "independent_same_group_batch_ordinal": completed_batches + 1,
        "per_point_total_exact_observation_counts": vector,
        "minimum_total_exact_observation_count": min(counts),
        "maximum_total_exact_observation_count": max(counts),
        "heterogeneous_point_observation_counts_allowed": True,
        "formal_gate_minimum_per_point_observations": 3,
        "repeat_cap_basis": "complete_same_group_batch_count",
        "novel_fingerprint_credit": 0,
        "same_exact_parameter_plan_set": True,
        "source_selection_sha_verified": True,
    }
    result["schema_version"] = "tpcc_exact_plan_repeat_validation_v5"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-score-span", type=float, default=0.0)
    args = parser.parse_args()
    source_selection_path = args.selection.resolve()
    source_ledger_path = args.ledger.resolve()
    base = v3.v2.import_base()
    source_selection = base.load(source_selection_path)
    source_ledger = base.load(source_ledger_path)
    output = v3.build(source_selection, source_ledger, args.minimum_score_span)
    result = add_repeat_contract(
        output,
        source_selection,
        source_selection_path=source_selection_path,
        source_selection_sha256=v4._sha256(source_selection_path),
        source_ledger_path=source_ledger_path,
        source_ledger_sha256=v4._sha256(source_ledger_path),
    )
    base.atomic_json(args.output, result)
    contract = result["validation_repeat_contract"]
    print(
        json.dumps(
            {
                "output": str(args.output),
                "roles": [item["selection_role"] for item in result["top5"]],
                "independent_same_group_batch_ordinal": contract[
                    "independent_same_group_batch_ordinal"
                ],
                "per_point_observation_counts": [
                    item["total_exact_observation_count"]
                    for item in contract[
                        "per_point_total_exact_observation_counts"
                    ]
                ],
                "novel_fingerprint_credit": 0,
                "new_workload_calls": 0,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
