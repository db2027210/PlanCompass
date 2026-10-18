from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "shell/build_mysql_job_distributed_v2_successor.py"
COLLECTOR_PATH = (
    ROOT / "shell/mysql_distributed_live_readback_qualification_v1.py"
)
ADOPTED_SERVICES_PATH = (
    ROOT / "shell/mysql_job_distributed_v2_adopted_services.py"
)
ROLLING_MONITOR_PATH = (
    ROOT / "shell/mysql_job_distributed_v2_rolling_monitor.py"
)
FORMAL_JOB_WORKLOAD_PATH = ROOT / "shell/formal_job_multiquery_workload.py"
PHYSICAL_COPY_PATH = ROOT / "shell/mysql_job_physical_copy_v1.py"
BOOTSTRAP_SPEC_RENDERER_PATH = (
    ROOT / "shell/render_mysql_job_v32_bootstrap_spec_v1.py"
)
QUALIFICATION_SPEC_RENDERER_PATH = (
    ROOT / "shell/render_mysql_job_v32_qualification_solo_v1.py"
)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILDER = load_module(BUILDER_PATH, "job_v2_builder")
COLLECTOR = load_module(COLLECTOR_PATH, "job_v2_sealed_collector")


def test_build_is_deterministic_and_current_collector_is_exact() -> None:
    first = BUILDER.build()
    second = BUILDER.build()
    assert first == second
    bundle = Path(first["bundle"])
    manifest = json.loads(
        (bundle / "bundle_manifest.json").read_text(encoding="utf-8")
    )
    assert bundle.name == manifest["bundle_id"] == first["bundle_id"]
    assert manifest["collector_sha256"] == BUILDER.sha256(COLLECTOR_PATH)
    assert manifest["collector_sha256"] != (
        "27240549d82200661615ac129bf081c0a7e01a2598faa5887be32c6fb821ac9b"
    )
    assert (
        BUILDER.sha256(
            bundle
            / "qualification/mysql_distributed_live_readback_qualification_v1.py"
        )
        == BUILDER.sha256(COLLECTOR_PATH)
    )
    helper_root = bundle / "mysql_tpch_client_immutable_identity_v1.py"
    helper_qualification = (
        bundle / "qualification/mysql_tpch_client_immutable_identity_v1.py"
    )
    assert helper_root.read_bytes() == helper_qualification.read_bytes()
    assert manifest["files"][helper_root.name] == BUILDER.sha256(helper_root)
    assert manifest["files"]["qualification/" + helper_root.name] == (
        BUILDER.sha256(helper_qualification)
    )
    assert BUILDER.sha256(
        bundle / "qualification/mysql_job_distributed_v2_adopted_services.py"
    ) == BUILDER.sha256(ADOPTED_SERVICES_PATH)
    assert BUILDER.sha256(
        bundle / "qualification/mysql_job_distributed_v2_rolling_monitor.py"
    ) == BUILDER.sha256(ROLLING_MONITOR_PATH)
    assert BUILDER.sha256(
        bundle / "qualification/formal_job_multiquery_workload.py"
    ) == BUILDER.sha256(FORMAL_JOB_WORKLOAD_PATH)
    assert BUILDER.sha256(
        bundle / "qualification/mysql_job_physical_copy_v1.py"
    ) == BUILDER.sha256(PHYSICAL_COPY_PATH)
    assert BUILDER.sha256(
        bundle / "controller/render_mysql_job_v32_bootstrap_spec_v1.py"
    ) == BUILDER.sha256(BOOTSTRAP_SPEC_RENDERER_PATH)
    assert BUILDER.sha256(
        bundle
        / "controller/render_mysql_job_v32_qualification_solo_v1.py"
    ) == BUILDER.sha256(QUALIFICATION_SPEC_RENDERER_PATH)
    runtime = COLLECTOR.verify_bundle(bundle)[1]
    assert runtime["schema_version"].endswith("_v2")
    assert runtime["client_resource_envelope"]["cpuset_cpus"] == "0-2,36-38"
    assert runtime["storage"]["full_physical_copy_required"] is True
    assert (
        runtime["same_live_planner_handoff"]["old_top5_rerun_authorized"]
        is False
    )
    assert runtime["same_live_production_services"].endswith(
        "mysql_job_distributed_v2_adopted_services.py"
    )
    assert runtime["rolling_full_host_monitor"].endswith(
        "mysql_job_distributed_v2_rolling_monitor.py"
    )
    assert runtime["toolchain"]["python_runtime"].endswith(
        "/bin/python3.11"
    )
    tree = json.loads((bundle / "runtime/python_runtime_tree_manifest_v3.json").read_text())["tree_content_sha256"]
    assert len(tree) == 64
    assert tree == runtime["toolchain"]["python_tree_content_sha256"]
    assert runtime["toolchain"]["python_runtime_root"].endswith(tree)
    assert runtime["database_counter_transport"]["kind"] == (
        "exact_client_container_docker_exec_stdout_v1"
    )
    assert (
        runtime["database_counter_transport"]["host_mysql_cli_required"]
        is False
    )
    invalidation = json.loads(
        (
            bundle
            / "authority/mysql_job_v32_449cd22_content_identity_invalidation_20260731.json"
        ).read_text(encoding="utf-8")
    )
    assert invalidation["bundle_id"] == (
        "449cd22df33192958ecb98c96b2fbbeba0f69a281115e7e046bfdc48ed2c2bc1"
    )
    assert invalidation["database_or_client_started"] is False
    afe_invalidation = json.loads(
        (
            bundle
            / "authority/mysql_job_v32_afe700_normalization_contract_invalidation_20260731.json"
        ).read_text(encoding="utf-8")
    )
    assert afe_invalidation["bundle_id"] == (
        "afe70067652f8ce5babaabe6584a3d5196d526af0766d690567fdd3059fcb5c8"
    )
    assert afe_invalidation["database_or_client_started"] is False
    f058_invalidation = json.loads(
        (
            bundle
            / "authority/mysql_job_v32_f0583a_sql_percent_invalidation_20260731.json"
        ).read_text(encoding="utf-8")
    )
    assert f058_invalidation["bundle_id"] == (
        "f0583a934806dc2125ea402bf0e85e30b0e637a55e424d3d1ea1580875c3774c"
    )
    assert f058_invalidation["failed_attempt_token"] == (
        "job-v2-20260730t195053z-07e47cc55f09d6a807e4eb07"
    )
    assert f058_invalidation["accepted_rounds_credit"] == 0
    fouree_invalidation = json.loads(
        (
            bundle
            / "authority/mysql_job_v32_4ee58d_client_managed_mount_invalidation_20260731.json"
        ).read_text(encoding="utf-8")
    )
    assert fouree_invalidation["bundle_id"] == (
        "4ee58d435e830333b56f5a91876303a9f65d176a6ce0a760dbb51cd078112e7d"
    )
    assert fouree_invalidation["workload_started"] is False
    f1c705_invalidation = json.loads(
        (
            bundle
            / "authority/mysql_job_v32_f1c705_client_anonymous_volume_invalidation_20260731.json"
        ).read_text(encoding="utf-8")
    )
    assert f1c705_invalidation["bundle_id"] == (
        "f1c7051a70019ca6534a6687754349c6633e8f9ef1a43ac0a23564372207ccd6"
    )
    assert f1c705_invalidation["workload_started"] is False
    assert (
        bundle / "qualification/client_mysql_datadir_mask/.sealed-empty"
    ).read_text(encoding="utf-8") == "sealed read-only client volume mask\n"
    sevene08_invalidation = json.loads(
        (
            bundle
            / "authority/mysql_job_v32_7e08bd_client_bridge_identity_invalidation_20260731.json"
        ).read_text(encoding="utf-8")
    )
    assert sevene08_invalidation["bundle_id"] == (
        "7e08bd2220b9f2c196163b71ba2d114d191b5c80377c2cd35e4778b7860a9d2f"
    )
    assert sevene08_invalidation["workload_started"] is False
    twofee_invalidation = json.loads(
        (
            bundle
            / "authority/mysql_job_v32_2fe2ae_transient_inspect_hash_invalidation_20260731.json"
        ).read_text(encoding="utf-8")
    )
    assert twofee_invalidation["bundle_id"] == (
        "2fe2ae0c618eb022109cefc4c95d0d424e29a7062cd47f14241c975f1f828042"
    )
    assert twofee_invalidation["workload_started"] is False
    sql_native_invalidation = json.loads(
        (
            bundle
            / "authority/"
            "mysql_job_v32_0239_sql_query_native_contract_invalidation_20260731.json"
        ).read_text(encoding="utf-8")
    )
    assert sql_native_invalidation["bundle_id"] == (
        "0239f5e17923776f23cd54b8c9c77e055d9a7470e600b32dc00d234c1ca40d46"
    )
    assert sql_native_invalidation["accepted_rounds_credit"] == 0
    import_invalidation = json.loads(
        (
            bundle
            / "authority/mysql_job_v32_6bcd_collector_import_invalidation_20260731.json"
        ).read_text(encoding="utf-8")
    )
    assert import_invalidation["bundle_id"] == (
        "6bcd66a5011081c31d8adc12372304f2ff93d511824114be06dc617ea1a6fa9f"
    )
    assert import_invalidation["workload_started"] is False
    assert import_invalidation["accepted_rounds_credit"] == 0


