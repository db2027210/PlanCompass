#!/usr/bin/env python3
"""Materialize XP capture-only authority for an already adopted service.

This entrypoint is intentionally evidence-only: it neither invokes Docker nor
starts HyBench.  It provides the two sidecar-bound documents consumed by the
sealed trace producer, so a capture can be distinguished from R0/formal work.
"""
import sys

# This script is copied into an immutable runtime bundle.  It must never
# create bytecode in that bundle, including when invoked directly by python.
sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time


SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{32,128}$")
CAPTURE_IO = {"io_read_bps": 1205862400, "io_write_bps": 1205862400}
ROLES = {
    "ap_client_source": "predecessor/predecessor/source/src/main/java/com/hybench/workload/APClient.java",
    "client_source": "predecessor/predecessor/source/src/main/java/com/hybench/workload/Client.java",
    "connector_jar": "predecessor/predecessor/HyBench-1.0-SNAPSHOT-formal-xp-binding-trace-candidate-jar-with-dependencies.jar",
    "driver_class": "predecessor/predecessor/driver/attribution-classes/formal/mysql/AttributingMysqlDriver.class",
    "formal_binding_trace_source": "predecessor/predecessor/source/src/main/java/com/hybench/util/FormalBindingTrace.java",
    "graceful_quiescence_source": "predecessor/predecessor/source/src/main/java/com/hybench/util/GracefulQuiescence.java",
    "hybench_fat_jar": "predecessor/predecessor/HyBench-1.0-SNAPSHOT-formal-xp-binding-trace-candidate-jar-with-dependencies.jar",
    "hybench_formal_main_class": "qualification/native_latency_producer/classes/com/hybench/HyBenchFormalMain.class",
    "hybench_formal_main_source": "predecessor/predecessor/source/provenance/formal_clients_20260721/com/hybench/HyBenchFormalMain.java",
    "hybench_source": "predecessor/predecessor/source/src/main/java/com/hybench/workload/Client.java",
    "random_generator_source": "predecessor/predecessor/source/src/main/java/com/hybench/util/RandomGenerator.java",
    "statement_file": "qualification/config/stmt_mysql_formal_case_normalized.toml",
    "tp_client_source": "predecessor/predecessor/source/src/main/java/com/hybench/workload/TPClient.java",
    "workload_properties": "qualification/config/hybench_xp_qualification.properties",
}


class AuthorityError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_sealed(path, label):
    path = Path(path); sidecar = Path(str(path) + ".sha256")
    if path.is_symlink() or not path.is_file() or sidecar.is_symlink() or not sidecar.is_file():
        raise AuthorityError("%s is not sealed" % label)
    if sidecar.read_text(encoding="ascii") != "%s  %s\n" % (sha256(path), path.name):
        raise AuthorityError("%s sidecar differs" % label)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuthorityError("%s is not an object" % label)
    return value


def write_sealed(path, value):
    path = Path(path); sidecar = Path(str(path) + ".sha256")
    if path.exists() or path.is_symlink() or sidecar.exists() or sidecar.is_symlink():
        raise AuthorityError("refusing to overwrite authority")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical(value)
    temporary = path.with_name(".%s.%d.tmp" % (path.name, os.getpid()))
    with temporary.open("xb") as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    os.replace(str(temporary), str(path))
    sidecar.write_text("%s  %s\n" % (hashlib.sha256(data).hexdigest(), path.name), encoding="ascii")


def private_token(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise AuthorityError("main-agent token is not private")
    value = path.read_text(encoding="utf-8").strip()
    if len(value) < 32 or any(char.isspace() for char in value):
        raise AuthorityError("main-agent token differs")
    return value


def materialize(bundle_path, adoption_path, attempt_id, token_path, output_directory):
    if SAFE_ID.fullmatch(attempt_id or "") is None:
        raise AuthorityError("capture attempt ID differs")
    bundle = Path(bundle_path).resolve(strict=True)
    manifest = load_sealed(bundle / "bundle_manifest.json", "bundle manifest")
    if (manifest.get("bundle_id") != bundle.name or manifest.get("formal_scope") != "mysql_only"
            or manifest.get("formal_launch_authorized") is not False):
        raise AuthorityError("bundle identity/scope differs")
    adoption = load_sealed(adoption_path, "adopted service")
    if (adoption.get("status") != "PASS_ADOPTED_SAME_LIVE_SERVICE"
            or adoption.get("bundle_id") != bundle.name
            or adoption.get("new_container_or_datadir_for_later_stage_prohibited") is not True):
        raise AuthorityError("same-live adoption differs")
    output = Path(output_directory).resolve()
    tmpfs = Path((adoption.get("host_output_tmpfs") or {}).get("path") or "").resolve()
    if output == tmpfs or tmpfs not in output.parents or output.exists() or output.is_symlink():
        raise AuthorityError("capture output must be a new attempt-owned tmpfs directory")
    files = manifest.get("files") or {}
    artifacts = {}
    for role, relative in sorted(ROLES.items()):
        candidate = bundle / relative
        if candidate.is_symlink() or not candidate.is_file() or files.get(relative) != sha256(candidate):
            raise AuthorityError("manifest-bound capture role differs: %s" % role)
        artifacts[role] = {"path": str(candidate), "sha256": sha256(candidate)}
    token = private_token(token_path)
    runtime = {
        "schema_version": "xp_runtime_authority_bundle_v1", "status": "SEALED",
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "launch_token": attempt_id, "workload_seed": 4508333, "artifacts": artifacts,
        "bundle_id": bundle.name, "adopted_service_sha256": sha256(adoption_path),
        "capture_only_resource_envelope": dict(CAPTURE_IO),
        "capture_is_r0": False, "accepted_rounds_credit": 0,
    }
    runtime_path = output / "runtime_authority.json"
    write_sealed(runtime_path, runtime)
    capture = {
        "schema_version": "mysql_xp_capture_launch_authorization_v1", "status": "CAPTURE_READY",
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "launch_token": attempt_id, "runtime_authority_sha256": sha256(runtime_path),
        "main_agent_token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
        "bundle_id": bundle.name, "adopted_service_sha256": sha256(adoption_path),
        "capture_launch_authorized": True, "outer_execution_authorized": False,
        "algorithm_c_execution_authorized": False, "capture_only_resource_envelope": dict(CAPTURE_IO),
        "capture_is_r0": False, "formal_r0_authorized": False, "formal_authorized": False,
        "accepted_round_authorized": False, "accepted_rounds_credit": 0,
        "expires_epoch": time.time() + 900.0,
    }
    capture_path = output / "capture_authorization.json"
    write_sealed(capture_path, capture)
    return {"status": "MATERIALIZED_CAPTURE_ONLY_NOT_LAUNCHED", "runtime_authority": str(runtime_path),
            "capture_authorization": str(capture_path), "capture_authorized": True,
            "formal_r0_authorized": False, "accepted_round_authorized": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--adopted-service", type=Path, required=True)
    parser.add_argument("--capture-attempt-id", required=True)
    parser.add_argument("--main-agent-token-file", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(materialize(args.bundle, args.adopted_service, args.capture_attempt_id,
                                 args.main_agent_token_file, args.output_directory), sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except AuthorityError as exc:
        raise SystemExit("NOT_READY: %s" % exc)
