#!/usr/bin/env python3
"""Materialize fresh A/B solo+paired qualification command authorities.

This is an offline control-plane renderer.  It never invokes Docker, SSH, a
database client, or a workload.  The caller must provide a fresh a336 TPC-C
snapshot, its same-live adopted-service authority, and the current TPC-H
bootstrap spec/evidence pair.  Four distinct token files are read only to bind
their digests; token contents are never copied to an output artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import secrets
import stat
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


class MaterializationError(RuntimeError):
    pass


HEX64 = re.compile(r"^[0-9a-f]{64}$")
A336_ID = "a336aef82ec2a8098840ff10c6046a18eca73480c5905a96373d265caaa5f723"
TPCC_ID = "7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77"
TPCC_MANIFEST_SHA = "e9d5e2213a14d2289c07063c0b24dfb68d6059f068c5c91388862f8a873b4cba"
TPCC_ATTEMPT = "tpcc-v16-7e2e13aa-20260801t121500p08"
TPCH_ID = "ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4"
TPCH_MANIFEST_SHA = "af1d0facca8b33e2638c0a2af6a751bececc407d483055ca064a34308d27bb52"
TPCH_ATTEMPT = "tpch-v2-20260803t022500z-afbe6badff313374deadbeef"
TPCH_AUTHORITY_MODULE_SHA = "120a848c63c6aaa5ecce24f55ed33d6b678d7a21e30ed2050b7a2efdd7a7b408"
TPCH_CLIENT_IDENTITY_HELPER_SHA = "8112d805623183b412a779cb9549273b40f7f1f02a1bc85d5d6407d8e02629d3"
COLLECTOR_ID = "feb2e6ea9cd866a3fb82c5fbe9dea14a68cb2a6f73d97de0f4a615c410c8caff"
COLLECTOR_MANIFEST_SHA = "2663d7b98567d5f6273c391bf62c88285ad55c82862d0c28dfffe6299428a4b5"
TPCC_DB_CPUSET = "21-24,57-60"
TPCC_CLIENT_CPUSET = "8-15,44-51"
TPCC_CLIENT_MEMS = "0"
FALSE_FLAGS = (
    "capture_authorized", "formal_r0_authorized", "formal_authorized",
    "accepted_round_authorized",
)
STATIC_PID_KEYS = {
    "pid", "process_id", "database_pid", "client_pid", "mysqld_pid",
}


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def regular(path: Path, label: str) -> Path:
    try:
        resolved = Path(path).resolve(strict=True)
    except OSError as exc:
        raise MaterializationError(f"{label} is absent") from exc
    if resolved.is_symlink() or not resolved.is_file():
        raise MaterializationError(f"{label} must be a regular file")
    return resolved


def load_sealed(path: Path, label: str) -> tuple[Path, dict[str, Any]]:
    path = regular(path, label)
    sidecar = regular(Path(str(path) + ".sha256"), label + " sidecar")
    if sidecar.read_text(encoding="ascii") != f"{sha256(path)}  {path.name}\n":
        raise MaterializationError(f"{label} sidecar differs")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MaterializationError(f"{label} JSON differs") from exc
    if not isinstance(value, dict):
        raise MaterializationError(f"{label} must contain one object")
    return path, value


def verify_bundle(path: Path, label: str, expected_id: str,
                  expected_manifest_sha: str) -> tuple[Path, Path, dict[str, Any]]:
    bundle = Path(path).resolve(strict=True)
    manifest_path, manifest = load_sealed(bundle / "bundle_manifest.json", label + " manifest")
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    if (
        bundle.name != expected_id or bundle_id != expected_id
        or sha_bytes(canonical(unsigned)) != expected_id
        or sha256(manifest_path) != expected_manifest_sha
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("postgresql_mutation_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise MaterializationError(f"{label} identity/scope differs")
    return bundle, manifest_path, manifest


def load_bundle_json(bundle: Path, manifest: Mapping[str, Any], relative: str,
                     label: str) -> tuple[Path, dict[str, Any]]:
    path = regular(bundle / relative, label)
    if (manifest.get("files") or {}).get(relative) != sha256(path):
        raise MaterializationError(f"{label} is outside sealed bundle closure")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MaterializationError(f"{label} JSON differs") from exc
    if not isinstance(value, dict):
        raise MaterializationError(f"{label} must contain one object")
    return path, value


def full_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or HEX64.fullmatch(value) is None:
        raise MaterializationError(f"{label} must be a full lowercase ID")
    return value


def false_boundary(value: Mapping[str, Any], label: str) -> None:
    for key in FALSE_FLAGS:
        if value.get(key) is not False:
            raise MaterializationError(f"{label} {key} differs")


def validate_snapshot(value: Mapping[str, Any], now: float) -> tuple[dict[str, Any], dict[str, Any]]:
    database = value.get("database") or {}
    client = value.get("client") or {}
    if (
        value.get("schema_version") != "mysql_tpcc_v16_same_live_readback_v1"
        or value.get("status") != "PASS_READ_ONLY_LIVE_IDENTITY_NOT_CAPTURE_AUTHORITY"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("postgresql_mutation_prohibited") is not True
        or value.get("hostname") != "node04"
        or value.get("bundle_id") != TPCC_ID
        or value.get("attempt_id") != TPCC_ATTEMPT
        or value.get("accepted_rounds_credit") != 0
        or database.get("cpuset_cpus") != TPCC_DB_CPUSET
        or database.get("device") != "/dev/nvme3n1"
        or database.get("device_major_minor") != "259:2"
        or database.get("mysql_port") != 65106
        or client.get("cpuset_cpus") != TPCC_CLIENT_CPUSET
    ):
        raise MaterializationError("a336 snapshot identity/resource differs")
    for key in ("capture_authorized", "formal_r0_authorized", "formal_launch_authorized",
                "accepted_round_authorized"):
        if value.get(key) is not False:
            raise MaterializationError("a336 snapshot authority boundary differs")
    try:
        observed = datetime.fromisoformat(str(value["collected_at_utc"]).replace("Z", "+00:00"))
    except (KeyError, ValueError) as exc:
        raise MaterializationError("a336 snapshot time differs") from exc
    age = now - observed.timestamp()
    if age < -5.0 or age > 120.0:
        raise MaterializationError("a336 snapshot is stale")
    full_id(database.get("container_id"), "TPC-C database container")
    full_id(client.get("container_id"), "TPC-C client container")
    return dict(database), dict(client)


def validate_service(value: Mapping[str, Any], database: Mapping[str, Any],
                     client: Mapping[str, Any]) -> dict[str, Any]:
    service_db = value.get("database") or {}
    service_client = value.get("client") or {}
    if (
        value.get("schema_version") != "mysql_tpcc_node04_adopted_service_authority_v2"
        or value.get("status") != "PASS"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("bundle_id") != TPCC_ID
        or value.get("attempt_id") != TPCC_ATTEMPT
        or value.get("same_live_database_required") is not True
        or value.get("container_recreated") is not False
        or value.get("database_restarted") is not False
        or value.get("datadir_replaced") is not False
        or value.get("formal_launch_authorized") is not False
        or value.get("accepted_round_authorized") is not False
        or service_db.get("container_id") != database.get("container_id")
        or service_client.get("container_id") != client.get("container_id")
        or service_db.get("process_start_ticks") != database.get("process_start_ticks")
        or service_client.get("process_start_ticks") != client.get("process_start_ticks")
    ):
        raise MaterializationError("adopted-service/snapshot continuity differs")
    required = ("name", "container_id", "image_id", "created", "inspect_config_sha256")
    if any(not isinstance(service_client.get(key), str) or not service_client[key] for key in required):
        raise MaterializationError("TPC-C client immutable command binding is incomplete")
    full_id(service_client["container_id"], "TPC-C service client")
    if HEX64.fullmatch(service_client["inspect_config_sha256"]) is None:
        raise MaterializationError("TPC-C client inspect config identity differs")
    return dict(service_client)


def load_module(path: Path, sealed_files: Mapping[str, Any]):
    path = regular(path, "TPC-H authority module")
    required = {
        "mysql_tpch_distributed_v2_authority.py": TPCH_AUTHORITY_MODULE_SHA,
        "mysql_tpch_client_immutable_identity_v1.py": TPCH_CLIENT_IDENTITY_HELPER_SHA,
    }
    for basename, expected_sha in required.items():
        source = regular(path.parent / basename, "TPC-H import source " + basename)
        if sealed_files.get(basename) != expected_sha or sha256(source) != expected_sha:
            raise MaterializationError("TPC-H import source hash differs: " + basename)
    spec = importlib.util.spec_from_file_location("tpch_current_args_authority", str(path))
    if spec is None or spec.loader is None:
        raise MaterializationError("TPC-H authority module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    helper_name = "mysql_tpch_client_immutable_identity_v1"
    absent = object()
    previous_helper = sys.modules.pop(helper_name, absent)
    previous_path = list(sys.path)
    try:
        sys.path.insert(0, str(path.parent))
        spec.loader.exec_module(module)
        helper = sys.modules.get(helper_name)
        helper_path = regular(Path(str(getattr(helper, "__file__", ""))),
                              "loaded TPC-H client identity helper")
        if helper_path != (path.parent / f"{helper_name}.py").resolve(strict=True):
            raise MaterializationError("loaded TPC-H client identity helper path differs")
        if sha256(helper_path) != TPCH_CLIENT_IDENTITY_HELPER_SHA:
            raise MaterializationError("loaded TPC-H client identity helper hash differs")
    finally:
        sys.path[:] = previous_path
        if previous_helper is absent:
            sys.modules.pop(helper_name, None)
        else:
            sys.modules[helper_name] = previous_helper
    return module


def peer_binding(scenario: str, attempt: str, metric_path: str) -> dict[str, Any]:
    return {
        "schema_version": "mysql_algorithm_c_paired_peer_workload_binding_v1",
        "scenario": scenario,
        "qualification_attempt_id": attempt,
        "workload_metric_stream_path": metric_path,
    }


def replace_attempt(command: dict[str, Any], attempt: str, metric_path: Path,
                    paired: Mapping[str, Any] | None) -> dict[str, Any]:
    command = json.loads(json.dumps(command))
    old = command["qualification_attempt_id"]
    command["qualification_attempt_id"] = attempt
    command["workload_metric_stream"]["path"] = str(metric_path)
    command["paired_peer_workload"] = None if paired is None else dict(paired)
    command["argv"] = [value.replace(old, attempt) for value in command["argv"]]
    return command


def build_tpcc_command(bundle: Path, manifest_path: Path, manifest: Mapping[str, Any],
                       runtime: Mapping[str, Any],
                       service_client: Mapping[str, Any], defaults_file: Path,
                       measurement_root: Path, mode: str, attempt: str,
                       paired: Mapping[str, Any] | None) -> dict[str, Any]:
    if mode not in ("qualification_solo", "qualification_paired"):
        raise MaterializationError("TPC-C qualification mode differs")
    template_path, template = load_bundle_json(
        bundle, manifest, "qualification/qualification_command_template.json",
        "TPC-C command template")
    del template_path
    recipe = template.get("native_java_launch_recipe") or {}
    classpath_relative = recipe.get("classpath_relative_entries")
    if not isinstance(classpath_relative, list) or not classpath_relative:
        raise MaterializationError("TPC-C native classpath recipe differs")
    jdk = (runtime.get("client_runtime_mounts") or {}).get("jdk") or {}
    python = (runtime.get("client_runtime_mounts") or {}).get("python") or {}
    java_host = regular(Path(str(jdk.get("source") or "")) / "bin/java", "TPC-C Java binary")
    if sha256(java_host) != (runtime.get("toolchain") or {}).get("java_binary_sha256"):
        raise MaterializationError("TPC-C Java binary hash differs")
    defaults_file = regular(defaults_file, "TPC-C defaults file")
    config_file = regular(defaults_file.parent / "tpcc.xml", "TPC-C BenchBase config")
    mounts = [
        {"source": str(bundle), "destination": "/opt/mysql-tpcc-bundle"},
        {"source": str(Path(jdk["source"]).resolve()), "destination": "/opt/mysql-tpcc-jdk"},
        {"source": str(Path(python["source"]).resolve()), "destination": "/opt/mysql-tpcc-python"},
        {"source": str(defaults_file.parent), "destination": "/opt/mysql-tpcc-config"},
    ]
    classpath_container = [str(Path("/opt/mysql-tpcc-bundle") / item)
                           for item in classpath_relative]
    classpath_host = [bundle / item for item in classpath_relative]
    code_paths = [java_host, config_file]
    for index, host_path in enumerate(classpath_host):
        if index == 0:
            for relative in recipe.get("overlay_class_files") or []:
                code_paths.append(regular(bundle / relative, "TPC-C overlay class"))
        else:
            code_paths.append(regular(host_path, "TPC-C classpath file"))
    code_files = {str(path): sha256(path) for path in code_paths}
    metric_path = measurement_root / f"{attempt}.host_metrics.jsonl"
    argv = [
        "/opt/mysql-tpcc-jdk/bin/java", "-cp", os.pathsep.join(classpath_container),
        "com.oltpbenchmark.DBWorkload", "-b", "tpcc", "-c",
        "/opt/mysql-tpcc-config/tpcc.xml", "--execute=true",
    ]
    return {
        "schema_version": "mysql_algorithm_c_debug_qualification_command_v1",
        "bundle_id": TPCC_ID,
        "bundle_manifest_sha256": sha256(manifest_path),
        "mode": mode,
        "qualification_attempt_id": attempt,
        "debug_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "argv": argv,
        "executable_sha256": sha256(java_host),
        "code_files": code_files,
        "environment": {
            "BENCHBASE_FORMAL_STOP_FILE": f"/measurement/{attempt}.benchbase-stop-request",
            "BENCHBASE_FORMAL_WARMUP_SECONDS": "60",
            "BENCHBASE_FORMAL_MAXIMUM_MEASUREMENT_SECONDS": "600",
        },
        "cwd": "/measurement",
        "workload_metric_stream": {
            "schema_version": "mysql_algorithm_c_debug_workload_metric_stream_v1",
            "path": str(metric_path),
        },
        "workload_metric_adapter": {
            "schema_version": "mysql_algorithm_c_debug_workload_metric_adapter_v1",
            "kind": "benchbase_tpcc",
        },
        "workload_process_isolation": {
            "cpuset_cpus": TPCC_CLIENT_CPUSET,
            "cpuset_mems": TPCC_CLIENT_MEMS,
            "root_and_descendants_exact": True,
        },
        "client_container": {
            "name": service_client["name"],
            "container_id": service_client["container_id"],
            "image_id": service_client["image_id"],
            "created": service_client["created"],
            "inspect_config_sha256": service_client["inspect_config_sha256"],
            "required_read_only_mounts": mounts,
        },
        "measurement_output": {
            "host_tmpfs_directory": str(measurement_root),
            "container_tmpfs_destination": "/measurement",
        },
        "paired_peer_workload": None if paired is None else dict(paired),
    }


def token_digest(path: Path, label: str) -> tuple[Path, str]:
    path = regular(path, label + " token")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise MaterializationError(label + " token mode must be 0600")
    token = path.read_text(encoding="ascii").strip()
    if len(token) < 48 or len(token) > 512 or any(char.isspace() for char in token):
        raise MaterializationError(label + " token content differs")
    return path, sha_bytes(token.encode("ascii"))


def authority(command: Mapping[str, Any], command_sha: str, token_sha: str,
              collector_manifest_sha: str, now: float, ttl: int) -> dict[str, Any]:
    return {
        "schema_version": "mysql_algorithm_c_main_agent_qualification_authority_v1",
        "authorized_by": "main_agent",
        "bundle_id": command["bundle_id"],
        "mode": command["mode"],
        "qualification_attempt_id": command["qualification_attempt_id"],
        "main_agent_token_sha256": token_sha,
        "command_spec_sha256": command_sha,
        "collector_bundle_id": COLLECTOR_ID,
        "collector_manifest_sha256": collector_manifest_sha,
        "debug_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "not_before_epoch": now - 1.0,
        "expires_epoch": now + float(ttl) - 1.0,
    }


def reject_static_pid(value: Any, where: str = "output") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            low = str(key).lower()
            if low in STATIC_PID_KEYS or low.endswith("_pid"):
                raise MaterializationError("static PID in " + where + "." + str(key))
            reject_static_pid(child, where + "." + str(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_static_pid(child, f"{where}[{index}]")


def write_sealed(path: Path, value: Mapping[str, Any], mode: int) -> str:
    payload = canonical(value)
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    digest = sha_bytes(payload)
    sidecar = Path(str(path) + ".sha256")
    with sidecar.open("x", encoding="ascii") as stream:
        stream.write(f"{digest}  {path.name}\n")
    os.chmod(path, mode)
    os.chmod(sidecar, mode)
    return digest


def base_args(bundle: Path, database_id: str, client_id: str,
              defaults: Path) -> list[str]:
    return [
        "--bundle", str(bundle), "--container", database_id,
        "--mysql-client-container", client_id,
        "--mysql-defaults-file", str(defaults),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a336-snapshot", type=Path, required=True)
    parser.add_argument("--tpcc-adopted-service", type=Path, required=True)
    parser.add_argument("--tpch-bootstrap-spec", type=Path, required=True)
    parser.add_argument("--tpch-bootstrap-evidence", type=Path, required=True)
    parser.add_argument("--tpch-bundle", type=Path, required=True)
    parser.add_argument("--tpcc-bundle", type=Path, required=True)
    parser.add_argument("--collector-bundle", type=Path, required=True)
    parser.add_argument("--tpcc-defaults-file", type=Path, required=True)
    for label in ("a-solo", "a-paired", "b-solo", "b-paired"):
        parser.add_argument(f"--{label}-token-file", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--valid-seconds", type=int, default=900)
    parser.add_argument("--materialization-id")
    args = parser.parse_args(argv)

    if args.valid_seconds < 240 or args.valid_seconds > 1800:
        raise MaterializationError("authority validity must be 240..1800 seconds")
    now = time.time()
    snapshot_path, snapshot = load_sealed(args.a336_snapshot, "a336 snapshot")
    database, snapshot_client = validate_snapshot(snapshot, now)
    service_path, service = load_sealed(args.tpcc_adopted_service, "TPC-C adopted service")
    service_client = validate_service(service, database, snapshot_client)
    tpch_spec_path, tpch_spec = load_sealed(args.tpch_bootstrap_spec, "TPC-H bootstrap spec")
    tpch_evidence_path, tpch_evidence = load_sealed(args.tpch_bootstrap_evidence,
                                                    "TPC-H bootstrap evidence")
    tpch_bundle, tpch_manifest_path, tpch_manifest = verify_bundle(
        args.tpch_bundle, "TPC-H", TPCH_ID, TPCH_MANIFEST_SHA)
    tpcc_bundle, tpcc_manifest_path, tpcc_manifest = verify_bundle(
        args.tpcc_bundle, "TPC-C", TPCC_ID, TPCC_MANIFEST_SHA)
    collector_bundle, collector_manifest_path, collector_manifest = verify_bundle(
        args.collector_bundle, "shared collector", COLLECTOR_ID, COLLECTOR_MANIFEST_SHA)
    if (
        tpch_spec.get("attempt_token") != TPCH_ATTEMPT
        or (tpch_spec.get("bundle") or {}).get("bundle_id") != TPCH_ID
        or Path(str((tpch_spec.get("bundle") or {}).get("path"))).resolve() != tpch_bundle
    ):
        raise MaterializationError("current TPC-H bootstrap spec differs")
    module_path = regular(tpch_bundle / "mysql_tpch_distributed_v2_authority.py",
                          "TPC-H authority module")
    if (tpch_manifest.get("files") or {}).get("mysql_tpch_distributed_v2_authority.py") != sha256(module_path):
        raise MaterializationError("TPC-H authority module is outside sealed closure")
    module = load_module(module_path, tpch_manifest.get("files") or {})
    identity = module.validate_bootstrap_evidence(tpch_evidence, tpch_spec)
    tpch_runtime_path, tpch_runtime = load_bundle_json(
        tpch_bundle, tpch_manifest, "distributed_runtime_contract.json", "TPC-H runtime")
    tpcc_runtime_path, tpcc_runtime = load_bundle_json(
        tpcc_bundle, tpcc_manifest, "distributed_runtime_contract.json", "TPC-C runtime")
    measurement_root = Path(str((service.get("measurement_bridge") or {}).get("host_path") or ""))
    if (not measurement_root.is_absolute() or not measurement_root.is_dir()
            or (service.get("measurement_bridge") or {}).get("filesystem") != "tmpfs"):
        raise MaterializationError("TPC-C measurement bridge differs")
    tpcc_defaults = regular(args.tpcc_defaults_file, "TPC-C defaults file")
    if tpcc_defaults.parent != Path(str((service.get("client_private_config") or {}).get(
            "authority_path") or "")).parent / "config":
        # Older same-live authorities placed config_materialization.json beside
        # the config directory but did not publish config_path itself.
        expected_sha = (((service.get("client_private_config") or {}).get("files") or {})
                        .get("mysql.cnf") or {}).get("sha256")
        if expected_sha != sha256(tpcc_defaults):
            raise MaterializationError("TPC-C defaults/service binding differs")

    materialization_id = args.materialization_id or secrets.token_hex(12)
    if re.fullmatch(r"[A-Za-z0-9_.-]{12,80}", materialization_id) is None:
        raise MaterializationError("materialization ID differs")
    attempts = {
        "A_solo": f"{TPCH_ATTEMPT}-qualification_solo-{materialization_id}",
        "A_paired": f"{TPCH_ATTEMPT}-qualification_paired-{materialization_id}",
        "B_solo": f"{TPCC_ATTEMPT}-qualification_solo-{materialization_id}",
        "B_paired": f"{TPCC_ATTEMPT}-qualification_paired-{materialization_id}",
    }
    if len(set(attempts.values())) != 4:
        raise AssertionError("attempt IDs are not unique")
    a_root = Path(tpch_spec["paths"]["host_measurement_tmpfs"])
    metric = {
        "A_solo": a_root / "qualification_solo.host_metrics.jsonl",
        "A_paired": a_root / "qualification_paired.host_metrics.jsonl",
        "B_solo": measurement_root / f"{attempts['B_solo']}.host_metrics.jsonl",
        "B_paired": measurement_root / f"{attempts['B_paired']}.host_metrics.jsonl",
    }
    a_solo_raw = module.build_command_spec(tpch_spec, tpch_evidence, "qualification_solo")
    a_paired_raw = module.build_command_spec(
        tpch_spec, tpch_evidence, "qualification_paired",
        peer_binding("tpcc_cpu", attempts["B_paired"], str(metric["B_paired"])))
    commands = {
        "A_solo": replace_attempt(a_solo_raw, attempts["A_solo"], metric["A_solo"], None),
        "A_paired": replace_attempt(
            a_paired_raw, attempts["A_paired"], metric["A_paired"],
            peer_binding("tpcc_cpu", attempts["B_paired"], str(metric["B_paired"]))),
    }
    commands["B_solo"] = build_tpcc_command(
        tpcc_bundle, tpcc_manifest_path, tpcc_manifest, tpcc_runtime, service_client, tpcc_defaults,
        measurement_root, "qualification_solo", attempts["B_solo"], None)
    commands["B_paired"] = build_tpcc_command(
        tpcc_bundle, tpcc_manifest_path, tpcc_manifest, tpcc_runtime, service_client, tpcc_defaults,
        measurement_root, "qualification_paired", attempts["B_paired"],
        peer_binding("tpch_memory", attempts["A_paired"], str(metric["A_paired"])))
    for label, command in commands.items():
        false_boundary(command, label + " command")
        reject_static_pid(command, label + " command")

    token_inputs = {
        "A_solo": args.a_solo_token_file,
        "A_paired": args.a_paired_token_file,
        "B_solo": args.b_solo_token_file,
        "B_paired": args.b_paired_token_file,
    }
    tokens = {label: token_digest(path, label) for label, path in token_inputs.items()}
    if len({str(row[0]) for row in tokens.values()}) != 4 or len({row[1] for row in tokens.values()}) != 4:
        raise MaterializationError("token/authority reuse is prohibited")
    output_root = args.output_root.resolve()
    if output_root.exists() or output_root.is_symlink():
        raise MaterializationError("refusing to reuse an output/authority root")
    output_root.mkdir(parents=True, mode=0o700)
    command_paths: dict[str, Path] = {}
    authority_paths: dict[str, Path] = {}
    command_shas: dict[str, str] = {}
    authority_shas: dict[str, str] = {}
    for label in ("A_solo", "A_paired", "B_solo", "B_paired"):
        command_path = output_root / f"{label}.command.json"
        command_sha = write_sealed(command_path, commands[label], 0o400)
        auth = authority(commands[label], command_sha, tokens[label][1],
                         sha256(collector_manifest_path), now, args.valid_seconds)
        false_boundary(auth, label + " authority")
        reject_static_pid(auth, label + " authority")
        authority_path = output_root / f"{label}.authority.json"
        authority_sha = write_sealed(authority_path, auth, 0o600)
        command_paths[label], authority_paths[label] = command_path, authority_path
        command_shas[label], authority_shas[label] = command_sha, authority_sha

    core_relative = "qualification/mysql_distributed_live_readback_qualification_v1.py"
    core_path = regular(collector_bundle / core_relative, "final shared collector core")
    if (collector_manifest.get("files") or {}).get(core_relative) != sha256(core_path):
        raise MaterializationError("final shared collector core is outside sealed closure")
    collector_core = load_module(core_path)
    envelopes = {
        "A": tpch_runtime["client_resource_envelope"],
        "B": tpcc_runtime["client_resource_envelope"],
    }
    for label in ("A_solo", "A_paired", "B_solo", "B_paired"):
        command = collector_core.validate_command_spec(
            command_paths[label], commands[label]["bundle_id"], commands[label]["mode"],
            envelopes[label[0]])
        token_text = tokens[label][0].read_text(encoding="ascii").strip()
        checked_authority = collector_core.validate_main_agent_authority(
            authority_paths[label], token_text, commands[label]["bundle_id"],
            commands[label]["mode"], command_paths[label])
        if (
            checked_authority.get("collector_bundle_id") != COLLECTOR_ID
            or checked_authority.get("collector_manifest_sha256") != sha256(collector_manifest_path)
            or checked_authority["expires_epoch"] - checked_authority["not_before_epoch"] > 1800
        ):
            raise MaterializationError(label + " final collector authority binding differs")
        reject_static_pid(command, label + " validated command")
        reject_static_pid(checked_authority, label + " validated authority")

    tpch_database_id = full_id(identity.get("database_container_id"), "TPC-H database container")
    tpch_client_id = full_id(identity.get("client_container_id"), "TPC-H client container")
    base = {
        "A": base_args(tpch_bundle, tpch_database_id, tpch_client_id,
                       Path(tpch_spec["mysql_defaults_file"])),
        "B": base_args(tpcc_bundle, database["container_id"], snapshot_client["container_id"],
                       tpcc_defaults),
    }
    args_map: dict[str, Any] = {
        "schema_version": "mysql_tpcc_v16_current_args_v1",
        "status": "SEALED_FRESH_FOUR_FLOW_ARGS_NOT_EXECUTED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "collector_bundle_id": COLLECTOR_ID,
        "base_collect_only_args": base,
        "qualification_args": {},
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
    }
    for label in ("A_solo", "A_paired", "B_solo", "B_paired"):
        lane = label[0]
        args_map["qualification_args"][label] = base[lane] + [
            "--main-agent-token-file", str(tokens[label][0]),
            "--main-agent-authority", str(authority_paths[label]),
            "--command-spec", str(command_paths[label]),
        ]
    reject_static_pid(args_map)
    args_map_sha = write_sealed(output_root / "current_args.json", args_map, 0o400)
    receipt = {
        "schema_version": "mysql_tpcc_v16_current_args_materialization_receipt_v1",
        "status": "PASS_OFFLINE_FRESH_SPECS_AUTHORITIES_NOT_EXECUTED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "materialization_id": materialization_id,
        "ttl_seconds": args.valid_seconds,
        "inputs": {
            "a336_snapshot_sha256": sha256(snapshot_path),
            "tpcc_adopted_service_sha256": sha256(service_path),
            "tpch_bootstrap_spec_sha256": sha256(tpch_spec_path),
            "tpch_bootstrap_evidence_sha256": sha256(tpch_evidence_path),
            "tpch_manifest_sha256": sha256(tpch_manifest_path),
            "tpch_runtime_sha256": sha256(tpch_runtime_path),
            "tpcc_manifest_sha256": sha256(tpcc_manifest_path),
            "collector_manifest_sha256": sha256(collector_manifest_path),
        },
        "attempts": attempts,
        "command_spec_sha256": command_shas,
        "authority_sha256": authority_shas,
        "current_args_sha256": args_map_sha,
        "static_pid_authority_prohibited": True,
        "authority_reuse_prohibited": True,
        "secret_material_copied_to_outputs": False,
        "debug_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
    }
    reject_static_pid(receipt)
    receipt_sha = write_sealed(output_root / "materialization_receipt.json", receipt, 0o400)
    print(json.dumps({
        "status": receipt["status"], "output_root": str(output_root),
        "current_args": str(output_root / "current_args.json"),
        "receipt_sha256": receipt_sha, "accepted_round_authorized": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (MaterializationError, OSError, UnicodeError, ValueError, KeyError) as exc:
        sys.stderr.write(f"NOT_READY: {exc}\n")
        raise SystemExit(2)
