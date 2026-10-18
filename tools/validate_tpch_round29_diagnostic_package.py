#!/usr/bin/env python3
"""Validate a TPC-H round29 diagnostic replay package without running it."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import tpch_round19_plan_regime_select as selector


EXPECTED_ACCEPTED_COUNT = 1
EXPECTED_INCUMBENT_SCORE = 3.8986963828608943
EXPECTED_TRUSTED_PROBE_ROLES = [
    "trusted",
    "trusted",
    "trusted",
    "probe",
    "probe",
]
EXPECTED_DISCOVERY_ROLES = ["probe"] * 5
WORKLOAD_DRIVER_NAME = (
    "run_pg_weighted_sql_baseline_profile_rolling_v4_completiongate.py"
)
FORBIDDEN_RUNNER_TEXT = (
    "args.accepted_counter.open",
    'state["accepted_count"] = accepted_after',
    '"incumbent_update_applied": True',
    '"range_update_applied": True',
    '"accepted_update_applied": True',
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def load_runner(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("round29_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load diagnostic runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate(
    selection_path: Path,
    state_path: Path,
    config_path: Path,
    gate_path: Path,
    runner_path: Path,
    workload_path: Optional[Path] = None,
    workload_driver_path: Optional[Path] = None,
    expected_workload_driver_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    selection = load(selection_path)
    state = load(state_path)
    config = load(config_path)
    gate = load(gate_path)
    runner_text = runner_path.read_text(encoding="utf-8")
    forbidden = [text for text in FORBIDDEN_RUNNER_TEXT if text in runner_text]
    if forbidden:
        raise RuntimeError(f"runner contains formal commit paths: {forbidden}")
    if "--diagnostic-only is mandatory" not in runner_text:
        raise RuntimeError("runner does not require explicit diagnostic mode")

    runner = load_runner(runner_path)
    candidates = runner.validate_selection(selection)
    roles = [candidate["selection_role"] for candidate in candidates]
    discovery = selection.get("selection_contract") in {
        "tpch_q1_completion_risk_probe5_discovery_v1",
        "exact_structural_plan_regime_stability_probe5_diagnostic_v1",
    }
    expected_roles = (
        EXPECTED_DISCOVERY_ROLES
        if discovery
        else EXPECTED_TRUSTED_PROBE_ROLES
    )
    if roles != expected_roles:
        raise RuntimeError(f"role mismatch: {roles}")

    registry = selection.get("validated_plan_regimes") or {}
    fingerprints = set()
    parameter_keys = set()
    regimes = []
    for rank, candidate in enumerate(candidates, 1):
        computed_structural_regime = selector.structural_plan_regime_id(
            candidate
        )
        if (
            candidate.get("structural_plan_regime_id")
            != computed_structural_regime
        ):
            raise RuntimeError(f"rank {rank} structural-regime mismatch")
        computed_regime = selector.exact_plan_regime_id(candidate)
        if candidate.get("exact_plan_regime_id") != computed_regime:
            raise RuntimeError(f"rank {rank} exact-plan-regime mismatch")
        fingerprint = selector.fingerprint_id(candidate)
        parameter_key = selector.parameter_key(candidate)
        fingerprints.add(fingerprint)
        parameter_keys.add(parameter_key)
        regimes.append(computed_regime)
        if discovery:
            non_corner = (
                candidate.get("actual_parameter_noncorner_audit") or {}
            )
            if (
                candidate.get("boundary_corner") is not False
                or non_corner.get("reason")
                != "actual_parameter_space_noncorner"
            ):
                raise RuntimeError(
                    f"discovery rank {rank} is not an audited non-corner"
                )
        else:
            if rank <= 3 and computed_regime not in registry:
                raise RuntimeError(
                    f"trusted rank {rank} is not in validated registry"
                )
            if rank > 3 and computed_regime in registry:
                raise RuntimeError(
                    f"probe rank {rank} is already a trusted regime"
                )
    if len(fingerprints) != 5 or len(parameter_keys) != 5:
        raise RuntimeError("Top-5 fingerprints or parameters are not unique")
    if not discovery and len(set(regimes[3:])) != 2:
        raise RuntimeError("probe regimes are not distinct")

    dependency_audit: Dict[str, Any] = {
        "checked": False,
        "workload_sha256": None,
        "workload_driver_sha256": None,
    }
    if workload_path is not None or workload_driver_path is not None:
        if workload_path is None or workload_driver_path is None:
            raise RuntimeError(
                "workload and workload driver must be validated together"
            )
        if not workload_path.is_file() or not workload_driver_path.is_file():
            raise RuntimeError("workload package dependency is missing")
        if workload_path.parent.resolve() != workload_driver_path.parent.resolve():
            raise RuntimeError(
                "workload and driver are not sealed in the same package"
            )
        workload_text = workload_path.read_text(encoding="utf-8")
        if WORKLOAD_DRIVER_NAME not in workload_text:
            raise RuntimeError(
                "workload does not reference the expected packaged driver"
            )
        driver_sha256 = sha256(workload_driver_path)
        if (
            expected_workload_driver_sha256 is not None
            and driver_sha256 != expected_workload_driver_sha256
        ):
            raise RuntimeError("workload driver SHA mismatch")
        dependency_audit = {
            "checked": True,
            "workload_sha256": sha256(workload_path),
            "workload_driver_sha256": driver_sha256,
        }

    if (
        state.get("schema_version")
        != "tpch_exact_plan_regime_formal_state_v1"
        or state.get("status") != "complete"
        or state.get("baseline_restored") is not True
        or int(state.get("physical_call_count", -1)) != 5
        or int(state.get("valid_replay_count", -1)) != 5
        or int(state.get("technical_failure_count", -1)) != 0
        or int(state.get("accepted_count", -1)) != EXPECTED_ACCEPTED_COUNT
        or int(state.get("batch_added_accepted_count", -1)) != 0
        or not math.isclose(
            float(state.get("incumbent_score")),
            EXPECTED_INCUMBENT_SCORE,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    ):
        raise RuntimeError("resume state is not the clean post-round28 state")

    formal = config.get("formal_outer_protocol") or {}
    diagnostic = config.get("_tpch_round29_diagnostic_replay") or {}
    if (
        int(formal.get("accepted_shrink_target", -1)) != 10
        or int(formal.get("formal_search_accepted_count_start", -1)) != 1
        or int(formal.get("formal_search_accepted_target", -1)) != 10
        or int(formal.get("formal_search_max_replay_top_k", -1)) != 5
        or diagnostic.get("diagnostic_only") is not True
        or diagnostic.get("accepted_update_allowed") is not False
        or diagnostic.get("incumbent_update_allowed") is not False
        or diagnostic.get("range_update_allowed") is not False
        or diagnostic.get("gradient_update_allowed") is not False
        or diagnostic.get("qps_used_for_inner_scoring") is not False
    ):
        raise RuntimeError("diagnostic config contract mismatch")

    runner.validate_gate_summary(gate)
    if (
        gate.get("diagnostic_only") is not True
        or gate.get("formal_state_unchanged") is not True
        or gate.get("selection_sha256") != state.get("selection_sha256")
    ):
        raise RuntimeError("diagnostic predecessor gate mismatch")

    return {
        "status": "passed",
        "selection_sha256": sha256(selection_path),
        "resume_state_sha256": sha256(state_path),
        "config_sha256": sha256(config_path),
        "gate_sha256": sha256(gate_path),
        "runner_sha256": sha256(runner_path),
        "roles": roles,
        "unique_fingerprint_count": len(fingerprints),
        "unique_parameter_count": len(parameter_keys),
        "trusted_validated_exact_regime_count": 0 if discovery else 3,
        "unknown_distinct_probe_regime_count": (
            len(set(regimes)) if discovery else 2
        ),
        "diagnostic_formal_commit_paths": 0,
        "maximum_physical_calls": 5,
        "package_dependency_audit": dependency_audit,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--resume-state", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--workload", type=Path)
    parser.add_argument("--workload-driver", type=Path)
    parser.add_argument("--expected-workload-driver-sha256")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = validate(
        args.selection,
        args.resume_state,
        args.config,
        args.gate,
        args.runner,
        args.workload,
        args.workload_driver,
        args.expected_workload_driver_sha256,
    )
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
