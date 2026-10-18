#!/usr/bin/env python3
"""Filesystem-only tests for the fail-closed MySQL T calibrator."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from calibrate_mysql_experience_table import CalibrationError, build_artifact, main


def _write(path: Path, value: object) -> str:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pointer(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _make_bundle(tmp_path: Path, params: tuple[str, ...] = ("p",)) -> tuple[dict, Path, dict[str, Path]]:
    workload = tmp_path / "workload.json"
    estimator = tmp_path / "estimator.json"
    space = tmp_path / "space.json"
    _write(workload, {"freeze_id": "mysql_workload_v1"})
    _write(estimator, {"database": "mysql", "model": "frozen"})
    ranges = {param: {"lower": 0.5, "upper": 2.0} for param in params}
    _write(space, {"active_params": list(params), "ranges": ranges})
    request: dict = {
        "schema_version": "mysql_t_calibration_request_v1",
        "binding": {
            "database": "mysql",
            "scenario": "unit_memory",
            "benchmark": "unitbench",
            "benchmark_version": "unitbench-v1",
            "version": "T_unit_memory_v1",
            "workload": {"freeze_id": "mysql_workload_v1", "artifact": _pointer(workload)},
            "estimator": {"id": "mysql_estimator", "version": "v1", "artifact": _pointer(estimator)},
            "parameter_space": {
                "version": "space-v1", "artifact": _pointer(space),
                "active_params": list(params), "ranges": ranges,
            },
        },
        "calibration_policy": {
            "minimum_contrasts_per_side": 3,
            "minimum_sign_consistency": 0.8,
            "maximum_relative_mad": 1.0,
            "neutral_effect_threshold": 0.005,
        },
        "contrasts": [],
    }
    evidence_paths: dict[str, Path] = {}
    common = {
        "schema_version": "mysql_formal_point_evidence_v1",
        "formal_point": True,
        "completeness": "COMPLETE",
        "database": "mysql",
        "engine_family": "mysql",
        "mysql_version": "8.0.test",
        "scenario": "unit_memory",
        "benchmark": "unitbench",
        "benchmark_version": "unitbench-v1",
        "workload_freeze_id": "mysql_workload_v1",
        "workload_registry_sha256": _pointer(workload)["sha256"],
        "estimator_id": "mysql_estimator",
        "estimator_version": "v1",
        "estimator_sha256": _pointer(estimator)["sha256"],
        "parameter_space_version": "space-v1",
        "parameter_space_sha256": _pointer(space)["sha256"],
        "active_params": list(params),
        "ranges": ranges,
        "artifact_checksums_verified": True,
        "resource_envelope": {
            "version": "envelope-v1", "sha256": "a" * 64,
            "readback_sha256": "b" * 64,
        },
        "correctness": {"passed": True},
        "execution": {"exit_code": 0, "timeout": False, "oom": False, "mysql_restart": False},
    }
    for repeat in range(1, 4):
        anchor_values = {param: 1.0 for param in params}
        anchor_resources = {
            "cpu": 0.50 + repeat * 0.001,
            "memory": 0.55 + repeat * 0.001,
            "io": 0.45 + repeat * 0.001,
        }
        anchor = {
            **common, "point_id": f"anchor_r{repeat}", "anchor_id": f"anchor_r{repeat}",
            "repeat_id": repeat, "evidence_class": "frozen_anchor",
            "params_requested": anchor_values, "params_effective": anchor_values,
            "params_readback": anchor_values,
            "resource_window": {
                "latest_window_seconds": 180, "latest_window_count": 6,
                "sample_coverage": 0.99, "stable": True,
                "utilization": anchor_resources,
            },
        }
        anchor_path = tmp_path / f"anchor_r{repeat}.json"
        _write(anchor_path, anchor)
        evidence_paths[f"anchor_r{repeat}"] = anchor_path
        for param in params:
            for side, value in (("lower", 0.5), ("upper", 2.0)):
                log_step = __import__("math").log(value)
                treatment_values = dict(anchor_values)
                treatment_values[param] = value
                treatment_resources = {
                    "cpu": anchor_resources["cpu"] + 0.040 * log_step,
                    "memory": anchor_resources["memory"] - 0.030 * log_step,
                    "io": anchor_resources["io"] + 0.020 * log_step,
                }
                point_id = f"{param}_{side}_r{repeat}"
                treatment = {
                    **common, "point_id": point_id, "anchor_id": f"anchor_r{repeat}",
                    "repeat_id": repeat, "evidence_class": "oat_treatment",
                    "params_requested": treatment_values, "params_effective": treatment_values,
                    "params_readback": treatment_values,
                    "resource_window": {
                        "latest_window_seconds": 180, "latest_window_count": 6,
                        "sample_coverage": 0.98, "stable": True,
                        "utilization": treatment_resources,
                    },
                }
                treatment_path = tmp_path / f"{point_id}.json"
                _write(treatment_path, treatment)
                evidence_paths[point_id] = treatment_path
                request["contrasts"].append({
                    "contrast_id": point_id, "parameter": param,
                    "anchor": _pointer(anchor_path), "treatment": _pointer(treatment_path),
                })
    request_path = tmp_path / "request.json"
    _write(request_path, request)
    return request, request_path, evidence_paths


def _rewrite_evidence(request: dict, path: Path, mutate) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    mutate(value)
    _write(path, value)
    new_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    for contrast in request["contrasts"]:
        for role in ("anchor", "treatment"):
            if contrast[role]["path"] == str(path):
                contrast[role]["sha256"] = new_sha


def _persist(request: dict, request_path: Path) -> None:
    _write(request_path, request)


def test_creates_deterministic_versioned_artifact_and_sidecar(tmp_path: Path) -> None:
    request, request_path, _ = _make_bundle(tmp_path)
    artifact = build_artifact(request, request_path)
    assert artifact["database"] == "mysql"
    assert artifact["version"] == "T_unit_memory_v1"
    assert artifact["provenance"]["cross_engine_prior_used"] is False
    assert isinstance(artifact["table"]["p"]["cpu"], float)
    assert artifact["effect_evidence"]["p"]["cpu"]["direction"] == "increase"
    assert artifact["effect_evidence"]["p"]["memory"]["direction"] == "decrease"
    assert artifact["effect_evidence"]["p"]["io"]["direction"] == "increase"
    assert artifact["effect_evidence"]["p"]["cpu"]["confidence"]["contrast_count"] == 6

    output = tmp_path / "T.json"
    assert main(["--request", str(request_path), "--output", str(output), "--json"]) == 0
    assert output.is_file() and (tmp_path / "T.json.sha256").is_file()
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    assert (tmp_path / "T.json.sha256").read_text().split()[0] == digest
    assert output.read_bytes() == json.dumps(
        json.loads(output.read_text()), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def test_rejects_postgresql_point_even_if_binding_says_mysql(tmp_path: Path) -> None:
    request, request_path, paths = _make_bundle(tmp_path)
    _rewrite_evidence(request, paths["p_lower_r1"], lambda row: row.update(database="postgresql"))
    _persist(request, request_path)
    with pytest.raises(CalibrationError, match="frozen MySQL binding"):
        build_artifact(request, request_path)


def test_rejects_joint_parameter_change(tmp_path: Path) -> None:
    request, request_path, paths = _make_bundle(tmp_path, ("p", "q"))
    def change_q(row: dict) -> None:
        for field in ("params_requested", "params_effective", "params_readback"):
            row[field]["q"] = 2.0
    _rewrite_evidence(request, paths["p_lower_r1"], change_q)
    _persist(request, request_path)
    with pytest.raises(CalibrationError, match="not single-parameter OAT"):
        build_artifact(request, request_path)


def test_rejects_incomplete_formal_point(tmp_path: Path) -> None:
    request, request_path, paths = _make_bundle(tmp_path)
    _rewrite_evidence(request, paths["anchor_r1"], lambda row: row.update(completeness="PARTIAL"))
    _persist(request, request_path)
    with pytest.raises(CalibrationError, match="not a COMPLETE formal point"):
        build_artifact(request, request_path)


def test_rejects_missing_side_replicate_without_writing_output(tmp_path: Path) -> None:
    request, request_path, _ = _make_bundle(tmp_path)
    request["contrasts"] = [
        row for row in request["contrasts"] if row["contrast_id"] != "p_lower_r3"
    ]
    _persist(request, request_path)
    output = tmp_path / "must_not_exist.json"
    assert main(["--request", str(request_path), "--output", str(output), "--json"]) == 2
    assert not output.exists()
    assert not (tmp_path / "must_not_exist.json.sha256").exists()


def test_rejects_evidence_checksum_mismatch(tmp_path: Path) -> None:
    request, request_path, paths = _make_bundle(tmp_path)
    paths["p_upper_r1"].write_text("tampered\n", encoding="utf-8")
    with pytest.raises(CalibrationError, match="SHA-256 mismatch"):
        build_artifact(request, request_path)
