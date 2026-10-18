#!/usr/bin/env python3
"""Render the one JOB v32 180-second solo-qualification authority.

This is an offline authority renderer.  It does not invoke Docker, touch a
database, or create a data-plane process.  It must be run on the host which
holds the already-complete bootstrap evidence, immediately before the one
debug-only qualification.  Its short-lived token binding deliberately makes
the result unusable as a capture, R0, debug-round, or formal authority.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import secrets
import stat
from typing import Any, Mapping


MYSQL_PORT = 65102
JOB_DATABASE = "imdb_job"
JOB_WEIGHTS = "10,10,10,10,10,10,30,10,7.5"


class RenderError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def collector_canonical(value: Any) -> bytes:
    """Match the sealed qualification collector's identity serialization."""
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(path: Path, label: str) -> Mapping[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RenderError("%s is absent or unsafe" % label)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RenderError("%s is not JSON: %s" % (label, exc))
    if not isinstance(value, dict):
        raise RenderError("%s is not an object" % label)
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(canonical(value) + b"\n")
    temporary.chmod(0o600)
    temporary.replace(path)
    sidecar = path.with_name(path.name + ".sha256")
    sidecar_temporary = sidecar.with_name(sidecar.name + ".tmp")
    sidecar_temporary.write_text(
        "%s  %s\n" % (sha256(path), path.name), encoding="ascii"
    )
    sidecar_temporary.chmod(0o600)
    sidecar_temporary.replace(sidecar)


def client_projection(row: Mapping[str, Any]) -> Mapping[str, Any]:
    """Match the sealed collector's immutable client inspect projection."""
    config = row.get("Config") or {}
    host = row.get("HostConfig") or {}
    mounts = row.get("Mounts") or []
    if not isinstance(config, dict) or not isinstance(host, dict):
        raise RenderError("client immutable inspect projection differs")
    fixed_mounts = []
    for mount in mounts:
        if not isinstance(mount, dict):
            raise RenderError("client immutable mount projection differs")
        fixed_mounts.append({
            "type": mount.get("Type"), "source": mount.get("Source"),
            "destination": mount.get("Destination"), "rw": mount.get("RW"),
        })
    fixed_mounts.sort(
        key=lambda value: (
            str(value["type"]), str(value["source"]),
            str(value["destination"]), str(value["rw"]),
        )
    )
    labels = config.get("Labels")
    return {
        "id": row.get("Id"), "name": row.get("Name"), "image": row.get("Image"),
        "config": {
            "image": config.get("Image"), "entrypoint": config.get("Entrypoint"),
            "cmd": config.get("Cmd"), "user": config.get("User"),
            "labels": dict(labels) if isinstance(labels, dict) else labels,
        },
        "host_config": {
            "cpu_period": host.get("CpuPeriod"), "cpu_quota": host.get("CpuQuota"),
            "cpuset_cpus": host.get("CpusetCpus"), "cpuset_mems": host.get("CpusetMems"),
            "memory": host.get("Memory"), "memory_swap": host.get("MemorySwap"),
            "pids_limit": host.get("PidsLimit"), "readonly_rootfs": host.get("ReadonlyRootfs"),
            "network_mode": host.get("NetworkMode"),
            "security_opt": sorted(str(value) for value in (host.get("SecurityOpt") or [])),
            "cap_drop": sorted(str(value) for value in (host.get("CapDrop") or [])),
            "log_config": host.get("LogConfig"), "restart_policy": host.get("RestartPolicy"),
        },
        "mounts": fixed_mounts,
    }


def extract_evidence(document: Mapping[str, Any]) -> Mapping[str, Any]:
    """Accept a direct evidence document or its bootstrap-authority wrapper."""
    if isinstance(document.get("bootstrap_evidence"), dict):
        return document["bootstrap_evidence"]
    if isinstance(document.get("evidence"), dict):
        return document["evidence"]
    return document


def extract_spec(document: Mapping[str, Any]) -> Mapping[str, Any]:
    """Use an embedded bootstrap spec only when the authority carries it."""
    for key in ("bootstrap_spec", "spec"):
        value = document.get(key)
        if isinstance(value, dict):
            return value
    raise RenderError(
        "bootstrap authority does not embed a bootstrap spec; "
        "--bootstrap-spec is required"
    )


def validate_inputs(
    spec: Mapping[str, Any], evidence: Mapping[str, Any]
) -> Mapping[str, Any]:
    bundle = spec.get("bundle") or {}
    paths = spec.get("paths") or {}
    bundle_id = bundle.get("bundle_id")
    manifest_sha256 = bundle.get("manifest_sha256")
    attempt = spec.get("attempt_token")
    expected_attempt_root = (
        "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/"
        "runtime_algorithm_c_successor_20260729/attempts/" + str(attempt)
    )
    if (
        not isinstance(attempt, str)
        or not attempt.startswith("job-v2-")
        or not isinstance(bundle_id, str)
        or len(bundle_id) != 64
        or not isinstance(manifest_sha256, str)
        or len(manifest_sha256) != 64
        or paths.get("attempt_root") != expected_attempt_root
    ):
        raise RenderError("bootstrap spec identity differs")
    database = evidence.get("database") or {}
    client = evidence.get("client") or {}
    database_pid = int(database.get("pid") or 0)
    client_pid = int(client.get("pid") or 0)
    if (
        database_pid <= 0
        or client_pid <= 0
        or not isinstance(database.get("inspect"), dict)
        or not isinstance(client.get("inspect"), dict)
    ):
        raise RenderError("bootstrap evidence does not bind the authoritative live PIDs")
    live = ((evidence.get("collect_only") or {}).get("document") or {})
    if (
        live.get("status") != "PASS_LIVE_READBACK_NOT_LAUNCH_AUTHORITY"
        or live.get("bundle_id") != bundle_id
        or int(((live.get("container") or {}).get("pid") or 0))
        != database_pid
    ):
        raise RenderError("bootstrap collect-only readback is not authoritative")
    measurement_bind = evidence.get("measurement_bind") or {}
    if measurement_bind != {
        "source": paths.get("host_measurement_tmpfs"),
        "destination": "/measurement",
        "filesystem": "tmpfs",
        "size_bytes": 1073741824,
        "uid": 1008,
        "gid": 1008,
        "mode": "0700",
        "mount_options": ["rw", "nosuid", "nodev", "noexec"],
    }:
        raise RenderError("bootstrap host measurement bind is not authoritative")
    client_host = client["inspect"].get("HostConfig") or {}
    client_cpuset = client_host.get("CpusetCpus")
    client_mems = client_host.get("CpusetMems")
    if not isinstance(client_cpuset, str) or not isinstance(client_mems, str):
        raise RenderError("bootstrap client CPU/NUMA isolation is absent")
    return {
        "attempt": attempt,
        "bundle_id": bundle_id,
        "bundle_manifest_sha256": manifest_sha256,
        "database_pid": database_pid,
        "client_pid": client_pid,
        "client_cpuset": client_cpuset,
        "client_mems": client_mems,
        "measurement_bind": measurement_bind,
    }


def build_command(
    spec: Mapping[str, Any],
    evidence: Mapping[str, Any],
    binding: Mapping[str, Any],
) -> Mapping[str, Any]:
    paths = spec["paths"]
    bundle = Path(spec["bundle"]["path"])
    python = Path(spec["python_runtime"]["executable_path"])
    adapter = bundle / "qualification/mysql_sql_debug_metric_adapter_v1.py"
    query_dir = bundle / "qualification/sql/job"
    defaults = Path(spec["mysql_defaults_file"])
    sql_paths = sorted(query_dir.glob("*.sql"))
    expected_names = {
        "01_job_1a.sql", "02_job_5a.sql", "03_job_9a.sql", "04_job_12a.sql",
        "05_job_16a.sql", "06_job_20a.sql", "07_job_26a.sql", "08_job_33a.sql",
        "09_job_sensitive_title_cast_role5_movie100k.sql",
    }
    if (
        python.is_symlink() or not python.is_file() or adapter.is_symlink()
        or not adapter.is_file() or defaults.is_symlink() or not defaults.is_file()
        or {path.name for path in sql_paths} != expected_names
    ):
        raise RenderError("sealed JOB qualification code closure differs")
    client_row = evidence["client"]["inspect"]
    host = client_row.get("HostConfig") or {}
    if (
        host.get("CpusetCpus") != binding["client_cpuset"]
        or host.get("CpusetMems") != binding["client_mems"]
    ):
        raise RenderError("authoritative client isolation differs")
    required_mounts = [
        {"source": str(bundle), "destination": str(bundle)},
        {"source": paths["python_runtime_root"], "destination": paths["python_runtime_root"]},
        {"source": paths["credentials_dir"], "destination": paths["credentials_dir"]},
    ]
    attempt_id = binding["attempt"] + "-qualification-solo"
    command = {
        "schema_version": "mysql_algorithm_c_debug_qualification_command_v1",
        "bundle_id": binding["bundle_id"],
        "bundle_manifest_sha256": binding["bundle_manifest_sha256"],
        "mode": "qualification_solo",
        "qualification_attempt_id": attempt_id,
        "debug_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "argv": [
            str(python), str(adapter), "--attempt-id=" + attempt_id,
            "--metric-stream", "/measurement/qualification_solo.native.jsonl",
            "--mysql-binary=/usr/bin/mysql", "--defaults-extra-file=" + str(defaults),
            "--host=127.0.0.1", "--port=" + str(MYSQL_PORT), "--user=root",
            "--database=" + JOB_DATABASE, "--query-dir=" + str(query_dir),
            "--concurrency=1", "--duration-seconds=240", "--weights=" + JOB_WEIGHTS,
            "--seed=4508333", "--query-timeout-seconds=600",
        ],
        "executable_sha256": sha256(python),
        "code_files": {str(path): sha256(path) for path in [python, adapter, defaults, *sql_paths]},
        "environment": {"QUALIFICATION_ROLE": "solo"},
        "cwd": "/measurement",
        "workload_metric_stream": {
            "schema_version": "mysql_algorithm_c_debug_workload_metric_stream_v1",
            "path": str(Path(paths["host_measurement_tmpfs"]) / "qualification_solo.host_metrics.jsonl"),
        },
        "workload_metric_adapter": {
            "schema_version": "mysql_algorithm_c_debug_workload_metric_adapter_v1",
            "kind": "sql_query_native",
        },
        "workload_process_isolation": {
            "cpuset_cpus": binding["client_cpuset"],
            "cpuset_mems": binding["client_mems"],
            "root_and_descendants_exact": True,
        },
        "client_container": {
            "name": spec["containers"]["client"],
            "container_id": client_row.get("Id"), "image_id": client_row.get("Image"),
            "created": client_row.get("Created"),
            "inspect_config_sha256": sha_bytes(
                collector_canonical(client_projection(client_row))
            ),
            "required_read_only_mounts": required_mounts,
            "measurement_bind": binding["measurement_bind"],
        },
        "measurement_output": {
            "host_tmpfs_directory": paths["host_measurement_tmpfs"],
            "container_tmpfs_destination": "/measurement",
        },
    }
    return command


