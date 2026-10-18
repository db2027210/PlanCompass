#!/usr/bin/env python3
"""Amend a sealed TPC-H qualification spec with live schema and fresh paths."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import time


DOCKER = ("docker", "--host", "unix:///run/ra-docker-mysql-tpch-v1.sock")


class RenderError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            digest.update(block)
    return digest.hexdigest()


def load_sealed(path, label):
    path = Path(path).resolve(strict=True)
    sidecar = Path(str(path) + ".sha256")
    if (
        path.is_symlink()
        or not path.is_file()
        or sidecar.is_symlink()
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii", errors="strict")
        != "%s  %s\n" % (sha256(path), path.name)
    ):
        raise RenderError("%s seal differs" % label)
    value = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    if not isinstance(value, dict):
        raise RenderError("%s must be an object" % label)
    return value


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sidecar = Path(str(path) + ".sha256")
    if path.exists() or sidecar.exists():
        raise RenderError("refusing to overwrite qualification spec")
    temporary = path.with_name(".%s.%s" % (path.name, secrets.token_hex(6)))
    temporary.write_bytes(canonical(value))
    os.chmod(str(temporary), 0o600)
    os.replace(str(temporary), str(path))
    sidecar.write_text("%s  %s\n" % (sha256(path), path.name), encoding="ascii")
    os.chmod(str(sidecar), 0o600)


def read_token(path):
    path = Path(path).resolve(strict=True)
    status = path.stat()
    if (
        path.is_symlink()
        or not path.is_file()
        or status.st_uid != os.getuid()
        or stat.S_IMODE(status.st_mode) != 0o600
    ):
        raise RenderError("main-agent token must be current-user 0600")
    value = path.read_text(encoding="ascii", errors="strict").strip()
    if (
        len(value) < 32
        or len(value) > 512
        or any(character.isspace() for character in value)
    ):
        raise RenderError("main-agent token differs")
    return value


def live_databases(client, defaults_file, port):
    completed = subprocess.run(
        list(DOCKER)
        + [
            "exec", "--user", "1008:1008", client,
            "/usr/bin/mysql",
            "--defaults-extra-file=%s" % defaults_file,
            "--user=root", "--protocol=TCP", "--host=127.0.0.1",
            "--port=%d" % port, "--batch", "--raw", "--skip-column-names",
            "--execute", "SHOW DATABASES",
        ],
        universal_newlines=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    if completed.returncode:
        raise RenderError("live TPC-H schema readback failed")
    return sorted(line.strip() for line in completed.stdout.splitlines() if line.strip())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--base-command", type=Path, required=True)
    parser.add_argument("--client-container", required=True)
    parser.add_argument("--sql-adapter", type=Path, required=True)
    parser.add_argument("--qualification-attempt-id", required=True)
    parser.add_argument(
        "--mode",
        choices=("qualification_solo", "qualification_paired"),
        required=True,
    )
    parser.add_argument("--peer-binding", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--authority-output", type=Path, required=True)
    parser.add_argument("--main-agent-token-file", type=Path, required=True)
    parser.add_argument("--valid-seconds", type=int, default=1800)
    args = parser.parse_args()
    if not 300 <= args.valid_seconds <= 3600:
        raise RenderError("qualification TTL must be 300..3600 seconds")
    if len(args.qualification_attempt_id) < 32:
        raise RenderError("qualification attempt ID differs")
    bundle = args.bundle.resolve(strict=True)
    manifest = load_sealed(bundle / "bundle_manifest.json", "bundle manifest")
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    if (
        bundle.name != bundle_id
        or hashlib.sha256(canonical(unsigned)).hexdigest() != bundle_id
        or manifest.get("schema_version")
        != "mysql_tpch_v23_recalibrated_successor_v3"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
    ):
        raise RenderError("target TPC-H bundle identity differs")
    spec = load_sealed(args.base_command, "base qualification command")
    argv = list(spec.get("argv") or [])
    if (
        spec.get("schema_version")
        != "mysql_algorithm_c_debug_qualification_command_v1"
        or spec.get("bundle_id") != bundle_id
        or spec.get("bundle_manifest_sha256")
        != sha256(bundle / "bundle_manifest.json")
        or spec.get("mode") != "qualification_solo"
        or spec.get("paired_peer_workload") is not None
        or spec.get("debug_only") is not True
        or spec.get("formal_r0_authorized") is not False
        or spec.get("formal_authorized") is not False
        or spec.get("accepted_round_authorized") is not False
        or (spec.get("client_container") or {}).get("name")
        != args.client_container
    ):
        raise RenderError("base qualification authority differs")
    adapter = args.sql_adapter.resolve(strict=True)
    adapter_bundle = adapter.parents[1]
    adapter_manifest = load_sealed(
        adapter_bundle / "bundle_manifest.json", "SQL adapter bundle manifest"
    )
    adapter_unsigned = dict(adapter_manifest)
    adapter_bundle_id = adapter_unsigned.pop("bundle_id", None)
    adapter_relative = str(adapter.relative_to(adapter_bundle))
    if (
        adapter.is_symlink()
        or not adapter.is_file()
        or adapter_bundle.name != adapter_bundle_id
        or hashlib.sha256(canonical(adapter_unsigned)).hexdigest()
        != adapter_bundle_id
        or adapter_manifest.get("schema_version")
        != "mysql_sql_debug_metric_adapter_bundle_v2"
        or adapter_manifest.get("native_sql_metric_shape")
        != "stable_query_duration_ms"
        or (adapter_manifest.get("files") or {}).get(adapter_relative)
        != sha256(adapter)
        or adapter_manifest.get("formal_launch_authorized") is not False
        or adapter_manifest.get("accepted_round_authorized") is not False
    ):
        raise RenderError("schema-stable SQL adapter identity differs")
    attempt_indexes = [i for i, value in enumerate(argv) if value.startswith("--attempt-id=")]
    database_indexes = [i for i, value in enumerate(argv) if value.startswith("--database=")]
    metric_switches = [i for i, value in enumerate(argv) if value == "--metric-stream"]
    defaults = [value.split("=", 1)[1] for value in argv if value.startswith("--defaults-extra-file=")]
    ports = [int(value.split("=", 1)[1]) for value in argv if value.startswith("--port=")]
    duration_indexes = [
        i for i, value in enumerate(argv) if value.startswith("--duration-seconds=")
    ]
    if (
        len(attempt_indexes) != 1
        or len(database_indexes) != 1
        or argv[database_indexes[0]] != "--database=tpch"
        or len(metric_switches) != 1
        or metric_switches[0] + 1 >= len(argv)
        or len(defaults) != 1
        or len(ports) != 1
        or len(duration_indexes) != 1
        or argv[duration_indexes[0]] != "--duration-seconds=240"
    ):
        raise RenderError("base TPC-H argv differs")
    old_adapter = argv[1]
    if (
        old_adapter
        != str(bundle / "qualification/mysql_sql_debug_metric_adapter_v1.py")
        or old_adapter not in (spec.get("code_files") or {})
    ):
        raise RenderError("base SQL adapter binding differs")
    databases = live_databases(args.client_container, defaults[0], ports[0])
    if "tpch" in databases or "tpch_sf10" not in databases:
        raise RenderError("live TPC-H schema identity is ambiguous")
    metric_root = Path(spec["measurement_output"]["host_tmpfs_directory"])
    host_metric = metric_root / (args.qualification_attempt_id + ".host_metrics.jsonl")
    container_metric = "/measurement/%s.native.jsonl" % args.qualification_attempt_id
    if host_metric.exists():
        raise RenderError("fresh host metric path already exists")
    argv[attempt_indexes[0]] = "--attempt-id=%s" % args.qualification_attempt_id
    argv[1] = str(adapter)
    argv[database_indexes[0]] = "--database=tpch_sf10"
    argv[duration_indexes[0]] = "--duration-seconds=240"
    argv[metric_switches[0] + 1] = container_metric
    result = dict(spec)
    result["mode"] = args.mode
    result["qualification_attempt_id"] = args.qualification_attempt_id
    result["argv"] = argv
    code_files = dict(spec["code_files"])
    code_files.pop(old_adapter)
    code_files[str(adapter)] = sha256(adapter)
    result["code_files"] = dict(sorted(code_files.items()))
    result["workload_metric_stream"] = {
        "schema_version": "mysql_algorithm_c_debug_workload_metric_stream_v1",
        "path": str(host_metric),
    }
    result["live_database_schema_binding"] = {
        "schema_version": "mysql_tpch_live_schema_binding_v1",
        "database": "tpch_sf10",
        "show_databases": databases,
        "readback_only": True,
    }
    result["native_sql_adapter_binding"] = {
        "schema_version": "mysql_sql_debug_metric_adapter_binding_v2",
        "bundle_id": adapter_bundle_id,
        "path": str(adapter),
        "sha256": sha256(adapter),
        "metric_shape": "stable_query_duration_ms",
        "workload_duration_seconds": 240,
        "collector_sample_window_seconds": 180,
    }
    if args.mode == "qualification_paired":
        if args.peer_binding is None:
            raise RenderError("paired qualification requires peer binding")
        result["paired_peer_workload"] = load_sealed(
            args.peer_binding, "peer workload binding"
        )
    elif args.peer_binding is not None:
        raise RenderError("solo qualification rejects peer binding")
    else:
        result["paired_peer_workload"] = None
    atomic_json(args.output, result)
    token = read_token(args.main_agent_token_file)
    now = time.time()
    authority = {
        "schema_version":
            "mysql_algorithm_c_main_agent_qualification_authority_v1",
        "authorized_by": "main_agent",
        "bundle_id": bundle_id,
        "mode": args.mode,
        "qualification_attempt_id": args.qualification_attempt_id,
        "main_agent_token_sha256": hashlib.sha256(
            token.encode("ascii")
        ).hexdigest(),
        "command_spec_sha256": sha256(args.output),
        "debug_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "not_before_epoch": now - 1.0,
        "expires_epoch": now + float(args.valid_seconds),
        "target_manifest_sha256": sha256(bundle / "bundle_manifest.json"),
        "sql_adapter_manifest_sha256": sha256(
            adapter_bundle / "bundle_manifest.json"
        ),
    }
    atomic_json(args.authority_output, authority)
    print(
        json.dumps(
            {
                "status":
                    "SEALED_FRESH_TPCH_QUALIFICATION_SPEC_AND_AUTHORITY_NOT_LAUNCHED",
                "output": str(args.output),
                "authority_output": str(args.authority_output),
                "mode": args.mode,
                "qualification_attempt_id": args.qualification_attempt_id,
                "database": "tpch_sf10",
                "accepted_round_authorized": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (RenderError, OSError, UnicodeError, ValueError, KeyError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
