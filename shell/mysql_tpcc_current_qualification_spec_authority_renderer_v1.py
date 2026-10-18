#!/usr/bin/env python3
"""Render debug-only TPC-C qualification specs for the adopted live bundle.

The renderer is Python 3.6 compatible and performs no workload or container
mutation.  The target bundle, adopted-service authority, historical sealed
qualification command, client immutable inspect projection, metric tmpfs, and
short-lived main-agent token are all bound into the emitted documents.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mysql_tpch_client_immutable_identity_v1 import (  # noqa: E402
    client_container_immutable_projection,
)


DOCKER = ("docker", "--host", "unix:///run/ra-docker-mysql-tpcc-v1.sock")


class RenderError(RuntimeError):
    pass


def canonical(value):
    return (
        json.dumps(
            value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def load_object(path, label, sidecar=True):
    path = Path(path).resolve(strict=True)
    if path.is_symlink() or not path.is_file():
        raise RenderError("%s must be a regular file" % label)
    if sidecar:
        sidecar_path = Path(str(path) + ".sha256")
        expected = "%s  %s\n" % (sha256(path), path.name)
        if (
            sidecar_path.is_symlink()
            or not sidecar_path.is_file()
            or sidecar_path.read_text(encoding="ascii", errors="strict")
            != expected
        ):
            raise RenderError("%s sidecar differs" % label)
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (UnicodeError, ValueError) as exc:
        raise RenderError("%s JSON differs" % label) from exc
    if not isinstance(value, dict):
        raise RenderError("%s must be an object" % label)
    return path, value


def validate_bundle(bundle):
    bundle = Path(bundle).resolve(strict=True)
    manifest_path, manifest = load_object(
        bundle / "bundle_manifest.json", "target bundle manifest"
    )
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    if (
        bundle.name != bundle_id
        or sha_bytes(canonical(unsigned)) != bundle_id
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
    ):
        raise RenderError("target bundle identity/scope differs")
    for relative, expected in (manifest.get("files") or {}).items():
        path = bundle / relative
        if path.is_symlink() or not path.is_file() or sha256(path) != expected:
            raise RenderError("target bundle closure differs: %s" % relative)
    return bundle, bundle_id, sha256(manifest_path)


def inspect_container(identifier):
    completed = subprocess.run(
        list(DOCKER) + ["inspect", str(identifier)],
        universal_newlines=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        raise RenderError("live TPC-C client inspect failed")
    try:
        rows = json.loads(completed.stdout)
    except ValueError as exc:
        raise RenderError("live TPC-C client inspect JSON differs") from exc
    if not isinstance(rows, list) or len(rows) != 1:
        raise RenderError("live TPC-C client inspect cardinality differs")
    return rows[0]


def mapped_host_path(container_path, mounts):
    candidate = Path(container_path)
    matches = []
    for row in mounts:
        destination = Path(row["destination"])
        if candidate == destination or destination in candidate.parents:
            matches.append(
                (len(destination.parts), destination, Path(row["source"]))
            )
    if not matches:
        raise RenderError(
            "container path is outside read-only mount closure: %s" % candidate
        )
    _length, destination, source = sorted(matches, reverse=True)[0]
    return source / candidate.relative_to(destination)


def regular_hashes(path):
    path = Path(path)
    if path.is_symlink():
        raise RenderError("symlink in qualification code closure")
    if path.is_file():
        return {str(path): sha256(path)}
    if not path.is_dir():
        raise RenderError("qualification code path is absent")
    result = {}
    for child in sorted(path.rglob("*")):
        if child.is_symlink() or (not child.is_file() and not child.is_dir()):
            raise RenderError("unsafe qualification code closure entry")
        if child.is_file():
            result[str(child)] = sha256(child)
    if not result:
        raise RenderError("qualification code directory is empty")
    return result


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
    if len(value) < 32 or any(character.isspace() for character in value):
        raise RenderError("main-agent token differs")
    return value


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sidecar = Path(str(path) + ".sha256")
    if path.exists() or sidecar.exists():
        raise RenderError("refusing to overwrite qualification material")
    temporary = path.with_name(".%s.%s" % (path.name, secrets.token_hex(6)))
    temporary.write_bytes(canonical(value))
    os.chmod(str(temporary), 0o600)
    os.replace(str(temporary), str(path))
    sidecar.write_text("%s  %s\n" % (sha256(path), path.name), encoding="ascii")
    os.chmod(str(sidecar), 0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--adopted-service", type=Path, required=True)
    parser.add_argument("--historical-command", type=Path, required=True)
    parser.add_argument("--main-agent-token-file", type=Path, required=True)
    parser.add_argument("--measurement-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=("qualification_solo", "qualification_paired"),
        required=True,
    )
    parser.add_argument("--qualification-attempt-id", required=True)
    parser.add_argument("--peer-binding", type=Path)
    parser.add_argument("--valid-seconds", type=int, default=900)
    args = parser.parse_args()

    if not 240 <= args.valid_seconds <= 1800:
        raise RenderError("qualification TTL differs")
    if (
        re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{31,159}",
            args.qualification_attempt_id,
        )
        is None
    ):
        raise RenderError("qualification attempt ID differs")
    bundle, bundle_id, manifest_sha = validate_bundle(args.bundle)
    _service_path, service = load_object(
        args.adopted_service, "adopted service"
    )
    command_path, historical = load_object(
        args.historical_command, "historical sealed qualification command"
    )
    client = service.get("client") or {}
    bridge = service.get("measurement_bridge") or {}
    if (
        service.get("schema_version")
        != "mysql_tpcc_node04_adopted_service_authority_v2"
        or service.get("status") != "PASS"
        or service.get("formal_scope") != "mysql_only"
        or service.get("cross_engine_comparison_prohibited") is not True
        or service.get("bundle_id") != bundle_id
        or service.get("formal_launch_authorized") is not False
        or service.get("accepted_round_authorized") is not False
        or historical.get("schema_version")
        != "mysql_tpcc_materialized_phase_command_v1"
        or historical.get("status") != "SEALED_QUALIFICATION_COMMAND"
        or historical.get("phase") != "qualification"
        or historical.get("formal_scope") != "mysql_only"
        or historical.get("bundle_id") != bundle_id
        or historical.get("attempt_id") != service.get("attempt_id")
        or historical.get("handoff_token_sha256")
        != service.get("handoff_token_sha256")
        or (historical.get("client_container") or {}).get("container_id")
        != client.get("container_id")
    ):
        raise RenderError("same-live service/command authority differs")

    row = inspect_container(client.get("container_id", ""))
    state = row.get("State") or {}
    if (
        row.get("Id") != client.get("container_id")
        or int(state.get("Pid") or 0) != int(client.get("pid") or 0)
        or state.get("Running") is not True
        or int(row.get("RestartCount") or 0) != int(client.get("restart_count") or 0)
    ):
        raise RenderError("adopted client live identity differs")

    mounts = []
    for mount in row.get("Mounts") or []:
        if mount.get("Type") in ("bind", "volume") and mount.get("RW") is False:
            mounts.append(
                {
                    "source": str(mount.get("Source")),
                    "destination": str(mount.get("Destination")),
                }
            )
    mounts.sort(key=lambda value: (value["source"], value["destination"]))
    if not mounts:
        raise RenderError("client read-only mount closure is absent")

    metric_root = args.measurement_root.resolve(strict=True)
    if (
        metric_root != Path(str(bridge.get("host_path") or "")).resolve(strict=True)
        or bridge.get("filesystem") != "tmpfs"
        or bridge.get("live_readback_verified") is not True
        or int(bridge.get("size_bytes") or 0) < 2147483648
    ):
        raise RenderError("measurement tmpfs authority differs")

    argv = list(historical.get("argv") or [])
    if not argv or argv[0] != "/opt/mysql-tpcc-jdk/bin/java":
        raise RenderError("frozen production TPC-C argv differs")
    classpath_indexes = set(
        index + 1
        for index, value in enumerate(argv[:-1])
        if value in ("-cp", "-classpath", "--class-path")
    )
    code_files = {}
    for index, value in enumerate(argv):
        if index in classpath_indexes or not value.startswith("/"):
            continue
        code_files.update(regular_hashes(mapped_host_path(value, mounts)))
    for index in sorted(classpath_indexes):
        for piece in argv[index].split(os.pathsep):
            code_files.update(regular_hashes(mapped_host_path(piece, mounts)))
    executable_host = mapped_host_path(argv[0], mounts)
    executable_sha = code_files.get(str(executable_host))
    if not executable_sha:
        raise RenderError("qualification executable is outside code closure")

    metric_path = metric_root / (args.qualification_attempt_id + ".metrics.jsonl")
    client_projection_sha = sha_bytes(
        canonical(client_container_immutable_projection(row))
    )
    spec = {
        "schema_version": "mysql_algorithm_c_debug_qualification_command_v1",
        "bundle_id": bundle_id,
        "mode": args.mode,
        "qualification_attempt_id": args.qualification_attempt_id,
        "argv": argv,
        "executable_sha256": executable_sha,
        "code_files": dict(sorted(code_files.items())),
        "workload_metric_stream": {
            "schema_version": "mysql_algorithm_c_debug_workload_metric_stream_v1",
            "path": str(metric_path),
        },
        "workload_metric_adapter": {
            "schema_version": "mysql_algorithm_c_debug_workload_metric_adapter_v1",
            "kind": "benchbase_tpcc",
        },
        "workload_process_isolation": {
            "cpuset_cpus": client["cpuset_cpus"],
            "cpuset_mems": client["cpuset_mems"],
            "root_and_descendants_exact": True,
        },
        "client_container": {
            "name": str(row.get("Name") or "").lstrip("/"),
            "container_id": row["Id"],
            "image_id": row.get("Image"),
            "created": row.get("Created"),
            "inspect_config_sha256": client_projection_sha,
            "required_read_only_mounts": mounts,
        },
        "measurement_output": {
            "host_tmpfs_directory": str(metric_root),
            "container_tmpfs_destination": "/measurement",
        },
        "environment": {
            "BENCHBASE_FORMAL_STOP_FILE": (
                "/measurement/"
                + args.qualification_attempt_id
                + ".benchbase-stop-request"
            ),
            "BENCHBASE_FORMAL_WARMUP_SECONDS": "60",
            "BENCHBASE_FORMAL_MAXIMUM_MEASUREMENT_SECONDS": "600",
        },
        "cwd": "/measurement",
        "debug_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
    }
    if args.mode == "qualification_paired":
        if args.peer_binding is None:
            raise RenderError("paired qualification requires peer binding")
        _peer_path, peer = load_object(args.peer_binding, "peer binding")
        spec["paired_peer_workload"] = peer
    elif args.peer_binding is not None:
        raise RenderError("solo qualification rejects peer binding")

    output_dir = args.output_dir.resolve()
    command_output = output_dir / (args.mode + ".command.json")
    atomic_json(command_output, spec)
    token = read_token(args.main_agent_token_file)
    now = time.time()
    authority = {
        "schema_version": "mysql_algorithm_c_main_agent_qualification_authority_v1",
        "authorized_by": "main_agent",
        "bundle_id": bundle_id,
        "mode": args.mode,
        "qualification_attempt_id": args.qualification_attempt_id,
        "main_agent_token_sha256": sha_bytes(token.encode("ascii")),
        "command_spec_sha256": sha256(command_output),
        "debug_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "not_before_epoch": now - 1.0,
        "expires_epoch": now + args.valid_seconds,
        "target_manifest_sha256": manifest_sha,
        "historical_command_sha256": sha256(command_path),
    }
    authority_output = output_dir / (args.mode + ".authority.json")
    atomic_json(authority_output, authority)
    print(
        json.dumps(
            {
                "status": "SEALED_CURRENT_TPCC_DEBUG_SPEC_NOT_LAUNCHED",
                "command": str(command_output),
                "authority": str(authority_output),
                "accepted_round_authorized": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (RenderError, OSError, ValueError, KeyError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
