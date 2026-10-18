#!/usr/bin/env python3
"""Issue a unique solo attempt from one fresh sealed scenario solo template."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import secrets
import stat


HERE = Path(__file__).resolve().parent


class ProducerError(RuntimeError):
    pass


def load_paired():
    path = HERE / "mysql_unified_qualification_paired_authority_v2.py"
    spec = importlib.util.spec_from_file_location("unified_paired_v2", path)
    if spec is None or spec.loader is None:
        raise ProducerError("paired producer closure is absent")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write(path, value, mode=0o400):
    payload = canonical(value)
    path.write_bytes(payload)
    path.chmod(mode)
    sidecar = path.with_name(path.name + ".sha256")
    sidecar.write_text(sha_bytes(payload) + "  " + path.name + "\n", encoding="ascii")
    sidecar.chmod(0o400)


def produce(args):
    paired = load_paired()
    bundle, manifest = paired.verify_bundle(args.scenario_bundle)
    collector_bundle, collector_manifest = paired.verify_bundle(args.collector_bundle)
    collector = args.collector.resolve(strict=True)
    try:
        relative = collector.relative_to(collector_bundle).as_posix()
    except ValueError as exc:
        raise ProducerError("collector is outside sealed collector bundle") from exc
    if (relative not in collector_manifest["files"]
            or collector_manifest["files"][relative] != sha256(collector)):
        raise ProducerError("collector is outside sealed collector closure")
    readback = paired.validate_readback(args.own_readback, "own readback")
    scenario = readback["scenario"]
    expected = paired.EXPECTED[scenario]
    if readback.get("bundle_id") != manifest["bundle_id"]:
        raise ProducerError("readback does not bind scenario bundle")
    command = paired.load(args.source_solo_command, "source solo command")
    authority = paired.load(args.source_solo_authority, "source solo authority")
    now = datetime.now(timezone.utc).timestamp()
    client = command.get("client_container") or {}
    transport = readback.get("database_counter_transport") or {}
    if (command.get("schema_version") != "mysql_algorithm_c_debug_qualification_command_v1"
            or command.get("mode") != "qualification_solo"
            or command.get("bundle_id") != manifest["bundle_id"]
            or command.get("debug_only") is not True
            or command.get("capture_authorized") is not False
            or command.get("formal_r0_authorized") is not False
            or command.get("formal_authorized") is not False
            or command.get("accepted_round_authorized") is not False
            or (command.get("workload_process_isolation") or {}).get("cpuset_cpus") != expected["client"]
            or client.get("name") != transport.get("client_container_name")
            or client.get("container_id") != transport.get("client_container_id")
            or client.get("inspect_config_sha256") != transport.get("client_inspect_config_sha256")
            or authority.get("schema_version") != "mysql_algorithm_c_main_agent_qualification_authority_v1"
            or authority.get("mode") != "qualification_solo"
            or authority.get("bundle_id") != manifest["bundle_id"]
            or authority.get("qualification_attempt_id") != command.get("qualification_attempt_id")
            or authority.get("command_spec_sha256") != sha256(args.source_solo_command)
            or authority.get("debug_only") is not True
            or authority.get("capture_authorized") is not False
            or authority.get("formal_r0_authorized") is not False
            or authority.get("formal_authorized") is not False
            or authority.get("accepted_round_authorized") is not False
            or float(authority.get("expires_epoch") or 0) <= now):
        raise ProducerError("fresh source solo template/authority differs")
    attempt = args.qualification_attempt_id
    if paired.SAFE_ID.fullmatch(attempt) is None:
        raise ProducerError("unique solo attempt ID differs")
    host_root = Path((command.get("measurement_output") or {}).get("host_tmpfs_directory", ""))
    container_root = Path((command.get("measurement_output") or {}).get("container_tmpfs_destination", ""))
    if not host_root.is_absolute() or not host_root.is_dir() or not container_root.is_absolute():
        raise ProducerError("solo measurement root differs")
    host_stream = host_root / ("qualification_solo_%s.host_metrics.jsonl" % attempt)
    native_stream = str(container_root / ("qualification_solo_%s.native.jsonl" % attempt))
    evidence = host_root / ("qualification_solo_%s_evidence.json" % attempt)
    for path in (host_stream, evidence):
        if path.exists() or path.is_symlink():
            raise ProducerError("unique solo output already exists")
    issued = datetime.now(timezone.utc)
    unique = dict(command)
    unique.update({
        "qualification_attempt_id": attempt,
        "argv": paired.replace_argv(command, attempt, native_stream),
        "environment": dict(command.get("environment") or {}, QUALIFICATION_ROLE="solo"),
        "workload_metric_stream": {
            "schema_version": "mysql_algorithm_c_debug_workload_metric_stream_v1",
            "path": str(host_stream),
        },
        "sealed_solo_producer": "mysql_unified_qualification_solo_authority_v1",
        "source_solo_command_sha256": sha256(args.source_solo_command),
        "source_solo_authority_sha256": sha256(args.source_solo_authority),
        "own_live_readback_sha256": sha256(args.own_readback),
        "collector_bundle_id": collector_manifest["bundle_id"],
        "collector_manifest_sha256": sha256(collector_bundle / "bundle_manifest.json"),
        "collector_entry_sha256": sha256(collector),
    })
    output = Path(args.output_directory)
    if output.exists() or output.is_symlink():
        raise ProducerError("solo output directory already exists")
    output.mkdir(parents=True, mode=0o700)
    token = secrets.token_urlsafe(48)
    token_path = output / "main_agent_token"
    token_path.write_text(token + "\n", encoding="ascii")
    token_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    command_path = output / "command.json"
    write(command_path, unique)
    solo_authority = {
        "schema_version": "mysql_algorithm_c_main_agent_qualification_authority_v1",
        "authorized_by": "main_agent", "bundle_id": manifest["bundle_id"],
        "mode": "qualification_solo", "qualification_attempt_id": attempt,
        "main_agent_token_sha256": sha_bytes(token.encode("utf-8")),
        "command_spec_sha256": sha256(command_path), "debug_only": True,
        "capture_authorized": False, "formal_r0_authorized": False,
        "formal_authorized": False, "accepted_round_authorized": False,
        "not_before_epoch": issued.timestamp(),
        "expires_epoch": (issued + timedelta(seconds=args.ttl_seconds)).timestamp(),
        "producer": "mysql_unified_qualification_solo_authority_v1",
        "source_solo_authority_sha256": sha256(args.source_solo_authority),
        "collector_bundle_id": collector_manifest["bundle_id"],
        "collector_manifest_sha256": sha256(collector_bundle / "bundle_manifest.json"),
    }
    authority_path = output / "main_agent_authority.json"
    write(authority_path, solo_authority)
    launch = {
        "schema_version": "mysql_unified_solo_qualification_launch_v1",
        "status": "SEALED_NOT_EXECUTED", "scenario": scenario,
        "collector": str(collector),
        "database_container": readback["container"]["container_name"].lstrip("/"),
        "client_container": client["name"], "output": str(evidence),
        "command_spec": str(command_path),
        "main_agent_authority": str(authority_path),
        "main_agent_token_file": str(token_path),
        "qualification_credit": 0, "r0_credit": 0,
        "accepted_rounds_credit": 0,
    }
    write(output / "launch.json", launch)
    return {"status": "SEALED_UNIQUE_SOLO_AUTHORITY_NOT_EXECUTED",
            "output": str(output), "scenario": scenario,
            "qualification_attempt_id": attempt,
            "expires_epoch": solo_authority["expires_epoch"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario-bundle", type=Path, required=True)
    parser.add_argument("--collector-bundle", type=Path, required=True)
    parser.add_argument("--collector", type=Path, required=True)
    parser.add_argument("--own-readback", type=Path, required=True)
    parser.add_argument("--source-solo-command", type=Path, required=True)
    parser.add_argument("--source-solo-authority", type=Path, required=True)
    parser.add_argument("--qualification-attempt-id", required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--ttl-seconds", type=int, default=900)
    args = parser.parse_args()
    if args.ttl_seconds < 180 or args.ttl_seconds > 900:
        parser.error("--ttl-seconds must be within 180..900")
    print(json.dumps(produce(args), sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (ProducerError, OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)