def test_sealed_entrypoints_import_with_empty_pythonpath_and_leave_no_pycache(
    tmp_path: Path,
) -> None:
    bundle = Path(BUILDER.build()["bundle"])
    entries = (
        "qualification/mysql_distributed_live_readback_qualification_v1.py",
        "qualification/mysql_job_distributed_v2_authority.py",
        "qualification/mysql_job_distributed_v2_adopted_services.py",
        "qualification/mysql_job_distributed_v2_rolling_monitor.py",
        "qualification/formal_job_multiquery_workload.py",
        "qualification/mysql_job_physical_copy_v1.py",
        "qualification/mysql_distributed_workload_metric_adapter_v1.py",
        "qualification/mysql_sql_debug_metric_adapter_v1.py",
        "controller/render_mysql_job_v32_bootstrap_spec_v1.py",
        "controller/render_mysql_job_v32_qualification_solo_v1.py",
    )
    environment = {"PATH": os.environ["PATH"], "PYTHONPATH": "",
                   "PYTHONDONTWRITEBYTECODE": "1"}
    for relative in entries:
        completed = subprocess.run(
            [sys.executable, "-B", str(bundle / relative), "--help"],
            cwd=tmp_path, env=environment, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        assert completed.returncode == 0, (relative, completed.stderr)
    assert not any(path.name == "__pycache__" for path in bundle.rglob("*"))


def test_bundle_contains_complete_algorithm_and_reconciliation_boundaries() -> None:
    result = BUILDER.build()
    bundle = Path(result["bundle"])
    config = json.loads((bundle / "config.json").read_text(encoding="utf-8"))
    reconciliation = json.loads(
        (
            bundle
            / "authority/formal_replay_authority_reconciliation_v1.json"
        ).read_text(encoding="utf-8")
    )
    assert config["parameter_count"] == 4
    assert config["candidate_count"] == 15
    assert config["enumerate_all_nonempty_subspaces"] is True
    assert config["original_gradient_only"] is True
    assert config["global_candidate_score_sort"] is True
    assert config["fixed_top_k_real_replay"] == 5
    assert config["qps_role"] == "outer_acceptance_after_real_replay_only"
    assert config["fingerprint_levels_required"] == ["L1", "L2", "L3"]
    assert config["accepted_rounds"] == "0/10"
    assert config["formal10_authorized"] is False
    assert reconciliation["status"] == "RECOVERY_AUTHORITY_RECONCILED_NO_RERUN"
    assert reconciliation["rerun_authorized"] is False
    assert reconciliation["replayed_candidate_ranks"] == [1, 2, 3, 4, 5]
    assert reconciliation["accepted_rounds_credit"] == 0


def test_private_publish_is_transactional_idempotent_and_tamper_closed(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "published"
    payloads = {"payload.txt": b"sealed\n"}
    files = {"payload.txt": BUILDER.sha_bytes(payloads["payload.txt"])}
    manifest = {
        "schema_version": "fixture",
        "bundle_id": "fixture",
        "files": files,
    }
    manifest_payload = BUILDER.canonical(manifest)
    first = BUILDER._publish(
        parent,
        "fixture",
        payloads,
        "bundle_manifest.json",
        manifest_payload,
        files,
        "fixture",
    )
    second = BUILDER._publish(
        parent,
        "fixture",
        payloads,
        "bundle_manifest.json",
        manifest_payload,
        files,
        "fixture",
    )
    assert first == second
    assert not list(parent.glob(".stage-*"))
    (first / "payload.txt").write_bytes(b"tampered\n")
    with pytest.raises(BUILDER.BuildError, match="closure differs"):
        BUILDER._publish(
            parent,
            "fixture",
            payloads,
            "bundle_manifest.json",
            manifest_payload,
            files,
            "fixture",
        )
    assert not list(parent.glob(".stage-*"))


def test_source_and_bundle_reject_generated_or_unsealed_entries(
    tmp_path: Path,
) -> None:
    root = tmp_path / "closure"
    root.mkdir()
    (root / "sealed.txt").write_bytes(b"ok")
    files = {"sealed.txt": BUILDER.sha_bytes(b"ok")}
    BUILDER.verify_file_map(root, files, "fixture")
    (root / "extra.txt").write_bytes(b"not sealed")
    with pytest.raises(BUILDER.BuildError, match="exact entry set"):
        BUILDER.verify_file_map(root, files, "fixture")
    generated = root / "__pycache__"
    generated.mkdir()
    (generated / "x.pyc").write_bytes(b"cache")
    with pytest.raises(BUILDER.BuildError, match="extra.txt"):
        BUILDER.verify_file_map(
            root, files, "fixture", allow_generated_extras=True
        )


def test_invalid_87550_bundle_is_not_a_payload_source() -> None:
    source = BUILDER_PATH.read_text(encoding="utf-8")
    assert "87550b383151" not in source
    assert "27240549d822" not in source
    result = BUILDER.build()
    source_manifest = json.loads(
        (
            Path(result["source"]) / "source_manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert source_manifest["base_source_bundle_id"] == (
        "7544c870c204cc08f0876ea2e435b4a9f5dc8a1f4ae8a4c9982152a8a729d204"
    )
    assert len(source_manifest["files"]) >= 983


def test_sealed_source_forces_physical_copy_and_remote_client_workload() -> None:
    result = BUILDER.build()
    source = Path(result["source"])
    services = (
        source / "shell/mysql_job_distributed_v2_adopted_services.py"
    ).read_text(encoding="utf-8")
    monitor = (
        source / "shell/mysql_job_distributed_v2_rolling_monitor.py"
    ).read_text(encoding="utf-8")
    physical_copy = (
        source / "shell/mysql_job_physical_copy_v1.py"
    ).read_text(encoding="utf-8")
    campaign = (
        source / "shell/manual_mysql_sgd_r0_campaign.py"
    ).read_text(encoding="utf-8")
    assert "os.read(" in physical_copy
    assert "os.write(" in physical_copy
    assert "FIEMAP_EXTENT_SHARED" in physical_copy
    assert "MYSQL_DATADIR_UID = 999" in physical_copy
    assert "target_permission_normalization" in physical_copy
    assert "copy_file_range(" not in physical_copy
    assert "{self.template_copy_command()}" in campaign
    assert "def template_copy_command" in services
    assert '"docker",\n                "exec"' in services
    assert "formal_job_multiquery_workload.py" in services
    assert "MYSQL_JOB_V2_ROLLING_GATE_SAMPLE_JSON=" in monitor


def test_invalidated_bundle_is_sealed_as_do_not_launch_authority() -> None:
    result = BUILDER.build()
    bundle = Path(result["bundle"])
    invalidation = json.loads(
        (
            bundle
            / "authority/mysql_job_v32_b7563277_invalidation_20260731.json"
        ).read_text(encoding="utf-8")
    )
    assert invalidation["status"] == "INVALIDATED_DO_NOT_LAUNCH"
    assert invalidation["bundle_id"].startswith("b7563277")
    assert invalidation["formal_launch_authorized"] is False
    assert invalidation["accepted_round_authorized"] is False
    target_permissions = json.loads(
        (
            bundle
            / "authority/mysql_job_v32_2d237b84_invalidation_20260731.json"
        ).read_text(encoding="utf-8")
    )
    assert target_permissions["bundle_id"].startswith("2d237b84")
    assert target_permissions["status"] == "INVALIDATED_DO_NOT_LAUNCH"
    target_closure = json.loads(
        (
            bundle
            / "authority/mysql_job_v32_4d5c50_target_closure_invalidation_20260731.json"
        ).read_text(encoding="utf-8")
    )
    assert target_closure["bundle_id"].startswith("4d5c50")
    assert target_closure["failure_stage"] == (
        "post_physical_copy_pre_database_target_closure_audit"
    )
    assert target_closure["database_or_client_started"] is False
    assert target_closure["database_pid"] == 0
    assert target_closure["client_pid"] == 0
