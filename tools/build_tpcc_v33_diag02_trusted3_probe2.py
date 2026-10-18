#!/usr/bin/env python3
"""Build one bounded TPC-C v33 Top-5 from diagnostic_02 only."""

from __future__ import annotations

import argparse
import copy
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

try:
    import build_tpcc_round14_robust_regime_top5_v24 as robust
except ModuleNotFoundError:
    from tools import build_tpcc_round14_robust_regime_top5_v24 as robust


MAX_GENERATION_ATTEMPTS = 1
MAX_BUILDER_WALL_SECONDS = 1800
EXPECTED_ROLES = [
    "trusted",
    "trusted",
    "trusted",
    "uncertain_probe",
    "uncertain_probe",
]


def _load(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def validate_clean_probe_source(
    selection: Mapping[str, Any],
    ledger: Mapping[str, Any],
) -> Dict[str, Any]:
    candidates = selection.get("top5") or []
    successful = ledger.get("successful") or []
    if (
        len(candidates) != 5
        or int(ledger.get("real_workload_invocation_count", -1)) != 5
        or len(successful) != 5
        or ledger.get("technical_failures") != []
    ):
        raise RuntimeError("TPC-C v33 source is not clean 5/5/0")
    selected = {int(item["rank"]): item for item in candidates}
    if set(selected) != set(range(1, 6)):
        raise RuntimeError("TPC-C v33 source ranks are incomplete")
    known_plans = set()
    source_ids = set()
    for result in successful:
        rank = int(result["rank"])
        candidate = selected.get(rank) or {}
        protocol = result.get("measurement_protocol") or {}
        fingerprint = result.get("three_level_fingerprint") or {}
        if (
            candidate.get("selection_role") != "uncertain_probe"
            or candidate.get("trusted") is not False
            or result.get("selection_role") != "uncertain_probe"
            or result.get("trusted") is not False
            or result.get("diagnostic_only") is not True
            or result.get("eligible_for_incumbent_or_accepted") is not False
            or protocol.get("stable") is not True
            or protocol.get("correctness_passed") is not True
            or protocol.get("technical_failure") is not False
            or result.get("workload_plan_fingerprint")
            != candidate.get("workload_plan_fingerprint")
            or fingerprint.get("id")
            != (candidate.get("three_level_fingerprint") or {}).get("id")
        ):
            raise RuntimeError(f"TPC-C v33 source rank {rank} is not a valid probe")
        known_plans.add(str(result["workload_plan_fingerprint"]))
        source_ids.add(str(fingerprint["id"]))
    return {
        "known_plans": sorted(known_plans),
        "source_fingerprint_ids": sorted(source_ids),
    }


def bounded_no_solution_audit(
    *,
    elapsed_seconds: float,
    error: BaseException,
) -> Dict[str, Any]:
    """Describe the single-attempt, zero-workload terminal outcome."""
    return {
        "contract": "tpcc_v33_bounded_no_solution_v1",
        "status": "no_solution_fail_closed",
        "generation_attempt_count": MAX_GENERATION_ATTEMPTS,
        "maximum_generation_attempts": MAX_GENERATION_ATTEMPTS,
        "maximum_builder_wall_seconds": MAX_BUILDER_WALL_SECONDS,
        "elapsed_seconds": round(float(elapsed_seconds), 6),
        "physical_workload_calls_added": 0,
        "formal_state_mutated": False,
        "manual_candidate_injection": False,
        "automatic_unbounded_retry": False,
        "error_type": type(error).__name__,
        "error_message": str(error),
    }


def validate_generated_top5(
    selection: Mapping[str, Any],
    *,
    source_contract: Mapping[str, Any],
    globally_used_ids: Sequence[str],
) -> Dict[str, Any]:
    top5 = selection.get("top5") or []
    roles = [item.get("selection_role") for item in top5]
    ids = [
        str((item.get("three_level_fingerprint") or {}).get("id") or "")
        for item in top5
    ]
    known_plans = set(source_contract["known_plans"])
    collisions = sorted(set(ids) & set(globally_used_ids))
    trusted = top5[:3]
    probes = top5[3:]
    if (
        len(top5) != 5
        or roles != EXPECTED_ROLES
        or len(set(ids)) != 5
        or collisions
        or selection.get("maximum_real_workload_invocations") != 5
        or selection.get("qps_role") != "outer_audit_only_not_inner_score"
        or any(item.get("boundary_corner") is not False for item in top5)
        or any(item.get("diagnostic_only") is not True for item in top5)
        or any(item.get("incumbent_update_eligible") is not False for item in top5)
        or any(item.get("trusted") is not True for item in trusted)
        or any(
            item.get("workload_plan_fingerprint") not in known_plans
            for item in trusted
        )
        or any(item.get("trusted") is not False for item in probes)
    ):
        raise RuntimeError(
            "TPC-C v33 generated Top-5 violates trusted3/probe2/novelty contract"
        )
    return {
        "contract": "tpcc_v33_diag02_training_only_top5_gate_v1",
        "passed": True,
        "roles": roles,
        "fingerprint_ids": ids,
        "global_collision_ids": collisions,
        "trusted_plan_regimes": [
            str(item["workload_plan_fingerprint"]) for item in trusted
        ],
        "probe_plan_regimes": [
            str(item["workload_plan_fingerprint"]) for item in probes
        ],
        "source_probe_rows_retroactively_trusted": False,
        "new_candidates_in_validated_exact_plan_regime_may_be_trusted": True,
        "physical_workload_calls_added": 0,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--formal-state", required=True, type=Path)
    parser.add_argument("--excluded-fingerprints", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = [
        args.selection.resolve(),
        args.ledger.resolve(),
        args.formal_state.resolve(),
        args.excluded_fingerprints.resolve(),
    ]
    if any(not path.is_file() for path in paths) or args.output.exists():
        raise RuntimeError("TPC-C v33 input missing or output already exists")
    selection_path, ledger_path, formal_state, exclusions_path = paths
    source_contract = validate_clean_probe_source(
        _load(selection_path),
        _load(ledger_path),
    )
    exclusions = _load(exclusions_path)
    globally_used_ids = [str(value) for value in exclusions["fingerprint_ids"]]
    if not set(source_contract["source_fingerprint_ids"]).issubset(
        set(globally_used_ids)
    ):
        raise RuntimeError("TPC-C v33 global exclusion set omits source probes")

    robust.SOURCES = ((selection_path, ledger_path, "v33_diag02_training_only"),)
    robust.OUTPUT = args.output.resolve()
    robust.PARAM_RANGES_OVERRIDE = formal_state
    robust.FORMAL_STATE_AUDIT_SOURCE = formal_state
    robust.CANDIDATE_RANGE_POLICY = "authoritative_fresh0_range_v33"
    robust.PROBES_MUST_BE_UNKNOWN = False
    robust.ALLOW_SPARSE_EXACT_REGIME_PROBES = True
    robust.SPARSE_EXACT_REGIME_MAX_OBSERVATIONS = 1
    robust.TRUSTED_PLAN_DIVERSITY_REQUIRED = False
    robust.DIAGNOSTIC_PROBE_ONLY = False
    robust.ALLOW_VALIDATED_ANCHOR_REPLAY = False
    robust.FORMAL_EXPECTED_IMPROVEMENT_REQUIRED = False
    robust.MIN_TRUSTED_PREDICTED_SCORE_SPAN = 0.0
    robust.MINIMUM_DIAGNOSTIC_SCORE_SPAN = 0.0
    robust.EXTRA_EXCLUDED_FINGERPRINTS = set(globally_used_ids)

    started = time.perf_counter()
    try:
        robust.main()
        generated = _load(args.output.resolve())
        gate = validate_generated_top5(
            generated,
            source_contract=source_contract,
            globally_used_ids=globally_used_ids,
        )
        generated["selection_contract"] = (
            "tpcc_robust_exact_plan_regime_trusted3_probe2_v1"
        )
        generated["diagnostic_role_evaluation_only"] = True
        generated["state_mutation_contract"] = {
            "accepted_update": False,
            "gradient_update": False,
            "incumbent_update": False,
            "range_update": False,
        }
        generated["v33_source_contract"] = source_contract
        generated["v33_generation_gate"] = gate
        generated["v33_bounded_generation"] = {
            "generation_attempt_count": 1,
            "maximum_generation_attempts": MAX_GENERATION_ATTEMPTS,
            "maximum_builder_wall_seconds": MAX_BUILDER_WALL_SECONDS,
            "automatic_unbounded_retry": False,
        }
        _atomic_json(args.output.resolve(), generated)
    except Exception as exc:
        if args.output.exists():
            quarantine = args.output.with_suffix(args.output.suffix + ".invalid")
            if quarantine.exists():
                raise RuntimeError("TPC-C v33 invalid-output quarantine exists") from exc
            os.replace(args.output, quarantine)
        _atomic_json(
            args.output.with_suffix(args.output.suffix + ".no_solution.json"),
            bounded_no_solution_audit(
                elapsed_seconds=time.perf_counter() - started,
                error=exc,
            ),
        )
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
