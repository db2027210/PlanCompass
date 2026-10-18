from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from shell import build_mysql_job_tpch_distributed_successors_v1 as builder


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def contract() -> dict:
    return load(builder.CONTRACT)


def predecessor_parts(name: str):
    return builder.predecessor(name)


def test_dry_run_is_deterministic_non_launchable_and_local_only() -> None:
    first = builder.build(False)
    second = builder.build(False)
    assert first == second
    assert first["status"] == (
        "READY_TO_PUBLISH_STAGED_NOT_LAUNCH_AUTHORIZED"
    )
    assert first["remote_commands_executed"] is False
    assert first["remote_data_plane_modified"] is False
    assert first["accepted_round_authorized"] is False
    assert set(first["successors"]) == {"job", "tpch"}


def test_cli_accepts_integrated_directory_aware_v3_but_remains_staged() -> None:
    completed = subprocess.run(
        [sys.executable, str(builder.BUILDER)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    result = json.loads(completed.stdout)
    assert result["status"] == (
        "READY_TO_PUBLISH_STAGED_NOT_LAUNCH_AUTHORIZED"
    )
    assert result["accepted_round_authorized"] is False


@pytest.mark.parametrize("name", ["job", "tpch"])
def test_source_is_full_predecessor_closure_plus_exact_staged_overlay(
    name: str,
) -> None:
    _, source, source_manifest = predecessor_parts(name)
    frozen = contract()
    layout = frozen["layout"][name]
    env = frozen["envelope_policy"][name]
    payloads = builder.source_payloads(
        name, source, source_manifest, layout, env
    )
    predecessor_files = source_manifest["files"]
    assert set(predecessor_files) <= set(payloads)
    sql_count = 9 if name == "job" else 22
    assert len(payloads) == len(predecessor_files) + 11 + sql_count
    for relative, digest in predecessor_files.items():
        if relative != "shell/manual_mysql_sgd_r0_campaign.py":
            assert builder.sha_bytes(payloads[relative]) == digest
    manual = payloads[
        "shell/manual_mysql_sgd_r0_campaign.py"
    ].decode("utf-8")
    assert "cp -a --reflink=auto" not in manual
    assert "cp -a --reflink=never" in manual
    assert "compute00_remote_ssh.lock" not in manual
    assert "RA_CONTROL_PLANE_SSH_LOCK" in manual
    assert 'JAVA = Path("/usr/bin/java")' not in manual
    assert "RA_JAVA_BINARY" in manual
    assert layout["ssh_target"] in manual
    assert layout["remote_project_path"] in manual
    assert layout["remote_template_path"] in manual
    assert layout["storage_device"] in manual
    assert repr(layout["cpuset_cpus"]) in manual
    assert f"{layout['planner_port']}," in manual
    compile(
        payloads["shell/manual_mysql_sgd_r0_campaign.py"],
        "manual_mysql_sgd_r0_campaign.py",
        "exec",
    )
    compile(
        payloads[
            "shell/mysql_algorithm_c_distributed_successor_staged_v1.py"
        ],
        "mysql_algorithm_c_distributed_successor_staged_v1.py",
        "exec",
    )
    compile(
        payloads["shell/runtime_tree_content_identity_v3.py"],
        "runtime_tree_content_identity_v3.py",
        "exec",
    )
    compile(
        payloads[
            "qualification/mysql_sql_debug_metric_adapter_v1.py"
        ],
        "mysql_sql_debug_metric_adapter_v1.py",
        "exec",
    )
    compile(
        payloads[
            "qualification/"
            "mysql_distributed_live_readback_qualification_v1.py"
        ],
        "mysql_distributed_live_readback_qualification_v1.py",
        "exec",
    )
    python_manifest = json.loads(
        payloads[builder.PYTHON_PORTABLE_MANIFEST_REL]
    )
    jdk_manifest = json.loads(
        payloads[builder.JDK_PORTABLE_MANIFEST_REL]
    )
    assert (
        python_manifest["tree_content_sha256"]
        == builder.PYTHON_TREE_CONTENT_SHA256
    )
    assert (
        jdk_manifest["tree_content_sha256"]
        == builder.JDK_TREE_CONTENT_SHA256
    )
    builder.runtime_tree.validate_portable_manifest(python_manifest)
    builder.runtime_tree.validate_portable_manifest(jdk_manifest)
    runtime_contract = json.loads(
        payloads["distributed_runtime_contract.json"]
    )
    assert runtime_contract["scenario"] == layout["scenario"]
    assert runtime_contract["storage"]["device"] == (
        layout["storage_device"]
    )
    assert runtime_contract["resource_envelope"]["mysql_port"] == (
        layout["planner_port"]
    )
    assert runtime_contract["toolchain"]["python_required"] is True
    assert runtime_contract["toolchain"]["java_required"] is (
        name == "tpch"
    )
    workload_contract = json.loads(
        payloads["qualification/debug_sql_workload_contract.json"]
    )
    assert workload_contract["native_metric_adapter"]["kind"] == (
        "sql_query_native"
    )
    assert workload_contract["query_closure"]["file_count"] == sql_count
    assert sum(
        relative.startswith(f"qualification/sql/{name}/")
        for relative in payloads
    ) == sql_count
    profile = json.loads(payloads[builder.PROFILE_REL[name]])
    assert profile == {
        "cpuset": layout["cpuset_cpus"],
        "mems": layout["cpuset_mems"],
        "cpu_cores": env["cpu_quota_cores"],
        "memory_bytes": env["memory_bytes"],
        "pids": env["pids_max"],
        "read_bps": env["io_read_bps"],
        "write_bps": env["io_write_bps"],
        "buffer_pool_bytes": env["buffer_pool_bytes"],
    }


@pytest.mark.parametrize("name", ["job", "tpch"])
def test_config_freezes_layout_envelope_ports_and_no_local_authority(
    name: str, tmp_path: Path,
) -> None:
    pred_manifest, _, _ = predecessor_parts(name)
    frozen = contract()
    layout = frozen["layout"][name]
    env = frozen["envelope_policy"][name]
    source = tmp_path / f"source-{name}"
    profile = source / builder.PROFILE_REL[name]
    profile.parent.mkdir(parents=True)
    profile.write_bytes(builder.profile_payload(layout, env))
    config = builder.successor_config(
        name,
        load(builder.PREDECESSORS[name] / "config.json"),
        source,
        "a" * 64,
        "b" * 64,
        layout,
        env,
    )
    assert config["production"] == {
        "formal_entry": "formal_mysql_sgd_campaign:run_production_campaign",
        "launch_authorized": False,
        "service_factory": builder.SERVICE,
    }
    resource = config["execution_resource_authority"]
    assert resource["execution_authorized"] is False
    for key, value in env.items():
        assert resource[key] == value
    assert resource["host_id"] == layout["host_id"]
    assert resource["cpuset_cpus"] == layout["cpuset_cpus"]
    assert resource["cpuset_mems"] == layout["cpuset_mems"]
    assert resource["storage_device"] == layout["storage_device"]
    assert (
        resource["storage_device_major_minor"]
        == layout["storage_device_major_minor"]
    )
    storage = config["execution_storage_authority"]
    assert storage["execution_authorized"] is False
    assert storage["host_id"] == layout["host_id"]
    assert storage["template_path"] == layout["remote_template_path"]
    assert storage["device"] == layout["storage_device"]
    endpoint = config["distributed_endpoint_authority"]
    assert endpoint["planner_host_port"] == layout["planner_port"]
    assert endpoint["replay_host_port"] == layout["replay_port"]
    assert endpoint["container_port"] == 3306
    assert config["mysql"]["port"] == layout["planner_port"]
    assert config["workload"]["host_control_cpuset"] == layout["cpuset_cpus"]
    assert "local_storage_profile_id" not in config
    assert "local_storage_manifest" not in config
    assert "local_storage_manifest_sha256" not in config
    assert (
        config["provenance_only"]["predecessor_local_storage"]
        ["local_storage_profile_id"]
    )
    assert pred_manifest["source_bundle_id"] != "a" * 64


def test_tpch_65103_is_planner_data_port_and_65203_is_replay_only(
    tmp_path: Path,
) -> None:
    frozen = contract()
    name = "tpch"
    layout = frozen["layout"][name]
    env = frozen["envelope_policy"][name]
    _, source, source_manifest = predecessor_parts(name)
    payloads = builder.source_payloads(
        name, source, source_manifest, layout, env
    )
    manual = payloads[
        "shell/manual_mysql_sgd_r0_campaign.py"
    ].decode("utf-8")
    block = manual.split('"tpch": Lane(', 1)[1].split(
        '"tpcc": Lane(', 1
    )[0]
    assert "65103" in block
    assert "65203" not in block
    target = tmp_path / "source"
    profile = target / builder.PROFILE_REL[name]
    profile.parent.mkdir(parents=True)
    profile.write_bytes(builder.profile_payload(layout, env))
    config = builder.successor_config(
        name,
        load(builder.PREDECESSORS[name] / "config.json"),
        target,
        "c" * 64,
        "d" * 64,
        layout,
        env,
    )
    assert config["mysql"]["port"] == 65103
    assert (
        config["distributed_endpoint_authority"]["planner_host_port"]
        == 65103
    )
    assert (
        config["distributed_endpoint_authority"]["replay_host_port"]
        == 65203
    )
    assert (
        config["distributed_endpoint_authority"]["port_semantics"]
        == "planner_and_replay_are_distinct_host_data_ports"
    )


def test_runtime_authorities_bind_manifests_but_require_live_readback() -> None:
    job = builder.RUNTIME["job"]
    assert job["host_id"] == "node02"
    assert job["python36_prohibited"] is True
    assert job["python_version"] == "3.11.11"
    assert job["runtime_install_root"].startswith(
        "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/"
        "runtime_algorithm_c_successor_20260729/runtime"
    )
    assert job["content_address_algorithm"] == (
        "sha256_canonical_relative_tree_entries_v3"
    )
    assert job["deployment_path_rule"].endswith(
        "python311-<tree_content_sha256>"
    )
    assert (
        job["runtime_content_address_state"]
        == "SOURCE_MANIFEST_SEALED_TARGET_READBACK_PENDING"
    )
    assert job["source_runtime_candidate"]["host_id"] == "node04"
    assert job["interpreter_path"].endswith(
        "python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4"
        "ebd87bc3c403c89/bin/python"
    )
    assert job["tree_content_sha256"] == builder.PYTHON_TREE_CONTENT_SHA256
    assert (
        job["portable_manifest_sha256"]
        == builder.PYTHON_PORTABLE_MANIFEST_SHA256
    )
    assert (
        job["tree_sha256_semantics"]
        == "provenance_only_legacy_location_bound_inventory_digest"
    )
    assert (
        job["state"]
        == "TARGET_DEPLOYMENT_AND_FRESH_READBACK_REQUIRED"
    )
    tpch = builder.RUNTIME["tpch"]
    assert tpch["python_version"] == "3.11.11"
    assert tpch["interpreter_path"] == (
        "/home/anonymous/recost_runtime_py311_24_11_1/bin/python"
    )
    assert tpch["interpreter_sha256"].startswith("05bd6d6d")
    assert tpch["tree_content_sha256"] == builder.PYTHON_TREE_CONTENT_SHA256
    assert (
        tpch["runtime_content_address_state"]
        == "SOURCE_MANIFEST_SEALED_TARGET_READBACK_PENDING"
    )
    assert (
        tpch["state"]
        == "SOURCE_MANIFEST_SEALED_FRESH_LIVE_READBACK_REQUIRED"
    )
    assert job["launch_authorized"] is tpch["launch_authorized"] is False
    jdk = builder.jdk_runtime_authority("tpch")
    assert jdk["required_by_workload"] is True
    assert jdk["tree_content_sha256"] == builder.JDK_TREE_CONTENT_SHA256
    assert jdk["java_sha256"].startswith("c8f339ba")


def test_publication_is_content_addressed_transactional_and_idempotent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    source_roots = {
        name: tmp_path / "sources" / name for name in ("job", "tpch")
    }
    bundle_roots = {
        name: tmp_path / "bundles" / name for name in ("job", "tpch")
    }
    output_roots = {
        name: tmp_path / "outputs" / name for name in ("job", "tpch")
    }
    monkeypatch.setattr(builder, "SOURCE_ROOTS", source_roots)
    monkeypatch.setattr(builder, "BUNDLE_ROOTS", bundle_roots)
    monkeypatch.setattr(builder, "OUTPUT_ROOTS", output_roots)
    first = builder.build(True)
    second = builder.build(True)
    assert first == second
    assert first["status"] == "PUBLISHED_STAGED_NOT_LAUNCH_AUTHORIZED"
    for name, result in first["successors"].items():
        source = Path(result["source_directory"])
        bundle = Path(result["bundle_directory"])
        source_manifest = load(source / "source_manifest.json")
        manifest = load(bundle / "bundle_manifest.json")
        assert source.name == "source-" + source_manifest["bundle_id"]
        assert bundle.name == manifest["bundle_id"]
        assert builder.sha256(bundle / "closure.json") == bundle.name
        builder.verify_file_map(
            source, source_manifest["files"], "test source"
        )
        builder.verify_file_map(bundle, manifest["files"], "test bundle")
        predecessor_manifest = load(
            builder.PREDECESSORS[name] / "bundle_manifest.json"
        )
        assert set(predecessor_manifest["files"]) <= {
            relative.removeprefix("predecessor/")
            for relative in manifest["files"]
            if relative.startswith("predecessor/")
        }
        launch = bundle / builder.LAUNCHER_NAME[name]
        checked = subprocess.run(
            [sys.executable, str(launch), "--verify-staged"],
            text=True,
            capture_output=True,
            check=False,
        )
        assert checked.returncode == 0, checked.stderr
        assert (
            json.loads(checked.stdout)["status"]
            == "STAGED_NOT_LAUNCH_AUTHORIZED"
        )
    assert not list(tmp_path.rglob("*.tmp.*"))


def test_changed_existing_publication_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        builder,
        "SOURCE_ROOTS",
        {name: tmp_path / "sources" / name for name in ("job", "tpch")},
    )
    monkeypatch.setattr(
        builder,
        "BUNDLE_ROOTS",
        {name: tmp_path / "bundles" / name for name in ("job", "tpch")},
    )
    monkeypatch.setattr(
        builder,
        "OUTPUT_ROOTS",
        {name: tmp_path / "outputs" / name for name in ("job", "tpch")},
    )
    result = builder.build(True)
    config = Path(result["successors"]["job"]["bundle_directory"]) / "config.json"
    config.chmod(0o600)
    config.write_text("{}\n", encoding="utf-8")
    with pytest.raises(builder.BuildError, match="closure differs"):
        builder.build(True)