def command_plan(
    spec: Mapping[str, Any],
    output_dir: Path,
    binding: Mapping[str, Any],
) -> Mapping[str, Any]:
    paths = spec["paths"]
    return {
        "schema_version": "mysql_job_v32_qualification_solo_launch_plan_v1",
        "scope": "mysql_only",
        "attempt_token": binding["attempt"],
        "stage": "debug_qualification_solo_180s",
        "authorized_actions": ["one_180_second_debug_qualification_solo"],
        "prohibited_actions": ["capture", "fresh_r0", "debug_round", "formal", "accepted_round"],
        "argv_with_token_placeholder": [
            spec["python_runtime"]["executable_path"], spec["collector"],
            "--qualification-solo", "--bundle", spec["bundle"]["path"],
            "--container", spec["containers"]["database"], "--mysql-client-container",
            spec["containers"]["client"], "--mysql-binary-in-client", "/usr/bin/mysql",
            "--mysql-defaults-file", spec["mysql_defaults_file"], "--output",
            str(
                Path(paths["host_measurement_tmpfs"])
                / "qualification_solo_evidence.json"
            ),
            "--main-agent-token", "<read-from-0600-token-file>",
            "--main-agent-authority", str(output_dir / "qualification_solo_authority.json"),
            "--command-spec", str(output_dir / "qualification_solo_command.json"),
        ],
        "pass_only_next_phase": "compare_bidirectional_dynamic_gate_then_fresh_r0_authority",
        "fresh_r0_authority_generation": {
            "entrypoint": spec["bundle"]["path"] + "/qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py",
            "requires": ["bootstrap_evidence.json", "PASS dynamic_gate.json"],
            "forbids": ["--formal10", "old checkpoint resume", "accepted round accounting"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap-spec", type=Path)
    parser.add_argument("--bootstrap-authority", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--ttl-seconds", type=int, default=900)
    args = parser.parse_args()
    if args.ttl_seconds < 60 or args.ttl_seconds > 3600:
        raise RenderError("qualification authority TTL must be 60..3600 seconds")
    bootstrap_authority = load(args.bootstrap_authority, "bootstrap authority")
    spec = (
        load(args.bootstrap_spec, "bootstrap spec")
        if args.bootstrap_spec is not None
        else extract_spec(bootstrap_authority)
    )
    evidence = extract_evidence(bootstrap_authority)
    binding = validate_inputs(spec, evidence)
    token_path = args.token_file
    if token_path.exists():
        raise RenderError("refusing to overwrite a main-agent token")
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(48)
    token_path.write_text(token + "\n", encoding="ascii")
    token_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    command = build_command(spec, evidence, binding)
    output_dir = args.output_dir
    command_path = output_dir / "qualification_solo_command.json"
    authority_path = output_dir / "qualification_solo_authority.json"
    plan_path = output_dir / "qualification_solo_launch_plan.json"
    atomic_json(command_path, command)
    now = datetime.now(timezone.utc)
    authority = {
        "schema_version": "mysql_algorithm_c_main_agent_qualification_authority_v1",
        "authorized_by": "main_agent", "bundle_id": binding["bundle_id"],
        "mode": "qualification_solo", "qualification_attempt_id": command["qualification_attempt_id"],
        "main_agent_token_sha256": sha_bytes(token.encode("utf-8")),
        "command_spec_sha256": sha256(command_path), "debug_only": True,
        "capture_authorized": False, "formal_r0_authorized": False,
        "formal_authorized": False, "accepted_round_authorized": False,
        "not_before_epoch": now.timestamp(),
        "expires_epoch": (now + timedelta(seconds=args.ttl_seconds)).timestamp(),
    }
    atomic_json(authority_path, authority)
    atomic_json(plan_path, command_plan(spec, output_dir, binding))
    print(json.dumps({
        "status": "RENDERED_DEBUG_QUALIFICATION_ONLY_NOT_EXECUTED",
        "command_spec": str(command_path), "authority": str(authority_path),
        "launch_plan": str(plan_path), "token_file": str(token_path),
        "expires_epoch": authority["expires_epoch"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RenderError as exc:
        print("NOT_READY: %s" % exc)
        raise SystemExit(2)
