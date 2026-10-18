#!/usr/bin/env python3
"""Offline tests for estimator-backed MySQL T freezing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from calibrate_mysql_experience_table import CalibrationError
from freeze_mysql_estimator_experience_table import build_estimator_artifact, main


def _write(path: Path, value: object) -> str:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pointer(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _bundle(tmp_path: Path) -> tuple[dict, Path, dict[str, Path]]:
    files: dict[str, Path] = {}
    for name in ("workload", "sql_registry", "estimator", "cpu_model", "memory_model", "io_model", "space"):
        path = tmp_path / f"{name}.json"
        _write(path, {"name": name, "database": "mysql"})
        files[name] = path
    estimator_sha = _pointer(files["estimator"])["sha256"]
    _write(files["workload"], {"freeze_id": "workload-v1", "database": "mysql"})
    _write(files["sql_registry"], {
        "schema_version": "mysql_plan_workload_registry_v1", "immutable": True,
        "freeze_id": "workload-v1", "benchmark": "unitbench",
        "queries": [{"order": 1, "query_id": "q1"}, {"order": 2, "query_id": "q2"}],
    })
    _write(files["space"], {
        "freeze_id": "workload-v1", "immutable": True,
        "estimator": {"path": str(files["estimator"].resolve()), "sha256": estimator_sha},
        "benchmark_candidate_parameters": {"unitbench": ["p"]},
        "all_parameter_definitions": {"p": {"lower": 0.5, "upper": 2.0}},
    })
    model_sha = {
        resource: _pointer(files[f"{resource}_model"])["sha256"]
        for resource in ("cpu", "memory", "io")
    }
    common = {
        "database": "mysql", "scenario": "unit_memory", "benchmark": "unitbench",
        "benchmark_version": "unit-v1", "query_ids": ["q1", "q2"],
    }
    points = {
        "anchor": ({"p": 1.0}, {"cpu": 0.5, "memory": 0.5, "io": 0.5}),
        "lower": ({"p": 0.5}, {"cpu": 0.4, "memory": 0.55, "io": 0.3}),
        "upper": ({"p": 2.0}, {"cpu": 0.7, "memory": 0.45, "io": 0.8}),
    }
    endpoint_rows: dict[str, dict] = {}
    for name, (params, prediction) in points.items():
        explain = tmp_path / f"{name}_explain.json"
        feature = tmp_path / f"{name}_feature.json"
        _write(explain, {
            "schema_version": "mysql_explain_bundle_v1", **common,
            "params": params, "plans": {"q1": {}, "q2": {}},
        })
        _write(feature, {
            "schema_version": "mysql_trained_feature_bundle_v1", **common,
            "params": params, "prediction": prediction,
            "estimator_sha256": estimator_sha, "model_artifact_sha256": model_sha,
            "feature_extraction_complete": True,
        })
        files[f"{name}_explain"] = explain
        files[f"{name}_feature"] = feature
        endpoint_rows[name] = {
            "params": params, "prediction": prediction,
            "explain_artifact": _pointer(explain), "feature_artifact": _pointer(feature),
        }
    evidence = {
        "schema_version": "mysql_trained_estimator_endpoint_evidence_v1",
        "source": "trained_estimator_experience", "database": "mysql",
        "scenario": "unit_memory", "benchmark": "unitbench", "benchmark_version": "unit-v1",
        "workload_freeze_id": "workload-v1",
        "workload_registry_sha256": _pointer(files["workload"])["sha256"],
        "sql_registry_sha256": _pointer(files["sql_registry"])["sha256"],
        "estimator_sha256": estimator_sha,
        "parameter_space_sha256": _pointer(files["space"])["sha256"],
        "active_params": ["p"], "ranges": {"p": {"lower": 0.5, "upper": 2.0}},
        "query_coverage": {
            "expected_query_ids": ["q1", "q2"], "covered_query_ids": ["q1", "q2"],
            "missing_query_ids": [], "coverage_ratio": 1.0,
        },
        "anchor": endpoint_rows["anchor"],
        "parameters": {"p": {"lower": endpoint_rows["lower"], "upper": endpoint_rows["upper"]}},
    }
    evidence_path = tmp_path / "endpoint_evidence.json"
    _write(evidence_path, evidence)
    files["evidence"] = evidence_path
    request = {
        "schema_version": "mysql_estimator_t_freeze_request_v1",
        "binding": {
            "database": "mysql", "scenario": "unit_memory", "benchmark": "unitbench",
            "benchmark_version": "unit-v1", "version": "T_unit_estimator_v1",
            "workload": {"freeze_id": "workload-v1", "artifact": _pointer(files["workload"])},
            "sql_registry": {"id": "sql-v1", "artifact": _pointer(files["sql_registry"])},
            "estimator": {
                "id": "trained-model", "version": "v1", "artifact": _pointer(files["estimator"]),
                "model_artifacts": {
                    resource: _pointer(files[f"{resource}_model"])
                    for resource in ("cpu", "memory", "io")
                },
            },
            "parameter_space": {
                "version": "space-v1", "artifact": _pointer(files["space"]),
                "active_params": ["p"], "ranges": {"p": {"lower": 0.5, "upper": 2.0}},
            },
        },
        "endpoint_evidence": _pointer(evidence_path),
        "sensitivity_tolerance": 1e-9,
    }
    request_path = tmp_path / "request.json"
    _write(request_path, request)
    return request, request_path, files


def _rewrite(path: Path, mutate) -> None:
    payload = json.loads(path.read_text())
    mutate(payload)
    _write(path, payload)


def test_freezes_explicit_model_backed_not_real_measurement(tmp_path: Path) -> None:
    request, request_path, _ = _bundle(tmp_path)
    artifact = build_estimator_artifact(request, request_path)
    assert artifact["table"]["p"] == pytest.approx({"cpu": 0.3, "memory": -0.1, "io": 0.5})
    assert artifact["provenance"]["source"] == "trained_estimator_experience"
    assert artifact["provenance"]["real_measurement"] is False
    assert artifact["effect_evidence"]["p"]["cpu"]["confidence"]["level"] == "MODEL_BACKED"
    output = tmp_path / "T.json"
    assert main(["--request", str(request_path), "--output", str(output), "--json"]) == 0
    assert hashlib.sha256(output.read_bytes()).hexdigest() == (tmp_path / "T.json.sha256").read_text().split()[0]


def test_fails_when_feature_bundle_is_missing(tmp_path: Path) -> None:
    request, request_path, files = _bundle(tmp_path)
    files["lower_feature"].unlink()
    with pytest.raises(CalibrationError, match="absent or a symlink"):
        build_estimator_artifact(request, request_path)


def test_complete_evidence_freezes_zero_row_when_parameter_has_no_detectable_effect(tmp_path: Path) -> None:
    request, request_path, files = _bundle(tmp_path)
    evidence = json.loads(files["evidence"].read_text())
    upper = evidence["parameters"]["p"]["upper"]
    upper["prediction"] = evidence["parameters"]["p"]["lower"]["prediction"]
    feature_path = files["upper_feature"]
    _rewrite(feature_path, lambda row: row.update(prediction=upper["prediction"]))
    upper["feature_artifact"] = _pointer(feature_path)
    _write(files["evidence"], evidence)
    request["endpoint_evidence"] = _pointer(files["evidence"])
    _write(request_path, request)
    artifact = build_estimator_artifact(request, request_path)
    assert artifact["table"]["p"] == {"cpu": 0.0, "memory": 0.0, "io": 0.0}
    for resource in ("cpu", "memory", "io"):
        item = artifact["effect_evidence"]["p"][resource]
        assert item["evidence_status"] == "NO_DETECTABLE_EFFECT"
        assert item["parameter_effect_status"] == "NO_DETECTABLE_EFFECT"
        assert item["confidence"]["level"] == "MODEL_BACKED_LOW_SENSITIVITY"
        assert item["confidence"]["decision"] == "NO_DETECTABLE_EFFECT"
        assert abs(item["observed_delta"]) <= item["tolerance"]
        assert item["confidence"]["query_coverage_ratio"] == 1.0


def test_fails_on_pg_feature_provenance(tmp_path: Path) -> None:
    request, request_path, files = _bundle(tmp_path)
    _rewrite(files["upper_feature"], lambda row: row.update(database="postgresql"))
    evidence = json.loads(files["evidence"].read_text())
    evidence["parameters"]["p"]["upper"]["feature_artifact"] = _pointer(files["upper_feature"])
    _write(files["evidence"], evidence)
    request["endpoint_evidence"] = _pointer(files["evidence"])
    _write(request_path, request)
    with pytest.raises(CalibrationError, match="binding mismatch"):
        build_estimator_artifact(request, request_path)
