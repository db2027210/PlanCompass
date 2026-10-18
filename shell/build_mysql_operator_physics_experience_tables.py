#!/usr/bin/env python3
"""Build and optionally promote five MySQL operator-physics experience tables.

This is an offline prior, not a measurement calibrator.  The signed effects
describe how increasing a MySQL cost parameter across its frozen log range
changes executor resource pressure through the plan operators it encourages or
discourages.  Every artifact explicitly records ``real_measurement=false``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SPACE_PATH = (
    ROOT
    / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v3"
    / "effective_parameter_spaces.json"
)
REGISTRY_ROOT = SPACE_PATH.parent / "plan_workload_registries"
READINESS = (
    ROOT
    / "config/formal/frozen_mysql_deployment_schedule_20260723_v4_mysql_only"
    / "readiness_manifest.json"
)
OUTPUT_ROOT = (
    ROOT
    / "config/formal/frozen_mysql_operator_physics_experience_tables_20260724_v1"
)
FREEZE_ID = "frozen_mysql_operator_physics_experience_tables_20260724_v1"
WORKLOAD_FREEZE_ID = "frozen_mysql_workloads_and_parameters_20260723_v3"
RESOURCES = ("cpu", "memory", "io")

SCENARIOS = {
    "job_memory": {
        "benchmark": "job",
        "registry": "job.mysql_plan_workload_registry_v1.json",
    },
    "hybench_xp_io": {
        "benchmark": "hybench_xp",
        "registry": "hybench_xp.production_binding_capture_contract_v1.json",
    },
    "tpch_memory": {
        "benchmark": "tpch",
        "registry": "tpch.production_binding_capture_contract_v1.json",
    },
    "tpcc_cpu": {
        "benchmark": "tpcc",
        "registry": "tpcc.production_binding_capture_contract_v1.json",
    },
}

# Effects are utilization-fraction deltas across the complete frozen log range.
# They are deliberately conservative physical priors, not planner cost values.
PARAMETER_RULES: dict[str, dict[str, Any]] = {
    "io_block_read_cost": {
        "planner_semantics": "raise the price of disk-page access",
        "discouraged_operators": [
            "disk-page-heavy table scan",
            "random heap/page fetch",
            "disk-backed materialization",
        ],
        "encouraged_operators": [
            "covering index access",
            "early selective lookup",
            "lower-page-count join order",
        ],
        "base_resource_effect": {"cpu": 0.08, "memory": 0.04, "io": -0.32},
    },
    "memory_block_read_cost": {
        "planner_semantics": "raise the price of buffer-resident page access",
        "discouraged_operators": [
            "buffer-page-heavy scan",
            "repeated cached inner lookup",
        ],
        "encouraged_operators": [
            "early selective predicate",
            "lower-page-count access path",
        ],
        "base_resource_effect": {"cpu": 0.04, "memory": -0.08, "io": 0.06},
    },
    "row_evaluate_cost": {
        "planner_semantics": "raise the price of evaluating each candidate row",
        "discouraged_operators": [
            "high-cardinality scan filter",
            "large nested-loop probe stream",
            "late predicate evaluation",
        ],
        "encouraged_operators": [
            "index condition pushdown",
            "early filtering",
            "selective join order",
        ],
        "base_resource_effect": {"cpu": -0.28, "memory": 0.08, "io": 0.04},
    },
    "key_compare_cost": {
        "planner_semantics": "raise the price of key comparison",
        "discouraged_operators": [
            "comparison-heavy filesort",
            "repeated ordered-key probe",
            "merge-style comparison stream",
        ],
        "encouraged_operators": [
            "hash lookup",
            "lower-comparison join order",
        ],
        "base_resource_effect": {"cpu": -0.16, "memory": 0.10, "io": 0.05},
    },
    "disk_temptable_create_cost": {
        "planner_semantics": "raise the fixed price of creating a disk temporary table",
        "discouraged_operators": [
            "disk temporary table",
            "disk materialization",
        ],
        "encouraged_operators": [
            "pipeline execution",
            "in-memory aggregation",
        ],
        "base_resource_effect": {"cpu": 0.05, "memory": 0.15, "io": -0.25},
    },
    "disk_temptable_row_cost": {
        "planner_semantics": "raise the per-row price of a disk temporary table",
        "discouraged_operators": [
            "large disk materialization",
            "spill-heavy sort or aggregate",
        ],
        "encouraged_operators": [
            "pipeline execution",
            "in-memory hash or aggregate",
        ],
        "base_resource_effect": {"cpu": 0.08, "memory": 0.18, "io": -0.30},
    },
    "memory_temptable_create_cost": {
        "planner_semantics": "raise the fixed price of creating an in-memory temporary table",
        "discouraged_operators": [
            "in-memory temporary table",
            "materialize-once plan",
        ],
        "encouraged_operators": [
            "pipeline recomputation",
            "disk-backed alternative when unavoidable",
        ],
        "base_resource_effect": {"cpu": 0.04, "memory": -0.20, "io": 0.18},
    },
    "memory_temptable_row_cost": {
        "planner_semantics": "raise the per-row price of an in-memory temporary table",
        "discouraged_operators": [
            "large in-memory materialization",
            "memory-heavy temporary aggregate",
        ],
        "encouraged_operators": [
            "pipeline execution",
            "bounded-memory or disk-backed alternative",
        ],
        "base_resource_effect": {"cpu": 0.06, "memory": -0.25, "io": 0.22},
    },
}

BENCHMARK_MULTIPLIERS = {
    "hybench_ap": {"cpu": 0.90, "memory": 1.20, "io": 1.00},
    "job": {"cpu": 1.20, "memory": 1.10, "io": 1.00},
    "hybench_xp": {"cpu": 1.00, "memory": 0.80, "io": 1.30},
    "tpch": {"cpu": 1.15, "memory": 1.15, "io": 1.10},
    "tpcc": {"cpu": 1.30, "memory": 0.70, "io": 0.70},
}


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def pointer(path: Path) -> dict[str, str]:
    return {"path": relative(path), "sha256": sha256_file(path)}


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json_with_sidecar(path: Path, value: Mapping[str, Any]) -> str:
    data = canonical_bytes(value)
    digest = sha256_bytes(data)
    atomic_write(path, data)
    atomic_write(
        path.with_name(path.name + ".sha256"),
        f"{digest}  {path.name}\n".encode("ascii"),
    )
    return digest


def model_document() -> dict[str, Any]:
    return {
        "schema_version": "mysql_operator_physics_resource_effect_model_v1",
        "version": FREEZE_ID,
        "database": "mysql",
        "formal_scope": "mysql_only",
        "cross_engine_prior_used": False,
        "real_measurement": False,
        "effect_unit": "utilization_fraction_change_across_frozen_parameter_log_range",
        "composition": (
            "parameter planner semantics -> encouraged/discouraged executor "
            "operators -> CPU/memory/IO physical pressure"
        ),
        "magnitude_policy": (
            "conservative bounded expert prior; optional R0/OAT calibration may "
            "rescale magnitude without changing the frozen source classification"
        ),
        "parameter_rules": PARAMETER_RULES,
        "benchmark_resource_multipliers": BENCHMARK_MULTIPLIERS,
    }


def load_spaces() -> tuple[dict[str, Any], str]:
    value = json.loads(SPACE_PATH.read_text(encoding="utf-8"))
    definitions = value["all_parameter_definitions"]
    candidates = value["benchmark_candidate_parameters"]
    spaces: dict[str, Any] = {}
    for benchmark, active in candidates.items():
        spaces[benchmark] = {
            "active_params": list(active),
            "ranges": {
                name: {
                    "lower": float(definitions[name]["lower"]),
                    "upper": float(definitions[name]["upper"]),
                }
                for name in active
            },
        }
    return spaces, sha256_file(SPACE_PATH)


def build_table(
    scenario: str,
    benchmark: str,
    registry_path: Path,
    space: Mapping[str, Any],
    space_sha: str,
    model_path: Path,
) -> dict[str, Any]:
    model_sha = sha256_file(model_path)
    registry_sha = sha256_file(registry_path)
    multiplier = BENCHMARK_MULTIPLIERS[benchmark]
    table: dict[str, Any] = {}
    evidence: dict[str, Any] = {}
    for parameter in space["active_params"]:
        rule = PARAMETER_RULES[parameter]
        row = {
            resource: round(
                float(rule["base_resource_effect"][resource])
                * float(multiplier[resource]),
                12,
            )
            for resource in RESOURCES
        }
        if any(not math.isfinite(value) or abs(value) > 0.5 for value in row.values()):
            raise ValueError(f"unsafe physical prior for {benchmark}.{parameter}: {row}")
        table[parameter] = row
        evidence[parameter] = {}
        for resource, value in row.items():
            direction = "increase" if value > 0 else "decrease" if value < 0 else "neutral"
            decision = "SUPPORTED" if value != 0 else "NO_DETECTABLE_EFFECT"
            evidence[parameter][resource] = {
                "direction": direction,
                "signed_effect_full_range": value,
                "observed_median_effect_full_range": value,
                "observed_delta": value,
                "magnitude_full_range": abs(value),
                "tolerance": 1e-12,
                "evidence_status": (
                    "DETECTED_EFFECT" if value != 0 else "NO_DETECTABLE_EFFECT"
                ),
                "parameter_effect_status": (
                    "DETECTED_EFFECT" if value != 0 else "NO_DETECTABLE_EFFECT"
                ),
                "confidence": {
                    "level": "PHYSICAL_PRIOR",
                    "decision": decision,
                    "source": "operator_physics_experience",
                    "real_measurement": False,
                    "cross_engine_prior_used": False,
                    "parameter_operator_chain_complete": True,
                    "magnitude_calibrated": False,
                },
            }

    request_sha = sha256_bytes(
        canonical_bytes(
            {
                "scenario": scenario,
                "benchmark": benchmark,
                "space_sha256": space_sha,
                "registry_sha256": registry_sha,
                "model_sha256": model_sha,
            }
        )
    )
    return {
        "schema_version": "mysql_experience_table_t_v1",
        "database": "mysql",
        "scenario": scenario,
        "benchmark": benchmark,
        "benchmark_version": WORKLOAD_FREEZE_ID,
        "version": f"{scenario}.operator_physics_T_v1",
        "workload_freeze_id": WORKLOAD_FREEZE_ID,
        "workload_registry_sha256": registry_sha,
        "sql_registry": {"id": registry_path.stem, "sha256": registry_sha},
        "estimator": {
            "id": "mysql_parameter_operator_resource_physics_v1",
            "version": FREEZE_ID,
            "sha256": model_sha,
        },
        "parameter_space": {
            "version": WORKLOAD_FREEZE_ID,
            "sha256": space_sha,
            "active_params": list(space["active_params"]),
            "ranges": dict(space["ranges"]),
        },
        "resource_columns": list(RESOURCES),
        "table": table,
        "effect_evidence": evidence,
        "calibration": {
            "method": "mysql_parameter_operator_resource_expert_prior_v1",
            "source": "operator_physics_experience",
            "real_measurement": False,
            "scale_status": "UNCALIBRATED_PHYSICAL_PRIOR",
            "magnitude_unit": (
                "utilization_fraction_change_across_frozen_parameter_log_range"
            ),
            "optional_calibration": (
                "R0 baseline scaling or paired OAT may refine magnitudes later"
            ),
        },
        "provenance": {
            "request_sha256": request_sha,
            "evidence_class": "mysql_operator_physics_parameter_operator_resource_v1",
            "source": "operator_physics_experience",
            "real_measurement": False,
            "cross_engine_prior_used": False,
            "operator_model": pointer(model_path),
            "generator": pointer(Path(__file__)),
        },
    }


def update_checksum_index(bundle: Path, manifest_digest: str) -> None:
    checksum_path = bundle / "checksums.sha256"
    values: dict[str, str] = {}
    for line in checksum_path.read_text(encoding="ascii").splitlines():
        parts = line.split("  ", 1)
        if len(parts) == 2:
            values[parts[1]] = parts[0]
    sidecar = bundle / "readiness_manifest.json.sha256"
    values["readiness_manifest.json"] = manifest_digest
    values["readiness_manifest.json.sha256"] = sha256_file(sidecar)
    atomic_write(
        checksum_path,
        "".join(f"{values[name]}  {name}\n" for name in sorted(values)).encode("ascii"),
    )


def promote(entries: Mapping[str, Mapping[str, str]]) -> None:
    manifest = json.loads(READINESS.read_text(encoding="utf-8"))
    for scenario, artifact_pointer in entries.items():
        manifest["scenarios"][scenario]["experience_table"] = {
            "version": f"{scenario}.operator_physics_T_v1",
            "artifact": dict(artifact_pointer),
        }
    digest = write_json_with_sidecar(READINESS, manifest)
    update_checksum_index(READINESS.parent, digest)


def build(promote_tables: bool) -> dict[str, Any]:
    spaces, space_sha = load_spaces()
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    model_path = OUTPUT_ROOT / "operator_physics_model_v1.json"
    write_json_with_sidecar(model_path, model_document())
    entries: dict[str, dict[str, str]] = {}
    for scenario, config in SCENARIOS.items():
        benchmark = config["benchmark"]
        registry_path = REGISTRY_ROOT / config["registry"]
        artifact_path = OUTPUT_ROOT / f"T.{scenario}.operator_physics_v1.json"
        artifact = build_table(
            scenario,
            benchmark,
            registry_path,
            spaces[benchmark],
            space_sha,
            model_path,
        )
        write_json_with_sidecar(artifact_path, artifact)
        entries[scenario] = pointer(artifact_path)
    bundle_manifest = {
        "schema_version": "mysql_operator_physics_experience_bundle_v1",
        "freeze_id": FREEZE_ID,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "real_measurement": False,
        "status": "FROZEN_OPERATOR_PHYSICS_PRIOR",
        "model": pointer(model_path),
        "parameter_space": pointer(SPACE_PATH),
        "tables": entries,
    }
    write_json_with_sidecar(OUTPUT_ROOT / "manifest.json", bundle_manifest)
    checksum_lines = []
    for path in sorted(OUTPUT_ROOT.iterdir()):
        if path.is_file() and path.name != "checksums.sha256":
            checksum_lines.append(f"{sha256_file(path)}  {path.name}\n")
    atomic_write(
        OUTPUT_ROOT / "checksums.sha256",
        "".join(checksum_lines).encode("ascii"),
    )
    if promote_tables:
        promote(entries)
    return {
        "status": "FROZEN_AND_PROMOTED" if promote_tables else "FROZEN",
        "freeze_id": FREEZE_ID,
        "output": relative(OUTPUT_ROOT),
        "tables": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--promote",
        action="store_true",
        help="write the five table pointers into the mysql_only readiness manifest",
    )
    args = parser.parse_args()
    print(json.dumps(build(args.promote), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
