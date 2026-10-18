#!/usr/bin/env python3
"""Seal a paired qualification command from one sealed solo authority."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat


EXPECTED = {
    "job_memory": {"database": "18-20,54-56", "client": "25-27,61-63", "daemon": "16-17,52-53"},
    "tpch_memory": {"database": "32-33,68-69", "client": "0-7,36-43", "daemon": "32-33,68-69"},
    "tpcc_cpu": {"database": "21-24,57-60", "client": "8-15,44-51", "daemon": "34-35,70-71"},
}
ALLOWED_PAIRS = {
    frozenset(("job_memory", "tpch_memory")),
    frozenset(("job_memory", "tpcc_cpu")),
    frozenset(("tpch_memory", "tpcc_cpu")),
}
HEX64 = re.compile(r"[0-9a-f]{64}$")
SAFE_ID = re.compile(r"[A-Za-z0-9_.-]{32,128}$")


class ProducerError(RuntimeError):
    pass


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


def load(path, label):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ProducerError(label + " is absent or symlinked")
    sidecar = path.with_name(path.name + ".sha256")
    required = sha256(path) + "  " + path.name + "\n"
    if (sidecar.is_symlink() or not sidecar.is_file()
            or sidecar.read_text(encoding="ascii") != required):
        raise ProducerError(label + " sidecar differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ProducerError(label + " must be an object")
    return value


def cpus(value):
    result = set()
    for part in value.split(","):
        ends = [int(item) for item in part.split("-")]
        result.update(range(ends[0], ends[-1] + 1))
    return result


def verify_bundle(bundle):
    bundle = Path(bundle).resolve(strict=True)
    manifest = json.loads((bundle / "bundle_manifest.json").read_text(encoding="utf-8"))
    files = manifest.get("files")
    if (bundle.name != manifest.get("bundle_id") or not isinstance(files, dict)
            or not files or not HEX64.fullmatch(bundle.name)):
        raise ProducerError("scenario bundle identity differs")
    for relative, expected in files.items():
        path = bundle / relative
        if path.is_symlink() or not path.is_file() or sha256(path) != expected:
            raise ProducerError("scenario bundle closure differs: " + relative)
    return bundle, manifest


def validate_readback(path, label):
    value = load(path, label)
    scenario = value.get("scenario")
    expected = EXPECTED.get(scenario)
    if (value.get("schema_version") != "mysql_algorithm_c_distributed_live_readback_v1"
            or value.get("status") != "PASS_LIVE_READBACK_NOT_LAUNCH_AUTHORITY"
            or value.get("hostname") != "node04" or expected is None
            or value.get("formal_scope") != "mysql_only"
            or value.get("cross_engine_comparison_prohibited") is not True
            or value.get("capture_authorized") is not False
            or value.get("formal_r0_authorized") is not False
            or value.get("formal_launch_authorized") is not False
            or value.get("accepted_round_authorized") is not False
            or not isinstance(value.get("bundle_id"), str)
            or not HEX64.fullmatch(value.get("bundle_id"))
            or (value.get("resource_envelope") or {}).get("cpuset_cpus") != expected["database"]
            or (value.get("client_resource_envelope") or {}).get("cpuset_cpus") != expected["client"]):
        raise ProducerError(label + " live authority differs")
    return value


def replace_argv(command, attempt_id, native_stream):
    argv = list(command.get("argv") or [])
    old_attempt_rows = [i for i, value in enumerate(argv) if value.startswith("--attempt-id=")]
    metric_rows = [i for i, value in enumerate(argv) if value == "--metric-stream"]
    if len(old_attempt_rows) != 1 or len(metric_rows) != 1 or metric_rows[0] + 1 >= len(argv):
        raise ProducerError("solo workload argv binding differs")
    argv[old_attempt_rows[0]] = "--attempt-id=" + attempt_id
    argv[metric_rows[0] + 1] = native_stream
    return argv


def produce(args):
    bundle, manifest = verify_bundle(args.scenario_bundle)
    own = validate_readback(args.own_readback, "own readback")
    peer = validate_readback(args.peer_readback, "peer readback")
    own_scenario = own["scenario"]; peer_scenario = peer["scenario"]
    if (frozenset((own_scenario, peer_scenario)) not in ALLOWED_PAIRS
            or own_scenario == peer_scenario):
        raise ProducerError("scenario pair is not one frozen node04 pair")
    if own.get("bundle_id") != manifest["bundle_id"]:
        raise ProducerError("own readback does not bind scenario bundle")
    own_union = cpus(EXPECTED[own_scenario]["database"]) | cpus(EXPECTED[own_scenario]["client"]) | cpus(EXPECTED[own_scenario]["daemon"])
    peer_union = cpus(EXPECTED[peer_scenario]["database"]) | cpus(EXPECTED[peer_scenario]["client"]) | cpus(EXPECTED[peer_scenario]["daemon"])
    if not own_union.isdisjoint(peer_union):
        raise ProducerError("frozen pair CPU union overlaps")
    if ((own.get("storage") or {}).get("major_minor")
            == (peer.get("storage") or {}).get("major_minor")
            or own.get("mysql_port") == peer.get("mysql_port")):
        raise ProducerError("live pair device or port overlaps")
    solo = load(args.solo_command, "solo command")
    solo_authority = load(args.solo_authority, "solo authority")
    now = datetime.now(timezone.utc).timestamp()
    if (solo.get("schema_version") != "mysql_algorithm_c_debug_qualification_command_v1"
            or solo.get("mode") != "qualification_solo"
            or solo.get("bundle_id") != manifest["bundle_id"]
            or solo.get("debug_only") is not True
            or solo.get("capture_authorized") is not False
            or solo.get("formal_r0_authorized") is not False
            or solo.get("formal_authorized") is not False
            or solo.get("accepted_round_authorized") is not False
            or (solo.get("workload_process_isolation") or {}).get("cpuset_cpus") != EXPECTED[own_scenario]["client"]
            or solo_authority.get("schema_version") != "mysql_algorithm_c_main_agent_qualification_authority_v1"
            or solo_authority.get("mode") != "qualification_solo"
            or solo_authority.get("bundle_id") != manifest["bundle_id"]
            or solo_authority.get("qualification_attempt_id") != solo.get("qualification_attempt_id")
            or solo_authority.get("debug_only") is not True
            or solo_authority.get("capture_authorized") is not False
            or solo_authority.get("formal_r0_authorized") is not False
            or solo_authority.get("formal_authorized") is not False
            or solo_authority.get("accepted_round_authorized") is not False
            or solo_authority.get("command_spec_sha256") != sha256(args.solo_command)
            or float(solo_authority.get("expires_epoch") or 0) <= now):
        raise ProducerError("sealed solo producer input differs or is stale")
    if (not SAFE_ID.fullmatch(args.qualification_attempt_id)
            or not SAFE_ID.fullmatch(args.peer_qualification_attempt_id)
            or args.qualification_attempt_id == args.peer_qualification_attempt_id):
        raise ProducerError("paired qualification attempt identity differs")
    host_root = Path((solo.get("measurement_output") or {}).get("host_tmpfs_directory", ""))
    container_root = Path((solo.get("measurement_output") or {}).get("container_tmpfs_destination", ""))
    peer_stream = Path(args.peer_workload_metric_stream)
    if (not host_root.is_absolute() or not host_root.is_dir()
            or not container_root.is_absolute() or not peer_stream.is_absolute()
            or not peer_stream.parent.is_dir() or peer_stream.is_symlink()):
        raise ProducerError("paired metric stream roots differ")
    slug = peer_scenario.replace("_", "-")
    own_host_stream = host_root / ("qualification_paired_%s.host_metrics.jsonl" % slug)
    native_stream = str(container_root / ("qualification_paired_%s.native.jsonl" % slug))
    if own_host_stream.exists() or own_host_stream.is_symlink():
        raise ProducerError("paired own metric stream already exists")
    command = dict(solo)
    command.update({
        "mode": "qualification_paired",
        "qualification_attempt_id": args.qualification_attempt_id,
        "argv": replace_argv(solo, args.qualification_attempt_id, native_stream),
        "environment": dict(solo.get("environment") or {}, QUALIFICATION_ROLE="paired"),
        "workload_metric_stream": {"schema_version": "mysql_algorithm_c_debug_workload_metric_stream_v1", "path": str(own_host_stream)},
        "paired_peer_workload": {
            "schema_version": "mysql_algorithm_c_paired_peer_workload_binding_v1",
            "scenario": peer_scenario,
            "qualification_attempt_id": args.peer_qualification_attempt_id,
            "workload_metric_stream_path": str(peer_stream),
        },
        "sealed_paired_producer": "mysql_job_v17_qualification_paired_authority_v1",
        "source_solo_command_sha256": sha256(args.solo_command),
        "source_solo_authority_sha256": sha256(args.solo_authority),
        "own_live_readback_sha256": sha256(args.own_readback),
        "peer_live_readback_sha256": sha256(args.peer_readback),
    })
    output = Path(args.output_directory)
    if output.exists() or output.is_symlink():
        raise ProducerError("paired output directory already exists")
    output.mkdir(parents=True, mode=0o700)
    token = secrets.token_urlsafe(48)
    token_path = output / "main_agent_token"
    token_path.write_text(token + "\n", encoding="ascii"); token_path.chmod(0o600)
    command_path = output / "command.json"
    command_payload = canonical(command); command_path.write_bytes(command_payload); command_path.chmod(0o400)
    (output / "command.json.sha256").write_text(sha_bytes(command_payload) + "  command.json\n", encoding="ascii"); (output / "command.json.sha256").chmod(0o400)
    issued = datetime.now(timezone.utc)
    authority = {
        "schema_version": "mysql_algorithm_c_main_agent_qualification_authority_v1",
        "authorized_by": "main_agent", "bundle_id": manifest["bundle_id"],
        "mode": "qualification_paired", "qualification_attempt_id": args.qualification_attempt_id,
        "main_agent_token_sha256": sha_bytes(token.encode("utf-8")),
        "command_spec_sha256": sha256(command_path), "debug_only": True,
        "capture_authorized": False, "formal_r0_authorized": False,
        "formal_authorized": False, "accepted_round_authorized": False,
        "not_before_epoch": issued.timestamp(),
        "expires_epoch": (issued + timedelta(seconds=args.ttl_seconds)).timestamp(),
        "producer": "mysql_job_v17_qualification_paired_authority_v1",
        "source_solo_authority_sha256": sha256(args.solo_authority),
    }
    authority_path = output / "main_agent_authority.json"
    authority_payload = canonical(authority); authority_path.write_bytes(authority_payload); authority_path.chmod(0o400)
    (output / "main_agent_authority.json.sha256").write_text(sha_bytes(authority_payload) + "  main_agent_authority.json\n", encoding="ascii"); (output / "main_agent_authority.json.sha256").chmod(0o400)
    launch = {
        "schema_version": "mysql_job_v17_paired_qualification_launch_v1",
        "status": "SEALED_NOT_EXECUTED", "scenario": own_scenario,
        "peer_scenario": peer_scenario, "collector": str(args.collector),
        "database_container": own["container"]["container_name"].lstrip("/"),
        "client_container": command["client_container"]["name"],
        "peer_container": peer["container"]["container_name"].lstrip("/"),
        "peer_readback": str(Path(args.peer_readback).resolve()),
        "output": str(host_root / ("qualification_paired_%s_evidence.json" % slug)),
        "command_spec": str(command_path), "main_agent_authority": str(authority_path),
        "main_agent_token_file": str(token_path), "qualification_credit": 0,
        "r0_credit": 0, "accepted_rounds_credit": 0,
    }
    launch_path = output / "launch.json"; launch_payload = canonical(launch)
    launch_path.write_bytes(launch_payload); launch_path.chmod(0o400)
    (output / "launch.json.sha256").write_text(sha_bytes(launch_payload) + "  launch.json\n", encoding="ascii"); (output / "launch.json.sha256").chmod(0o400)
    return {"status": "SEALED_PAIRED_AUTHORITY_NOT_EXECUTED", "output": str(output), "scenario": own_scenario, "peer_scenario": peer_scenario, "expires_epoch": authority["expires_epoch"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario-bundle", type=Path, required=True)
    parser.add_argument("--collector", type=Path, required=True)
    parser.add_argument("--own-readback", type=Path, required=True)
    parser.add_argument("--peer-readback", type=Path, required=True)
    parser.add_argument("--solo-command", type=Path, required=True)
    parser.add_argument("--solo-authority", type=Path, required=True)
    parser.add_argument("--qualification-attempt-id", required=True)
    parser.add_argument("--peer-qualification-attempt-id", required=True)
    parser.add_argument("--peer-workload-metric-stream", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--ttl-seconds", type=int, default=900)
    args = parser.parse_args()
    if args.ttl_seconds < 180 or args.ttl_seconds > 900:
        parser.error("--ttl-seconds must be within 180..900")
    bundle, manifest = verify_bundle(args.scenario_bundle)
    collector = args.collector.resolve(strict=True)
    try: relative = collector.relative_to(bundle).as_posix()
    except ValueError: raise ProducerError("collector is outside scenario bundle")
    if (relative not in manifest["files"] or manifest["files"][relative] != sha256(collector)):
        raise ProducerError("collector is outside sealed scenario closure")
    print(json.dumps(produce(args), sort_keys=True))


if __name__ == "__main__":
    try: main()
    except (ProducerError, OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)
