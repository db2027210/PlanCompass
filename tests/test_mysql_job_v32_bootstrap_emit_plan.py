"""Offline assurance for the sealed JOB v32 bootstrap handoff.

This deliberately invokes ``command_plan`` only.  It never invokes the
executor and therefore cannot create containers, copy a datadir, or contact a
remote host.  Keeping this separate from the authority tests makes the exact
content-addressed bundle the object under test.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ID = "0239f5e17923776f23cd54b8c9c77e055d9a7470e600b32dc00d234c1ca40d46"
INVALIDATED_BUNDLE_ID = "bd316145e38bfc3c50230a02cd06cfb39a568cd28c5b3b4e2a67c691796a01d3"
BUNDLE = ROOT / "artifacts/mysql_job_v32_distributed_v2_successor" / BUNDLE_ID
SOURCE_AUTHORITY = ROOT / "shell/mysql_job_distributed_v2_authority.py"
INVALIDATION = ROOT / "preflight/mysql_job_v32_bd316145_invalidation_20260731.json"
CURRENT_INVALIDATION = ROOT / "preflight/mysql_job_v32_82e2867d_invalidation_20260731.json"
TARGET_PERMISSION_INVALIDATION = ROOT / "preflight/mysql_job_v32_2d237b84_invalidation_20260731.json"
TARGET_CLOSURE_INVALIDATION = ROOT / "preflight/mysql_job_v32_4d5c50_target_closure_invalidation_20260731.json"
F058_INVALIDATION = ROOT / "preflight/mysql_job_v32_f0583a_sql_percent_invalidation_20260731.json"
EIGHTF_INVALIDATION = ROOT / "preflight/mysql_job_v32_8f55631_client_credentials_invalidation_20260731.json"
FOUREE_INVALIDATION = ROOT / "preflight/mysql_job_v32_4ee58d_client_managed_mount_invalidation_20260731.json"
F1C705_INVALIDATION = ROOT / "preflight/mysql_job_v32_f1c705_client_anonymous_volume_invalidation_20260731.json"
SPEC_VALIDATOR = ROOT / "shell/validate_mysql_job_v32_bootstrap_spec_v1.py"
SPEC_RENDERER = ROOT / "shell/render_mysql_job_v32_bootstrap_spec_v1.py"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_authority(path: Path, module_name: str):
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.dont_write_bytecode = previous


def _short_lived_spec(authority, manifest_sha256: str) -> dict:
    attempt = "job-v2-" + ("a" * 32)
    attempt_root = f"{authority.RUNTIME_ROOT}/attempts/{attempt}"
    bundle_path = f"{authority.RUNTIME_ROOT}/bundles/{BUNDLE_ID}"
    runtime_root = (
        f"{authority.RUNTIME_ROOT}/runtime/python311-"
        f"{authority.PYTHON_TREE_CONTENT_SHA256}"
    )
    suffix = re.sub(r"[^a-z0-9]", "_", attempt.lower())[:48]
    database = f"ra_mysql_job_v2_{suffix}_db"
    client = f"ra_mysql_job_v2_{suffix}_client"
    now = datetime.now(timezone.utc)
    return {
        "schema_version": "mysql_job_distributed_v2_bootstrap_spec_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "scenario": authority.SCENARIO,
        "host_id": authority.HOSTNAME,
        "attempt_token": attempt,
        "bundle": {
            "path": bundle_path,
            "bundle_id": BUNDLE_ID,
            "manifest_sha256": manifest_sha256,
        },
        "contract_sha256": authority.CONTRACT_SHA256,
        "reconciliation_sha256": authority.RECONCILIATION_SHA256,
        "mysql_image": {
            "reference": "mysql@sha256:" + ("d" * 64),
            "image_id": "sha256:" + ("e" * 64),
        },
        "client_image": {
            "reference": "client@sha256:" + ("f" * 64),
            "image_id": "sha256:" + ("1" * 64),
        },
        "paths": {
            "template": authority.TEMPLATE,
            "runtime_root": authority.RUNTIME_ROOT,
            "attempt_root": attempt_root,
            "datadir": attempt_root + "/datadir",
            "credentials_dir": attempt_root + "/credentials",
            "measurement_tmpfs": attempt_root + "/measurement",
            "python_runtime_root": runtime_root,
        },
        "containers": {"database": database, "client": client},
        "database_counter_bridge": {
            "kind": "exact_client_container_docker_exec_stdout_v1",
            "client_container": client,
            "mysql_binary_in_client": authority.MYSQL_BINARY_IN_CLIENT,
            "stdout_only": True,
            "docker_socket_mount_prohibited": True,
            "target_device_write_prohibited": True,
        },
        "python_runtime": {
            "manifest_relative": authority.PYTHON_MANIFEST_RELATIVE,
            "manifest_sha256": authority.PYTHON_MANIFEST_SHA256,
            "tree_content_sha256": authority.PYTHON_TREE_CONTENT_SHA256,
            "executable_relative": authority.PYTHON_EXECUTABLE_RELATIVE,
            "executable_path": runtime_root + "/" + authority.PYTHON_EXECUTABLE_RELATIVE,
            "executable_sha256": authority.PYTHON_EXECUTABLE_SHA256,
        },
        "mysql_defaults_file": attempt_root + "/credentials/mysql.cnf",
        "collector": bundle_path + "/qualification/mysql_distributed_live_readback_qualification_v1.py",
        "created_at_utc": now.isoformat(),
        "expires_at_utc": (now + timedelta(minutes=15)).isoformat(),
        "main_agent_token_sha256": hashlib.sha256(b"offline-test-only").hexdigest(),
        "qualification_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
    }


def test_sealed_bundle_is_exact_and_matches_current_authority_source() -> None:
    manifest = json.loads((BUNDLE / "bundle_manifest.json").read_text())
    assert manifest["bundle_id"] == BUNDLE_ID
    assert manifest["formal_scope"] == "mysql_only"
    assert manifest["cross_engine_comparison_prohibited"] is True
    assert manifest["formal10_authorized"] is False
    assert manifest["accepted_round_authorized"] is False

    from shell.verify_remote_content_addressed_bundle import verify

    assert verify(str(BUNDLE), BUNDLE_ID) == len(manifest["files"])
    bundled = BUNDLE / "qualification/mysql_job_distributed_v2_authority.py"
    assert _sha256(bundled) == manifest["files"]["qualification/mysql_job_distributed_v2_authority.py"]
    assert _sha256(SOURCE_AUTHORITY) == _sha256(bundled)
    authority = _load_authority(bundled, "job_v32_authority_tree_identity")
    runtime_tree = json.loads((BUNDLE / "runtime/python_runtime_tree_manifest_v3.json").read_text())
    tree = runtime_tree["tree_content_sha256"]
    assert len(tree) == 64
    assert tree == authority.PYTHON_TREE_CONTENT_SHA256


def test_previous_bd316_bundle_is_explicitly_do_not_launch() -> None:
    invalidation = json.loads(INVALIDATION.read_text())
    assert invalidation["bundle_id"] == INVALIDATED_BUNDLE_ID
    assert invalidation["status"] == "INVALIDATED_DO_NOT_LAUNCH"
    assert invalidation["formal_launch_authorized"] is False
    assert invalidation["accepted_round_authorized"] is False
    manifest = json.loads((BUNDLE / "bundle_manifest.json").read_text())
    relative = "authority/mysql_job_v32_bd316145_invalidation_20260731.json"
    assert _sha256(INVALIDATION) == manifest["files"][relative]


def test_82e_python_tree_typo_bundle_is_explicitly_do_not_launch() -> None:
    invalidation = json.loads(CURRENT_INVALIDATION.read_text())
    assert invalidation["bundle_id"] == "82e2867d1f1081c6a391a728ebe4d5bc915959e7a4c8353389391006f62a2f52"
    assert invalidation["status"] == "INVALIDATED_DO_NOT_LAUNCH"
    assert invalidation["formal_launch_authorized"] is False
    assert invalidation["accepted_round_authorized"] is False
    manifest = json.loads((BUNDLE / "bundle_manifest.json").read_text())
    relative = "authority/mysql_job_v32_82e2867d_invalidation_20260731.json"
    assert _sha256(CURRENT_INVALIDATION) == manifest["files"][relative]


def test_4ee_client_managed_mount_bundle_is_explicitly_do_not_launch() -> None:
    invalidation = json.loads(FOUREE_INVALIDATION.read_text())
    assert invalidation["bundle_id"] == (
        "4ee58d435e830333b56f5a91876303a9f65d176a6ce0a760dbb51cd078112e7d"
    )
    assert invalidation["status"] == "INVALIDATED_DO_NOT_LAUNCH"
    assert invalidation["workload_started"] is False
    manifest = json.loads((BUNDLE / "bundle_manifest.json").read_text())
    relative = "authority/mysql_job_v32_4ee58d_client_managed_mount_invalidation_20260731.json"
    assert _sha256(FOUREE_INVALIDATION) == manifest["files"][relative]


def test_f1c705_client_anonymous_volume_bundle_is_explicitly_do_not_launch() -> None:
    invalidation = json.loads(F1C705_INVALIDATION.read_text())
    assert invalidation["bundle_id"] == (
        "f1c7051a70019ca6534a6687754349c6633e8f9ef1a43ac0a23564372207ccd6"
    )
    assert invalidation["workload_started"] is False
    manifest = json.loads((BUNDLE / "bundle_manifest.json").read_text())
    relative = "authority/mysql_job_v32_f1c705_client_anonymous_volume_invalidation_20260731.json"
    assert _sha256(F1C705_INVALIDATION) == manifest["files"][relative]


def test_2d237_permission_bundle_is_explicitly_do_not_launch() -> None:
    invalidation = json.loads(TARGET_PERMISSION_INVALIDATION.read_text())
    assert invalidation["bundle_id"] == "2d237b84b1fb699098214af959fc975bcd8cfdfb979e177f2f8602f0c1e9045a"
    assert invalidation["status"] == "INVALIDATED_DO_NOT_LAUNCH"
    assert invalidation["database_or_client_started"] is False
    manifest = json.loads((BUNDLE / "bundle_manifest.json").read_text())
    relative = "authority/mysql_job_v32_2d237b84_invalidation_20260731.json"
    assert _sha256(TARGET_PERMISSION_INVALIDATION) == manifest["files"][relative]


def test_4d5_target_closure_bundle_is_explicitly_do_not_launch() -> None:
    invalidation = json.loads(TARGET_CLOSURE_INVALIDATION.read_text())
    assert invalidation["bundle_id"] == (
        "4d5c50aea338b42d34b5e5adf84f12532a1a1b7059091eeb57d04df149c41cc8"
    )
    assert invalidation["status"] == "INVALIDATED_DO_NOT_LAUNCH"
    assert invalidation["failure_stage"] == (
        "post_physical_copy_pre_database_target_closure_audit"
    )
    assert invalidation["database_or_client_started"] is False
    assert invalidation["database_pid"] == 0
    assert invalidation["client_pid"] == 0
    manifest = json.loads((BUNDLE / "bundle_manifest.json").read_text())
    relative = "authority/mysql_job_v32_4d5c50_target_closure_invalidation_20260731.json"
    assert _sha256(TARGET_CLOSURE_INVALIDATION) == manifest["files"][relative]


def test_f058_sql_percent_bundle_and_attempt_are_explicitly_denied() -> None:
    invalidation = json.loads(F058_INVALIDATION.read_text())
    assert invalidation["bundle_id"] == (
        "f0583a934806dc2125ea402bf0e85e30b0e637a55e424d3d1ea1580875c3774c"
    )
    assert invalidation["bundle_manifest_sha256"] == (
        "2ca38c0e241cb22c5c0ec7deb3484ef0d0f18cb39473a41846a664a1a78c3846"
    )
    assert invalidation["failed_attempt_token"] == (
        "job-v2-20260730t195053z-07e47cc55f09d6a807e4eb07"
    )
    assert invalidation["status"] == "INVALIDATED_DO_NOT_LAUNCH"
    assert invalidation["accepted_rounds_credit"] == 0
    manifest = json.loads((BUNDLE / "bundle_manifest.json").read_text())
    relative = (
        "authority/"
        "mysql_job_v32_f0583a_sql_percent_invalidation_20260731.json"
    )
    assert _sha256(F058_INVALIDATION) == manifest["files"][relative]


def test_8f_client_credentials_bundle_and_attempt_are_explicitly_denied() -> None:
    invalidation = json.loads(EIGHTF_INVALIDATION.read_text())
    assert invalidation["bundle_id"] == (
        "8f55631aaea99b6851c460822fe5099b8762f04183f71f0fdc37aae7b0d173f6"
    )
    assert invalidation["failed_attempt_token"] == (
        "job-v2-20260730t201329z-6425f9a8eb0ec2662cf111ba"
    )
    assert invalidation["database_pid"] == 6088
    assert invalidation["client_credentials_readable"] is False
    assert invalidation["workload_started"] is False
    assert invalidation["formal_r0_started"] is False
    assert invalidation["accepted_rounds_credit"] == 0
    manifest = json.loads((BUNDLE / "bundle_manifest.json").read_text())
    relative = (
        "authority/"
        "mysql_job_v32_8f55631_client_credentials_invalidation_20260731.json"
    )
    assert _sha256(EIGHTF_INVALIDATION) == manifest["files"][relative]


def test_static_emit_plan_is_qualification_only_and_never_executes_commands(
    monkeypatch,
) -> None:
    authority = _load_authority(
        BUNDLE / "qualification/mysql_job_distributed_v2_authority.py",
        "job_v32_authority_from_sealed_bundle",
    )
    manifest_sha256 = _sha256(BUNDLE / "bundle_manifest.json")

    def _must_not_execute(*_args, **_kwargs):
        raise AssertionError("emit-plan must not execute a subprocess")

    monkeypatch.setattr(authority.subprocess, "run", _must_not_execute)
    assert authority.CONTRACT_PATH == (
        BUNDLE
        / "authority/mysql_algorithm_c_distributed_four_scene_successor_contract_v2_20260729.json"
    )
    assert authority.RECONCILIATION_PATH == (
        BUNDLE / "authority/formal_replay_authority_reconciliation_v1.json"
    )
    spec = _short_lived_spec(authority, manifest_sha256)
    plan = authority.command_plan(spec)

    assert plan["bundle_id"] == BUNDLE_ID
    assert plan["qualification_only"] is True
    assert plan["accepted_round_authorized"] is False
    assert plan["formal_scope"] == "mysql_only"
    assert plan["cross_engine_comparison_prohibited"] is True
    assert plan["postgresql_mutation_prohibited"] is True
    assert plan["database"]["argv"][0:2] == ["docker", "run"]
    assert "--cpu-quota" in plan["database"]["argv"]
    assert "115000" in plan["database"]["argv"]
    assert "18-20,54-56" in plan["database"]["argv"]
    assert "/dev/nvme1n1:7340032" in plan["database"]["argv"]
    assert "--read-only" in plan["client"]["argv"]
    assert ["--user", "1008:1008"] == plan["client"]["argv"][
        plan["client"]["argv"].index("--user"):
        plan["client"]["argv"].index("--user") + 2
    ]
    assert "/measurement:rw,nosuid,nodev,noexec,size=1073741824,uid=1008,gid=1008,mode=0700" in plan["client"]["argv"]
    assert "0-2,36-38" in plan["client"]["argv"]
    assert (
        "type=bind,src=%s/qualification/client_mysql_datadir_mask,"
        "dst=/var/lib/mysql,readonly" % spec["bundle"]["path"]
    ) in plan["client"]["argv"]
    assert plan["collect_only"]["argv"][0] == spec["python_runtime"]["executable_path"]
    assert plan["preflight"]["docker_socket_mount_authorized"] is False
    assert plan["preflight"]["host_mysql_cli_required"] is False


def test_live_client_preflight_requires_nonroot_identity_credential_read_and_tmpfs_write(
    monkeypatch,
) -> None:
    authority = _load_authority(
        BUNDLE / "qualification/mysql_job_distributed_v2_authority.py",
        "job_v32_client_access_preflight",
    )
    spec = _short_lived_spec(
        authority, _sha256(BUNDLE / "bundle_manifest.json")
    )
    client = spec["containers"]["client"]
    inspected = {
        "Id": "a" * 64,
        "Name": "/" + client,
        "Config": {"User": "1008:1008"},
        "Mounts": [],
        "State": {"Pid": 4321},
    }
    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "mysql  Ver 8.0", "")

    monkeypatch.setattr(authority, "_run", fake_run)
    result = authority._client_sql_bridge_preflight(spec, inspected)
    assert calls[0][:5] == ["docker", "exec", "--user", "1008:1008", client]
    assert "test -r " in calls[0][-1]
    assert "/measurement/.ra-client-preflight.$$" in calls[0][-1]
    assert calls[1][:5] == ["docker", "exec", "--user", "1008:1008", client]
    assert result["execution_uid_gid"] == [1008, 1008]
    assert result["credentials_readable"] is True
    assert result["measurement_writable"] is True


def test_every_sealed_qualification_module_imports_detached_from_checkout() -> None:
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": ""}
    entrypoints = sorted((BUNDLE / "qualification").glob("*.py")) + [
        BUNDLE / "qualification/shell/manual_mysql_sgd_r0_campaign.py"
    ]
    for entrypoint in entrypoints:
        completed = subprocess.run(
            [sys.executable, str(entrypoint), "--help"],
            cwd="/tmp",
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert completed.returncode == 0, (
            f"detached sealed entrypoint failed: {entrypoint.name}\n"
            f"{completed.stderr}"
        )


def test_legacy_tunnel_host_mysql_and_cp_paths_are_unreachable() -> None:
    adopted = (BUNDLE / "qualification/mysql_job_distributed_v2_adopted_services.py").read_text()
    monitor = (BUNDLE / "qualification/mysql_job_distributed_v2_rolling_monitor.py").read_text()
    assert "super().start_sampler()" not in adopted
    assert "MySQLCLIClient(" not in adopted
    assert "str(MYSQL)" not in adopted
    assert "def start_tunnel(self)" in adopted
    assert "self.tunnel = None" in adopted
    assert "exact_client_docker_exec_stdout" in adopted
    assert '"docker",' in adopted and '"exec",' in adopted
    assert "client-defaults-file" in adopted
    assert "client_defaults_file" in monitor
    assert '"/usr/bin/mysql"' in monitor
    assert "cp -a" not in adopted
    assert "--reflink=auto" not in adopted


def test_filled_short_lived_spec_passes_read_only_controller_validator(
    tmp_path: Path,
) -> None:
    validator_spec = importlib.util.spec_from_file_location(
        "job_v32_spec_validator", SPEC_VALIDATOR
    )
    assert validator_spec and validator_spec.loader
    validator = importlib.util.module_from_spec(validator_spec)
    validator_spec.loader.exec_module(validator)
    authority = _load_authority(
        BUNDLE / "qualification/mysql_job_distributed_v2_authority.py",
        "job_v32_authority_for_controller_validator",
    )
    document = _short_lived_spec(
        authority, _sha256(BUNDLE / "bundle_manifest.json")
    )
    path = tmp_path / "filled_bootstrap_spec.json"
    payload = json.dumps(document, sort_keys=True, indent=2) + "\n"
    path.write_text(payload, encoding="utf-8")
    path.with_name(path.name + ".sha256").write_text(
        hashlib.sha256(payload.encode()).hexdigest() + "  " + path.name + "\n",
        encoding="ascii",
    )
    result = validator.validate(BUNDLE, path)
    assert result["status"] == "PASS_STATIC_EMIT_PLAN_NOT_EXECUTED"
    assert result["bundle_id"] == BUNDLE_ID
    assert result["qualification_only"] is True
    assert result["accepted_round_authorized"] is False
    assert result["host_mysql_cli_required"] is False
    assert result["docker_socket_mount_authorized"] is False


def test_renderer_writes_a_short_lived_qualification_only_sidecar_spec(
    tmp_path: Path,
) -> None:
    output = tmp_path / "bootstrap_spec.json"
    token = tmp_path / "main_agent_token"
    token.write_text("offline-test-token\n", encoding="utf-8")
    token.chmod(0o600)
    completed = subprocess.run(
        [
            sys.executable, str(SPEC_RENDERER),
            "--bundle-manifest", str(BUNDLE / "bundle_manifest.json"),
            "--mysql-image-reference", "mysql@sha256:" + ("d" * 64),
            "--mysql-image-id", "sha256:" + ("e" * 64),
            "--client-image-reference", "client@sha256:" + ("f" * 64),
            "--client-image-id", "sha256:" + ("1" * 64),
            "--main-agent-token-file", str(token),
            "--attempt-token", "job-v2-20260731t000000z-rendered-test-0123456789ab",
            "--output", str(output),
        ],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    document = json.loads(output.read_text())
    runtime_tree = json.loads((BUNDLE / "runtime/python_runtime_tree_manifest_v3.json").read_text())
    assert len(document["python_runtime"]["tree_content_sha256"]) == 64
    assert document["python_runtime"]["tree_content_sha256"] == runtime_tree["tree_content_sha256"]
    assert document["python_runtime"]["executable_path"].endswith(
        runtime_tree["tree_content_sha256"] + "/bin/python3.11"
    )
    assert document["formal_r0_authorized"] is False
    assert document["formal_authorized"] is False
    assert document["accepted_round_authorized"] is False
    assert output.with_name(output.name + ".sha256").is_file()
