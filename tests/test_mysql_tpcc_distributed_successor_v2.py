from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "shell/build_mysql_tpcc_distributed_successor_v2.py"


def builder():
    spec = importlib.util.spec_from_file_location(
        "tpcc_successor_v2_builder", BUILDER_PATH
    )
    assert spec is not None and spec.loader is not None
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_two_independent_builds_are_deterministic_and_immutable(
    tmp_path: Path,
) -> None:
    module = builder()
    first = module.build(tmp_path / "first")
    second = module.build(tmp_path / "second")
    assert first.name == second.name
    first_manifest = module.verify_published(first)
    second_manifest = module.verify_published(second)
    assert first_manifest == second_manifest
    assert first_manifest["shared_collector_sha256"] == module.sha256(
        module.SHARED_COLLECTOR
    )
    assert first_manifest["shared_metric_adapter_sha256"] == module.sha256(
        module.SHARED_METRIC_ADAPTER
    )
    assert first_manifest["readiness_verifier_sha256"] == module.sha256(
        module.READINESS_VERIFIER
    )
    assert first_manifest["shared_collector_sha256"] == (
        module.EXPECTED_SHARED_COLLECTOR_SHA256
    )
    assert first_manifest["adopted_service_sha256"] == module.sha256(
        module.ADOPTED_SERVICE
    )
    assert first_manifest["phase_materializer_sha256"] == module.sha256(
        module.PHASE_MATERIALIZER
    )
    assert first_manifest["formal_launch_authorized"] is False
    assert first_manifest["accepted_round_authorized"] is False
    assert first_manifest["supersedes_bundle_id"] == module.PREVIOUS_V2_ID
    assert first_manifest["source_base_bundle_id"] == module.BASE_ID
    assert not os.access(first / "bundle_manifest.json", os.W_OK)


def test_sealed_verifier_and_composite_capture_contract(tmp_path: Path) -> None:
    module = builder()
    bundle = module.build(tmp_path / "out")
    runtime = json.loads(
        (bundle / "distributed_runtime_contract.json").read_text()
    )
    assert runtime["runtime_capabilities"] == {
        "qualification": True,
        "typed_capture_after_gate": True,
        "typed_capture_mode":
            "materialized_composite_client_command_only",
        "legacy_local_capture_entry_launch_authorized": False,
        "production_trace_registry_v2_seal": True,
        "same_live_container_handoff_authority": True,
        "node04_adopted_service_bootstrap": True,
        "private_tmpfs_metric_bridge": True,
        "ordered_phase_command_materializer": True,
        "manifest_bound_mounted_jdk_python": True,
        "docker_image_id_live_readback": True,
        "client_target_device_prefault_zero_io_gate": True,
        "fresh_r0": False,
        "debug": False,
        "formal_outer": False,
    }
    command = json.loads(
        (
            bundle
            / "qualification/typed_capture_command_template_v2.json"
        ).read_text()
    )
    assert command["argv"][1].endswith(
        "formal_tpcc_composite_workload.py"
    )
    assert "com.oltpbenchmark.DBWorkload" in command["actual_java_argv"]
    assert command["required_observed_binding_count"] == 34
    assert command["environment"][
        "MYSQL_TPCC_TYPED_TRACE_ROLE"
    ] == "OWNER"
    assert command["qps_resource_input_prohibited"] is True
    assert (
        bundle
        / "authority/mysql_tpcc_node04_adopted_service_v1.py"
    ).is_file()
    assert (
        bundle
        / "authority/mysql_tpcc_materialize_phase_command_v1.py"
    ).is_file()
    assert (
        bundle / "authority/runtime_tree_content_identity_v3.py"
    ).is_file()
    invalidation = json.loads(
        (
            bundle
            / f"authority/invalidation_{module.PREVIOUS_V2_ID}.json"
        ).read_text()
    )
    assert invalidation["status"] == "INVALIDATED_DO_NOT_LAUNCH"
    assert invalidation["invalidated_bundle_id"] == module.PREVIOUS_V2_ID
    assert invalidation["replacement_shared_collector_sha256"] == (
        module.sha256(module.SHARED_COLLECTOR)
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(
                bundle
                / "authority/tpcc_v2_formal10_readiness_verifier.py"
            ),
            "--bundle",
            str(bundle),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert completed.returncode == 1
    audit = json.loads(completed.stdout)
    assert audit["status"] == "NOT_READY"
    assert audit["collector_payload_id"] == module.COLLECTOR_PAYLOAD_ID
    assert "real production typed trace registry v2 is absent" in audit[
        "blockers"
    ]


def test_idempotent_republish_reuses_exact_identity(tmp_path: Path) -> None:
    module = builder()
    root = tmp_path / "out"
    first = module.build(root)
    second = module.build(root)
    assert first == second
    module.verify_published(second)
