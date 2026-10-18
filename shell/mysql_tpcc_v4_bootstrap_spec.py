#!/usr/bin/env python3
"""Render and statically emit a qualification-only TPC-C v6 handoff.

This controller-side helper is deliberately incapable of contacting node04
or invoking Docker.  It binds a short-lived attempt to the immutable v6
bundle and produces only argv lists which the main agent may later review and
run *on node04*.  The sealed service requires the handoff token to equal the
attempt ID, so the spec records that public identifier but never adds a second
raw-token field; the executable plan refers only to the remote 0600 file.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import sys
from typing import Any, Mapping


BUNDLE_ID = "2185b4b072121e9ab8d6bb6ed4ee0da0e9ebde955ca6a92e5a20f416287597fe"
HOST = "node04"
RUNTIME_ROOT = (
    "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/"
    "runtime_algorithm_c_successor_20260729"
)
BUNDLE_REMOTE = RUNTIME_ROOT + "/bundles/" + BUNDLE_ID
SERVICE_REMOTE = BUNDLE_REMOTE + "/authority/mysql_tpcc_node04_adopted_service_v1.py"
MATERIALIZER_REMOTE = BUNDLE_REMOTE + "/authority/mysql_tpcc_materialize_phase_command_v2.py"
CONFIG_MATERIALIZER_REMOTE = BUNDLE_REMOTE + "/authority/mysql_tpcc_config_materialize_v1.py"
PYTHON_TREE = "3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89"
PYTHON_MANIFEST_SHA256 = "b38aafcaa706300d90d029200d7f24e62d5480d53a72fe88082b72bd7a3441a5"
HOST_PYTHON = RUNTIME_ROOT + "/runtime/python311-" + PYTHON_TREE + "/bin/python3.11"
IMAGE_ID = "sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
IMAGE_REFERENCE = "mysql:8.0"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
TOKEN = re.compile(r"^tpcc-v4-[A-Za-z0-9_.:-]{16,112}$")


class SpecError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".%s.%s.tmp" % (path.name, secrets.token_hex(8)))
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def verify_local_bundle(manifest_path: Path) -> str:
    manifest_path = manifest_path.resolve(strict=True)
    bundle = manifest_path.parent
    if bundle.name != BUNDLE_ID or manifest_path.name != "bundle_manifest.json":
        raise SpecError("local TPC-C v4 bundle path differs")
    if bundle.is_symlink() or manifest_path.is_symlink():
        raise SpecError("local TPC-C v4 bundle must not be symlinked")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    if (
        bundle_id != BUNDLE_ID
        or hashlib.sha256(canonical(unsigned)).hexdigest() != BUNDLE_ID
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise SpecError("local TPC-C v4 bundle identity/scope differs")
    for relative, expected in (manifest.get("files") or {}).items():
        candidate = bundle / str(relative)
        if candidate.is_symlink() or not candidate.is_file() or sha256(candidate) != expected:
            raise SpecError("local TPC-C v4 bundle closure differs: " + str(relative))
    return sha256(manifest_path)


def _under(value: str, root: str, label: str) -> None:
    path, parent = PurePosixPath(value), PurePosixPath(root)
    if not value.startswith("/") or ".." in path.parts or path == parent or parent not in path.parents:
        raise SpecError(label + " escapes attempt root")


def _parse_time(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SpecError("invalid timestamp") from exc
    if result.tzinfo is None:
        raise SpecError("timestamp must include timezone")
    return result.astimezone(timezone.utc)


def validate(spec: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    expected = {
        "schema_version", "formal_scope", "cross_engine_comparison_prohibited",
        "postgresql_mutation_prohibited", "scenario", "host", "bundle",
        "attempt", "images", "paths", "created_at_utc", "expires_at_utc",
        "runtime_python", "qualification_only", "capture_authorized", "formal_r0_authorized",
        "formal_authorized", "accepted_round_authorized",
    }
    if not isinstance(spec, Mapping) or set(spec) != expected:
        raise SpecError("TPC-C v4 bootstrap spec key set differs")
    if (
        spec["schema_version"] != "mysql_tpcc_v4_short_lived_bootstrap_spec_v1"
        or spec["formal_scope"] != "mysql_only"
        or spec["cross_engine_comparison_prohibited"] is not True
        or spec["postgresql_mutation_prohibited"] is not True
        or spec["scenario"] != "tpcc_cpu"
        or spec["host"] != HOST
        or spec["qualification_only"] is not True
        or spec["capture_authorized"] is not False
        or spec["formal_r0_authorized"] is not False
        or spec["formal_authorized"] is not False
        or spec["accepted_round_authorized"] is not False
    ):
        raise SpecError("TPC-C v4 scope/authority boundary differs")
    bundle = spec["bundle"]
    if bundle != {
        "id": BUNDLE_ID,
        "remote_path": BUNDLE_REMOTE,
        "manifest_sha256": bundle.get("manifest_sha256") if isinstance(bundle, Mapping) else None,
    } or not HEX64.fullmatch(str(bundle.get("manifest_sha256") or "")):
        raise SpecError("TPC-C v4 bundle binding differs")
    attempt = spec["attempt"]
    if not isinstance(attempt, Mapping) or set(attempt) != {"id", "handoff_token_sha256", "remote_token_file", "mysql_password_file"}:
        raise SpecError("TPC-C v4 attempt binding differs")
    attempt_id = str(attempt["id"])
    if TOKEN.fullmatch(attempt_id) is None or not HEX64.fullmatch(str(attempt["handoff_token_sha256"])):
        raise SpecError("TPC-C v4 attempt token differs")
    root = RUNTIME_ROOT + "/" + attempt_id
    if attempt["remote_token_file"] != root + "/handoff-token":
        raise SpecError("TPC-C v4 remote token file differs")
    if attempt["mysql_password_file"] != root + "/mysql-password":
        raise SpecError("TPC-C v10 remote password file differs")
    images = spec["images"]
    if not isinstance(images, Mapping) or set(images) != {"database", "client"}:
        raise SpecError("TPC-C v4 image binding differs")
    for role in ("database", "client"):
        value = images[role]
        if not isinstance(value, Mapping) or value != {"reference": IMAGE_REFERENCE, "image_id": IMAGE_ID}:
            raise SpecError("TPC-C v4 exact image binding differs: " + role)
    paths = spec["paths"]
    expected_paths = {
        "attempt_root": root,
        "config_dir": root + "/config",
        "datadir": root + "/datadir",
        "measurement_bridge": root + "/measurement",
        "physical_copy_authority": root + "/physical_copy_authority.json",
        "service_plan": root + "/bootstrap_plan.json",
        "adoption": root + "/adopted_service.json",
        "prefault": root + "/prefault_zero_io.json",
        "qualification_command": root + "/qualification_command.json",
        "typed_capture_command": root + "/typed_capture_command.json",
        "config_materialization": root + "/config_materialization.json",
        "ledger": root + "/same_live_handoff.json",
    }
    if paths != expected_paths:
        raise SpecError("TPC-C v4 attempt-owned paths differ")
    for label, value in paths.items():
        if label != "attempt_root":
            _under(str(value), root, "path " + label)
    runtime_python = spec["runtime_python"]
    if runtime_python != {
        "root": HOST_PYTHON.rsplit("/bin/python3.11", 1)[0],
        "executable": HOST_PYTHON,
        "tree_content_sha256": PYTHON_TREE,
        "manifest_relative": "toolchain/python-3.11.11-tree-manifest.json",
        "manifest_sha256": PYTHON_MANIFEST_SHA256,
    }:
        raise SpecError("TPC-C v7 content-addressed Python binding differs")
    if runtime_python["root"].startswith("/mnt/nvme3n1/"):
        raise SpecError("TPC-C bootstrap Python must not use the root-filesystem alias")
    created, expires = _parse_time(str(spec["created_at_utc"])), _parse_time(str(spec["expires_at_utc"]))
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if expires <= created or (expires - created).total_seconds() > 1800 or now >= expires:
        raise SpecError("TPC-C v4 spec is stale or validity exceeds 30 minutes")
    return dict(spec)


def make_spec(manifest_path: Path, attempt_id: str, token: str, *, now: datetime | None = None) -> dict[str, Any]:
    manifest_sha = verify_local_bundle(manifest_path)
    if attempt_id != token or TOKEN.fullmatch(attempt_id) is None:
        raise SpecError("attempt ID and handoff token must be identical safe TPC-C v4 tokens")
    now = now or datetime.now(timezone.utc)
    root = RUNTIME_ROOT + "/" + attempt_id
    value = {
        "schema_version": "mysql_tpcc_v4_short_lived_bootstrap_spec_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "scenario": "tpcc_cpu",
        "host": HOST,
        "bundle": {"id": BUNDLE_ID, "remote_path": BUNDLE_REMOTE, "manifest_sha256": manifest_sha},
        "attempt": {
            "id": attempt_id,
            "handoff_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "remote_token_file": root + "/handoff-token",
            "mysql_password_file": root + "/mysql-password",
        },
        "images": {
            "database": {"reference": IMAGE_REFERENCE, "image_id": IMAGE_ID},
            "client": {"reference": IMAGE_REFERENCE, "image_id": IMAGE_ID},
        },
        "runtime_python": {
            "root": HOST_PYTHON.rsplit("/bin/python3.11", 1)[0],
            "executable": HOST_PYTHON,
            "tree_content_sha256": PYTHON_TREE,
            "manifest_relative": "toolchain/python-3.11.11-tree-manifest.json",
            "manifest_sha256": PYTHON_MANIFEST_SHA256,
        },
        "paths": {
            "attempt_root": root, "config_dir": root + "/config", "datadir": root + "/datadir",
            "measurement_bridge": root + "/measurement",
            "physical_copy_authority": root + "/physical_copy_authority.json",
            "service_plan": root + "/bootstrap_plan.json", "adoption": root + "/adopted_service.json",
            "prefault": root + "/prefault_zero_io.json", "qualification_command": root + "/qualification_command.json",
            "typed_capture_command": root + "/typed_capture_command.json",
            "config_materialization": root + "/config_materialization.json",
            "ledger": root + "/same_live_handoff.json",
        },
        "created_at_utc": now.astimezone(timezone.utc).isoformat(),
        "expires_at_utc": (now.astimezone(timezone.utc) + timedelta(minutes=30)).isoformat(),
        "qualification_only": True, "capture_authorized": False, "formal_r0_authorized": False,
        "formal_authorized": False, "accepted_round_authorized": False,
    }
    return validate(value, now=now)


def command_plan(spec: Mapping[str, Any]) -> dict[str, Any]:
    spec = validate(spec)
    paths, attempt = spec["paths"], spec["attempt"]
    base = [HOST_PYTHON, SERVICE_REMOTE]
    plan_args = [
        "--bundle", BUNDLE_REMOTE, "--image-id", IMAGE_ID,
        "--config-dir", paths["config_dir"], "--datadir", paths["datadir"],
        "--measurement-bridge", paths["measurement_bridge"], "--attempt-id", attempt["id"],
        "--handoff-token-file", attempt["remote_token_file"], "--output", paths["service_plan"],
    ]
    materializer = [HOST_PYTHON, MATERIALIZER_REMOTE, "materialize", "--bundle", BUNDLE_REMOTE,
                    "--service", paths["adoption"], "--token-file", attempt["remote_token_file"],
                    "--config-in-container", "/opt/mysql-tpcc-config/tpcc.xml",
                    "--mysql-defaults-in-container", "/opt/mysql-tpcc-config/mysql.cnf",
                    "--prefault-authority", paths["prefault"]]
    config_materializer = [
        HOST_PYTHON, CONFIG_MATERIALIZER_REMOTE, "materialize", "--bundle", BUNDLE_REMOTE,
        "--attempt-id", attempt["id"], "--config-dir", paths["config_dir"],
        "--mysql-password-file", attempt["mysql_password_file"], "--output", paths["config_materialization"],
    ]
    return {
        "schema_version": "mysql_tpcc_v4_static_emit_plan_v1",
        "status": "PLAN_NOT_EXECUTED_NOT_AUTHORITY",
        "host": HOST, "ssh_prohibited": True, "remote_shell_wrapper_prohibited": True,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True, "bundle_id": BUNDLE_ID,
        "attempt_id": attempt["id"], "handoff_token_sha256": attempt["handoff_token_sha256"],
        "qualification_only": True, "capture_authorized": False, "formal_r0_authorized": False,
        "formal_authorized": False, "accepted_round_authorized": False,
        "required_preconditions": [
            "main_agent_only_remote_execution", "remote_bundle_exact_closure_pass",
            "token_file_exists_regular_mode_0600_and_equals_attempt_id",
            "mysql_password_file_exists_regular_mode_0600_external_to_bundle",
            "config_materialization_PASS_with_regular_tpcc.xml_and_mysql.cnf_mode_0600",
            "physical_copy_authority_PASS_before_bootstrap",
            "measurement_bridge_is_own_tmpfs_mount_before_bootstrap",
            "client_/usr/bin/mysql_--version_passes_after_bootstrap",
            "prefault_zero_target_device_io_PASS_before_any_phase",
            "qualification_gate_PASS_before_typed_capture",
        ],
        "commands_to_run_on_node04": {
            "prepare_physical_copy": base + ["prepare-copy", "--attempt-id", attempt["id"], "--output", paths["physical_copy_authority"]],
            "materialize_attempt_config_before_service_plan": config_materializer,
            "emit_service_plan_only": base + ["plan", *plan_args],
            "bootstrap_after_main_agent_authorization": base + ["bootstrap", "--plan", paths["service_plan"], "--output", paths["adoption"]],
            "adopt_readback": base + ["adopt", "--plan", paths["service_plan"], "--output", paths["adoption"]],
            "prefault_zero_io_gate": base + ["prefault", "--plan", paths["service_plan"], "--output", paths["prefault"]],
            "bridge_readback": ["findmnt", "--noheadings", "--output", "FSTYPE,TARGET", "--target", paths["measurement_bridge"]],
            "client_mysql_cli_readback_template": ["docker", "exec", "CLIENT_NAME_FROM_ADOPTION", "/usr/bin/mysql", "--version"],
            "materialize_qualification_only": materializer + ["--phase", "qualification", "--output", paths["qualification_command"]],
            "materialize_typed_capture_after_qualification_gate_only": materializer + ["--phase", "typed_capture", "--output", paths["typed_capture_command"]],
        },
        "client_mysql_cli_contract": {
            "binary": "/usr/bin/mysql", "host_mysql_cli_prohibited": True,
            "docker_socket_mount_prohibited": True, "target_datadir_mount_prohibited": True,
            "counter_transport": "exact_client_container_docker_exec_stdout_v2",
        },
        "physical_copy_authority": {
            "schema_version": "mysql_tpcc_physical_copy_authority_v1",
            "required_status": "PASS", "full_physical_copy": True,
            "overlay_used": False, "reflink_used": False, "device_major_minor": "259:2",
        },
        "measurement_bridge_contract": {"filesystem": "tmpfs", "private_attempt_directory": True, "size_bytes": 2147483648},
        "host_python_runtime": spec["runtime_python"],
    }


def load_sidecar(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    sidecar = path.with_name(path.name + ".sha256")
    expected = hashlib.sha256(payload).hexdigest() + "  " + path.name + "\n"
    if sidecar.is_symlink() or not sidecar.is_file() or sidecar.read_text(encoding="ascii") != expected:
        raise SpecError("bootstrap spec SHA-256 sidecar differs")
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise SpecError("bootstrap spec must be a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--render", action="store_true")
    modes.add_argument("--emit-plan", type=Path)
    parser.add_argument("--bundle-manifest", type=Path)
    parser.add_argument("--attempt-id")
    parser.add_argument("--main-agent-token-file", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.render:
        if not all((args.bundle_manifest, args.attempt_id, args.main_agent_token_file, args.output)):
            raise SpecError("--render requires bundle manifest, attempt, token file, and output")
        token_path = args.main_agent_token_file.resolve(strict=True)
        if token_path.is_symlink() or stat.S_IMODE(token_path.stat().st_mode) & 0o077:
            raise SpecError("main-agent token file is symlinked or too permissive")
        token = token_path.read_text(encoding="utf-8").strip()
        value = make_spec(args.bundle_manifest, args.attempt_id, token)
        payload = canonical(value)
        atomic(args.output, payload)
        atomic(args.output.with_name(args.output.name + ".sha256"), hashlib.sha256(payload).hexdigest().encode() + b"  " + args.output.name.encode() + b"\n")
        print(json.dumps({"status": "PASS_RENDERED_QUALIFICATION_ONLY_SPEC", "bundle_id": BUNDLE_ID, "accepted_round_authorized": False}, sort_keys=True))
    else:
        value = command_plan(load_sidecar(args.emit_plan))
        print(json.dumps(value, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, SpecError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
