#!/usr/bin/env python3
"""No-database tests for trained-estimator endpoint evidence collection."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from calibrate_mysql_experience_table import CalibrationError
from collect_mysql_trained_estimator_endpoint_evidence import (
    _verify_production_config,
    _validated_request,
    collect_endpoint_evidence,
)
from freeze_mysql_estimator_experience_table import build_estimator_artifact
from mysql_plan_clustering import build_canonical_plan_bundle


def _write(path: Path, value: object) -> str:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pointer(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _fixture(tmp_path: Path) -> tuple[dict, Path, dict[str, Path]]:
    files: dict[str, Path] = {}
    registry = tmp_path / "registry.json"
    _write(registry, {
        "schema_version": "mysql_plan_workload_registry_v1",
        "immutable": True,
        "freeze_id": "workload-v1",
        "benchmark": "unitbench",
        "queries": [
            {"order": 1, "query_id": "q1"},
            {"order": 2, "query_id": "q2"},
        ],
    })
    files["registry"] = registry
    for name in ("workload", "estimator", "cpu", "memory", "io", "space", "config"):
        path = tmp_path / f"{name}.json"
        _write(path, {"name": name, "database": "mysql"})
        files[name] = path
    _write(files["workload"], {"freeze_id": "workload-v1", "database": "mysql"})
    estimator_sha = _pointer(files["estimator"])["sha256"]
    _write(files["space"], {
        "freeze_id": "workload-v1", "immutable": True,
        "estimator": {"path": str(files["estimator"].resolve()), "sha256": estimator_sha},
        "benchmark_candidate_parameters": {"unitbench": ["p1", "p2"]},
        "all_parameter_definitions": {
            "p1": {"lower": 0.5, "upper": 2.0},
            "p2": {"lower": 1.0, "upper": 9.0},
        },
    })
    binding = {
        "database": "mysql",
        "scenario": "unit_memory",
        "benchmark": "unitbench",
        "benchmark_version": "unit-v1",
        "version": "T_unit_v1",
        "workload": {"freeze_id": "workload-v1", "artifact": _pointer(files["workload"])},
        "sql_registry": {"id": "registry-v1", "artifact": _pointer(registry)},
        "estimator": {
            "id": "trained", "version": "v1", "artifact": _pointer(files["estimator"]),
            "model_artifacts": {
                resource: _pointer(files[resource]) for resource in ("cpu", "memory", "io")
            },
        },
        "parameter_space": {
            "version": "space-v1", "artifact": _pointer(files["space"]),
            "active_params": ["p1", "p2"],
            "ranges": {
                "p1": {"lower": 0.5, "upper": 2.0},
                "p2": {"lower": 1.0, "upper": 9.0},
            },
        },
    }
    request = {
        "schema_version": "mysql_trained_estimator_endpoint_collection_request_v1",
        "binding": binding,
        "anchor": {"p1": 1.0, "p2": 3.0},
        "trained_estimator_config": _pointer(files["config"]),
        "sensitivity_tolerance": 1e-9,
    }
    request_path = tmp_path / "request.json"
    _write(request_path, request)
    return request, request_path, files


class FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def snapshot(self) -> dict[str, str]:
        self.calls.append("snapshot")
        return {"state": "original"}

    def apply(self, params: dict[str, float]) -> None:
        self.calls.append(("apply", dict(params)))

    def restore(self, snapshot: object) -> None:
        self.calls.append(("restore", snapshot))


class FakeProvider:
    def __init__(self, bundle: dict) -> None:
        self.bundle = bundle

    def collect(self) -> dict:
        return {
            "cpu": {"F_scan_tuple": 10.0},
            "io": {"mysql_N_rows_examined": 10.0},
            "plan_rows": [{"mysql_N_rows_examined": 10.0}],
            "active_sql_count": 2.0,
            "canonical_plan_bundle": copy.deepcopy(self.bundle),
        }


class FakeEstimator:
    def __init__(self, adapter: FakeAdapter, bundle: dict, *, fail_at: int | None = None) -> None:
        self.adapter = adapter
        self.provider = FakeProvider(bundle)
        self.calls = 0
        self.fail_at = fail_at

    def __call__(self, _sql_set: object, params: dict[str, float]) -> dict:
        self.calls += 1
        self.adapter.apply(params)
        features = self.provider.collect()
        if self.fail_at == self.calls:
            raise RuntimeError("synthetic failure")
        prediction = {
            "cpu": params["p1"] + params["p2"],
            "memory": 2.0 * params["p1"],
            "io": 3.0 * params["p2"],
        }
        return {
            **prediction,
            "_metadata": {"canonical_plan_bundle": features["canonical_plan_bundle"]},
        }


def _bundle(registry_sha: str) -> dict:
    captures = []
    for order, query_id in enumerate(("q1", "q2"), 1):
        captures.append({
            "query_id": query_id,
            "query_sql_sha256": hashlib.sha256(query_id.encode()).hexdigest(),
            "query_order": order,
            "query_weight": 1.0,
            "dynamic": False,
            "binding_id": "static",
            "binding_values": None,
            "plan_mode": "custom",
            "generic_plan_semantics_available": None,
            "required_binding_ids": [],
            "required_plan_modes": [],
            "raw_explain": {
                "query_block": {
                    "select_id": 1,
                    "table": {"table_name": query_id, "access_type": "ALL", "rows_examined_per_scan": 10},
                }
            },
        })
    return build_canonical_plan_bundle(
        captures,
        registry_query_ids=["q1", "q2"],
        registry_sha256=registry_sha,
        registry_authority_verified=True,
    )


def test_collects_all_oat_endpoints_serially_and_freezer_accepts(tmp_path: Path) -> None:
    request, request_path, files = _fixture(tmp_path)
    adapter = FakeAdapter()
    bundle = _bundle(_pointer(files["registry"])["sha256"])
    result = collect_endpoint_evidence(
        request,
        request_path,
        tmp_path / "out",
        adapter_factory=lambda _runtime: adapter,
        estimator_factory=lambda current, _config: FakeEstimator(current, bundle),
    )
    assert result["endpoint_count"] == 5
    assert adapter.calls[0] == "snapshot"
    assert [row[1] for row in adapter.calls if isinstance(row, tuple) and row[0] == "apply"] == [
        {"p1": 1.0, "p2": 3.0},
        {"p1": 0.5, "p2": 3.0},
        {"p1": 2.0, "p2": 3.0},
        {"p1": 1.0, "p2": 1.0},
        {"p1": 1.0, "p2": 9.0},
    ]
    assert adapter.calls[-1] == ("restore", {"state": "original"})
    output = tmp_path / "out"
    for artifact in output.glob("*.json"):
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == Path(
            str(artifact) + ".sha256"
        ).read_text().split()[0]
    evidence = json.loads((output / "endpoint_evidence.json").read_text())
    assert Path(evidence["collector"]["path"]).name == (
        "collect_mysql_trained_estimator_endpoint_evidence.py"
    )
    assert len(evidence["collector"]["sha256"]) == 64
    assert evidence["collection_execution"]["cost_apply_and_explain_max_concurrency"] == 1
    explain_path = output / evidence["anchor"]["explain_artifact"]["path"]
    explain = json.loads(explain_path.read_text())
    assert explain["query_ids"] == ["q1", "q2"]
    assert explain["canonical_plan_bundle"]["full_explain_preserved"] is True
    assert len(explain["raw_explain_captures"]) == 2
    freeze_path = output / "freeze_request.json"
    artifact = build_estimator_artifact(json.loads(freeze_path.read_text()), freeze_path)
    assert list(artifact["table"]) == ["p1", "p2"]


def test_failure_restores_and_publishes_no_partial_directory(tmp_path: Path) -> None:
    request, request_path, files = _fixture(tmp_path)
    adapter = FakeAdapter()
    bundle = _bundle(_pointer(files["registry"])["sha256"])
    with pytest.raises(CalibrationError, match="endpoint collection failed"):
        collect_endpoint_evidence(
            request,
            request_path,
            tmp_path / "out",
            adapter_factory=lambda _runtime: adapter,
            estimator_factory=lambda current, _config: FakeEstimator(current, bundle, fail_at=3),
        )
    assert adapter.calls[-1] == ("restore", {"state": "original"})
    assert not (tmp_path / "out").exists()
    assert not list(tmp_path.glob(".out.staging.*"))


def test_rejects_credentials_before_adapter_creation(tmp_path: Path) -> None:
    request, request_path, _files = _fixture(tmp_path)
    request["password"] = "CHANGE_ME"
    called = False

    def adapter_factory(_runtime: object) -> object:
        nonlocal called
        called = True
        raise AssertionError

    with pytest.raises(CalibrationError, match="credential field"):
        collect_endpoint_evidence(
            request,
            request_path,
            tmp_path / "out",
            adapter_factory=adapter_factory,
            estimator_factory=lambda _adapter, _config: object(),
        )
    assert called is False
    assert not (tmp_path / "out").exists()


def test_rejects_stale_estimator_embedded_in_parameter_space(tmp_path: Path) -> None:
    request, request_path, files = _fixture(tmp_path)
    space = json.loads(files["space"].read_text())
    space["estimator"]["sha256"] = "0" * 64
    _write(files["space"], space)
    request["binding"]["parameter_space"]["artifact"] = _pointer(files["space"])
    _write(request_path, request)
    with pytest.raises(CalibrationError, match="embedded estimator identity"):
        _validated_request(request, request_path)


def test_production_scope_explicitly_rejects_job_route_and_write_registry(tmp_path: Path) -> None:
    request, request_path, files = _fixture(tmp_path)
    validated = _validated_request(request, request_path)
    base_config = {
        "type": "trained_models",
        "benchmark": "JOB",
        "artifacts": {resource: str(files[resource]) for resource in ("cpu", "memory", "io")},
        "feature_source": {
            "workload_freeze_id": "workload-v1",
            "workload_registry_sha256": _pointer(files["registry"])["sha256"],
            "workload_registry_path": str(files["registry"]),
            "require_strict_plan_registry": True,
            "queries": [{"query_id": "q1", "sql": "SELECT 1"}],
        },
    }
    with pytest.raises(CalibrationError, match="no benchmark route"):
        _verify_production_config(validated, base_config)
    base_config["benchmark"] = "C"
    base_config["feature_source"]["queries"] = [
        {"query_id": "q1", "sql": "UPDATE account SET balance=balance+1"}
    ]
    with pytest.raises(CalibrationError, match="write-transaction registry"):
        _verify_production_config(validated, base_config)


def test_production_scope_accepts_parenthesized_select_but_rejects_parenthesized_write(
    tmp_path: Path,
) -> None:
    request, request_path, files = _fixture(tmp_path)
    validated = _validated_request(request, request_path)
    base_config = {
        "type": "trained_models",
        "benchmark": "H",
        "artifacts": {resource: str(files[resource]) for resource in ("cpu", "memory", "io")},
        "feature_source": {
            "workload_freeze_id": "workload-v1",
            "workload_registry_sha256": _pointer(files["registry"])["sha256"],
            "workload_registry_path": str(files["registry"]),
            "require_strict_plan_registry": True,
            "queries": [{"query_id": "q1", "sql": "(( SELECT 1)) UNION (SELECT 2)"}],
        },
    }
    _verify_production_config(validated, base_config)
    base_config["feature_source"]["queries"] = [
        {"query_id": "q1", "sql": "(( UPDATE account SET balance=balance+1))"}
    ]
    with pytest.raises(CalibrationError, match="write-transaction registry"):
        _verify_production_config(validated, base_config)
