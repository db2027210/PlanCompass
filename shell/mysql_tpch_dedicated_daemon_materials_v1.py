#!/usr/bin/env python3
"""Seal short-lived, unexecuted TPC-H dedicated-daemon bootstrap materials."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys
from typing import Any


sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import render_mysql_tpch_dedicated_daemon_bootstrap_spec_v1 as renderer
import mysql_tpch_systemd_oneshot_materializer_v1 as oneshot


ROOT = HERE.parent
BUNDLE_ID = renderer.BUNDLE_ID
MANIFEST_SHA256 = renderer.MANIFEST_SHA256
DEFAULT_BUNDLE = renderer.DEFAULT_BUNDLE_MANIFEST.parent
DEFAULT_OUTPUT_ROOT = ROOT / "artifacts/mysql_tpch_dedicated_daemon_materials_v1"
MYSQL_REFERENCE = (
    "mysql@sha256:a3dff78d876222746a0bacc36dd7e4bf9e673c85fb7ee0d12ed25bd32c43c19b"
)
MYSQL_IMAGE_ID = (
    "sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
)
VALID_MINUTES = 60
SUPERSEDED_DE3E_INVALIDATION = (
    ROOT
    / "preflight/mysql_tpch_de3e_materials_expiry_invalidation_20260731.json"
)
SUPERSEDED_DE3E_RELATIVE = "superseded_de3e_materials_zero_credit.json"
INVALIDATED_313 = (
    ROOT / "preflight/mysql_tpch_313_nonselfcontained_invalidation_20260731.json"
)
INVALIDATED_313_RELATIVE = "invalidated_313_bundle_and_e0c_attempt_zero_credit.json"
SUPERSEDED_1496 = (
    ROOT / "preflight/mysql_tpch_1496_jit_supersession_invalidation_20260731.json"
)
SUPERSEDED_1496_RELATIVE = "superseded_1496_jit_materials_zero_credit.json"
INVALIDATED_ATTEMPTS = [
    "tpch-v2-20260731t061622z-c2010824144d4a9e25c642b3",
    "tpch-v2-20260731t062238z-ca0a603ef5ecbde72b954009",
    "tpch-v2-20260731t063136z-46d2e76f2b8909b303d1cc92",
    "tpch-v2-20260731t065258z-0e9809412d5763c036cf2733",
    "tpch-v2-20260731t070031z-de3e56b01a58576693636298",
    "tpch-v2-20260731t070344z-e0c1e4ee696815e5e0b030f3",
    "tpch-v2-20260731t071316z-1496247c71c015ce5d9df4fe",
]


class MaterialError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return renderer.canonical(value)


def sha256(path: Path) -> str:
    return renderer.sha256(path)


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise MaterialError("material file absent or symlinked: " + path.name)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise MaterialError("material JSON object required: " + path.name)
    return value


def write_new(path: Path, payload: bytes, mode: int = 0o444) -> None:
    sidecar = Path(str(path) + ".sha256")
    if any(item.exists() or item.is_symlink() for item in (path, sidecar)):
        raise MaterialError("refusing material overwrite: " + path.name)
    temporary = path.with_name("." + path.name + "." + secrets.token_hex(8))
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.rename(temporary, path)
    sidecar.write_text(
        hashlib.sha256(payload).hexdigest() + "  " + path.name + "\n",
        encoding="ascii",
    )
    os.chmod(path, mode)
    os.chmod(sidecar, mode)


def verify_materials(directory: Path) -> dict[str, Any]:
    directory = directory.resolve(strict=True)
    material = load(directory / "materials_manifest.json")
    if material.get("attempt_token") in INVALIDATED_ATTEMPTS:
        raise MaterialError("materials attempt is invalidated or superseded")
    spec = load(directory / "bootstrap_spec.json")
    plan = load(directory / "command_plan.json")
    superseded = load(directory / SUPERSEDED_DE3E_RELATIVE)
    invalidated_313 = load(directory / INVALIDATED_313_RELATIVE)
    superseded_1496 = load(directory / SUPERSEDED_1496_RELATIVE)
    files = material.get("files")
    if not isinstance(files, dict):
        raise MaterialError("material closure differs")
    for name, digest in files.items():
        path = directory / name
        sidecar = Path(str(path) + ".sha256")
        if (
            path.is_symlink()
            or not path.is_file()
            or sha256(path) != digest
            or sidecar.is_symlink()
            or not sidecar.is_file()
            or sidecar.read_text(encoding="ascii")
            != digest + "  " + path.name + "\n"
        ):
            raise MaterialError("material digest differs: " + name)
    token = directory / "main_agent_token"
    launch = plan.get("bootstrap_launch") or {}
    installation = plan.get("remote_authority_installation") or {}
    daemon_adoption = plan.get("dedicated_daemon_adoption") or {}
    argv = launch.get("argv") or []
    token_install = installation.get("main_agent_token") or {}
    audit_install = installation.get("readonly_audit_files") or {}
    if (
        material.get("status") != "SEALED_QUALIFICATION_ONLY_NOT_EXECUTED"
        or material.get("bundle_id") != BUNDLE_ID
        or material.get("bundle_manifest_sha256") != MANIFEST_SHA256
        or stat.S_IMODE(token.stat().st_mode) != 0o600
        or token.stat().st_uid != os.geteuid()
        or spec.get("attempt_token") != material.get("attempt_token")
        or spec.get("bundle", {}).get("bundle_id") != BUNDLE_ID
        or spec.get("dedicated_daemon", {}).get("socket")
        != "/run/ra-docker-mysql-tpch-v1.sock"
        or spec.get("bootstrap_execution", {}).get("unit")
        != launch.get("unit")
        or launch.get("mode") != "direct_host_systemd_transient_oneshot"
        or launch.get("service_type") != "oneshot"
        or launch.get("remain_after_exit") is not True
        or (launch.get("expected_running_identity") or {}).get("ppid") != 1
        or launch.get("token_transport") != "token_file"
        or "--main-agent-token-file" not in argv
        or "--main-agent-token" in argv
        or "--wait" in argv
        or "--collect" in argv
        or token.read_text(encoding="ascii").strip() in argv
        or (launch.get("controller_poll") or {}).get(
            "unit_retained_for_audit"
        ) is not True
        or token_install.get("owner") != "root:root"
        or token_install.get("uid") != 0
        or token_install.get("gid") != 0
        or token_install.get("mode") != "0600"
        or token_install.get("source_sha256") != sha256(token)
        or token_install.get("remote_staging_source_expected_uid") != 600
        or token_install.get("staging_owner_is_not_execution_authority")
        is not True
        or token_install.get("remote_sha256_must_equal_source") is not True
        or token_install.get("install_before_systemd_run") is not True
        or audit_install.get("owner") != "root:root"
        or audit_install.get("mode") != "0444"
        or audit_install.get("immutable_during_attempt") is not True
        or plan.get("invalidated_attempts_zero_credit")
        != INVALIDATED_ATTEMPTS
        or daemon_adoption.get("mode")
        != "adopt_exact_live_transient_unit"
        or daemon_adoption.get("unit_fragment")
        != "/run/systemd/system/ra-docker-mysql-tpch-v1.service"
        or daemon_adoption.get("transient") is not True
        or daemon_adoption.get("static_unit_deployment_prohibited")
        is not True
        or daemon_adoption.get("live_unit_mutation_prohibited") is not True
        or superseded.get("status") != "INVALIDATED_DO_NOT_RUN_OR_CREDIT"
        or superseded.get("attempt_token")
        != "tpch-v2-20260731t070031z-de3e56b01a58576693636298"
        or superseded.get("accepted_rounds_credit") != 0
        or superseded.get("replay_or_rerun_authorized") is not False
        or invalidated_313.get("status")
        != "INVALIDATED_DO_NOT_RUN_OR_CREDIT"
        or invalidated_313.get("bundle_id")
        != "313637860bee5f333c11d8ae92689ef674585d8e7311c82b75c1cb7cc1fd9555"
        or invalidated_313.get("affected_attempts")
        != ["tpch-v2-20260731t070344z-e0c1e4ee696815e5e0b030f3"]
        or invalidated_313.get("accepted_rounds_credit") != 0
        or invalidated_313.get("replay_or_rerun_authorized") is not False
        or superseded_1496.get("status")
        != "INVALIDATED_DO_NOT_RUN_OR_CREDIT"
        or superseded_1496.get("attempt_token")
        != "tpch-v2-20260731t071316z-1496247c71c015ce5d9df4fe"
        or superseded_1496.get("materials_manifest_sha256")
        != "14539bd2d2b4aa8ca8f360d4c69fded6a1d70ec629f061847c7f2439196b63fc"
        or superseded_1496.get("accepted_rounds_credit") != 0
        or superseded_1496.get("replay_or_rerun_authorized") is not False
        or (plan.get("invalidated_313_bundle_and_e0c_attempt") or {}).get(
            "sha256"
        ) != sha256(directory / INVALIDATED_313_RELATIVE)
        or (plan.get("invalidated_313_bundle_and_e0c_attempt") or {}).get(
            "credit"
        ) != 0
        or (plan.get("invalidated_313_bundle_and_e0c_attempt") or {}).get(
            "reuse_prohibited"
        ) is not True
        or (plan.get("superseded_1496_jit_materials") or {}).get("sha256")
        != sha256(directory / SUPERSEDED_1496_RELATIVE)
        or (plan.get("superseded_1496_jit_materials") or {}).get("credit") != 0
        or (plan.get("superseded_1496_jit_materials") or {}).get(
            "reuse_prohibited"
        ) is not True
        or plan.get("unique_final_materials") is not True
        or plan.get("target_host") != {
            "hostname": "node04",
            "address": "192.0.2.7",
        }
        or (plan.get("frozen_fresh_r0_envelope") or {}).get("cpuset_cpus")
        != "18-20,54-56"
        or (plan.get("frozen_fresh_r0_envelope") or {}).get(
            "io_read_bps"
        ) != 100663296
        or (plan.get("frozen_fresh_r0_envelope") or {}).get(
            "io_write_bps"
        ) != 100663296
        or (plan.get("database_counter_timeout_gate") or {}).get(
            "fail_closed"
        ) is not True
        or (plan.get("database_counter_timeout_gate") or {}).get(
            "required_timeout_count"
        ) != 0
        or (plan.get("database_counter_timeout_gate") or {}).get(
            "required_missing_sample_count"
        ) != 0
        or plan.get("qualification_only") is not True
        or plan.get("formal_r0_authorized") is not False
        or plan.get("accepted_round_authorized") is not False
    ):
        raise MaterialError("material authority boundary differs")
    expires = datetime.fromisoformat(
        material["expires_at_utc"].replace("Z", "+00:00")
    )
    if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
        raise MaterialError("materials expired")
    return {
        "status": "PASS_VERIFIED_UNEXPIRED_NOT_EXECUTED",
        "bundle_id": BUNDLE_ID,
        "attempt_token": material["attempt_token"],
        "materials_directory": str(directory),
        "expires_at_utc": material["expires_at_utc"],
        "valid_minutes": material["valid_minutes"],
        "materials_manifest_sha256": sha256(
            directory / "materials_manifest.json"
        ),
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


def create(bundle: Path, output_root: Path) -> dict[str, Any]:
    bundle = bundle.resolve(strict=True)
    if bundle != DEFAULT_BUNDLE.resolve(strict=True):
        raise MaterialError("generator bound only to dedicated successor")
    renderer.verify_successor(bundle / "bundle_manifest.json")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    attempt = (
        "tpch-v2-" + now.strftime("%Y%m%dt%H%M%Sz").lower()
        + "-" + secrets.token_hex(12)
    )
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    final = output_root / attempt
    stage = output_root / ("." + attempt + "." + secrets.token_hex(8))
    if final.exists() or final.is_symlink():
        raise MaterialError("attempt materials already exist")
    stage.mkdir(mode=0o700)
    try:
        token = stage / "main_agent_token"
        descriptor = os.open(token, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(
                descriptor,
                ("tpch-main-agent-" + secrets.token_urlsafe(36) + "\n").encode(
                    "ascii"
                ),
            )
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        spec_path = stage / "bootstrap_spec.json"
        spec = renderer.render(argparse.Namespace(
            bundle_manifest=bundle / "bundle_manifest.json",
            mysql_image_reference=MYSQL_REFERENCE,
            mysql_image_id=MYSQL_IMAGE_ID,
            client_image_reference=MYSQL_REFERENCE,
            client_image_id=MYSQL_IMAGE_ID,
            main_agent_token_file=token,
            attempt_token=attempt,
            valid_minutes=VALID_MINUTES,
            output=spec_path,
        ))
        write_new(spec_path, canonical(spec))
        remote_material_root = spec["paths"]["attempt_root"] + "/authority"
        token_remote = remote_material_root + "/main_agent_token"
        spec_remote = remote_material_root + "/bootstrap_spec.json"
        output_remote = remote_material_root + "/bootstrap_evidence.json"
        fixture_path = bundle / renderer.SYSTEMD_HELP_FIXTURE
        sealed_materializer = bundle / renderer.SYSTEMD_MATERIALIZER
        if sha256(sealed_materializer) != sha256(
            HERE / "mysql_tpch_systemd_oneshot_materializer_v1.py"
        ):
            raise MaterialError("local/sealed systemd materializer differs")
        launch = oneshot.materialize(
            spec,
            bundle_path=spec["bundle"]["path"],
            remote_spec=spec_remote,
            remote_token_file=token_remote,
            remote_output=output_remote,
            help_fixture=load(fixture_path),
        )
        superseded_path = stage / SUPERSEDED_DE3E_RELATIVE
        write_new(
            superseded_path, SUPERSEDED_DE3E_INVALIDATION.read_bytes()
        )
        invalidated_313_path = stage / INVALIDATED_313_RELATIVE
        write_new(invalidated_313_path, INVALIDATED_313.read_bytes())
        superseded_1496_path = stage / SUPERSEDED_1496_RELATIVE
        write_new(superseded_1496_path, SUPERSEDED_1496.read_bytes())
        token_digest = sha256(token)
        remote_installation = {
            "schema_version":
                "mysql_tpch_remote_authority_installation_gate_v1",
            "status": "REQUIRED_BEFORE_SYSTEMD_RUN_NOT_EXECUTED",
            "remote_authority_root": remote_material_root,
            "main_agent_token": {
                "source_name": "main_agent_token",
                "remote_staging_source_expected_owner": "anonymous",
                "remote_staging_source_expected_uid": 600,
                "staging_owner_is_not_execution_authority": True,
                "target": token_remote,
                "owner": "root:root",
                "uid": 0,
                "gid": 0,
                "mode": "0600",
                "source_sha256": token_digest,
                "remote_sha256_must_equal_source": True,
                "regular_file_required": True,
                "symlink_prohibited": True,
                "install_before_systemd_run": True,
                "token_content_in_os_argv_prohibited": True,
            },
            "readonly_audit_files": {
                "source_names": [
                    "bootstrap_spec.json",
                    "bootstrap_spec.json.sha256",
                    "command_plan.json",
                    "command_plan.json.sha256",
                    "materials_manifest.json",
                    "materials_manifest.json.sha256",
                    "main_agent_token.sha256",
                ],
                "owner": "root:root",
                "uid": 0,
                "gid": 0,
                "mode": "0444",
                "regular_files_required": True,
                "symlinks_prohibited": True,
                "immutable_during_attempt": True,
            },
            "post_install_readback": {
                "required_fields": [
                    "path", "uid", "gid", "mode", "sha256", "file_type",
                ],
                "token_expected": {
                    "uid": 0,
                    "gid": 0,
                    "mode": "0600",
                    "sha256": token_digest,
                    "file_type": "regular",
                },
                "fail_closed": True,
            },
            "systemd_run_before_pass_prohibited": True,
            "qualification_only": True,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
        }
        plan = {
            "schema_version": "mysql_tpch_dedicated_daemon_plan_v1",
            "status": "SEALED_PLAN_NOT_EXECUTED",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "postgresql_mutation_prohibited": True,
            "bundle_id": BUNDLE_ID,
            "attempt_token": attempt,
            "unique_final_materials": True,
            "target_host": {
                "hostname": "node04",
                "address": "192.0.2.7",
            },
            "scenario": "tpch_memory",
            "dedicated_daemon_adoption": {
                "mode": "adopt_exact_live_transient_unit",
                "unit": spec["dedicated_daemon"]["unit"],
                "unit_fragment":
                    spec["dedicated_daemon"]["unit_fragment"],
                "transient": spec["dedicated_daemon"]["transient"],
                "main_pid": spec["dedicated_daemon"]["main_pid"],
                "main_pid_starttime_ticks":
                    spec["dedicated_daemon"]["main_pid_starttime_ticks"],
                "main_pid_cpus_allowed_list":
                    spec["dedicated_daemon"]["main_pid_cpus_allowed_list"],
                "control_group":
                    spec["dedicated_daemon"]["control_group"],
                "cpu_shares": spec["dedicated_daemon"]["cpu_shares"],
                "memory_limit_bytes":
                    spec["dedicated_daemon"]["memory_limit_bytes"],
                "tasks_max": spec["dedicated_daemon"]["tasks_max"],
                "systemctl_fixture": renderer.LIVE_DAEMON_SYSTEMCTL_FIXTURE,
                "proc_fixture": renderer.LIVE_DAEMON_PROC_FIXTURE,
                "static_unit_deployment_prohibited": True,
                "live_unit_mutation_prohibited": True,
                "postgresql_mutation_prohibited": True,
            },
            "bootstrap_launch": launch,
            "remote_authority_installation": remote_installation,
            "frozen_fresh_r0_envelope": {
                "cpu_quota_cores": 4,
                "cpuset_cpus": "18-20,54-56",
                "cpuset_mems": "1",
                "io_read_bps": 100663296,
                "io_write_bps": 100663296,
                "device": "/dev/nvme0n1",
                "device_major_minor": "259:1",
                "mount": "/mnt/nvme0n1",
                "dedicated_ssd": True,
                "database_network_mode": "host",
                "client_network_mode": "host",
                "mysqld_port": 65103,
            },
            "database_counter_timeout_gate": {
                "sample_timeout_seconds": 3,
                "required_timeout_count": 0,
                "required_missing_sample_count": 0,
                "all_required_counter_names_must_be_present": True,
                "fail_closed": True,
                "r0_credit_on_failure": 0,
                "accepted_rounds_credit_on_failure": 0,
            },
            "readiness_failure_evidence_before_cleanup_required": True,
            "superseded_de3e_materials": {
                "path": SUPERSEDED_DE3E_RELATIVE,
                "sha256": sha256(superseded_path),
                "credit": 0,
                "reuse_prohibited": True,
            },
            "invalidated_313_bundle_and_e0c_attempt": {
                "path": INVALIDATED_313_RELATIVE,
                "sha256": sha256(invalidated_313_path),
                "credit": 0,
                "reuse_prohibited": True,
            },
            "superseded_1496_jit_materials": {
                "path": SUPERSEDED_1496_RELATIVE,
                "sha256": sha256(superseded_1496_path),
                "credit": 0,
                "reuse_prohibited": True,
            },
            "invalidated_attempts_zero_credit": INVALIDATED_ATTEMPTS,
            "attempt_or_unit_reuse_prohibited": True,
            "old_attempts_and_bundles_credit": 0,
            "qualification_only": True,
            "formal_r0_authorized": False,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
        }
        plan_path = stage / "command_plan.json"
        write_new(plan_path, canonical(plan))
        files = {
            "bootstrap_spec.json": sha256(spec_path),
            "command_plan.json": sha256(plan_path),
            "main_agent_token": sha256(token),
            SUPERSEDED_DE3E_RELATIVE: sha256(superseded_path),
            INVALIDATED_313_RELATIVE: sha256(invalidated_313_path),
            SUPERSEDED_1496_RELATIVE: sha256(superseded_1496_path),
        }
        token_sidecar = Path(str(token) + ".sha256")
        token_sidecar.write_text(
            files["main_agent_token"] + "  main_agent_token\n",
            encoding="ascii",
        )
        os.chmod(token_sidecar, 0o444)
        material = {
            "schema_version": "mysql_tpch_dedicated_daemon_materials_v1",
            "status": "SEALED_QUALIFICATION_ONLY_NOT_EXECUTED",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "postgresql_mutation_prohibited": True,
            "bundle_id": BUNDLE_ID,
            "bundle_manifest_sha256": MANIFEST_SHA256,
            "attempt_token": attempt,
            "created_at_utc": spec["created_at_utc"],
            "expires_at_utc": spec["expires_at_utc"],
            "valid_minutes": VALID_MINUTES,
            "files": files,
            "main_agent_token_mode": "0600",
            "token_in_os_argv_prohibited": True,
            "unique_final_materials": True,
            "qualification_only": True,
            "formal_r0_authorized": False,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
        }
        write_new(stage / "materials_manifest.json", canonical(material))
        verify_materials(stage)
        os.rename(stage, final)
        return verify_materials(final)
    except BaseException:
        if stage.exists():
            for path in stage.rglob("*"):
                if path.is_file():
                    os.chmod(path, 0o600)
            for path in sorted(stage.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            stage.rmdir()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--create-fresh", action="store_true")
    mode.add_argument("--verify-materials", type=Path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--expected-bundle-id")
    args = parser.parse_args()
    if args.create_fresh:
        if (
            args.bundle is None
            or args.output_root is None
            or args.expected_bundle_id != BUNDLE_ID
        ):
            parser.error(
                "--create-fresh requires explicit --bundle, --output-root, "
                "and the exact --expected-bundle-id"
            )
        result = create(args.bundle, args.output_root)
    else:
        if (
            args.bundle is not None
            or args.output_root is not None
            or args.expected_bundle_id is not None
        ):
            parser.error(
                "--verify-materials accepts only the exact materials directory"
            )
        result = verify_materials(args.verify_materials)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (MaterialError, renderer.RenderError, OSError, ValueError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
