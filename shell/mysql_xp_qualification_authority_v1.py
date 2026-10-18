#!/usr/bin/env python3
"""Materialize one short-lived XP debug-qualification command and authority.

This utility never starts Docker or a workload.  It binds a command to an
already adopted same-live database/client pair and to files that were present
when the immutable bundle was published.
"""

import sys
sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time


SCENARIO = "hybench_xp_io"
B67_INVALIDATED = (
    "b67bf54fe87f540591170e01c757dc24e622ea0ae80a6970b305eb33c64dfaaa"
)
BUNDLE_DESTINATION = Path("/opt/mysql-xp-bundle")
RUNTIME_DESTINATION = Path("/opt/mysql-xp-jdk")
CONFIG_DESTINATION = Path("/opt/mysql-xp-config")
MEASUREMENT_DESTINATION = Path("/measurement")
QUALIFICATION_CONFIG = "hybench_xp_qualification.properties"
STATEMENTS = "stmt_mysql_formal_case_normalized.toml"
MYSQL_DEFAULTS = "mysql-client.cnf"
CLIENT_CPUSET = "3-11,39-47"
CLIENT_MEMSET = "0"
SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{32,128}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class AuthorityError(RuntimeError):
    pass


def canonical(value):
    return (
        json.dumps(
            value, ensure_ascii=True, indent=2, sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_sealed(path, label):
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    if path.is_symlink() or not path.is_file():
        raise AuthorityError("%s must be a regular file" % label)
    expected = "%s  %s\n" % (sha256(path), path.name)
    if (
        sidecar.is_symlink()
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii") != expected
    ):
        raise AuthorityError("%s sidecar differs" % label)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuthorityError("%s must be a JSON object" % label)
    return value


def verify_bundle(bundle):
    bundle = Path(bundle).resolve(strict=True)
    manifest = load_sealed(bundle / "bundle_manifest.json", "bundle manifest")
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    if (
        bundle_id != bundle.name
        or SHA_RE.fullmatch(bundle.name) is None
        or sha_bytes(canonical(unsigned)) != bundle.name
        or manifest.get("schema_version") not in (
            "mysql_xp_distributed_formal10_launch_v3",
            "mysql_xp_node02_successor_bundle_v18",
            "mysql_xp_node02_successor_bundle_v19",
        )
        or manifest.get("scenario") != SCENARIO
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or B67_INVALIDATED
        not in (manifest.get("invalid_development_bundle_ids") or [])
    ):
        raise AuthorityError("XP qualification bundle identity differs")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise AuthorityError("bundle file closure is absent")
    return bundle, manifest


def verify_adoption(path, bundle_id):
    adoption = load_sealed(path, "adopted service")
    database = adoption.get("database") or {}
    client = adoption.get("client") or {}
    host_tmpfs = adoption.get("host_output_tmpfs") or {}
    runtime = adoption.get("runtime_publication") or {}
    if (
        adoption.get("schema_version")
        != "mysql_xp_node02_adopted_service_authority_v1"
        or adoption.get("status") != "PASS_ADOPTED_SAME_LIVE_SERVICE"
        or adoption.get("bundle_id") != bundle_id
        or adoption.get("formal_scope") != "mysql_only"
        or adoption.get("cross_engine_comparison_prohibited") is not True
        or adoption.get("new_container_or_datadir_for_later_stage_prohibited")
        is not True
        or adoption.get("host_mysql_binary_used") is not False
        or len(str(database.get("container_id") or "")) != 64
        or int(database.get("pid") or 0) <= 1
        or int(database.get("pid_starttime_ticks") or 0) <= 0
        or len(str(client.get("container_id") or "")) != 64
        or int(client.get("pid") or 0) <= 1
        or int(client.get("pid_starttime_ticks") or 0) <= 0
        or host_tmpfs.get("filesystem") != "tmpfs"
        or int(host_tmpfs.get("size_bytes") or 0) < 2147483648
        or runtime.get("live_java_binary_sha256")
        != "c8f339ba5d433ef5ecacef4de0640b0a35c99381804413bfbc24375a0410e51c"
    ):
        raise AuthorityError("adopted same-live service differs")
    return adoption


def require_bundle_file(bundle, manifest, relative):
    path = bundle / relative
    expected = (manifest.get("files") or {}).get(relative)
    if (
        path.is_symlink()
        or not path.is_file()
        or not isinstance(expected, str)
        or sha256(path) != expected
    ):
        raise AuthorityError("manifest-bound file differs: %s" % relative)
    return path


def read_token(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise AuthorityError("main-agent token file must be regular")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise AuthorityError("main-agent token file must be private")
    token = path.read_text(encoding="utf-8").strip()
    if len(token) < 32 or len(token) > 512 or any(ch.isspace() for ch in token):
        raise AuthorityError("main-agent token differs")
    return token


def validate_peer_metric_target(path, peer_attempt_id):
    """Validate a peer stream target without requiring launch-order races.

    Before either paired authority is materialized, the other attempt's leaf
    directory normally does not exist yet.  Requiring that leaf to exist makes
    two mutually bound authorities impossible to create sequentially.  Bind
    the exact conventional filename and attempt-owned leaf instead, and
    require the leaf itself or its immediate existing parent to be a real
    directory.
    """
    path = Path(path)
    parent = path.parent
    ancestor = parent if parent.exists() else parent.parent
    if (
        not path.is_absolute()
        or path.name != "workload_metrics.jsonl"
        or parent.name != peer_attempt_id
        or path.is_symlink()
        or parent.is_symlink()
        or ancestor.is_symlink()
        or not ancestor.is_dir()
    ):
        raise AuthorityError("paired peer metric path differs")
    return path


def atomic_pair(command_path, command, authority_path, authority):
    paths = [
        Path(command_path),
        Path(str(command_path) + ".sha256"),
        Path(authority_path),
        Path(str(authority_path) + ".sha256"),
    ]
    if any(path.exists() or path.is_symlink() for path in paths):
        raise AuthorityError("refusing to overwrite qualification authority")
    for path in (Path(command_path), Path(authority_path)):
        path.parent.mkdir(parents=True, exist_ok=True)
    command_payload = canonical(command)
    authority_payload = canonical(authority)
    payloads = {
        Path(command_path): command_payload,
        Path(str(command_path) + ".sha256"): (
            "%s  %s\n"
            % (sha_bytes(command_payload), Path(command_path).name)
        ).encode("ascii"),
        Path(authority_path): authority_payload,
        Path(str(authority_path) + ".sha256"): (
            "%s  %s\n"
            % (sha_bytes(authority_payload), Path(authority_path).name)
        ).encode("ascii"),
    }
    temporaries = {}
    try:
        for path, payload in payloads.items():
            temporary = path.with_name(".%s.%d.tmp" % (path.name, os.getpid()))
            descriptor = os.open(
                str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            temporaries[path] = temporary
        for path in paths:
            os.replace(str(temporaries[path]), str(path))
    finally:
        for temporary in temporaries.values():
            if temporary.exists():
                temporary.unlink()


def materialize(
    bundle_path,
    adoption_path,
    mode,
    attempt_id,
    token_file,
    output_directory,
    peer_scenario=None,
    peer_attempt_id=None,
    peer_metric_stream=None,
):
    if mode not in ("qualification_solo", "qualification_paired"):
        raise AuthorityError("qualification mode differs")
    if SAFE_ID.fullmatch(attempt_id or "") is None:
        raise AuthorityError("qualification attempt ID differs")
    paired_values = (peer_scenario, peer_attempt_id, peer_metric_stream)
    if mode == "qualification_solo" and any(paired_values):
        raise AuthorityError("solo qualification cannot bind a peer")
    if mode == "qualification_paired" and (
        not all(paired_values)
        or SAFE_ID.fullmatch(peer_attempt_id or "") is None
        or peer_attempt_id == attempt_id
        or peer_scenario == SCENARIO
    ):
        raise AuthorityError("paired peer binding differs")

    bundle, manifest = verify_bundle(bundle_path)
    adoption = verify_adoption(adoption_path, manifest["bundle_id"])
    config_root = bundle / "qualification/config"
    config = require_bundle_file(
        bundle, manifest,
        "qualification/config/" + QUALIFICATION_CONFIG,
    )
    statements = require_bundle_file(
        bundle, manifest, "qualification/config/" + STATEMENTS
    )
    require_bundle_file(
        bundle, manifest, "qualification/config/" + MYSQL_DEFAULTS
    )
    overlay = bundle / "qualification/native_latency_producer/classes"
    jar = require_bundle_file(
        bundle, manifest,
        "predecessor/predecessor/"
        "HyBench-1.0-SNAPSHOT-formal-xp-binding-trace-"
        "candidate-jar-with-dependencies.jar",
    )
    overlay_class = require_bundle_file(
        bundle, manifest,
        "qualification/native_latency_producer/classes/"
        "com/hybench/HyBenchFormalMain.class",
    )
    runtime_root = Path(
        adoption["runtime_publication"]["live_tree_readback"]["root"]
    )
    java = runtime_root / "bin/java"
    if (
        java.is_symlink()
        or not java.is_file()
        or sha256(java)
        != adoption["runtime_publication"]["live_java_binary_sha256"]
    ):
        raise AuthorityError("published Java executable differs")

    output_directory = Path(output_directory).resolve()
    host_tmpfs = Path(adoption["host_output_tmpfs"]["path"]).resolve()
    if (
        output_directory == host_tmpfs
        or host_tmpfs not in output_directory.parents
        or output_directory.is_symlink()
    ):
        raise AuthorityError("qualification output must be attempt-owned tmpfs")
    output_directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    metric_stream = output_directory / "workload_metrics.jsonl"
    command_path = output_directory / "command.json"
    authority_path = output_directory / "main_agent_authority.json"

    bundle_destination = BUNDLE_DESTINATION
    runtime_destination = RUNTIME_DESTINATION
    config_destination = CONFIG_DESTINATION
    argv = [
        str(runtime_destination / "bin/java"),
        "-cp",
        str(
            bundle_destination
            / "qualification/native_latency_producer/classes"
        )
        + ":"
        + str(
            bundle_destination
            / "predecessor/predecessor/"
            "HyBench-1.0-SNAPSHOT-formal-xp-binding-trace-"
            "candidate-jar-with-dependencies.jar"
        ),
        "com.hybench.HyBenchFormalMain",
        "xp",
        str(config_destination / QUALIFICATION_CONFIG),
        str(config_destination / STATEMENTS),
    ]
    code_files = {
        str(java): sha256(java),
        str(jar): sha256(jar),
        str(overlay_class): sha256(overlay_class),
        str(config): sha256(config),
        str(statements): sha256(statements),
    }
    command = {
        "schema_version":
            "mysql_algorithm_c_debug_qualification_command_v1",
        "bundle_id": manifest["bundle_id"],
        "mode": mode,
        "debug_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "qualification_attempt_id": attempt_id,
        "workload_metric_stream": {
            "schema_version":
                "mysql_algorithm_c_debug_workload_metric_stream_v1",
            "path": str(metric_stream),
        },
        "workload_metric_adapter": {
            "schema_version":
                "mysql_algorithm_c_debug_workload_metric_adapter_v1",
            "kind": "hybench_xp",
        },
        "workload_process_isolation": {
            "cpuset_cpus": CLIENT_CPUSET,
            "cpuset_mems": CLIENT_MEMSET,
            "root_and_descendants_exact": True,
        },
        "client_container": {
            "name": adoption["client"]["name"],
            "container_id": adoption["client"]["container_id"],
            "image_id": adoption["client"]["image_id"],
            "created": adoption["client"]["created"],
            "inspect_config_sha256":
                adoption["client"]["inspect_config_sha256"],
            "required_read_only_mounts": [
                {
                    "source": str(bundle),
                    "destination": str(bundle_destination),
                },
                {
                    "source": str(runtime_root),
                    "destination": str(runtime_destination),
                },
                {
                    "source": str(config_root),
                    "destination": str(config_destination),
                },
            ],
        },
        "measurement_output": {
            "host_tmpfs_directory": str(host_tmpfs),
            "container_tmpfs_destination": str(MEASUREMENT_DESTINATION),
        },
        "argv": argv,
        "executable_sha256": sha256(java),
        "code_files": code_files,
        "environment": {},
        "cwd": str(MEASUREMENT_DESTINATION),
    }
    if mode == "qualification_paired":
        peer_metric_stream = validate_peer_metric_target(
            peer_metric_stream, peer_attempt_id
        )
        command["paired_peer_workload"] = {
            "schema_version":
                "mysql_algorithm_c_paired_peer_workload_binding_v1",
            "scenario": peer_scenario,
            "qualification_attempt_id": peer_attempt_id,
            "workload_metric_stream_path": str(peer_metric_stream),
        }

    token = read_token(token_file)
    now = time.time()
    authority = {
        "schema_version":
            "mysql_algorithm_c_main_agent_qualification_authority_v1",
        "authorized_by": "main_agent",
        "bundle_id": manifest["bundle_id"],
        "mode": mode,
        "qualification_attempt_id": attempt_id,
        "main_agent_token_sha256": sha_bytes(token.encode("utf-8")),
        "command_spec_sha256": sha_bytes(canonical(command)),
        "debug_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "not_before_epoch": now - 1.0,
        "expires_epoch": now + 900.0,
    }
    atomic_pair(command_path, command, authority_path, authority)
    return {
        "schema_version":
            "mysql_xp_qualification_materialization_result_v1",
        "status": "MATERIALIZED_NOT_LAUNCHED",
        "mode": mode,
        "bundle_id": manifest["bundle_id"],
        "adopted_service_sha256": sha256(adoption_path),
        "qualification_attempt_id": attempt_id,
        "command_spec": str(command_path),
        "command_spec_sha256": sha256(command_path),
        "main_agent_authority": str(authority_path),
        "main_agent_authority_sha256": sha256(authority_path),
        "workload_metric_stream": str(metric_stream),
        "main_agent_token_not_persisted": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--adopted-service", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=("qualification_solo", "qualification_paired"),
        required=True,
    )
    parser.add_argument("--qualification-attempt-id", required=True)
    parser.add_argument("--main-agent-token-file", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--peer-scenario")
    parser.add_argument("--peer-qualification-attempt-id")
    parser.add_argument("--peer-workload-metric-stream", type=Path)
    args = parser.parse_args(argv)
    result = materialize(
        args.bundle,
        args.adopted_service,
        args.mode,
        args.qualification_attempt_id,
        args.main_agent_token_file,
        args.output_directory,
        args.peer_scenario,
        args.peer_qualification_attempt_id,
        args.peer_workload_metric_stream,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
