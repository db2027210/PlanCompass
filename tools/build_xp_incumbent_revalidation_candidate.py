#!/usr/bin/env python3
"""Build one fresh EXPLAIN-only XP incumbent revalidation candidate."""

from __future__ import annotations

import argparse
import copy
import importlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, Mapping


RESOURCES = ("cpu", "io", "memory")


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    """Write a JSON object atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--accepted-incumbent", required=True, type=Path)
    parser.add_argument("--query-snapshot", required=True, type=Path)
    parser.add_argument("--formal-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=59630, type=int)
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--password", default="postgres")
    parser.add_argument("--dbname", default="hybench_sf10x")
    return parser.parse_args()


def first_mapping(
    *values: Any,
    fallback: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return the first non-empty mapping, or an explicit fallback."""
    for value in values:
        if isinstance(value, Mapping) and value:
            return copy.deepcopy(dict(value))
    return copy.deepcopy(dict(fallback))


def main() -> int:
    args = parse_args()
    if args.output.exists():
        raise FileExistsError(f"output already exists: {args.output}")
    bundle_root = args.bundle_root.resolve()
    sys.path.insert(0, str(bundle_root))
    module = importlib.import_module("xp_round10_explain_only_entrypoint")

    inputs = module._load_authoritative_inputs(
        args.config,
        args.selection,
        args.accepted_incumbent,
        args.query_snapshot,
    )
    os.environ.setdefault("PGPASSWORD", args.password)
    audit = module.ExplainOnlyAudit()
    session = module.LocalPgExplainSession(
        args.host,
        args.port,
        args.user,
        args.dbname,
        audit,
    )
    baseline_check: Dict[str, Any] = {}
    try:
        factory = module._estimator_factory(
            inputs=inputs,
            formal_root=args.formal_root,
            session=session,
            audit=audit,
            baseline_check=baseline_check,
        )
        active = list(
            (
                inputs["config"].get("experience_table_contract")
                or {}
            ).get("active_params")
            or inputs["incumbent_params"]
        )
        estimate = factory(active, "xp_incumbent_protocol_revalidation")
        resource, _stats, metadata = estimate(
            inputs["queries"],
            inputs["incumbent_params"],
        )
    finally:
        session.close()

    capability = {
        name: float(inputs["config"]["capability"][name])
        for name in RESOURCES
    }
    predicted_resource = {
        name: float(resource[name])
        for name in RESOURCES
    }
    utilization = {
        name: predicted_resource[name] / capability[name]
        for name in RESOURCES
    }
    fingerprint = module._fresh_three_level_fingerprint(
        metadata,
        predicted_resource,
        capability,
    )
    if fingerprint.get("valid") is not True:
        raise RuntimeError(
            "fresh incumbent fingerprint is invalid: "
            + ",".join(fingerprint.get("validation_errors") or [])
        )

    seed = copy.deepcopy(dict(inputs["seed"]))
    seed_metadata = seed.get("final_estimation_metadata") or {}
    parameter_names = sorted(inputs["incumbent_params"])
    zeros = {name: 0.0 for name in parameter_names}
    zero_delta = {
        name: {
            "before": float(inputs["incumbent_params"][name]),
            "proposed_before_clip": float(
                inputs["incumbent_params"][name]
            ),
            "after_clip": float(inputs["incumbent_params"][name]),
            "absolute": 0.0,
            "relative": 0.0,
            "clipped": False,
            "boundary_side": None,
        }
        for name in parameter_names
    }
    sensitivity = first_mapping(
        metadata.get("T"),
        metadata.get("sensitivity_table"),
        seed.get("T"),
        seed_metadata.get("T"),
        fallback=inputs["config"].get("sensitivity_table") or {},
    )
    confidence = first_mapping(
        metadata.get("confidence"),
        seed.get("confidence"),
        seed_metadata.get("confidence"),
        fallback={name: 1.0 for name in parameter_names},
    )
    score_derivative = first_mapping(
        seed.get("dScore/dU"),
        seed.get("dScore_dU"),
        seed_metadata.get("dScore/dU"),
        seed_metadata.get("dScore_dU"),
        fallback={
            name: {resource_name: 0.0 for resource_name in RESOURCES}
            for name in parameter_names
        },
    )
    candidate_score = float(module.acquisition_score(utilization))
    candidate = {
        "rank": 1,
        "selection_role": "incumbent_protocol_revalidation",
        "diagnostic_only": True,
        "candidate_origin": "fresh_incumbent_explain_revalidation",
        "probe_kind": "incumbent_protocol_revalidation",
        "boundary_corner": False,
        "boundary_plateau": False,
        "candidate_params": copy.deepcopy(inputs["incumbent_params"]),
        "optimized_params": copy.deepcopy(inputs["incumbent_params"]),
        "feature_ratio": copy.deepcopy(
            metadata.get("feature_ratios")
            or metadata.get("feature_ratio")
        ),
        "predicted_resource": predicted_resource,
        "final_resource": copy.deepcopy(predicted_resource),
        "utilization": utilization,
        "dScore/dU": score_derivative,
        "T": sensitivity,
        "confidence": confidence,
        "g_j": zeros,
        "eta": 0.0,
        "delta_param": zero_delta,
        "candidate_score": candidate_score,
        "workload_plan_fingerprint": fingerprint["plan_level"],
        "three_level_fingerprint": fingerprint,
        "used_for_gradient": False,
        "used_for_inner_incumbent_update": False,
        "used_for_formal_state_update": False,
        "qps_used_for_inner_scoring": False,
        "final_estimation_metadata": copy.deepcopy(metadata),
    }
    module.validate_diagnostic_record(
        candidate,
        context="XP incumbent revalidation candidate",
    )
    payload = {
        "contract": "xp_incumbent_protocol_revalidation_candidate_v1",
        "status": "ready",
        "diagnostic_only": True,
        "maximum_physical_workload_invocations": 1,
        "accepted_update_applied": False,
        "incumbent_update_applied": False,
        "range_update_applied": False,
        "gradient_update_applied": False,
        "qps_used_for_inner_scoring": False,
        "affinity": {
            "cpuset_cpus": "32-51,288-307",
            "excluded_cpus": "184-199,440-455",
        },
        "runtime_guard": audit.snapshot(),
        "baseline_explain_audit": baseline_check,
        "candidate": candidate,
    }
    atomic_json(args.output, payload)
    print(
        json.dumps(
            {
                "status": "ready",
                "output": str(args.output),
                "fingerprint": fingerprint["id"],
                "predicted_score": candidate_score,
                "workload_calls": 0,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
