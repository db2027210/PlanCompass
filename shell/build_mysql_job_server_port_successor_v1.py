#!/usr/bin/env python3
"""Build immutable JOB v35 with distinct TCP-target and server-port binding.

Local/offline only.  The publisher copies and verifies the complete v34
closure, replaces only the readiness collector, adds the v34 zero-credit
invalidation, and publishes transactionally by its content address.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PREDECESSOR_ID = "e7d077d8dbc382f36a1dcf5bbbebfe321e4e7e02ab2a7bc92280f8cb72af4ff1"
PREDECESSOR_MANIFEST_SHA256 = (
    "67d2d79d74e79455673b6b3cf9701647fd4a1bb8e74397c4970b278829587c81"
)
PREDECESSOR = (
    ROOT / "artifacts/mysql_job_v34_measurement_bind_successor" / PREDECESSOR_ID
)
INVALIDATION = (
    ROOT
    / "preflight/mysql_job_v34_e7d_server_port_readiness_invalidation_20260731.json"
)
BUNDLE_ROOT = ROOT / "artifacts/mysql_job_v35_server_port_successor"
COLLECTOR_RELATIVE = (
    "qualification/mysql_job_distributed_live_readback_qualification_v2.py"
)
INVALIDATION_RELATIVE = (
    "authority/mysql_job_v34_e7d_server_port_readiness_invalidation_20260731.json"
)
HOST_PORT = 65102
SERVER_PORT = 3306
AUTHENTICATED_USER = "root"


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (
        json.dumps(
            value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def sha_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise BuildError("absent or symlinked: %s" % path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError("JSON object required: %s" % path)
    return value


def verify_predecessor():
    manifest_path = PREDECESSOR / "bundle_manifest.json"
    manifest = load(manifest_path)
    if (
        PREDECESSOR.name != PREDECESSOR_ID
        or sha256(manifest_path) != PREDECESSOR_MANIFEST_SHA256
        or manifest.get("bundle_id") != PREDECESSOR_ID
        or manifest.get("schema_version")
        != "mysql_algorithm_c_distributed_successor_bundle_v1"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("JOB v34 predecessor identity/authority differs")
    unsigned = {key: value for key, value in manifest.items() if key != "bundle_id"}
    if sha_bytes(canonical(unsigned)) != PREDECESSOR_ID:
        raise BuildError("JOB v34 content address differs")
    for relative, expected in (manifest.get("files") or {}).items():
        path = PREDECESSOR / relative
        if (
            path.is_symlink()
            or not path.is_file()
            or sha256(path) != expected
        ):
            raise BuildError("JOB v34 closure differs: %s" % relative)
    return manifest


def verify_invalidation():
    value = load(INVALIDATION)
    contract = value.get("required_successor_contract") or {}
    if (
        value.get("schema_version")
        != "mysql_job_v34_server_port_readiness_invalidation_v1"
        or value.get("status") != "INVALIDATED_DO_NOT_RERUN"
        or value.get("bundle_id") != PREDECESSOR_ID
        or value.get("bundle_manifest_sha256")
        != PREDECESSOR_MANIFEST_SHA256
        or value.get("database_and_client_started") is not True
        or value.get("workload_started") is not False
        or value.get("accepted_rounds_credit") != 0
        or value.get("capture_credit") != 0
        or value.get("qualification_credit") != 0
        or value.get("replay_or_rerun_authorized") is not False
        or contract.get("tcp_target_port") != HOST_PORT
        or contract.get("server_mysql_port") != SERVER_PORT
        or contract.get("authenticated_user") != AUTHENTICATED_USER
        or contract.get("database_counter_tcp_target_port") != HOST_PORT
        or contract.get("client_identity_verified_before_probe") is not True
        or contract.get("client_identity_verified_after_probe") is not True
    ):
        raise BuildError("JOB v34 zero-credit invalidation differs")


def collector_payload():
    source = (PREDECESSOR / COLLECTOR_RELATIVE).read_text(encoding="utf-8")
    old = '''def client_tcp_mysql_readiness(transport, port, seconds=120):
    """Bounded authenticated TCP readiness through the exact client cgroup.

    The database is deliberately not required to publish its port on the host.
    Every probe is executed as the frozen client principal and validates the
    container's full ID, PID/start ticks and immutable inspect projection.
    """
    if not isinstance(seconds, int) or seconds < 1 or seconds > 180:
        raise QualificationError("client TCP readiness bound differs")
    deadline = time.monotonic() + seconds
    attempts = 0
    last_error = None
    while time.monotonic() < deadline:
        attempts += 1
        client_id = _verified_database_counter_client(transport)
        argv = [
            "docker", "exec", "--user", "1008:1008", client_id,
            transport["mysql_binary_in_client"],
            "--defaults-extra-file=%s" % transport["mysql_defaults_file_in_client"],
            "--protocol=TCP", "--host=127.0.0.1", "--port=%d" % int(port),
            "--connect-timeout=1", "--ssl-mode=DISABLED",
            "--get-server-public-key", "--batch", "--raw",
            "--skip-column-names",
            "--execute=SELECT @@port, SUBSTRING_INDEX(CURRENT_USER(),'@',1)",
        ]
        try:
            output = run(argv, timeout=5, label="client-internal TCP MySQL readiness")
            rows = [line for line in output.splitlines() if line]
            if rows != ["%d\\troot" % int(port)]:
                raise QualificationError("client TCP MySQL readiness identity differs")
            _verified_database_counter_client(transport)
            return {
                "status": "PASS_EXACT_CLIENT_INTERNAL_TCP_MYSQL_READINESS",
                "client_container_id": client_id,
                "mysql_port": int(port), "attempts": attempts,
                "host_port_publication_required": False,
                "accepted_round_authorized": False,
            }
        except QualificationError as exc:
            last_error = exc
            if time.monotonic() >= deadline:
                break
            time.sleep(1)
    raise QualificationError(
        "client-internal TCP MySQL readiness timed out after %d seconds: %s"
        % (seconds, last_error)
    )
'''
    new = '''def client_tcp_mysql_readiness(
    transport, host_port, server_port, seconds=120
):
    """Bind the exact client TCP target and server identity independently.

    ``host_port`` is the client-visible forwarding target.  ``server_port`` is
    the server's internal ``@@port`` identity.  Both and CURRENT_USER must
    match the frozen v35 contract, with the complete adopted-client identity
    verified before and after every successful probe.
    """
    if (
        not isinstance(host_port, int)
        or isinstance(host_port, bool)
        or host_port != 65102
        or not isinstance(server_port, int)
        or isinstance(server_port, bool)
        or server_port != 3306
    ):
        raise QualificationError("client TCP target/server port contract differs")
    if not isinstance(seconds, int) or seconds < 1 or seconds > 180:
        raise QualificationError("client TCP readiness bound differs")
    deadline = time.monotonic() + seconds
    attempts = 0
    last_error = None
    while time.monotonic() < deadline:
        attempts += 1
        client_id = _verified_database_counter_client(transport)
        argv = [
            "docker", "exec", "--user", "1008:1008", client_id,
            transport["mysql_binary_in_client"],
            "--defaults-extra-file=%s" % transport["mysql_defaults_file_in_client"],
            "--protocol=TCP", "--host=127.0.0.1",
            "--port=%d" % host_port,
            "--connect-timeout=1", "--ssl-mode=DISABLED",
            "--get-server-public-key", "--batch", "--raw",
            "--skip-column-names",
            "--execute=SELECT @@port, SUBSTRING_INDEX(CURRENT_USER(),'@',1)",
        ]
        try:
            output = run(argv, timeout=5, label="client-internal TCP MySQL readiness")
            rows = [line for line in output.splitlines() if line]
            if rows != ["%d\\troot" % server_port]:
                raise QualificationError("client TCP MySQL readiness identity differs")
            verified_after_id = _verified_database_counter_client(transport)
            if verified_after_id != client_id:
                raise QualificationError(
                    "database-counter client container identity changed"
                )
            return {
                "status": "PASS_EXACT_CLIENT_INTERNAL_TCP_MYSQL_READINESS",
                "client_container_id_before_probe": client_id,
                "client_container_id_after_probe": verified_after_id,
                "tcp_target_host": "127.0.0.1",
                "tcp_target_port": host_port,
                "server_mysql_port": server_port,
                "authenticated_user": "root",
                "attempts": attempts,
                "host_port_publication_required": False,
                "accepted_round_authorized": False,
            }
        except QualificationError as exc:
            last_error = exc
            if time.monotonic() >= deadline:
                break
            time.sleep(1)
    raise QualificationError(
        "client-internal TCP MySQL readiness timed out after %d seconds: %s"
        % (seconds, last_error)
    )
'''
    if source.count(old) != 1:
        raise BuildError("JOB v34 readiness source differs")
    source = source.replace(old, new)
    old_call = (
        "    tcp_readiness = client_tcp_mysql_readiness("
        "database_counter_transport, port)\n"
    )
    new_call = (
        "    tcp_readiness = client_tcp_mysql_readiness(\n"
        "        database_counter_transport, port, 3306\n"
        "    )\n"
    )
    if source.count(old_call) != 1:
        raise BuildError("JOB v34 readiness call site differs")
    source = source.replace(old_call, new_call)
    return source.encode("utf-8")


def payloads(predecessor_manifest):
    result = {}
    for relative in sorted((predecessor_manifest.get("files") or {})):
        if relative == "closure.json":
            continue
        result[relative] = (PREDECESSOR / relative).read_bytes()
    result[COLLECTOR_RELATIVE] = collector_payload()
    result[INVALIDATION_RELATIVE] = INVALIDATION.read_bytes()
    file_hashes_without_closure = {
        relative: sha_bytes(payload) for relative, payload in sorted(result.items())
    }
    old_closure = load(PREDECESSOR / "closure.json")
    closure = dict(old_closure)
    closure["files"] = file_hashes_without_closure
    closure["collector_sha256"] = sha_bytes(result[COLLECTOR_RELATIVE])
    closure["sql_metric_bridge_collector_sha256"] = closure["collector_sha256"]
    closure["predecessor_bundle_id"] = PREDECESSOR_ID
    closure["predecessor_bundle_manifest_sha256"] = (
        PREDECESSOR_MANIFEST_SHA256
    )
    closure["v34_server_port_readiness_invalidation_sha256"] = sha256(
        INVALIDATION
    )
    closure["client_tcp_readiness_contract"] = {
        "tcp_target_host": "127.0.0.1",
        "tcp_target_port": HOST_PORT,
        "server_mysql_port": SERVER_PORT,
        "authenticated_user": AUTHENTICATED_USER,
        "database_counter_tcp_target_port": HOST_PORT,
        "full_client_identity_before_and_after": True,
    }
    result["closure.json"] = canonical(closure)
    return result


def verify_published(bundle, manifest):
    if bundle.name != manifest.get("bundle_id"):
        raise BuildError("published directory identity differs")
    for relative, expected in (manifest.get("files") or {}).items():
        path = bundle / relative
        if path.is_symlink() or not path.is_file() or sha256(path) != expected:
            raise BuildError("published closure differs: %s" % relative)
    if sha_bytes(canonical({
        key: value for key, value in manifest.items() if key != "bundle_id"
    })) != bundle.name:
        raise BuildError("published content address differs")


def publish(payload_map, manifest):
    bundle_id = manifest["bundle_id"]
    target = BUNDLE_ROOT / bundle_id
    BUNDLE_ROOT.mkdir(parents=True, exist_ok=True)
    if target.exists():
        existing = load(target / "bundle_manifest.json")
        if existing != manifest:
            raise BuildError("existing content address has different manifest")
        verify_published(target, existing)
        return target
    stage = BUNDLE_ROOT / (".%s.%s.tmp" % (bundle_id, secrets.token_hex(8)))
    stage.mkdir(mode=0o700)
    try:
        for relative, payload in sorted(payload_map.items()):
            path = stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(path, 0o444)
        manifest_payload = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(manifest_payload)
        (stage / "bundle_manifest.json.sha256").write_text(
            "%s  bundle_manifest.json\n" % sha_bytes(manifest_payload),
            encoding="ascii",
        )
        os.chmod(stage / "bundle_manifest.json", 0o444)
        os.chmod(stage / "bundle_manifest.json.sha256", 0o444)
        os.rename(stage, target)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    verify_published(target, manifest)
    return target


def build():
    predecessor = verify_predecessor()
    verify_invalidation()
    content = payloads(predecessor)
    files = {
        relative: sha_bytes(payload) for relative, payload in sorted(content.items())
    }
    identity = {
        key: value
        for key, value in predecessor.items()
        if key not in ("bundle_id", "files")
    }
    identity.update(
        {
            "status":
                "SEALED_DEBUG_BOOTSTRAP_QUALIFICATION_NOT_R0_OR_FORMAL_AUTHORITY",
            "predecessor_bundle_id": PREDECESSOR_ID,
            "predecessor_bundle_manifest_sha256":
                PREDECESSOR_MANIFEST_SHA256,
            "collector_sha256": sha_bytes(content[COLLECTOR_RELATIVE]),
            "builder_sha256": sha256(HERE),
            "v34_server_port_readiness_invalidation_sha256":
                sha256(INVALIDATION),
            "client_tcp_readiness_contract": {
                "tcp_target_host": "127.0.0.1",
                "tcp_target_port": HOST_PORT,
                "server_mysql_port": SERVER_PORT,
                "authenticated_user": AUTHENTICATED_USER,
                "database_counter_tcp_target_port": HOST_PORT,
                "full_client_identity_before_and_after": True,
            },
            "qualification_authorized": True,
            "fresh_r0_required": True,
            "formal_launch_authorized": False,
            "formal10_authorized": False,
            "launch_authorized": False,
            "accepted_round_authorized": False,
            "files": files,
        }
    )
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity)
    manifest["bundle_id"] = bundle_id
    target = publish(content, manifest)
    return {
        "schema_version": "mysql_job_server_port_successor_build_result_v1",
        "status": "PASS_IMMUTABLE_SUCCESSOR_PUBLISHED",
        "bundle_id": bundle_id,
        "bundle": str(target),
        "bundle_manifest_sha256": sha256(target / "bundle_manifest.json"),
        "collector_sha256": manifest["collector_sha256"],
        "v34_invalidation_sha256": sha256(INVALIDATION),
        "tcp_target_port": HOST_PORT,
        "server_mysql_port": SERVER_PORT,
        "authenticated_user": AUTHENTICATED_USER,
        "database_counter_tcp_target_port": HOST_PORT,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


if __name__ == "__main__":
    try:
        print(json.dumps(build(), indent=2, sort_keys=True))
    except (BuildError, OSError, ValueError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
