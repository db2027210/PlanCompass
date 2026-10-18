from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "shell/audit_mysql_job_tpch_distributed_successors.py"
SPEC = importlib.util.spec_from_file_location("distributed_audit", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
audit_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_module)


def fixtures():
    contract = json.loads(
        audit_module.CONTRACT.read_text(encoding="utf-8")
    )
    manifests = {
        name: json.loads(
            (bundle / "bundle_manifest.json").read_text(encoding="utf-8")
        )
        for name, bundle in audit_module.BUNDLES.items()
    }
    return contract, manifests


def test_full_local_audit_passes_without_remote_authority() -> None:
    result = audit_module.audit()
    assert result["status"] == "AUDIT_PASS_STAGED_NOT_LAUNCH_AUTHORIZED"
    assert result["remote_commands_executed"] is False
    assert result["remote_data_plane_authorized"] is False
    assert result["accepted_round_authorized"] is False


def test_old_local_layout_fails_closed() -> None:
    contract, manifests = fixtures()
    contract["layout"]["job"]["host_id"] = "compute00"
    contract["layout"]["job"]["cpuset_cpus"] = "176-183,432-439"
    assert "distributed layout differs" in audit_module.validate_contract(
        contract, manifests
    )


def test_calibrated_limit_change_is_not_a_layout_successor() -> None:
    contract, manifests = fixtures()
    contract["envelope_policy"]["tpch"]["cpu_quota_cores"] = 3
    assert any(
        "tpch changes calibrated limits" in item
        for item in audit_module.validate_contract(contract, manifests)
    )


def test_reflink_auto_shortcut_fails_closed() -> None:
    contract, manifests = fixtures()
    contract["required_source_changes"].remove(
        "replace template copy --reflink=auto with --reflink=never"
    )
    contract["prohibited_shortcuts"].remove("retain --reflink=auto")
    problems = audit_module.validate_contract(contract, manifests)
    assert "minimum source change set differs" in problems
    assert "fail-closed shortcut prohibitions are incomplete" in problems


def test_incomplete_fresh_remote_gate_fails_closed() -> None:
    contract, manifests = fixtures()
    contract["fresh_remote_authorization_inputs"].pop()
    assert "fresh remote authorization gates are incomplete" in (
        audit_module.validate_contract(contract, manifests)
    )


def test_planner_replay_port_relation_fails_closed() -> None:
    contract, manifests = fixtures()
    contract["layout"]["job"]["replay_port"] = 65102
    problems = audit_module.validate_contract(contract, manifests)
    assert "distributed layout differs" in problems
    assert "job replay port is not planner+100" in problems
