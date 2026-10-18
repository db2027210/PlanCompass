#!/usr/bin/env python3
"""Build and verify the node01 TPC-H attempt57 follow-up manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence


LANE_ROOT = Path(
    "/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/"
    "pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01"
)
ATTEMPT_ROOT = (
    LANE_ROOT
    / "experience_sgd/attempt57_sf10_strict_r0_seeded_cpu7p7_w36_20260727"
)
MANIFEST_PATH = ATTEMPT_ROOT / "followup/attempt57_followup_manifest_v5.json"
CALIBRATION_ROOT = ATTEMPT_ROOT / "followup/direction_calibration_v5"
CALIBRATION_SUMMARY = CALIBRATION_ROOT / "calibration_summary.json"
READINESS_PATH = ATTEMPT_ROOT / "followup/outer_launch_readiness_v5.json"

FORBIDDEN_TOKENS = ("node04", "attempt48")
PARAMETERS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
DEFAULTS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}
ENDPOINTS = {
    "seq_page_cost": (0.1, 100.0),
    "random_page_cost": (0.1, 100.0),
    "cpu_tuple_cost": (0.001, 0.1),
    "cpu_index_tuple_cost": (0.0005, 0.05),
    "cpu_operator_cost": (0.00025, 0.025),
    "parallel_setup_cost": (10.0, 10000.0),
    "parallel_tuple_cost": (0.001, 1.0),
}
PROFILE = {
    "machine": "node01",
    "benchmark": "tpch",
    "scale_factor": 10,
    "database": "fullgrid_tpch_sf10_migrated_20260727",
    "container": "pg5_tpch_ap_slot_node01_20260726",
    "host_port": 63301,
    "container_port": 64304,
    "cpu_limit_cores": 7.7,
    "workers": 36,
    "memory_limit_bytes": 2820000000,
    "io_read_cap_mib_s": 16.0,
    "io_write_cap_mib_s": 20.0,
    "io_total_cap_mib_s": 36.0,
    "seed": 20260722,
    "measurement_schedule": "balanced_seeded_cycle",
    "warmup_seconds": 180,
    "minimum_measurement_seconds": 180,
    "maximum_measurement_seconds": 600,
    "target_resource": "memory",
    "candidate_parameter_application": "transaction_local_set_local",
    "candidate_immediate_readback_required": True,
    "global_parameter_changes_allowed": False,
}


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def reject_forbidden(value: Any, *, field: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            reject_forbidden(item, field=f"{field}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            reject_forbidden(item, field=f"{field}[{index}]")
        return
    if isinstance(value, str):
        text = value.lower()
        found = [token for token in FORBIDDEN_TOKENS if token in text]
        if found:
            raise RuntimeError(f"{field} contains forbidden lineage {found}: {value}")


def require_attempt57_path(path: Path, *, field: str) -> Path:
    resolved = path.resolve()
    reject_forbidden(resolved, field=field)
    try:
        resolved.relative_to(ATTEMPT_ROOT)
    except ValueError as exc:
        raise RuntimeError(f"{field} must be attempt57-owned: {resolved}") from exc
    return resolved


def finite_float(value: Any, *, field: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise RuntimeError(f"{field} must be finite")
    return result


def validate_resource_samples(path: Path) -> Dict[str, Any]:
    rows = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise RuntimeError(f"invalid resource sample at {path}:{line_number}")
        rows.append(row)
    if len(rows) < 6:
        raise RuntimeError(f"insufficient attempt57 resource samples: {path}")
    expected_caps = (
        PROFILE["io_read_cap_mib_s"] * 1024.0 * 1024.0,
        PROFILE["io_write_cap_mib_s"] * 1024.0 * 1024.0,
        PROFILE["io_total_cap_mib_s"] * 1024.0 * 1024.0,
    )
    for line_number, row in enumerate(rows, start=1):
        cpu_utilization = finite_float(
            row.get("cpu_utilization"),
            field=f"{path}:{line_number}:cpu_utilization",
        )
        raw_cpu = finite_float(
            row.get("raw_cpu_cores"),
            field=f"{path}:{line_number}:raw_cpu_cores",
        )
        memory_utilization = finite_float(
            row.get("memory_utilization"),
            field=f"{path}:{line_number}:memory_utilization",
        )
        raw_memory = finite_float(
            row.get("raw_memory_bytes"),
            field=f"{path}:{line_number}:raw_memory_bytes",
        )
        cpu_denominator = (
            raw_cpu / cpu_utilization
            if cpu_utilization > 1e-12
            else PROFILE["cpu_limit_cores"]
        )
        memory_denominator = (
            raw_memory / memory_utilization
            if memory_utilization > 1e-12
            else PROFILE["memory_limit_bytes"]
        )
        actual_caps = (
            finite_float(
                row.get("io_read_cap_bytes_s"),
                field=f"{path}:{line_number}:io_read_cap_bytes_s",
            ),
            finite_float(
                row.get("io_write_cap_bytes_s"),
                field=f"{path}:{line_number}:io_write_cap_bytes_s",
            ),
            finite_float(
                row.get("io_total_cap_bytes_s"),
                field=f"{path}:{line_number}:io_total_cap_bytes_s",
            ),
        )
        if (
            not math.isclose(
                cpu_denominator,
                PROFILE["cpu_limit_cores"],
                rel_tol=1e-9,
                abs_tol=1e-9,
            )
            or not math.isclose(
                memory_denominator,
                PROFILE["memory_limit_bytes"],
                rel_tol=1e-9,
                abs_tol=1.0,
            )
            or actual_caps != expected_caps
        ):
            raise RuntimeError(
                f"attempt57 resource denominator mismatch at {path}:{line_number}"
            )
    return {
        "resource_samples_path": str(path.resolve()),
        "resource_samples_sha256": sha256_path(path),
        "resource_sample_count": len(rows),
    }


def validate_r0_summary(path: Path) -> Dict[str, Any]:
    summary = read_json(path)
    decision = summary.get("r0_decision_window")
    decision = decision if isinstance(decision, dict) else {}
    utilization = decision.get("utilization_pct")
    utilization = utilization if isinstance(utilization, dict) else {}
    values = {
        name: finite_float(utilization.get(name), field=f"{path}:{name}")
        for name in ("cpu", "memory", "io")
    }
    cleanup = summary.get("worker_cleanup")
    cleanup = cleanup if isinstance(cleanup, dict) else {}
    gate = summary.get("r0_profile_construction_gate")
    gate = gate if isinstance(gate, dict) else {}
    valid = (
        summary.get("status") == "complete"
        and decision.get("stable") is True
        and int(summary.get("failed_queries") or 0) == 0
        and finite_float(summary.get("db_qps"), field=f"{path}:db_qps") > 0.0
        and cleanup.get("database_backends_fully_released") is True
        and gate.get("target_resource") == "memory"
        and gate.get("passed") is True
        and 30.0 <= values["cpu"] <= 50.0
        and 85.0 <= values["memory"] <= 90.0
        and 30.0 <= values["io"] <= 50.0
    )
    if not valid:
        raise RuntimeError(f"strict R0 evidence failed attempt57 gate: {path}")
    return {
        "summary_path": str(path.resolve()),
        "summary_sha256": sha256_path(path),
        "db_qps": float(summary["db_qps"]),
        "utilization_pct": values,
        **validate_resource_samples(path.parent / "resource_samples.jsonl"),
    }


def validate_r0_manifest(path: Path) -> Dict[str, Any]:
    manifest = read_json(path)
    workload = manifest.get("workload")
    workload = workload if isinstance(workload, dict) else {}
    endpoint = manifest.get("endpoint")
    endpoint = endpoint if isinstance(endpoint, dict) else {}
    timing = manifest.get("timing")
    timing = timing if isinstance(timing, dict) else {}
    io = manifest.get("io_utilization")
    io = io if isinstance(io, dict) else {}
    expected = {
        "benchmark": manifest.get("benchmark"),
        "workers": workload.get("workers"),
        "seed": workload.get("seed"),
        "measurement_schedule": workload.get("measurement_schedule"),
        "port": endpoint.get("port"),
        "database": endpoint.get("database"),
        "container": manifest.get("container"),
        "warmup_seconds": timing.get("warmup"),
        "minimum_measurement_seconds": timing.get("minimum"),
        "io_read_cap_mib_s": io.get("read_cap_mib_s"),
        "io_write_cap_mib_s": io.get("write_cap_mib_s"),
        "io_total_cap_mib_s": io.get("total_cap_mib_s"),
    }
    required = {
        "benchmark": PROFILE["benchmark"],
        "workers": PROFILE["workers"],
        "seed": PROFILE["seed"],
        "measurement_schedule": PROFILE["measurement_schedule"],
        "port": PROFILE["host_port"],
        "database": PROFILE["database"],
        "container": PROFILE["container"],
        "warmup_seconds": PROFILE["warmup_seconds"],
        "minimum_measurement_seconds": PROFILE["minimum_measurement_seconds"],
        "io_read_cap_mib_s": PROFILE["io_read_cap_mib_s"],
        "io_write_cap_mib_s": PROFILE["io_write_cap_mib_s"],
        "io_total_cap_mib_s": PROFILE["io_total_cap_mib_s"],
    }
    if expected != required:
        raise RuntimeError(
            f"attempt57 R0 run manifest profile mismatch: actual={expected}, expected={required}"
        )
    return {
        "run_manifest_path": str(path.resolve()),
        "run_manifest_sha256": sha256_path(path),
        "query_files": list(workload.get("query_files") or []),
        "query_weights": dict(workload.get("weights") or {}),
    }


def r0_evidence() -> List[Dict[str, Any]]:
    state_path = ATTEMPT_ROOT / "r0_supervisor_state.json"
    state = read_json(state_path)
    if (
        state.get("status") != "complete"
        or state.get("phase") != "strict_r0_complete"
        or int(state.get("passed_repeats") or 0) != 3
    ):
        raise RuntimeError("attempt57 strict R0 supervisor state is not complete 3/3")
    summaries = sorted((ATTEMPT_ROOT / "r0").glob("repeat_*/summary.json"))
    if len(summaries) != 3:
        raise RuntimeError(f"attempt57 requires exactly three R0 summaries, got {len(summaries)}")
    records = []
    for index, summary_path in enumerate(summaries, start=1):
        require_attempt57_path(summary_path, field=f"r0_repeat_{index}")
        summary_record = validate_r0_summary(summary_path)
        run_record = validate_r0_manifest(summary_path.parent / "run_manifest.json")
        records.append({"repeat": index, **summary_record, **run_record})
    return records


def calibration_probes() -> List[Dict[str, Any]]:
    probes: List[Dict[str, Any]] = []
    for parameter in PARAMETERS:
        for direction, value in zip(("down", "up"), ENDPOINTS[parameter]):
            params = dict(DEFAULTS)
            params[parameter] = value
            probes.append(
                {
                    "calibration_id": f"axis_{parameter}_{direction}",
                    "parameter": parameter,
                    "direction": "decrease" if direction == "down" else "increase",
                    "params": params,
                }
            )
    return probes


def runtime_artifact(path: Path, *, role: str) -> Dict[str, Any]:
    resolved = require_attempt57_path(path, field=role)
    if not resolved.is_file():
        raise RuntimeError(f"missing attempt57 runtime artifact: {resolved}")
    return {"role": role, "path": str(resolved), "sha256": sha256_path(resolved)}


def generate_manifest(
    *,
    output: Path,
    workload_runner: Path,
    point_runner: Path,
    sql_dir: Path,
    dependencies: Sequence[Path] = (),
) -> Dict[str, Any]:
    output = require_attempt57_path(output, field="manifest_output")
    evidence = r0_evidence()
    sql_dir = require_attempt57_path(sql_dir, field="sql_dir")
    query_paths = sorted(path for path in sql_dir.glob("q*.sql") if path.is_file())
    expected_names = evidence[0]["query_files"]
    if [path.name for path in query_paths] != expected_names:
        raise RuntimeError("attempt57-owned SQL set does not match the sealed R0 query filenames")
    for record in evidence[1:]:
        if (
            record["query_files"] != expected_names
            or record["query_weights"] != evidence[0]["query_weights"]
        ):
            raise RuntimeError("strict R0 repeats used different query manifests")
    sql_artifacts = [
        {
            "role": "tpch_query",
            "path": str(path.resolve()),
            "sha256": sha256_path(path),
        }
        for path in query_paths
    ]
    r0_binding = [
        {
            "repeat": record["repeat"],
            "summary_sha256": record["summary_sha256"],
            "run_manifest_sha256": record["run_manifest_sha256"],
            "resource_samples_sha256": record["resource_samples_sha256"],
        }
        for record in evidence
    ]
    manifest: Dict[str, Any] = {
        "schema_version": 1,
        "contract_id": "pg4_tpch_attempt57_followup_v5",
        "identity": {
            "machine": "node01",
            "lane_root": str(LANE_ROOT),
            "attempt_root": str(ATTEMPT_ROOT),
            "source_attempt": ATTEMPT_ROOT.name,
            "reuse_node04_attempt48_allowed": False,
        },
        "profile": dict(PROFILE),
        "parameters": {
            "active": list(PARAMETERS),
            "defaults": dict(DEFAULTS),
            "endpoint_bounds": {
                name: list(ENDPOINTS[name]) for name in PARAMETERS
            },
        },
        "strict_r0": {
            "required_repeats": 3,
            "evidence": evidence,
            "binding_sha256": hashlib.sha256(canonical_json(r0_binding)).hexdigest(),
        },
        "runtime_artifacts": [
            runtime_artifact(workload_runner, role="weighted_workload_runner"),
            runtime_artifact(point_runner, role="attempt57_calibration_point_runner"),
            *[
                runtime_artifact(path, role="runtime_dependency")
                for path in dependencies
            ],
            *sql_artifacts,
        ],
        "calibration": {
            "root": str(CALIBRATION_ROOT),
            "summary_path": str(CALIBRATION_SUMMARY),
            "design": "single_coordinate_full_endpoint_batch",
            "expected_usable_points": 14,
            "probes": calibration_probes(),
        },
        "outer_launch": {
            "readiness_path": str(READINESS_PATH),
            "requires_calibration_complete": True,
            "optimizer_started_by_this_package": False,
        },
    }
    reject_forbidden(manifest, field="generated_manifest")
    if output.exists():
        existing = read_json(output)
        if canonical_json(existing) != canonical_json(manifest):
            raise RuntimeError(
                f"existing attempt57 manifest differs; refuse overwrite: {output}"
            )
        return existing
    atomic_json(output, manifest)
    return manifest


def verify_runtime_artifacts(manifest: Mapping[str, Any]) -> None:
    artifacts = manifest.get("runtime_artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RuntimeError("runtime_artifacts must be a non-empty list")
    roles: List[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise RuntimeError("invalid runtime artifact record")
        roles.append(str(artifact.get("role")))
        path = require_attempt57_path(Path(str(artifact.get("path"))), field="artifact")
        if sha256_path(path) != artifact.get("sha256"):
            raise RuntimeError(f"runtime artifact SHA256 mismatch: {path}")
        if (
            artifact.get("role") == "weighted_workload_runner"
            and path.name != "run_tpch_attempt57_weighted_workload.py"
        ):
            raise RuntimeError("attempt57 must use its session-local weighted runner")
    if roles.count("weighted_workload_runner") != 1:
        raise RuntimeError("manifest must freeze exactly one attempt57 workload runner")
    if roles.count("attempt57_calibration_point_runner") != 1:
        raise RuntimeError("manifest must freeze exactly one attempt57 point runner")


def verify_manifest(path: Path, *, require_calibration: bool = False) -> Dict[str, Any]:
    path = require_attempt57_path(path, field="manifest")
    manifest = read_json(path)
    reject_forbidden(manifest, field="manifest")
    if manifest.get("contract_id") != "pg4_tpch_attempt57_followup_v5":
        raise RuntimeError("unexpected attempt57 follow-up contract")
    if manifest.get("identity") != {
        "machine": "node01",
        "lane_root": str(LANE_ROOT),
        "attempt_root": str(ATTEMPT_ROOT),
        "source_attempt": ATTEMPT_ROOT.name,
        "reuse_node04_attempt48_allowed": False,
    }:
        raise RuntimeError("attempt57 identity binding mismatch")
    if manifest.get("profile") != PROFILE:
        raise RuntimeError("attempt57 CPU7.7/workers36/SF10 profile binding mismatch")
    parameters = manifest.get("parameters")
    parameters = parameters if isinstance(parameters, dict) else {}
    expected_parameters = {
        "active": list(PARAMETERS),
        "defaults": dict(DEFAULTS),
        "endpoint_bounds": {
            name: list(ENDPOINTS[name]) for name in PARAMETERS
        },
    }
    if parameters != expected_parameters:
        raise RuntimeError("attempt57 seven-parameter binding mismatch")
    calibration = manifest.get("calibration")
    expected_calibration = {
        "root": str(CALIBRATION_ROOT),
        "summary_path": str(CALIBRATION_SUMMARY),
        "design": "single_coordinate_full_endpoint_batch",
        "expected_usable_points": 14,
        "probes": calibration_probes(),
    }
    if calibration != expected_calibration:
        raise RuntimeError("attempt57 calibration design binding mismatch")
    require_attempt57_path(CALIBRATION_ROOT, field="calibration_root")
    require_attempt57_path(CALIBRATION_SUMMARY, field="calibration_summary")
    current_evidence = r0_evidence()
    strict_r0 = manifest.get("strict_r0")
    strict_r0 = strict_r0 if isinstance(strict_r0, dict) else {}
    if strict_r0.get("required_repeats") != 3:
        raise RuntimeError("manifest strict R0 repeat requirement changed")
    frozen = strict_r0.get("evidence")
    if not isinstance(frozen, list) or len(frozen) != 3:
        raise RuntimeError("manifest does not freeze exactly three R0 repeats")
    for current, expected in zip(current_evidence, frozen):
        for key in (
            "summary_sha256",
            "run_manifest_sha256",
            "resource_samples_sha256",
        ):
            if current[key] != expected.get(key):
                raise RuntimeError(
                    f"attempt57 R0 {key} changed for repeat {current['repeat']}"
                )
    current_binding = [
        {
            "repeat": record["repeat"],
            "summary_sha256": record["summary_sha256"],
            "run_manifest_sha256": record["run_manifest_sha256"],
            "resource_samples_sha256": record["resource_samples_sha256"],
        }
        for record in current_evidence
    ]
    current_binding_sha = hashlib.sha256(canonical_json(current_binding)).hexdigest()
    if strict_r0.get("binding_sha256") != current_binding_sha:
        raise RuntimeError("attempt57 R0 composite binding SHA256 changed")
    verify_runtime_artifacts(manifest)
    artifacts = manifest["runtime_artifacts"]
    query_artifacts = [
        artifact for artifact in artifacts if artifact.get("role") == "tpch_query"
    ]
    expected_query_names = current_evidence[0]["query_files"]
    if [Path(str(item["path"])).name for item in query_artifacts] != expected_query_names:
        raise RuntimeError("manifest TPC-H query artifact set changed")
    if require_calibration:
        verify_calibration_summary(manifest, path)
    return manifest


def verify_calibration_summary(
    manifest: Mapping[str, Any],
    manifest_path: Path,
) -> Dict[str, Any]:
    summary_path = Path(str((manifest.get("calibration") or {}).get("summary_path")))
    summary_path = require_attempt57_path(summary_path, field="calibration_summary")
    summary = read_json(summary_path)
    probes = {
        probe["calibration_id"]: probe
        for probe in (manifest.get("calibration") or {}).get("probes") or []
    }
    expected_ids = set(probes)
    points = summary.get("points")
    points = points if isinstance(points, list) else []
    actual_ids = {
        str(point.get("calibration_id"))
        for point in points
        if isinstance(point, dict) and point.get("status") == "usable"
    }
    if (
        summary.get("status") != "complete"
        or summary.get("manifest_sha256") != sha256_path(manifest_path)
        or actual_ids != expected_ids
        or not expected_ids
        or len(points) != len(expected_ids)
        or summary.get("expected_usable_points") != len(expected_ids)
        or summary.get("usable_points") != len(expected_ids)
    ):
        raise RuntimeError("attempt57 calibration is incomplete or bound to another manifest")
    manifest_sha256 = sha256_path(manifest_path)
    points_root = (CALIBRATION_ROOT / "points").resolve()
    for point in points:
        calibration_id = str(point["calibration_id"])
        probe = probes[calibration_id]
        if (
            point.get("manifest_sha256") != manifest_sha256
            or point.get("parameter") != probe["parameter"]
            or point.get("direction") != probe["direction"]
            or point.get("params") != probe["params"]
        ):
            raise RuntimeError(f"calibration ledger binding mismatch: {calibration_id}")
        result_path = require_attempt57_path(
            Path(str(point.get("point_result_path") or "")),
            field=f"calibration_result_{calibration_id}",
        )
        try:
            result_path.resolve().relative_to(points_root)
        except ValueError as exc:
            raise RuntimeError(
                f"calibration result is outside attempt57 points: {result_path}"
            ) from exc
        if sha256_path(result_path) != point.get("point_result_sha256"):
            raise RuntimeError(f"calibration result SHA256 changed: {result_path}")
        result = read_json(result_path)
        protocol = result.get("candidate_measurement_protocol")
        protocol = protocol if isinstance(protocol, dict) else {}
        cleanup = result.get("cleanup")
        cleanup = cleanup if isinstance(cleanup, dict) else {}
        application = result.get("candidate_parameter_application")
        application = application if isinstance(application, dict) else {}
        if not (
            result.get("status") == "complete"
            and result.get("calibration_id") == calibration_id
            and result.get("manifest_sha256") == manifest_sha256
            and result.get("effective_parameters") == probe["params"]
            and protocol.get("stable") is True
            and protocol.get("correctness_passed") is True
            and cleanup.get("database_backends_fully_released") is True
            and cleanup.get("defaults_restored") is True
            and cleanup.get("global_defaults_unchanged") is True
            and application.get("mode") == "transaction_local_set_local"
            and application.get("alter_system_used") is False
            and application.get("global_settings_changed") is False
            and application.get("immediate_readback_required") is True
            and application.get("workers_expected") == PROFILE["workers"]
            and application.get("workers_verified") == PROFILE["workers"]
        ):
            raise RuntimeError(f"calibration result is not usable: {result_path}")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate")
    generate.add_argument("--output", type=Path, default=MANIFEST_PATH)
    generate.add_argument("--workload-runner", type=Path, required=True)
    generate.add_argument("--point-runner", type=Path, required=True)
    generate.add_argument("--sql-dir", type=Path, required=True)
    generate.add_argument(
        "--dependency",
        type=Path,
        action="append",
        default=[],
        help="Additional attempt57-owned runtime module to SHA-pin.",
    )
    verify = subparsers.add_parser("verify")
    verify.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    verify.add_argument(
        "--phase",
        choices=("calibration", "outer"),
        default="calibration",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "generate":
        manifest = generate_manifest(
            output=args.output,
            workload_runner=args.workload_runner,
            point_runner=args.point_runner,
            sql_dir=args.sql_dir,
            dependencies=args.dependency,
        )
        print(
            json.dumps(
                {
                    "status": "manifest_generated",
                    "path": str(args.output.resolve()),
                    "sha256": sha256_path(args.output.resolve()),
                    "r0_binding_sha256": manifest["strict_r0"]["binding_sha256"],
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    verify_manifest(args.manifest, require_calibration=args.phase == "outer")
    print(
        json.dumps(
            {
                "status": "preflight_passed",
                "phase": args.phase,
                "manifest": str(args.manifest.resolve()),
                "manifest_sha256": sha256_path(args.manifest.resolve()),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
