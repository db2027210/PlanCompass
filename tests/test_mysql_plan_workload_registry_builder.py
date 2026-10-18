from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load_script("mysql_registry_builder", ROOT / "shell/build_mysql_plan_workload_registry.py")
verifier = load_script("mysql_registry_verifier", ROOT / "shell/verify_mysql_plan_workload_registry.py")


def test_offline_builder_publishes_only_complete_static_job(tmp_path: Path) -> None:
    result = builder.build(tmp_path, check=False)
    assert result["status"] == "PASS"
    verified = verifier.verify(tmp_path)
    assert verified["registry_sha256"] == result["registry_sha256"]
    audit = json.loads((tmp_path / "registry_build_audit.json").read_text())
    assert [row["benchmark"] for row in audit["published"]] == ["job"]
    assert {row["benchmark"] for row in audit["unpublished"]} == {
        "hybench_ap", "tpch", "tpcc", "hybench_xp"
    }
    assert all(row["decision"] == "FAIL_CLOSED_NOT_PUBLISHED" for row in audit["unpublished"])
    gaps = json.loads((tmp_path / "binding_authority_gap_audit.json").read_text())
    assert gaps["mysql_dynamic_plan_cluster_deletion_authorized"] is False
    by_benchmark = {row["benchmark"]: row for row in gaps["benchmarks"]}
    assert len(by_benchmark["hybench_ap"]["queries"]) == 13
    assert len(by_benchmark["tpch"]["queries"]) == 22
    assert by_benchmark["tpch"]["trace_audit"]["missing_completed_query_ids"] == ["Q9", "Q15"]


def test_check_mode_and_float_weights_are_strict(tmp_path: Path) -> None:
    builder.build(tmp_path, check=False)
    builder.build(tmp_path, check=True)
    path = tmp_path / "job.mysql_plan_workload_registry_v1.json"
    document = json.loads(path.read_text())
    document["queries"][0]["weight"] = 10
    path.write_bytes(builder.canonical_bytes(document))
    digest = builder.sha256_file(path)
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n")
    with pytest.raises(ValueError, match="JSON float"):
        verifier.verify(tmp_path)


def test_builder_check_detects_noncanonical_or_stale_output(tmp_path: Path) -> None:
    builder.build(tmp_path, check=False)
    path = tmp_path / "registry_build_audit.json"
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="canonical artifact differs"):
        builder.build(tmp_path, check=True)


def test_binding_gap_audit_cannot_invent_a_representative_binding(tmp_path: Path) -> None:
    builder.build(tmp_path, check=False)
    path = tmp_path / "binding_authority_gap_audit.json"
    document = json.loads(path.read_text())
    document["benchmarks"][0]["queries"][0]["representative_bindings"] = [[123]]
    path.write_bytes(builder.canonical_bytes(document))
    digest = builder.sha256_file(path)
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n")
    with pytest.raises(ValueError, match="invalid AP binding closure"):
        verifier.verify(tmp_path)
