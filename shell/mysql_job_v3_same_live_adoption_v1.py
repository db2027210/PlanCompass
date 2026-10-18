#!/usr/bin/env python3
"""Refresh JOB qualification bootstrap authority by adopting the same live service.

The program is deliberately control-plane only.  It performs selected-field,
read-only Docker/systemd/proc readback, never requests Docker Config.Env, never
opens the MySQL defaults file, and never starts, stops, signals, or recreates a
container.  Its output is bootstrap-equivalent input for the sealed JOB v3
qualification controller; it grants no qualification, R0, formal, or accepted
round credit by itself.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Mapping


sys.dont_write_bytecode = True
JOB_ID = "6b4844bcdcc7ada8dfd76c6676f87c98dc382aa76979504d3edc65baaab52dd7"
JOB_MANIFEST_SHA256 = "3ee84d8e47827ff1a4221e3f8828d41e85f3d6aca2e15cf69dc610aa460f2ce6"
CONTROL_ID = "6b55e29ef3c47cca437c94501511317c6c79dd1579be3dc6e3a4983587245f74"
CONTROL_MANIFEST_SHA256 = "5c5e59098304942800ef44dc17c777ffa6df0a81261a275b526a9737474c77ba"
SOURCE_SPEC_SHA256 = "d8cc7b9de54c68812281822175c725dd2f49fa5652728e4719f1c39c8bf09d77"
SOURCE_EVIDENCE_SHA256 = "e425b4161c08d1e50350df56222ec8912861373685333e512fa783bfc80f0493"
BASELINE_SHA256 = "57a1d3192e8bd621207968347c441faddfefb7246ee74b9e2db0a3df0972110a"
XP_V23_ID = "ba68d4a57f7ebb03f8e9c662250492aac7341757c66c56256665c41b5ee3a77e"
XP_BINDING_BASENAME = "xp_successor_binding.json"
SEALED_PYTHON = Path("/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/runtime_algorithm_c_successor_20260729/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89/bin/python3.11")
SEALED_PYTHON_SHA256 = "05bd6d6d900bd990b8c00d17df3b3e1425d6c32f0408f07cd29bc495ac4aff39"
SEALED_PYTHON_TREE_SHA256 = "3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89"
HEX64 = re.compile(r"[0-9a-f]{64}$")
RFC3339_UTC = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?Z$"
)


class AdoptionError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path, label: str, expected_sha: str | None = None) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise AdoptionError(label + " is absent or unsafe")
    actual = sha256(path)
    sidecar = Path(str(path) + ".sha256")
    if (
        sidecar.is_symlink() or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii") != f"{actual}  {path.name}\n"
        or (expected_sha is not None and actual != expected_sha)
    ):
        raise AdoptionError(label + " sidecar or digest differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AdoptionError(label + " must be a JSON object")
    return value


def verify_running_python(executable: Path | None = None,
                          version: tuple[int, ...] | None = None) -> Path:
    """Fail closed before any remote readback under node02's Python 3.6."""
    observed_version = tuple(sys.version_info if version is None else version)
    if observed_version[:3] != (3, 11, 11):
        raise AdoptionError("same-live adoption requires sealed Python 3.11.11")
    requested = Path(sys.executable if executable is None else executable)
    if requested.is_symlink():
        raise AdoptionError("sealed JOB Python binary/tree identity differs")
    candidate = requested.resolve(strict=True)
    if (
        candidate != SEALED_PYTHON
        or candidate.is_symlink()
        or not candidate.is_file()
        or SEALED_PYTHON_TREE_SHA256 not in str(candidate)
        or sha256(candidate) != SEALED_PYTHON_SHA256
    ):
        raise AdoptionError("sealed JOB Python binary/tree identity differs")
    return candidate


