#!/usr/bin/env python3
"""Build a sealed TPC-C exact-repeat selection with immutable provenance."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Mapping

try:
    import build_tpcc_exact_plan_repeat_validation_v3 as v3
except ModuleNotFoundError:
    from tools import build_tpcc_exact_plan_repeat_validation_v3 as v3


REPEAT_CONTRACT = "tpcc_sealed_exact_top5_independent_repeat_v32"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _params(candidate: Mapping[str, Any]) -> Dict[str, float]:
    raw = candidate.get("params") or candidate.get("candidate_params")
    if not isinstance(raw, Mapping) or not raw:
        raise RuntimeError("exact-repeat candidate is missing parameters")
    return {name: float(raw[name]) for name in sorted(raw)}


def exact_top5_identity(selection: Mapping[str, Any]) -> Dict[str, Any]:
    """Return an order- and role-independent exact parameter/plan identity."""
    points = []
    for candidate in selection.get("top5") or []:
        plan = str(candidate.get("workload_plan_fingerprint") or "")
        if not plan.startswith("ratio-plan-sha256:"):
            raise RuntimeError("exact-repeat candidate has no validated plan")
        params = _params(candidate)
        points.append(
            {
                "parameter_sha256": _canonical_sha256(params),
                "params": params,
                "workload_plan_fingerprint": plan,
            }
        )
    if len(points) != 5:
        raise RuntimeError("exact-repeat identity requires exactly five points")
    points.sort(
        key=lambda item: (
            item["parameter_sha256"],
            item["workload_plan_fingerprint"],
        )
    )
    if len({_canonical_sha256(item) for item in points}) != 5:
        raise RuntimeError("exact-repeat Top-5 contains duplicate exact points")
    return {
        "contract": "tpcc_exact_parameter_plan_set_v1",
        "points": points,
        "sha256": _canonical_sha256(points),
    }


def add_repeat_contract(
    output: Dict[str, Any],
    source_selection: Mapping[str, Any],
    *,
    source_selection_path: Path,
    source_selection_sha256: str,
    source_ledger_path: Path,
    source_ledger_sha256: str,
) -> Dict[str, Any]:
    result = copy.deepcopy(output)
    source_identity = exact_top5_identity(source_selection)
    repeat_identity = exact_top5_identity(result)
    if repeat_identity["sha256"] != source_identity["sha256"]:
        raise RuntimeError("repeat builder changed the exact parameter/plan set")
    counts = [
        int((candidate.get("confidence") or {}).get("measurement_count", -1))
        for candidate in result["top5"]
    ]
    if min(counts) < 1 or len(set(counts)) != 1:
        raise RuntimeError(
            f"exact-repeat Top-5 has inconsistent prior run counts: {counts}"
        )
    prior_count = counts[0]
    result["validation_repeat_contract"] = {
        "contract": REPEAT_CONTRACT,
        "source_selection_path": str(source_selection_path.resolve()),
        "source_selection_sha256": source_selection_sha256,
        "source_ledger_path": str(source_ledger_path.resolve()),
        "source_ledger_sha256": source_ledger_sha256,
        "source_top5_identity_sha256": source_identity["sha256"],
        "repeat_top5_identity_sha256": repeat_identity["sha256"],
        "prior_complete_run_count": prior_count,
        "independent_run_ordinal": prior_count + 1,
        "novel_fingerprint_credit": 0,
        "same_exact_parameter_plan_set": True,
        "source_selection_sha_verified": True,
    }
    result["schema_version"] = "tpcc_exact_plan_repeat_validation_v4"
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
    output = v3.build(
        source_selection,
        source_ledger,
        args.minimum_score_span,
    )
    result = add_repeat_contract(
        output,
        source_selection,
        source_selection_path=source_selection_path,
        source_selection_sha256=_sha256(source_selection_path),
        source_ledger_path=source_ledger_path,
        source_ledger_sha256=_sha256(source_ledger_path),
    )
    base.atomic_json(args.output, result)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "roles": [item["selection_role"] for item in result["top5"]],
                "independent_run_ordinal": result[
                    "validation_repeat_contract"
                ]["independent_run_ordinal"],
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
