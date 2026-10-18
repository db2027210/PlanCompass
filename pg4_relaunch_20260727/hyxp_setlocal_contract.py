#!/usr/bin/env python3
"""Fail-closed manifest and audit helpers for HyBench XP/IO setlocal calibration."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Dict, Mapping
import zipfile


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
LANE_ROOT = Path(
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
    "hybench_xp_compute00_59630"
)
OUTPUT_ROOT = (
    LANE_ROOT
    / "experience_sgd/attempt42_setlocal_direction_calibration_20260727"
)
MANIFEST_PATH = OUTPUT_ROOT / "sealed/setlocal_calibration_manifest.json"
DB_LOCK = Path("/tmp/resource_adaptive_hyxp_pg59630_db_lease.lock")
PROFILE = {
    "machine": "compute00",
    "workload": "hybench_xp",
    "scenario": "io",
    "database": "hybench_sf10x",
    "port": 59630,
    "cpu_quota_cores": 10.5,
    "memory_bytes": 30404000000,
    "io_read_bps": 100000,
    "io_write_bps": 837500,
    "minimum_measurement_seconds": 180,
    "maximum_measurement_seconds": 600,
}


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
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


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(value), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def validate_params(value: Mapping[str, Any]) -> Dict[str, float]:
    if set(value) != set(PARAMETERS):
        raise RuntimeError("candidate must contain exactly seven planner parameters")
    params = {name: float(value[name]) for name in PARAMETERS}
    if any(not math.isfinite(item) or item <= 0.0 for item in params.values()):
        raise RuntimeError("candidate parameters must be finite and positive")
    return params


def parameter_environment(params: Mapping[str, Any]) -> str:
    checked = validate_params(params)
    return ";".join(f"{name}={checked[name]:.17g}" for name in PARAMETERS)


def probes() -> list[Dict[str, Any]]:
    result = []
    for name in PARAMETERS:
        for direction, value in zip(("decrease", "increase"), ENDPOINTS[name]):
            params = dict(DEFAULTS)
            params[name] = value
            result.append(
                {
                    "calibration_id": f"axis_{name}_{direction}",
                    "parameter": name,
                    "direction": direction,
                    "params": params,
                }
            )
    return result


def artifact(path: Path, role: str) -> Dict[str, str]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise RuntimeError(f"missing setlocal artifact: {resolved}")
    return {
        "role": role,
        "path": str(resolved),
        "sha256": sha256_path(resolved),
    }


def generate_manifest(
    output: Path,
    *,
    driver_jar: Path,
    driver_source: Path,
    shadow_launcher: Path,
    measurement_adapter: Path,
    measurement_wrapper: Path,
    restore_script: Path,
    warmup_props: Path,
    measurement_props: Path,
    statements: Path,
    hybench_jar: Path,
    postgres_jar: Path,
) -> Dict[str, Any]:
    try:
        output.resolve().relative_to(OUTPUT_ROOT.resolve())
    except ValueError as exc:
        raise RuntimeError("manifest output must be in the new attempt42 namespace") from exc
    records = [
        artifact(driver_jar, "setlocal_driver_jar"),
        artifact(driver_source, "setlocal_driver_source"),
        artifact(shadow_launcher, "shadow_launcher"),
        artifact(measurement_adapter, "measurement_adapter"),
        artifact(measurement_wrapper, "frozen_measurement_wrapper"),
        artifact(restore_script, "snapshot_restore"),
        artifact(warmup_props, "base_warmup_props"),
        artifact(measurement_props, "base_measurement_props"),
        artifact(statements, "hybench_statements"),
        artifact(hybench_jar, "hybench_jar"),
        artifact(postgres_jar, "postgres_jdbc_jar"),
        artifact(Path(__file__), "setlocal_contract"),
        artifact(
            Path(__file__).with_name("run_hyxp_setlocal_direction_calibration.py"),
            "calibration_entry",
        ),
        artifact(
            Path(__file__).with_name("build_hyxp_setlocal_driver.sh"),
            "driver_build_script",
        ),
    ]
    manifest = {
        "schema_version": 1,
        "contract_id": "hyxp_io_setlocal_direction_calibration_v3",
        "output_root": str(OUTPUT_ROOT),
        "profile": PROFILE,
        "parameters": {
            "names": list(PARAMETERS),
            "defaults": DEFAULTS,
            "endpoint_bounds": {
                name: list(ENDPOINTS[name]) for name in PARAMETERS
            },
        },
        "application_contract": {
            "mode": "transaction_local_set_local",
            "connections": ["tp", "ap"],
            "apply_before_first_statement_in_each_transaction": True,
            "immediate_current_setting_readback": True,
            "reset_application_state_on_commit_or_rollback": True,
            "autocommit_allowed": False,
            "global_parameter_writes_allowed": False,
            "global_defaults_must_match_before_and_after": True,
        },
        "calibration": {
            "design": "one_parameter_at_a_time_endpoints",
            "points_per_scope": 14,
            "probes": probes(),
        },
        "artifacts": records,
    }
    if output.exists():
        existing = read_json(output)
        if canonical_json(existing) != canonical_json(manifest):
            raise RuntimeError(f"existing manifest differs; refuse overwrite: {output}")
        return existing
    atomic_json(output, manifest)
    return manifest


def artifacts_by_role(manifest: Mapping[str, Any]) -> Dict[str, Path]:
    result: Dict[str, Path] = {}
    for item in manifest.get("artifacts") or []:
        if not isinstance(item, dict):
            raise RuntimeError("invalid manifest artifact")
        role = str(item.get("role") or "")
        if role in result:
            raise RuntimeError(f"duplicate artifact role: {role}")
        path = Path(str(item.get("path") or "")).resolve()
        if sha256_path(path) != item.get("sha256"):
            raise RuntimeError(f"artifact SHA changed: {path}")
        result[role] = path
    return result


def verify_driver_build(driver_jar: Path, driver_source: Path) -> None:
    class_prefix = "com/resourceadaptive/hyxp/SetLocalPostgresDriver"
    with tempfile.TemporaryDirectory() as temporary:
        classes = Path(temporary) / "classes"
        classes.mkdir()
        subprocess.run(
            [
                "javac",
                "--release",
                "11",
                "-d",
                str(classes),
                str(driver_source),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        expected = {
            str(path.relative_to(classes)): path.read_bytes()
            for path in classes.rglob("*.class")
        }
    with zipfile.ZipFile(driver_jar) as archive:
        actual = {
            name: archive.read(name)
            for name in archive.namelist()
            if name.startswith(class_prefix) and name.endswith(".class")
        }
    if expected != actual:
        raise RuntimeError("setlocal driver jar does not match the sealed Java source")


def verify_manifest(path: Path) -> Dict[str, Any]:
    resolved = path.resolve()
    try:
        resolved.relative_to(OUTPUT_ROOT.resolve())
    except ValueError as exc:
        raise RuntimeError("manifest must be in the new attempt42 namespace") from exc
    manifest = read_json(resolved)
    if manifest.get("contract_id") != "hyxp_io_setlocal_direction_calibration_v3":
        raise RuntimeError("unexpected HyBench setlocal contract")
    if manifest.get("output_root") != str(OUTPUT_ROOT):
        raise RuntimeError("setlocal output root changed")
    if manifest.get("profile") != PROFILE:
        raise RuntimeError("HyBench XP/IO profile changed")
    if manifest.get("calibration", {}).get("probes") != probes():
        raise RuntimeError("setlocal probe design changed")
    application = manifest.get("application_contract") or {}
    required_true = (
        "apply_before_first_statement_in_each_transaction",
        "immediate_current_setting_readback",
        "reset_application_state_on_commit_or_rollback",
        "global_defaults_must_match_before_and_after",
    )
    if (
        application.get("mode") != "transaction_local_set_local"
        or application.get("connections") != ["tp", "ap"]
        or any(application.get(name) is not True for name in required_true)
        or application.get("autocommit_allowed") is not False
        or application.get("global_parameter_writes_allowed") is not False
    ):
        raise RuntimeError("setlocal application contract changed")
    roles = artifacts_by_role(manifest)
    required_roles = {
        "setlocal_driver_jar",
        "setlocal_driver_source",
        "shadow_launcher",
        "measurement_adapter",
        "frozen_measurement_wrapper",
        "snapshot_restore",
        "base_warmup_props",
        "base_measurement_props",
        "hybench_statements",
        "hybench_jar",
        "postgres_jdbc_jar",
        "setlocal_contract",
        "calibration_entry",
        "driver_build_script",
    }
    if set(roles) != required_roles:
        raise RuntimeError(f"manifest artifact roles changed: {sorted(roles)}")
    if roles["shadow_launcher"].name != "hybench_unblocked":
        raise RuntimeError("shadow launcher must be staged as hybench_unblocked")
    verify_driver_build(
        roles["setlocal_driver_jar"],
        roles["setlocal_driver_source"],
    )
    source = roles["setlocal_driver_source"].read_text(encoding="utf-8")
    if (
        source.count("SET LOCAL ") != 1
        or "current_setting('" not in source
        or "applied = false" not in source
    ):
        raise RuntimeError("JDBC driver lacks frozen transaction-local semantics")
    prohibited = "ALTER" + " SYSTEM"
    for role in (
        "setlocal_driver_source",
        "shadow_launcher",
        "measurement_adapter",
        "calibration_entry",
    ):
        if prohibited.lower() in roles[role].read_text(encoding="utf-8").lower():
            raise RuntimeError(f"global planner write found in {role}")
    return manifest


def rewrite_props(source: Path, destination: Path) -> None:
    values: Dict[str, str] = {}
    order = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            order.append((None, line))
            continue
        key, value = line.split("=", 1)
        values[key] = value
        order.append((key, None))
    driver = "com.resourceadaptive.hyxp.SetLocalPostgresDriver"
    values["classname"] = driver
    values["classname_ap"] = driver
    for key, role in (("url", "tp"), ("url_tp", "tp"), ("url_ap", "ap")):
        raw = values.get(key)
        if raw is None or not raw.startswith("jdbc:postgresql:"):
            raise RuntimeError(f"base props lacks PostgreSQL URL: {key}")
        values[key] = f"jdbc:hyxp-setlocal:{role}:{raw}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    rendered = [
        line if key is None else f"{key}={values[key]}"
        for key, line in order
    ]
    destination.write_text(
        "\n".join(rendered) + "\n",
        encoding="utf-8",
    )


def validate_global_readback(value: Mapping[str, Any]) -> Dict[str, float]:
    actual = validate_params(value)
    for name in PARAMETERS:
        if not math.isclose(actual[name], DEFAULTS[name], rel_tol=1e-12, abs_tol=1e-12):
            raise RuntimeError(f"global default changed: {name}={actual[name]}")
    return actual


def validate_audit(
    path: Path,
    *,
    run_id: str,
    phase: str,
    params: Mapping[str, Any],
) -> Dict[str, Any]:
    expected = validate_params(params)
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        raise RuntimeError(f"empty setlocal JDBC audit: {path}")
    roles = set()
    connections = set()
    sequences: Dict[str, int] = {}
    for row in rows:
        connection_id = str(row.get("connection_id") or "")
        sequence = int(row.get("transaction_sequence") or 0)
        if (
            row.get("event") != "transaction_local_parameters_applied"
            or row.get("run_id") != run_id
            or row.get("phase") != phase
            or row.get("immediate_readback_passed") is not True
            or row.get("global_writes_used") is not False
            or validate_params(row.get("parameters") or {}) != expected
            or sequence != sequences.get(connection_id, 0) + 1
        ):
            raise RuntimeError(f"invalid setlocal JDBC audit row: {row}")
        role = str(row.get("role") or "")
        if role not in {"tp", "ap"}:
            raise RuntimeError(f"invalid JDBC role in audit: {role}")
        sequences[connection_id] = sequence
        roles.add(role)
        connections.add(connection_id)
    if roles != {"tp", "ap"}:
        raise RuntimeError(f"measurement lacks TP/AP setlocal evidence: {sorted(roles)}")
    return {
        "rows": len(rows),
        "roles": sorted(roles),
        "connections": len(connections),
        "audit_sha256": sha256_path(path),
        "all_transactions_immediate_readback_passed": True,
        "global_writes_used": False,
    }


def point_seal(
    *,
    manifest_path: Path,
    audit_path: Path,
    global_before_path: Path,
    global_after_path: Path,
    summary_path: Path,
    run_id: str,
    params: Mapping[str, Any],
) -> Dict[str, Any]:
    manifest = verify_manifest(manifest_path)
    before = validate_global_readback(read_json(global_before_path))
    after = validate_global_readback(read_json(global_after_path))
    if before != after:
        raise RuntimeError("global planner defaults changed across HyBench point")
    audit = validate_audit(
        audit_path,
        run_id=run_id,
        phase="measurement",
        params=params,
    )
    summary = read_json(summary_path)
    if (
        summary.get("status") != "ok"
        or summary.get("stable") is not True
        or summary.get("correctness_passed") is not True
    ):
        raise RuntimeError("HyBench XP measurement is not formally usable")
    windows_path = summary_path.parent / "measurement_windows.jsonl"
    windows = []
    for line in windows_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("event") == "measurement_window":
            windows.append(row)
    windows = windows[-6:]
    if len(windows) != 6:
        raise RuntimeError("HyBench XP point lacks six formal measurement windows")

    def mean(field: str) -> float:
        values = [float(row[field]) for row in windows]
        if any(not math.isfinite(value) for value in values):
            raise RuntimeError(f"non-finite HyBench measurement field: {field}")
        return sum(values) / len(values)

    raw_result = {
        "status": "ok",
        "double_loop_measurement_usable": True,
        "workload": "hybench_xp",
        "target_resource": "io",
        "scenario": "io",
        "params": validate_params(params),
        "db_qps": mean("db_qps"),
        "resource_utilization": {
            "cpu": mean("cpu_utilization"),
            "memory": mean("memory_utilization"),
            "io": mean("io_utilization"),
        },
        "runtime_capability": {
            "cpu": PROFILE["cpu_quota_cores"],
            "memory": PROFILE["memory_bytes"],
            "io": PROFILE["io_read_bps"] + PROFILE["io_write_bps"],
        },
        "candidate_measurement_protocol": {
            "stable": True,
            "effective_measurement_seconds": summary.get(
                "effective_measurement_seconds"
            ),
            "stop_reason": summary.get("stop_reason"),
        },
        "summary_path": str(summary_path.resolve()),
        "setlocal_evidence_only": True,
    }
    return {
        "schema_version": 1,
        "status": "usable",
        "manifest_sha256": sha256_path(manifest_path),
        "requested_parameters": validate_params(params),
        "candidate_parameter_application": {
            "mode": "transaction_local_set_local",
            "tp_ap_connections_verified": True,
            **audit,
        },
        "global_defaults": {
            "before": before,
            "after": after,
            "unchanged": True,
            "global_parameter_writes_allowed": False,
        },
        "measurement_summary_path": str(summary_path.resolve()),
        "measurement_summary_sha256": sha256_path(summary_path),
        "raw_result": raw_result,
        "profile": manifest["profile"],
    }
