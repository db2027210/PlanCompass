from __future__ import annotations

import copy
from pathlib import Path

import pytest

from formal_mysql_sgd_campaign import (
    FormalSGDNotReady,
    _production_r0_identity,
    _require_ready_manifest,
)
from shell.mysql_xp_formal_r0_entry_v1 import r0_campaign_identity
from shell import mysql_xp_launch_contract_v2 as contract


def _identity() -> dict:
    return {
        "schema_version": "mysql_xp_formal_r0_identity_v1",
        "bundle_id": "b" * 64,
        "capture_at_seal_sha256": "a" * 64,
        "capture_trace_sha256": "c" * 64,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "resource_envelope": {
            "cpu_cores": 5.3,
            "cpuset": "184-199,440-455",
            "mems": "1",
            "read_bps": 29360128,
            "write_bps": 524288,
        },
        "mysql_port": 65304,
        "workload_seed": 4508333,
    }


def test_r0_identity_survives_downstream_registry_binding(tmp_path: Path) -> None:
    authority = tmp_path / "authority.json"
    authority.write_text("{}\n", encoding="utf-8")
    before = {"formal_r0_identity": _identity(), "workload_registry": {"status": "pending"}}
    after = copy.deepcopy(before)
    after["workload_registry"] = {
        "status": "sealed",
        "path": "/immutable/mixed_registry.json",
        "sha256": "d" * 64,
    }
    expected = r0_campaign_identity(_identity())
    assert _production_r0_identity(
        before,
        benchmark="hybench_xp",
        readiness_manifest=authority,
        registration={"mutable": "before"},
        service_factory_spec="mysql_algorithm_c_local_services:build",
    ) == expected
    assert _production_r0_identity(
        after,
        benchmark="hybench_xp",
        readiness_manifest=authority,
        registration={"mutable": "after"},
        service_factory_spec="mysql_algorithm_c_local_services:build",
    ) == expected


def test_explicit_r0_identity_is_xp_only(tmp_path: Path) -> None:
    authority = tmp_path / "authority.json"
    authority.write_text("{}\n", encoding="utf-8")
    with pytest.raises(FormalSGDNotReady, match="explicit XP"):
        _production_r0_identity(
            {"formal_r0_identity": _identity()},
            benchmark="job",
            readiness_manifest=authority,
            registration={},
            service_factory_spec="mysql_algorithm_c_local_services:build",
        )


def test_launch_contract_has_disjoint_exact_drivers() -> None:
    assert set(contract.DRIVERS) == {"capture", "formal_r0", "formal_outer"}
    assert contract.DRIVERS["formal_r0"].name == "mysql_xp_formal_r0_entry.py"
    assert contract.DRIVERS["formal_outer"].name == "formal_mysql_sgd_campaign.py"
    assert contract.DRIVERS["formal_r0"] != contract.DRIVERS["formal_outer"]


def test_outer_authority_is_accepted_only_with_exact_contract_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "outer.json"
    document = {
        "schema_version": "mysql_xp_outer_authorization_v1",
        "status": "SEALED_OUTER_AUTHORIZED",
        "launch_authorized": True,
        "bundle_id": "b" * 64,
        "config_sha256": "c" * 64,
        "formal_r0_passed": True,
        "strict_registries_authorized": True,
        "outer_execution_authorized": True,
        "algorithm_c_execution_authorized": True,
        "accepted_round_authorized": False,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
    }
    import json
    import hashlib

    path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    monkeypatch.setenv("MYSQL_XP_CONTRACT_BUNDLE_ID", "b" * 64)
    monkeypatch.setenv("MYSQL_XP_CONTRACT_CONFIG_SHA256", "c" * 64)
    monkeypatch.setenv("MYSQL_XP_CONTRACT_MODE", "formal_outer")
    monkeypatch.setenv(
        "MYSQL_XP_CONTRACT_OUTER_AUTHORIZATION_SHA256",
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    assert _require_ready_manifest(path)["outer_execution_authorized"] is True
    monkeypatch.setenv("MYSQL_XP_CONTRACT_MODE", "formal_r0")
    with pytest.raises(FormalSGDNotReady, match="outer authority"):
        _require_ready_manifest(path)