def verify_xp_binding_basename(path: Path) -> dict[str, Any]:
    path = Path(path)
    if path.name != XP_BINDING_BASENAME:
        raise AdoptionError("XP successor binding basename differs")
    return load_json(path, "XP v23 successor binding")


def verify_bundle(path: Path, bundle_id: str, manifest_sha: str,
                  exact_closure: bool = False) -> dict[str, Any]:
    requested = Path(path)
    if requested.is_symlink():
        raise AdoptionError("bundle directory identity differs")
    resolved = requested.resolve(strict=True)
    if not resolved.is_dir() or resolved.name != bundle_id:
        raise AdoptionError("bundle directory identity differs")
    manifest_path = resolved / "bundle_manifest.json"
    manifest = load_json(manifest_path, "bundle manifest", manifest_sha)
    if (
        manifest.get("bundle_id") != bundle_id
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("postgresql_mutation_prohibited") is not True
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise AdoptionError("bundle manifest semantics differ")
    if exact_closure:
        actual = {
            item.relative_to(resolved).as_posix(): sha256(item)
            for item in resolved.rglob("*") if item.is_file()
            and item.name not in {"bundle_manifest.json", "bundle_manifest.json.sha256"}
        }
        if actual != manifest.get("files"):
            raise AdoptionError("control bundle exact file closure differs")
    return manifest


def load_xp_validator(control: Path):
    source = control / "controller/mysql_xp_successor_binding_v1.py"
    expected = json.loads((control / "bundle_manifest.json").read_text(encoding="utf-8"))["files"][
        "controller/mysql_xp_successor_binding_v1.py"
    ]
    if source.is_symlink() or not source.is_file() or sha256(source) != expected:
        raise AdoptionError("sealed XP binding validator differs")
    spec = importlib.util.spec_from_file_location("_job_v3_xp_binding", source)
    if spec is None or spec.loader is None:
        raise AdoptionError("cannot load sealed XP binding validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_command(argv: list[str]) -> str:
    completed = subprocess.run(
        argv, check=False, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=20,
    )
    if completed.returncode != 0:
        raise AdoptionError("read-only command failed: " + argv[0])
    return completed.stdout.strip()


def parse_systemd_main_pid(value: str) -> int:
    """Parse the exact systemd 219 `show --property=MainPID` output."""
    match = re.fullmatch(r"MainPID=([1-9][0-9]*)", value)
    if match is None:
        raise AdoptionError("dedicated Docker daemon systemd MainPID readback differs")
    return int(match.group(1))


DOCKER_FIELDS = {
    "container_id": "{{json .Id}}",
    "name": "{{json .Name}}",
    "running": "{{json .State.Running}}",
    "pid": "{{json .State.Pid}}",
    "started_at": "{{json .State.StartedAt}}",
    "image_id": "{{json .Image}}",
    "cpu_period_us": "{{json .HostConfig.CpuPeriod}}",
    "cpu_quota_us": "{{json .HostConfig.CpuQuota}}",
    "cpuset_cpus": "{{json .HostConfig.CpusetCpus}}",
    "cpuset_mems": "{{json .HostConfig.CpusetMems}}",
    "memory_bytes": "{{json .HostConfig.Memory}}",
    "memory_swap_bytes": "{{json .HostConfig.MemorySwap}}",
    "pids_max": "{{json .HostConfig.PidsLimit}}",
    "network_mode": "{{json .HostConfig.NetworkMode}}",
    "root_filesystem_read_only": "{{json .HostConfig.ReadonlyRootfs}}",
    "io_read": "{{json .HostConfig.BlkioDeviceReadBps}}",
    "io_write": "{{json .HostConfig.BlkioDeviceWriteBps}}",
    "mounts": "{{json .Mounts}}",
}

STABLE_MOUNT_FIELDS = (
    "Type", "Name", "Source", "Destination", "Driver", "Mode", "RW",
    "Propagation",
)
STABLE_MOUNT_SORT_FIELDS = (
    "Destination", "Source", "Type", "Mode", "RW", "Propagation", "Driver",
    "Name",
)


def docker_readback(socket_path: Path, name: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    endpoint = "unix://" + str(socket_path)
    for key, template in DOCKER_FIELDS.items():
        raw = run_command([
            "docker", "--host", endpoint, "inspect", "--type", "container",
            "--format", template, name,
        ])
        try:
            result[key] = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AdoptionError("selected Docker inspect field is not JSON: " + key) from exc
    result["name"] = str(result["name"]).lstrip("/")
    return result


def rfc3339_utc_nanoseconds(value: Any, label: str) -> int:
    """Parse an exact UTC instant without float or microsecond truncation."""
    if not isinstance(value, str):
        raise AdoptionError(label + " must be an RFC3339 UTC timestamp")
    match = RFC3339_UTC.fullmatch(value)
    if match is None:
        raise AdoptionError(label + " must be an RFC3339 UTC timestamp")
    year, month, day, hour, minute, second = map(int, match.groups()[:6])
    fraction = match.group(7) or ""
    try:
        whole = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    except ValueError as exc:
        raise AdoptionError(label + " must be an RFC3339 UTC timestamp") from exc
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = whole - epoch
    whole_seconds = delta.days * 86400 + delta.seconds
    return whole_seconds * 1_000_000_000 + int(fraction.ljust(9, "0") or "0")


def canonical_io_limits(value: Any, label: str) -> list[dict[str, Any]]:
    """Normalize Docker throttle arrays without weakening path/rate identity."""
    if value is None:
        value = []
    if not isinstance(value, list):
        raise AdoptionError(label + " Docker I/O throttle readback differs")
    result = []
    seen = set()
    for row in value:
        if not isinstance(row, Mapping):
            raise AdoptionError(label + " Docker I/O throttle row differs")
        path, rate_value = row.get("Path"), row.get("Rate")
        if not isinstance(path, str) or not path.startswith("/dev/"):
            raise AdoptionError(label + " Docker I/O throttle path differs")
        if not isinstance(rate_value, int) or isinstance(rate_value, bool) or rate_value < 0:
            raise AdoptionError(label + " Docker I/O throttle rate differs")
        if path in seen:
            raise AdoptionError(label + " Docker I/O throttle path is duplicated")
        seen.add(path)
        result.append({"Path": path, "Rate": rate_value})
    return sorted(result, key=lambda row: (row["Path"], row["Rate"]))


def canonical_mounts(value: Any, label: str) -> list[dict[str, Any]]:
    """Keep only contract-relevant immutable mount fields in stable order."""
    if not isinstance(value, list):
        raise AdoptionError(label + " Docker mount readback differs")
    result = []
    destinations = set()
    for row in value:
        if not isinstance(row, Mapping):
            raise AdoptionError(label + " Docker mount row differs")
        destination = row.get("Destination")
        source = row.get("Source")
        if not isinstance(destination, str) or not destination.startswith("/"):
            raise AdoptionError(label + " Docker mount destination differs")
        if not isinstance(source, str) or not source.startswith("/"):
            raise AdoptionError(label + " Docker mount source differs")
        if destination in destinations:
            raise AdoptionError(label + " Docker mount destination is duplicated")
        destinations.add(destination)
        result.append({key: row.get(key) for key in STABLE_MOUNT_FIELDS})
    return sorted(
        result,
        key=lambda row: tuple(str(row.get(key)) for key in STABLE_MOUNT_SORT_FIELDS),
    )


def canonical_stable_container_readback(value: Mapping[str, Any],
                                        label: str) -> dict[str, Any]:
    """Project exactly the stable identity/resource contract for two-read equality."""
    if not isinstance(value, Mapping) or set(value) != set(DOCKER_FIELDS):
        raise AdoptionError(label + " selected Docker field closure differs")
    result = dict(value)
    result["io_read"] = canonical_io_limits(value.get("io_read"), label + " read")
    result["io_write"] = canonical_io_limits(value.get("io_write"), label + " write")
    result["mounts"] = canonical_mounts(value.get("mounts"), label)
    return result


def stable_readback_changes(first: Mapping[str, Any],
                            second: Mapping[str, Any]) -> list[str]:
    changes = []
    for role in ("database", "client"):
        left = canonical_stable_container_readback(first.get(role) or {}, role)
        right = canonical_stable_container_readback(second.get(role) or {}, role)
        for key in DOCKER_FIELDS:
            if key == "started_at":
                differs = rfc3339_utc_nanoseconds(
                    left[key], role + " first StartedAt"
                ) != rfc3339_utc_nanoseconds(
                    right[key], role + " second StartedAt"
                )
            else:
                differs = left[key] != right[key]
            if differs:
                changes.append(role + "." + key)
    return changes


def validate_same_live_mount_closure(source_evidence: Mapping[str, Any],
                                     live: Mapping[str, Any]) -> None:
    """Bind the complete stable mount set to the sealed bootstrap evidence."""
    for role in ("database", "client"):
        source = (((source_evidence.get(role) or {}).get("inspect") or {}).get("Mounts"))
        expected = canonical_mounts(source, "source " + role)
        observed = canonical_mounts((live.get(role) or {}).get("mounts"), "live " + role)
        if observed != expected:
            raise AdoptionError(role + " stable mount closure differs from sealed bootstrap")


def proc_identity(proc_root: Path, pid: int) -> dict[str, Any]:
    if pid <= 1:
        raise AdoptionError("live process PID differs")
    stat_text = (proc_root / str(pid) / "stat").read_text(encoding="ascii")
    close = stat_text.rfind(")")
    fields = stat_text[close + 2:].split()
    if close < 0 or len(fields) < 20:
        raise AdoptionError("live process stat differs")
    status: dict[str, str] = {}
    for line in (proc_root / str(pid) / "status").read_text(encoding="utf-8").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            status[key] = value.strip()
    return {
        "pid": pid,
        "startticks": int(fields[19]),
        "process": status.get("Name"),
        "cpus_allowed_list": status.get("Cpus_allowed_list"),
        "mems_allowed_list": status.get("Mems_allowed_list"),
    }


def cpuset(value: str) -> set[int]:
    result: set[int] = set()
    for part in value.split(","):
        ends = part.split("-", 1)
        start, stop = int(ends[0]), int(ends[-1])
        result.update(range(start, stop + 1))
    return result


def rate(rows: Any, device: str) -> int:
    if not isinstance(rows, list):
        raise AdoptionError("Docker I/O throttle readback differs")
    matches = [row for row in rows if isinstance(row, dict) and row.get("Path") == device]
    if len(matches) != 1 or not isinstance(matches[0].get("Rate"), int):
        raise AdoptionError("Docker I/O throttle device differs")
    return int(matches[0]["Rate"])


def validate_container(row: Mapping[str, Any], expected: Mapping[str, Any],
                       resource: Mapping[str, Any], process: Mapping[str, Any],
                       role: str) -> None:
    for key in ("container_id", "container_name", "pid", "started_at_utc", "network_mode"):
        live_key = {"container_name": "name", "started_at_utc": "started_at"}.get(key, key)
        if key == "started_at_utc":
            differs = rfc3339_utc_nanoseconds(
                row.get(live_key), role + " live StartedAt"
            ) != rfc3339_utc_nanoseconds(
                expected.get(key), role + " baseline started_at_utc"
            )
        else:
            differs = row.get(live_key) != expected.get(key)
        if differs:
            raise AdoptionError(f"{role} live {key} differs")
    if row.get("running") is not True:
        raise AdoptionError(role + " container is not running")
    for key in ("cpu_period_us", "cpu_quota_us", "cpuset_cpus", "cpuset_mems",
                "memory_bytes", "memory_swap_bytes", "pids_max"):
        if row.get(key) != resource.get(key):
            raise AdoptionError(f"{role} live resource {key} differs")
    if process.get("pid") != row.get("pid") or process.get("startticks") != expected.get("startticks"):
        raise AdoptionError(role + " PID/startticks differs")
    if (
        process.get("cpus_allowed_list") != resource.get("process_cpus_allowed_list")
        or process.get("mems_allowed_list") != resource.get("process_mems_allowed_list")
    ):
        raise AdoptionError(role + " process CPU/NUMA readback differs")


def validate_live(baseline: Mapping[str, Any], socket_path: Path,
                  proc_root: Path) -> dict[str, Any]:
    candidate = baseline["candidate_non_secret_adopted_service_identity"]
    resources = baseline["effective_resource_and_isolation_readback"]
    db_expected, client_expected = candidate["database"], candidate["client"]
    first = {
        "database": docker_readback(socket_path, db_expected["container_name"]),
        "client": docker_readback(socket_path, client_expected["container_name"]),
    }
    second = {
        "database": docker_readback(socket_path, db_expected["container_name"]),
        "client": docker_readback(socket_path, client_expected["container_name"]),
    }
    changes = stable_readback_changes(first, second)
    if changes:
        raise AdoptionError(
            "container identity/resource readback is not stable across two reads: "
            + ",".join(changes)
        )
    first = {
        role: canonical_stable_container_readback(row, role)
        for role, row in first.items()
    }
    processes = {
        role: proc_identity(proc_root, int(row["pid"])) for role, row in first.items()
    }
    validate_container(first["database"], db_expected, resources["database"],
                       processes["database"], "database")
    validate_container(first["client"], client_expected, resources["client"],
                       processes["client"], "client")
    if first["database"].get("root_filesystem_read_only") not in (False, None):
        raise AdoptionError("database root filesystem policy differs")
    if first["client"].get("root_filesystem_read_only") is not True:
        raise AdoptionError("client root filesystem is not read-only")
    device = resources["database"]["io_device"]
    if (
        rate(first["database"]["io_read"], device) != resources["database"]["io_read_bps"]
        or rate(first["database"]["io_write"], device) != resources["database"]["io_write_bps"]
    ):
        raise AdoptionError("database I/O bandwidth envelope differs")
    daemon_expected = candidate["dedicated_docker_daemon"]
    if str(socket_path) != daemon_expected["socket"]:
        raise AdoptionError("dedicated Docker socket differs")
    daemon_pid = parse_systemd_main_pid(run_command([
        "systemctl", "show", "--property=MainPID", daemon_expected["unit"],
    ]))
    daemon_process = proc_identity(proc_root, daemon_pid)
    if (
        daemon_pid != daemon_expected["pid"]
        or daemon_process["startticks"] != daemon_expected["startticks"]
        or daemon_process["process"] != daemon_expected["process"]
        or daemon_process["cpus_allowed_list"] != resources["dedicated_daemon_cpuset_cpus"]
    ):
        raise AdoptionError("dedicated Docker daemon identity/isolation differs")
    sets = [cpuset(resources["database"]["cpuset_cpus"]),
            cpuset(resources["client"]["cpuset_cpus"]),
            cpuset(resources["dedicated_daemon_cpuset_cpus"])]
    if any(sets[left] & sets[right] for left, right in ((0, 1), (0, 2), (1, 2))):
        raise AdoptionError("database/client/daemon CPU isolation overlaps")
    return {
        "database": dict(first["database"], process=processes["database"]),
        "client": dict(first["client"], process=processes["client"]),
        "dedicated_docker_daemon": {
            "unit": daemon_expected["unit"], "socket": str(socket_path),
            "process": daemon_process,
        },
        "resource_envelope": resources,
        "stable_across_two_selected_field_reads": True,
    }


def validate_storage_and_listener(spec: Mapping[str, Any], baseline: Mapping[str, Any],
                                  live: Mapping[str, Any]) -> None:
    paths = spec.get("paths") or {}
    datadir = str(paths.get("datadir") or "")
    measurement = str(paths.get("host_measurement_tmpfs") or "")
    if not Path(datadir).is_absolute() or not Path(measurement).is_absolute():
        raise AdoptionError("JOB storage paths differ")
    resources = baseline["effective_resource_and_isolation_readback"]
    if run_command(["findmnt", "-n", "-o", "FSTYPE", "--target", datadir]) != resources["database"]["datadir_filesystem"]:
        raise AdoptionError("database datadir filesystem differs")
    if run_command(["findmnt", "-n", "-o", "FSTYPE", "--target", measurement]) != resources["client"]["measurement_filesystem"]:
        raise AdoptionError("measurement filesystem differs")
    db_mounts = live["database"].get("mounts") or []
    client_mounts = live["client"].get("mounts") or []
    if not any(
        isinstance(row, dict) and row.get("Source") == datadir
        for row in db_mounts
    ):
        raise AdoptionError("database datadir mount differs")
    if not any(
        isinstance(row, dict) and row.get("Source") == measurement
        and row.get("Destination") == "/measurement" and row.get("RW") is True
        for row in client_mounts
    ):
        raise AdoptionError("client measurement bind differs")
    if any(
        isinstance(row, dict) and (
            row.get("Source") == datadir or row.get("Destination") == datadir
        ) for row in client_mounts
    ):
        raise AdoptionError("client unexpectedly mounts the target datadir")
    port = int((baseline["candidate_non_secret_adopted_service_identity"]["database"])["mysql_port"])
    listener = run_command(["ss", "-H", "-ltn", "sport", "=", f":{port}"])
    if f":{port}" not in listener or "LISTEN" not in listener:
        raise AdoptionError("MySQL TCP listener readback differs")


def write_sealed(path: Path, value: Mapping[str, Any]) -> None:
    payload = canonical(value)
    if path.exists() or path.is_symlink() or Path(str(path) + ".sha256").exists():
        raise AdoptionError("refusing to overwrite same-live adoption material")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    Path(str(path) + ".sha256").write_text(
        f"{hashlib.sha256(payload).hexdigest()}  {path.name}\n", encoding="ascii"
    )
    Path(str(path) + ".sha256").chmod(0o600)


def extracted_evidence(value: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("bootstrap_evidence", "evidence"):
        if isinstance(value.get(key), dict):
            return value[key]
    return value


def safe_inspect(row: Mapping[str, Any]) -> dict[str, Any]:
    """Retain exactly the non-secret inspect projection needed by sealed v3."""
    config = row.get("Config") or {}
    host = row.get("HostConfig") or {}
    state = row.get("State") or {}
    return {
        "Id": row.get("Id"), "Name": row.get("Name"), "Image": row.get("Image"),
        "Created": row.get("Created"),
        "State": {key: state.get(key) for key in ("Running", "Pid", "StartedAt")},
        "Config": {key: config.get(key) for key in ("Image", "Entrypoint", "Cmd", "User", "Labels")},
        "HostConfig": {
            key: host.get(key) for key in (
                "CpuPeriod", "CpuQuota", "CpusetCpus", "CpusetMems", "Memory",
                "MemorySwap", "PidsLimit", "ReadonlyRootfs", "NetworkMode",
                "SecurityOpt", "CapDrop", "LogConfig", "RestartPolicy",
                "BlkioDeviceReadBps", "BlkioDeviceWriteBps",
            )
        },
        "Mounts": row.get("Mounts") or [],
    }


def safe_bootstrap_evidence(value: Mapping[str, Any]) -> dict[str, Any]:
    """Copy bootstrap evidence while excluding every Docker Config.Env value."""
    result = json.loads(json.dumps(value, ensure_ascii=True, allow_nan=False))
    for role in ("database", "client"):
        role_value = result.get(role)
        source_role = value.get(role)
        if isinstance(role_value, dict) and isinstance(source_role, Mapping):
            inspect = source_role.get("inspect")
            if isinstance(inspect, Mapping):
                role_value["inspect"] = safe_inspect(inspect)
    return result


def materialize(args: argparse.Namespace) -> dict[str, Any]:
    verify_running_python()
    if args.ttl_seconds < 300 or args.ttl_seconds > 900:
        raise AdoptionError("adoption TTL must be 300..900 seconds")
    baseline_path = args.baseline_audit or (
        Path(__file__).resolve().parents[1] / "authority/job_v3_readonly_readiness_audit.json"
    )
    baseline = load_json(baseline_path, "read-only baseline audit", BASELINE_SHA256)
    spec = load_json(args.bootstrap_spec, "source bootstrap spec", SOURCE_SPEC_SHA256)
    source_evidence = load_json(
        args.bootstrap_evidence, "source bootstrap evidence", SOURCE_EVIDENCE_SHA256
    )
    job_bundle = args.job_bundle.resolve(strict=True)
    control = args.job_control_bundle.resolve(strict=True)
    verify_bundle(job_bundle, JOB_ID, JOB_MANIFEST_SHA256)
    verify_bundle(control, CONTROL_ID, CONTROL_MANIFEST_SHA256, exact_closure=True)
    verify_xp_binding_basename(args.xp_successor_binding)
    xp = load_xp_validator(control).validate_binding(
        args.xp_successor_binding, args.xp_bundle.resolve(strict=True)
    )
    if xp.get("xp_bundle_id") != XP_V23_ID:
        raise AdoptionError("XP v23 successor binding differs")
    audit_bootstrap = baseline["job_bootstrap_evidence"]
    audit_candidate = baseline["candidate_non_secret_adopted_service_identity"]
    if (
        spec.get("attempt_token") != audit_bootstrap["attempt_token"]
        or (spec.get("bundle") or {}).get("bundle_id") != JOB_ID
        or Path(str((spec.get("bundle") or {}).get("path"))).resolve() != job_bundle
        or (spec.get("containers") or {}).get("database") != audit_candidate["database"]["container_name"]
        or (spec.get("containers") or {}).get("client") != audit_candidate["client"]["container_name"]
        or source_evidence.get("status") != audit_bootstrap["status"]
        or source_evidence.get("spec_sha256") != SOURCE_SPEC_SHA256
    ):
        raise AdoptionError("source bootstrap identity differs from sealed baseline")
    evidence = extracted_evidence(source_evidence)
    for role in ("database", "client"):
        row = ((evidence.get(role) or {}).get("inspect") or {})
        expected = audit_candidate[role]
        if (
            str(row.get("Name") or "").lstrip("/") != expected["container_name"]
            or row.get("Id") != expected["container_id"]
            or int(((row.get("State") or {}).get("Pid") or (evidence.get(role) or {}).get("pid") or 0)) != expected["pid"]
        ):
            raise AdoptionError("source bootstrap container identity differs")
    defaults = Path(str(spec.get("mysql_defaults_file") or ""))
    if (
        not defaults.is_absolute() or defaults.is_symlink() or not defaults.is_file()
        or stat.S_IMODE(defaults.stat().st_mode) & 0o077
    ):
        raise AdoptionError("MySQL defaults path is absent, symlinked, or too permissive")
    if (
        not args.dedicated_docker_socket.is_absolute()
        or args.dedicated_docker_socket.is_symlink()
        or not stat.S_ISSOCK(args.dedicated_docker_socket.stat().st_mode)
    ):
        raise AdoptionError("dedicated Docker endpoint is not an exact Unix socket")
    live = validate_live(baseline, args.dedicated_docker_socket, args.proc_root)
    validate_same_live_mount_closure(evidence, live)
    validate_storage_and_listener(spec, baseline, live)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=args.ttl_seconds)
    refreshed_spec = dict(spec)
    refreshed_spec["created_at_utc"] = now.isoformat()
    refreshed_spec["expires_at_utc"] = expires.isoformat()
    refreshed_spec["same_live_adoption_successor"] = {
        "schema_version": "mysql_job_v3_same_live_adoption_binding_v1",
        "source_spec_sha256": SOURCE_SPEC_SHA256,
        "source_evidence_sha256": SOURCE_EVIDENCE_SHA256,
        "job_control_bundle_id": CONTROL_ID,
        "xp_successor_bundle_id": XP_V23_ID,
        "container_recreated": False,
        "database_or_client_started_or_stopped": False,
    }
    output = args.output_directory
    if output.exists() or output.is_symlink():
        raise AdoptionError("same-live adoption output directory already exists")
    output.mkdir(parents=True, mode=0o700)
    spec_path = output / "refreshed_bootstrap_spec.json"
    write_sealed(spec_path, refreshed_spec)
    authority = {
        "schema_version": "mysql_job_v3_same_live_adoption_authority_v1",
        "status": "PASS_SAME_LIVE_ADOPTED_SHORT_TTL_NOT_EXECUTED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "source_bootstrap_spec_sha256": SOURCE_SPEC_SHA256,
        "source_bootstrap_evidence_sha256": SOURCE_EVIDENCE_SHA256,
        "refreshed_bootstrap_spec_sha256": sha256(spec_path),
        "job_bundle_id": JOB_ID,
        "job_control_bundle_id": CONTROL_ID,
        "xp_successor_bundle_id": XP_V23_ID,
        "xp_successor_binding_sha256": sha256(args.xp_successor_binding),
        "observed_at_utc": now.isoformat(),
        "expires_at_utc": expires.isoformat(),
        "live_readback": live,
        "credentials": {"path": str(defaults), "content_read": False, "content_returned": False},
        "container_recreated": False,
        "database_or_client_started_or_stopped": False,
        "qualification_authorized": False,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
    }
    authority_path = output / "same_live_adoption_authority.json"
    write_sealed(authority_path, authority)
    refreshed_evidence = {
        "schema_version": "mysql_job_v3_same_live_bootstrap_equivalent_evidence_v1",
        "status": "PASS_SAME_LIVE_BOOTSTRAP_EQUIVALENT_NOT_EXECUTED",
        "spec_sha256": sha256(spec_path),
        "same_live_adoption_authority": authority,
        "bootstrap_evidence": safe_bootstrap_evidence(evidence),
        "qualification_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
    }
    evidence_path = output / "refreshed_bootstrap_evidence.json"
    write_sealed(evidence_path, refreshed_evidence)
    return {
        "status": authority["status"],
        "refreshed_bootstrap_spec": str(spec_path),
        "refreshed_bootstrap_evidence": str(evidence_path),
        "same_live_adoption_authority": str(authority_path),
        "expires_at_utc": authority["expires_at_utc"],
        "next": "run sealed JOB v3 qualification authority before expiry",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap-spec", type=Path, required=True)
    parser.add_argument("--bootstrap-evidence", type=Path, required=True)
    parser.add_argument("--job-bundle", type=Path, required=True)
    parser.add_argument("--job-control-bundle", type=Path, required=True)
    parser.add_argument("--xp-successor-binding", type=Path, required=True)
    parser.add_argument("--xp-bundle", type=Path, required=True)
    parser.add_argument("--dedicated-docker-socket", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--ttl-seconds", type=int, default=900)
    parser.add_argument("--baseline-audit", type=Path)
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"))
    args = parser.parse_args(argv)
    print(json.dumps(materialize(args), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AdoptionError, OSError, ValueError, KeyError, TypeError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
