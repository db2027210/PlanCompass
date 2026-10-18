#!/usr/bin/env python3
"""Materialize and execute fail-closed TPC-C adopted-service phase commands.

The five phases share one attempt/handoff token and one live DB identity.
Materialization never grants accepted-round credit. Qualification and capture
are explicitly non-formal; R0/debug require their own sealed phase authority;
formal10 additionally requires a READY audit produced by the sealed readiness
verifier.  Execution re-adopts the service before and after the workload.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Mapping, Sequence


PHASES = ("qualification", "typed_capture", "fresh_r0", "debug", "formal10_launch")
JAVA = "/opt/mysql-tpcc-jdk/bin/java"
PYTHON = "/opt/mysql-tpcc-python/bin/python3.11"
JDK_TREE_SHA256 = (
    "578395e883d31c692a1c01c37910d22dbdafbcf95e373c7e0f6776a26f5c844c"
)
PYTHON_TREE_SHA256 = (
    "3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89"
)
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
PLACEHOLDER_RE = re.compile(r"(?:REPLACE_WITH|OMIT_FOR_|COPY_EXACTLY|\$\{)")


class PhaseError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise PhaseError(f"regular JSON file required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PhaseError(f"JSON object required: {path}")
    sidecar = path.with_name(path.name + ".sha256")
    if sidecar.exists():
        expected = f"{sha256(path)}  {path.name}\n"
        if sidecar.is_symlink() or sidecar.read_text(encoding="ascii") != expected:
            raise PhaseError(f"SHA-256 sidecar differs: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    sidecar = path.with_name(path.name + ".sha256")
    sidecar_temporary = sidecar.with_name(f".{sidecar.name}.{os.getpid()}.tmp")
    payload = canonical(value)
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        digest = sha256(temporary)
        with sidecar_temporary.open("xb") as stream:
            stream.write(f"{digest}  {path.name}\n".encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.replace(sidecar_temporary, sidecar)
    finally:
        for candidate in (temporary, sidecar_temporary):
            if candidate.exists():
                candidate.unlink()


def _service_module(bundle: Path):
    path = bundle / "authority/mysql_tpcc_node04_adopted_service_v1.py"
    spec = importlib.util.spec_from_file_location("sealed_tpcc_adopted_service", path)
    if spec is None or spec.loader is None:
        raise PhaseError("sealed adopted-service module cannot be loaded")
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def validate_service(
    service: Mapping[str, Any], bundle: Path, token: str
) -> None:
    manifest = load(bundle / "bundle_manifest.json")
    if (
        service.get("schema_version")
        != "mysql_tpcc_node04_adopted_service_authority_v1"
        or service.get("status") != "PASS"
        or service.get("service_ready") is not True
        or service.get("formal_scope") != "mysql_only"
        or service.get("cross_engine_comparison_prohibited") is not True
        or service.get("bundle_id") != manifest.get("bundle_id")
        or service.get("attempt_id") != token
        or service.get("handoff_token_sha256")
        != sha_bytes(token.encode("utf-8"))
        or service.get("formal_launch_authorized") is not False
        or service.get("accepted_round_authorized") is not False
    ):
        raise PhaseError("service authority identity/scope differs")
    database = service.get("database") or {}
    client = service.get("client") or {}
    runtimes = service.get("runtime_tree_readback") or {}
    if (
        SHA_RE.fullmatch(str(database.get("container_id") or "")) is None
        or int(database.get("pid") or 0) <= 1
        or int(database.get("process_start_ticks") or 0) <= 0
        or database.get("device_major_minor") != "259:2"
        or database.get("cpuset_cpus") != "21-24,57-60"
        or database.get("cpuset_mems") != "1"
        or database.get("mysql_port") != 65106
        or SHA_RE.fullmatch(str(client.get("container_id") or "")) is None
        or int(client.get("pid") or 0) <= 1
        or client.get("cpuset_cpus") != "8-15,44-51"
        or client.get("cpuset_mems") != "0"
        or client.get("root_filesystem_read_only") is not True
        or client.get("target_mysql_device_write_prohibited") is not True
        or (service.get("measurement_bridge") or {}).get("filesystem") != "tmpfs"
        or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", str(database.get("image_id") or "")
        )
        or client.get("image_id") != database.get("image_id")
        or (runtimes.get("jdk") or {}).get("tree_content_sha256")
        != JDK_TREE_SHA256
        or (runtimes.get("python") or {}).get("tree_content_sha256")
        != PYTHON_TREE_SHA256
        or (runtimes.get("jdk") or {}).get("mounted_read_only") is not True
        or (runtimes.get("python") or {}).get("mounted_read_only") is not True
    ):
        raise PhaseError("service DB/client/tmpfs envelope differs")


def validate_prefault(
    prefault: Mapping[str, Any], service: Mapping[str, Any]
) -> None:
    client = service["client"]
    if (
        prefault.get("schema_version")
        != "mysql_tpcc_client_target_device_prefault_v1"
        or prefault.get("status") != "PASS_PREFAULT_ZERO_IO_BASELINE"
        or prefault.get("bundle_id") != service.get("bundle_id")
        or prefault.get("attempt_id") != service.get("attempt_id")
        or prefault.get("handoff_token_sha256")
        != service.get("handoff_token_sha256")
        or prefault.get("client_container_id") != client.get("container_id")
        or prefault.get("client_pid") != client.get("pid")
        or prefault.get("client_process_start_ticks")
        != client.get("process_start_ticks")
        or prefault.get("target_device_major_minor") != "259:2"
        or prefault.get("quiet_window_delta")
        != {
            "rbytes": 0, "wbytes": 0, "rios": 0,
            "wios": 0, "dbytes": 0, "dios": 0,
        }
        or prefault.get("real_window_all_target_device_io_must_be_zero")
        is not True
        or prefault.get("accepted_round_authorized") is not False
    ):
        raise PhaseError("client target-device prefault authority differs")


def _replace(value: Any, replacements: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        for before, after in replacements.items():
            value = value.replace(before, after)
        return value
    if isinstance(value, list):
        return [_replace(item, replacements) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _replace(item, replacements) for key, item in value.items()}
    return value


def _phase_authorized(
    phase: str,
    authority: Mapping[str, Any] | None,
    service: Mapping[str, Any],
) -> bool:
    if phase in {"qualification", "typed_capture"}:
        return True
    expected = {
        "fresh_r0": "mysql_tpcc_fresh_r0_authority_v1",
        "debug": "mysql_tpcc_debug_authority_v1",
        "formal10_launch": "mysql_tpcc_formal10_launch_authority_v1",
    }[phase]
    return bool(
        authority
        and authority.get("schema_version") == expected
        and authority.get("status") == "PASS"
        and authority.get("phase") == phase
        and authority.get("formal_scope") == "mysql_only"
        and authority.get("bundle_id") == service.get("bundle_id")
        and authority.get("attempt_id") == service.get("attempt_id")
        and authority.get("handoff_token_sha256")
        == service.get("handoff_token_sha256")
        and authority.get("db_container_id")
        == (service.get("database") or {}).get("container_id")
        and authority.get("db_pid")
        == (service.get("database") or {}).get("pid")
        and authority.get("db_process_start_ticks")
        == (service.get("database") or {}).get("process_start_ticks")
        and authority.get("accepted_round_authorized") is False
    )


def materialize(
    *,
    bundle: Path,
    service: Mapping[str, Any],
    token: str,
    phase: str,
    config_in_container: str,
    mysql_defaults_in_container: str,
    phase_authority: Mapping[str, Any] | None,
    readiness_audit: Mapping[str, Any] | None,
    prefault_authority: Mapping[str, Any],
) -> dict[str, Any]:
    bundle = bundle.resolve(strict=True)
    validate_service(service, bundle, token)
    validate_prefault(prefault_authority, service)
    if phase not in PHASES:
        raise PhaseError(f"unknown phase: {phase}")
    if not config_in_container.startswith("/opt/mysql-tpcc-config/"):
        raise PhaseError("config must come from the sealed read-only config mount")
    if not mysql_defaults_in_container.startswith("/opt/mysql-tpcc-config/"):
        raise PhaseError("MySQL credentials must be referenced by defaults file")
    if not _phase_authorized(phase, phase_authority, service):
        raise PhaseError(f"{phase} lacks its sealed phase authority")
    template_name = (
        "typed_capture_command_template_v2.json"
        if phase == "typed_capture"
        else "qualification_command_template.json"
    )
    template = load(bundle / "qualification" / template_name)
    replacements = {
        "${JAVA_BINARY}": JAVA,
        "${PYTHON_BINARY}": PYTHON,
        "${BUNDLE_ROOT}": "/opt/mysql-tpcc-bundle",
        "${CONFIG_ABSOLUTE}": config_in_container,
        "${MYSQL_DEFAULTS_FILE}": mysql_defaults_in_container,
        "${MEASUREMENT_TMPFS}": "/measurement",
        "${QUALIFICATION_ATTEMPT_ID}": token,
        "${MYSQL_JDBC_URL}": "jdbc:mysql://127.0.0.1:65106/tpcc",
    }
    materialized = _replace(template, replacements)
    java_argv = materialized.get(
        "actual_java_argv",
        (materialized.get("native_java_launch_recipe") or {}).get(
            "materialized_argv"
        ),
    )
    if not isinstance(java_argv, list):
        raise PhaseError("materialized native DBWorkload argv is absent")
    workload_argv = materialized["argv"]
    client = service["client"]
    environment = dict(materialized.get("environment") or {})
    environment.update({
        "BENCHBASE_FORMAL_WARMUP_SECONDS": "60",
        "BENCHBASE_FORMAL_MAXIMUM_MEASUREMENT_SECONDS": "600",
        "BENCHBASE_FORMAL_STOP_FILE": f"/measurement/{phase}-stop",
        "MYSQL_TPCC_PHASE": phase,
        "MYSQL_TPCC_ATTEMPT_ID": token,
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    docker_exec = ["docker", "exec"]
    for key in sorted(environment):
        docker_exec += ["--env", f"{key}={environment[key]}"]
    docker_exec += [
        "--workdir", "/measurement",
        client["name"],
        *workload_argv,
    ]
    status = {
        "qualification": "SEALED_QUALIFICATION_COMMAND",
        "typed_capture": "SEALED_CAPTURE_COMMAND",
        "fresh_r0": "SEALED_R0_COMMAND",
        "debug": "SEALED_DEBUG_COMMAND",
        "formal10_launch": "SEALED_FORMAL10_COMMAND",
    }[phase]
    value = {
        "schema_version": (
            "mysql_tpcc_formal10_materialized_command_v1"
            if phase == "formal10_launch"
            else "mysql_tpcc_materialized_phase_command_v1"
        ),
        "status": status,
        "phase": phase,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "bundle_id": service["bundle_id"],
        "attempt_id": token,
        "handoff_token_sha256": service["handoff_token_sha256"],
        "db_identity": {
            key: service["database"][key]
            for key in (
                "container_id", "pid", "process_start_ticks", "datadir",
                "device_major_minor", "cpuset_cpus", "cpuset_mems", "mysql_port",
            )
        },
        "client_container": dict(client),
        "native_java_launch_recipe": materialized["native_java_launch_recipe"],
        "actual_java_argv": java_argv,
        "argv": workload_argv,
        "docker_exec_argv": docker_exec,
        "environment": environment,
        "config_in_container": config_in_container,
        "mysql_defaults_in_container": mysql_defaults_in_container,
        "actual_main_class": "com.oltpbenchmark.DBWorkload",
        "classpath_overlay_first": True,
        "same_live_database_required": True,
        "manifest_bound_java_binary": JAVA,
        "manifest_bound_python_binary": PYTHON,
        "jdk_tree_content_sha256": JDK_TREE_SHA256,
        "python_tree_content_sha256": PYTHON_TREE_SHA256,
        "prefault_authority_sha256":
            sha_bytes(canonical(prefault_authority)),
        "target_device_real_window_io_required_zero": True,
        "capture_only": phase == "typed_capture",
        "formal_r0_authorized": phase == "fresh_r0",
        "formal_authorized": phase == "formal10_launch",
        "accepted_round_authorized": False,
        "qps_resource_input_prohibited": True,
        "readiness_audit_required_for_execute":
            phase == "formal10_launch",
    }
    if PLACEHOLDER_RE.search(json.dumps(value, sort_keys=True)):
        raise PhaseError("materialized command retains a placeholder")
    reject_qps(value)
    value["command_spec_sha256"] = sha_bytes(canonical(value))
    return value


def reject_qps(value: Any, location: str = "command") -> None:
    forbidden = {"qps", "throughput", "transactions_per_second"}
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() in forbidden:
                raise PhaseError(f"QPS resource input prohibited: {location}.{key}")
            reject_qps(item, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            reject_qps(item, f"{location}[{index}]")


def _same_database(before: Mapping[str, Any], after: Mapping[str, Any]) -> None:
    fields = (
        "container_id", "pid", "process_start_ticks", "datadir",
        "device_major_minor", "cpuset_cpus", "cpuset_mems", "mysql_port",
    )
    if any(before["database"].get(key) != after["database"].get(key) for key in fields):
        raise PhaseError("same-live database identity changed during phase")
    if before.get("handoff_token_sha256") != after.get("handoff_token_sha256"):
        raise PhaseError("handoff token changed during phase")


def record_phase(
    ledger_path: Path,
    *,
    command: Mapping[str, Any],
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    returncode: int,
    target_device_io_delta: Mapping[str, int],
) -> dict[str, Any]:
    phase = str(command["phase"])
    _same_database(before, after)
    existing = load(ledger_path) if ledger_path.exists() else None
    if existing is None:
        phases: dict[str, Any] = {}
        ledger = {
            "schema_version": "mysql_tpcc_same_live_container_formal10_handoff_v1",
            "status": "INCOMPLETE",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "attempt_id": command["attempt_id"],
            "bundle_id": command["bundle_id"],
            "db_container_id": before["database"]["container_id"],
            "db_pid": before["database"]["pid"],
            "db_process_start_ticks": before["database"]["process_start_ticks"],
            "db_datadir": before["database"]["datadir"],
            "container_recreated": False,
            "datadir_replaced": False,
            "database_restarted": False,
            "phases": phases,
            "accepted_round_authorized": False,
        }
    else:
        ledger = dict(existing)
        phases = dict(ledger.get("phases") or {})
    expected_index = len(phases)
    if expected_index >= len(PHASES) or PHASES[expected_index] != phase:
        raise PhaseError("phase order/replay differs; refusing ledger mutation")
    if returncode != 0:
        raise PhaseError("failed phase cannot enter PASS handoff ledger")
    if (
        set(target_device_io_delta)
        != {"rbytes", "wbytes", "rios", "wios", "dbytes", "dios"}
        or any(int(value) != 0 for value in target_device_io_delta.values())
    ):
        raise PhaseError(
            "client target-device IO was nonzero in the real phase window"
        )
    db = after["database"]
    client = after["client"]
    phases[phase] = {
        "status": "PASS",
        "attempt_id": command["attempt_id"],
        "bundle_id": command["bundle_id"],
        "db_container_id": db["container_id"],
        "db_pid": db["pid"],
        "db_process_start_ticks": db["process_start_ticks"],
        "db_datadir": db["datadir"],
        "db_device_major_minor": db["device_major_minor"],
        "db_cpuset_cpus": db["cpuset_cpus"],
        "db_cpuset_mems": db["cpuset_mems"],
        "mysql_port": db["mysql_port"],
        "client_container_id": client["container_id"],
        "client_pid": client["pid"],
        "client_cpuset_cpus": client["cpuset_cpus"],
        "client_cpuset_mems": client["cpuset_mems"],
        "live_readback_sha256": after["live_readback_sha256"],
        "command_spec_sha256": command["command_spec_sha256"],
        "observed_epoch_ms": time.time_ns() // 1_000_000,
        "accepted_round_authorized": False,
        "client_target_device_io_delta":
            dict(target_device_io_delta),
        "client_target_device_io_zero_verified": True,
    }
    ledger["phases"] = phases
    if list(phases) == list(PHASES):
        ledger["status"] = "PASS"
    elif list(phases) == list(PHASES[:-1]):
        ledger["status"] = "PRELAUNCH_READY"
    else:
        ledger["status"] = "INCOMPLETE"
    return ledger


def execute(
    bundle: Path,
    plan_path: Path,
    service_path: Path,
    command_path: Path,
    ledger_path: Path,
    readiness_audit_path: Path | None,
    prefault_authority_path: Path,
) -> int:
    command = load(command_path)
    validate_prefault(load(prefault_authority_path), load(service_path))
    if command["phase"] == "formal10_launch":
        if readiness_audit_path is None:
            raise PhaseError("formal10 execution requires --readiness-audit")
        readiness = load(readiness_audit_path)
        if not (
            readiness.get("schema_version")
            == "mysql_tpcc_v2_formal10_readiness_audit_v1"
            and readiness.get("status") == "READY"
            and readiness.get("bundle_id") == command["bundle_id"]
            and readiness.get("blockers") == []
        ):
            raise PhaseError("formal10 execution readiness audit differs")
    service_module = _service_module(bundle)
    before = service_module.adopt(load(plan_path))
    validate_service(before, bundle, command["attempt_id"])
    _same_database(load(service_path), before)
    io_before = service_module._target_io_snapshot(
        before["client"]["pid"]
    )
    completed = subprocess.run(command["docker_exec_argv"], check=False)
    io_after = service_module._target_io_snapshot(
        before["client"]["pid"]
    )
    io_delta = service_module._io_delta(io_before, io_after)
    after = service_module.adopt(load(plan_path))
    ledger = record_phase(
        ledger_path, command=command, before=before, after=after,
        returncode=completed.returncode,
        target_device_io_delta=io_delta,
    )
    atomic_json(service_path, after)
    atomic_json(ledger_path, ledger)
    return completed.returncode


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("materialize", "execute"))
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--service", type=Path, required=True)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--phase", choices=PHASES)
    parser.add_argument("--config-in-container")
    parser.add_argument("--mysql-defaults-in-container")
    parser.add_argument("--phase-authority", type=Path)
    parser.add_argument("--readiness-audit", type=Path)
    parser.add_argument("--prefault-authority", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--command", type=Path)
    parser.add_argument("--ledger", type=Path)
    args = parser.parse_args(argv)
    if args.action == "materialize":
        if not all((
            args.token_file, args.phase, args.config_in_container,
            args.mysql_defaults_in_container, args.output,
        )):
            raise PhaseError("materialize lacks required arguments")
        token = args.token_file.read_text(encoding="utf-8").strip()
        value = materialize(
            bundle=args.bundle,
            service=load(args.service),
            token=token,
            phase=args.phase,
            config_in_container=args.config_in_container,
            mysql_defaults_in_container=args.mysql_defaults_in_container,
            phase_authority=(
                load(args.phase_authority) if args.phase_authority else None
            ),
            readiness_audit=(
                load(args.readiness_audit) if args.readiness_audit else None
            ),
            prefault_authority=load(args.prefault_authority),
        )
        atomic_json(args.output, value)
        print(json.dumps({"status": "PASS", "output": str(args.output)}, sort_keys=True))
        return 0
    if not all((args.plan, args.command, args.ledger)):
        raise PhaseError("execute lacks --plan/--command/--ledger")
    return execute(
        args.bundle, args.plan, args.service, args.command, args.ledger,
        args.readiness_audit, args.prefault_authority,
    )


if __name__ == "__main__":
    raise SystemExit(main())
