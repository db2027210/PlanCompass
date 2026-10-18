#!/usr/bin/env python3
"""Build the immutable TPC-H Unix-listener-identity successor offline."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PUBLISHER = ROOT / "shell/build_mysql_tpch_live_daemon_successor_v1.py"
PUBLISHER_SHA256 = (
    "1bdace086bde58947cd0fa8c56ff75380e268d2146a5e2e855c58f99caa68378"
)
PREDECESSOR_ID = (
    "543a980444cae28689918e100ea2300c61edbde16c30478d4bf5193fce2955d4"
)
PREDECESSOR_MANIFEST_SHA256 = (
    "a1d9adda7af1a8129d777987c7ff9883854daf016eb81e260b79abad4387a96e"
)
PREDECESSOR = (
    ROOT / "artifacts/mysql_tpch_live_daemon_successor_v1"
    / PREDECESSOR_ID
)
BUNDLE_ROOT = ROOT / "artifacts/mysql_tpch_socket_identity_successor_v1"
AUTHORITY = "mysql_tpch_distributed_v2_authority.py"
COLLECTOR = "qualification/mysql_distributed_live_readback_qualification_v1.py"
RUNTIME = "distributed_runtime_contract.json"
INVALIDATION = (
    ROOT / "preflight/mysql_tpch_543_socket_inode_invalidation_20260731.json"
)
SOCKET_FIXTURE = (
    ROOT / "preflight/node04_tpch_daemon_socket_identity_fixture_v1.json"
)
INVALIDATION_RELATIVE = "invalidated_543_socket_inode_bundle.json"
SOCKET_FIXTURE_RELATIVE = (
    "fixtures/node04_tpch_daemon_socket_identity_fixture_v1.json"
)
BUILDER_RELATIVE = "builder/build_mysql_tpch_socket_identity_successor_v1.py"


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2,
                   allow_nan=False) + "\n"
    ).encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def load(path: Path) -> dict[str, Any]:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise BuildError("absent or symlinked: " + str(path))
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError("JSON object required: " + str(path))
    return value


def publisher():
    if sha256(PUBLISHER) != PUBLISHER_SHA256:
        raise BuildError("sealed publisher identity differs")
    spec = importlib.util.spec_from_file_location("tpch_socket_publisher", PUBLISHER)
    if spec is None or spec.loader is None:
        raise BuildError("publisher is not importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def atomic_publisher():
    return publisher().atomic_publisher()


def verify_predecessor() -> dict[str, Any]:
    manifest_path = PREDECESSOR / "bundle_manifest.json"
    manifest = load(manifest_path)
    unsigned = {key: value for key, value in manifest.items() if key != "bundle_id"}
    prohibited = [
        path for path in PREDECESSOR.rglob("*")
        if path.name == "__pycache__" or path.suffix == ".pyc"
    ]
    if (
        sha256(manifest_path) != PREDECESSOR_MANIFEST_SHA256
        or manifest.get("bundle_id") != PREDECESSOR_ID
        or sha_bytes(canonical(unsigned)) != PREDECESSOR_ID
        or atomic_publisher().relative_files(PREDECESSOR) != manifest.get("files")
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or prohibited
    ):
        raise BuildError("543 predecessor identity/closure differs")
    return manifest


def verify_inputs() -> None:
    invalid = load(INVALIDATION)
    fixture = load(SOCKET_FIXTURE)
    stdout = fixture.get("stdout")
    if (
        invalid.get("status") != "INVALIDATED_DO_NOT_RUN_OR_CREDIT"
        or invalid.get("bundle_id") != PREDECESSOR_ID
        or invalid.get("bundle_manifest_sha256")
        != PREDECESSOR_MANIFEST_SHA256
        or invalid.get("container_started") is not False
        or invalid.get("database_started") is not False
        or invalid.get("workload_started") is not False
        or invalid.get("accepted_rounds_credit") != 0
        or invalid.get("replay_or_rerun_authorized") is not False
        or hashlib.sha256(str(stdout).encode()).hexdigest()
        != "9ccd4e410ed5fee46ed8a104a55eae0717cf244cc3a6a4fc20ef26c7e3b211f3"
        or fixture.get("socket_path")
        != "/run/ra-docker-mysql-tpch-v1.sock"
        or fixture.get("main_pid") != 97636
        or fixture.get("filesystem_inode") != 571336813
        or fixture.get("filesystem_mode") != "socket"
        or fixture.get("path_row_count") != 6
        or fixture.get("listener_selector")
        != {"flags": "00010000", "type": "0001", "state": "01"}
        or fixture.get("listener_count") != 1
        or fixture.get("listener_kernel_inode") != 571336812
        or fixture.get("listener_fd_target") != "socket:[571336812]"
        or (fixture.get("main_pid_fd_targets") or {}).get("3")
        != "socket:[571336812]"
    ):
        raise BuildError("socket identity invalidation/fixture differs")


SOCKET_HELPER = r'''
def _unix_listener_identity(
    socket_path: str, main_pid: int
) -> dict[str, Any]:
    try:
        lines = Path("/proc/net/unix").read_text(
            encoding="ascii"
        ).splitlines()
    except (OSError, UnicodeError) as exc:
        raise AuthorityError("Unix socket table readback failed") from exc
    path_rows = []
    listeners = []
    for line in lines:
        fields = line.split()
        if len(fields) < 8 or fields[7] != socket_path:
            continue
        row = {
            "raw": line,
            "flags": fields[3],
            "type": fields[4],
            "state": fields[5],
            "inode": int(fields[6]),
            "path": fields[7],
        }
        path_rows.append(row)
        if (
            row["flags"] == "00010000"
            and row["type"] == "0001"
            and row["state"] == "01"
        ):
            listeners.append(row)
    if len(listeners) != 1:
        raise AuthorityError(
            "Unix socket exact-path listener cardinality differs"
        )
    listener = listeners[0]
    try:
        fd_targets = {
            entry.name: os.readlink(entry)
            for entry in (
                Path("/proc") / str(main_pid) / "fd"
            ).iterdir()
        }
    except (OSError, PermissionError) as exc:
        raise AuthorityError(
            "dedicated daemon socket FD inventory is unreadable"
        ) from exc
    expected_target = "socket:[%d]" % listener["inode"]
    owners = sorted(
        name for name, target in fd_targets.items()
        if target == expected_target
    )
    if not owners:
        raise AuthorityError(
            "dedicated daemon MainPID does not own listener inode"
        )
    return {
        "schema_version": "mysql_tpch_unix_listener_identity_v1",
        "socket_path": socket_path,
        "path_row_count": len(path_rows),
        "listener_count": 1,
        "listener_flags": listener["flags"],
        "listener_type": listener["type"],
        "listener_state": listener["state"],
        "listener_kernel_inode": listener["inode"],
        "listener_fd_target": expected_target,
        "main_pid": main_pid,
        "main_pid_owner_fds": owners,
        "main_pid_holds_listener": True,
    }
'''


COLLECTOR_SOCKET_HELPER = SOCKET_HELPER.replace(
    "AuthorityError", "QualificationError"
)


def replace_once(source: str, old: str, new: str, label: str) -> str:
    if source.count(old) != 1:
        raise BuildError("patch point differs: " + label)
    return source.replace(old, new, 1)


def patch_socket_identity(
    source: str, *, collector: bool = False
) -> str:
    helper = COLLECTOR_SOCKET_HELPER if collector else SOCKET_HELPER
    marker = (
        "\ndef dedicated_daemon_readback():\n"
        if collector
        else "\ndef _dedicated_daemon_readback(\n"
    )
    source = replace_once(
        source, marker, "\n" + helper + marker,
        "socket listener helper",
    )
    if collector:
        source = replace_once(
            source,
            '''    socket_state = os.stat(DOCKER_SOCKET, follow_symlinks=False)
    if (
''',
            '''    socket_state = os.stat(DOCKER_SOCKET, follow_symlinks=False)
    listener_identity = _unix_listener_identity(
        DOCKER_SOCKET, main_pid
    )
    if (
''',
            "collector listener call",
        )
        source = replace_once(
            source,
            '''        "socket": DOCKER_SOCKET,
        "socket_uri": DOCKER_SOCKET_URI,
''',
            '''        "socket": DOCKER_SOCKET,
        "socket_uri": DOCKER_SOCKET_URI,
        "socket_filesystem_inode": socket_state.st_ino,
        "socket_listener_identity": listener_identity,
''',
            "collector listener evidence",
        )
    else:
        source = replace_once(
            source,
            '''    socket_identity = "socket:[%d]" % socket_state.st_ino
    try:
        fd_targets = {
            os.readlink(path)
            for path in (Path("/proc") / str(main_pid) / "fd").iterdir()
        }
    except (OSError, PermissionError) as exc:
        raise AuthorityError("dedicated daemon socket owner is unreadable") from exc
    if socket_identity not in fd_targets:
        raise AuthorityError("dedicated daemon MainPID does not own socket")
''',
            '''    listener_identity = _unix_listener_identity(
        DOCKER_SOCKET, main_pid
    )
''',
            "authority listener replacement",
        )
        source = replace_once(
            source,
            '''        "socket_inode": socket_state.st_ino,
        "socket_owned_by_main_pid": True,
''',
            '''        "socket_filesystem_inode": socket_state.st_ino,
        "socket_listener_identity": listener_identity,
        "socket_listener_kernel_inode":
            listener_identity["listener_kernel_inode"],
        "socket_owned_by_main_pid": True,
''',
            "authority listener evidence",
        )
    return source


def authority_payload() -> bytes:
    source = (PREDECESSOR / AUTHORITY).read_text(encoding="utf-8")
    return patch_socket_identity(source).encode("utf-8")


def collector_payload() -> bytes:
    source = (PREDECESSOR / COLLECTOR).read_text(encoding="utf-8")
    return patch_socket_identity(source, collector=True).encode("utf-8")


def runtime_payload() -> bytes:
    value = load(PREDECESSOR / RUNTIME)
    fixture = load(SOCKET_FIXTURE)
    value["socket_listener_authority"] = {
        "socket_path": fixture["socket_path"],
        "filesystem_validation": "S_IFSOCK_only",
        "filesystem_inode_is_not_kernel_listener_inode": True,
        "proc_net_unix_exact_path_required": True,
        "listener_selector": fixture["listener_selector"],
        "listener_cardinality": 1,
        "listener_kernel_inode": fixture["listener_kernel_inode"],
        "main_pid_fd_target_required": fixture["listener_fd_target"],
        "no_listener_policy": "fail_closed",
        "multiple_listener_policy": "fail_closed",
        "fd_not_held_policy": "fail_closed",
        "fixture_sha256": sha256(SOCKET_FIXTURE),
    }
    return canonical(value)


def payloads(predecessor: dict[str, Any]) -> dict[str, bytes]:
    result = {
        relative: (PREDECESSOR / relative).read_bytes()
        for relative in sorted(predecessor["files"])
    }
    result[AUTHORITY] = authority_payload()
    result[COLLECTOR] = collector_payload()
    result[RUNTIME] = runtime_payload()
    result[INVALIDATION_RELATIVE] = INVALIDATION.read_bytes()
    result[SOCKET_FIXTURE_RELATIVE] = SOCKET_FIXTURE.read_bytes()
    result[BUILDER_RELATIVE] = HERE.read_bytes()
    return result


def build() -> dict[str, Any]:
    predecessor = verify_predecessor()
    verify_inputs()
    content = payloads(predecessor)
    files = {name: sha_bytes(data) for name, data in sorted(content.items())}
    identity = {
        key: value for key, value in predecessor.items()
        if key not in ("bundle_id", "files")
    }
    components = dict(identity.get("component_hashes") or {})
    components["bootstrap_authority"] = files[AUTHORITY]
    components["collector"] = files[COLLECTOR]
    socket_contract = json.loads(runtime_payload())["socket_listener_authority"]
    identity.update({
        "predecessor_bundle_id": PREDECESSOR_ID,
        "predecessor_manifest_sha256": PREDECESSOR_MANIFEST_SHA256,
        "builder_sha256": sha256(HERE),
        "component_hashes": components,
        "distributed_runtime_contract_sha256": files[RUNTIME],
        "invalidated_543_socket_inode_bundle_sha256": sha256(INVALIDATION),
        "node04_socket_identity_fixture_sha256": sha256(SOCKET_FIXTURE),
        "socket_listener_authority": socket_contract,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "offline_build_only": True,
        "remote_actions": 0,
        "files": files,
    })
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity)
    manifest["bundle_id"] = bundle_id
    atomic = atomic_publisher()
    atomic.BUNDLE_ROOT = BUNDLE_ROOT
    bundle = atomic.publish(content, manifest)
    return {
        "schema_version": "mysql_tpch_socket_identity_successor_build_v1",
        "status": "PASS_IMMUTABLE_SUCCESSOR_PUBLISHED",
        "bundle_id": bundle_id,
        "bundle": str(bundle),
        "bundle_manifest_sha256": sha256(bundle / "bundle_manifest.json"),
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions": 0,
    }


def main() -> int:
    print(json.dumps(build(), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BuildError, OSError, UnicodeError, ValueError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
