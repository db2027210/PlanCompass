#!/usr/bin/env python3
"""Read-only verifier for a sealed, self-contained TPC-H successor bundle."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
import sys
from typing import Any


sys.dont_write_bytecode = True
BUNDLE = Path(__file__).resolve().parents[1]
MANIFEST = BUNDLE / "bundle_manifest.json"
SIDECAR = BUNDLE / "bundle_manifest.json.sha256"
AUTHORITY = BUNDLE / "mysql_tpch_distributed_v2_authority.py"
COLLECTOR = (
    BUNDLE / "qualification/mysql_distributed_live_readback_qualification_v1.py"
)
RUNTIME = BUNDLE / "distributed_runtime_contract.json"
SOCKET_FIXTURE = (
    BUNDLE / "fixtures/node04_tpch_daemon_socket_identity_fixture_v1.json"
)
INVALIDATION = BUNDLE / "invalidated_313_nonselfcontained_bundle.json"
MATERIALIZER = BUNDLE / "launch/mysql_tpch_systemd_oneshot_materializer_v1.py"


class VerifyError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise VerifyError("absent or symlinked: " + str(path))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerifyError("invalid JSON: " + str(path)) from exc
    if not isinstance(value, dict):
        raise VerifyError("JSON object required: " + str(path))
    return value


def relative_files() -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(BUNDLE.rglob("*")):
        relative = path.relative_to(BUNDLE).as_posix()
        if path.is_symlink():
            raise VerifyError("symlink in sealed closure: " + relative)
        if path.is_file():
            if path in (MANIFEST, SIDECAR):
                continue
            if path.name == "__pycache__" or path.suffix == ".pyc":
                raise VerifyError("bytecode/cache in sealed closure: " + relative)
            result[relative] = sha256(path)
        elif not path.is_dir():
            raise VerifyError("special file in sealed closure: " + relative)
    return result


def verify() -> dict[str, Any]:
    manifest = load(MANIFEST)
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    manifest_sha = sha256(MANIFEST)
    if (
        BUNDLE.name != bundle_id
        or hashlib.sha256(canonical(unsigned)).hexdigest() != bundle_id
        or SIDECAR.is_symlink()
        or not SIDECAR.is_file()
        or SIDECAR.read_text(encoding="ascii")
        != manifest_sha + "  bundle_manifest.json\n"
        or relative_files() != manifest.get("files")
    ):
        raise VerifyError("bundle identity, sidecar, or closure differs")
    if (
        manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("postgresql_mutation_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or manifest.get("self_contained_sealed_verification") is not True
        or manifest.get("sealed_bundle_pytest_regression_count") != 39
    ):
        raise VerifyError("formal scope or fail-closed authority differs")

    fixture = load(SOCKET_FIXTURE)
    invalidation = load(INVALIDATION)
    runtime = load(RUNTIME)
    authority = AUTHORITY.read_text(encoding="utf-8")
    collector = COLLECTOR.read_text(encoding="utf-8")
    materializer = MATERIALIZER.read_text(encoding="utf-8")
    socket_contract = manifest.get("socket_listener_authority") or {}
    if (
        fixture.get("socket_path") != "/run/ra-docker-mysql-tpch-v1.sock"
        or fixture.get("main_pid") != 97636
        or fixture.get("filesystem_mode") != "socket"
        or fixture.get("filesystem_inode") != 571336813
        or fixture.get("listener_count") != 1
        or fixture.get("listener_kernel_inode") != 571336812
        or fixture.get("listener_fd_target") != "socket:[571336812]"
        or fixture.get("listener_selector")
        != {"flags": "00010000", "type": "0001", "state": "01"}
        or socket_contract.get("filesystem_validation") != "S_IFSOCK_only"
        or socket_contract.get("listener_cardinality") != 1
        or socket_contract.get("listener_kernel_inode") != 571336812
        or socket_contract.get("main_pid_fd_target_required")
        != "socket:[571336812]"
        or invalidation.get("status") != "INVALIDATED_DO_NOT_RUN_OR_CREDIT"
        or invalidation.get("accepted_rounds_credit") != 0
        or invalidation.get("bundle_id")
        != "313637860bee5f333c11d8ae92689ef674585d8e7311c82b75c1cb7cc1fd9555"
    ):
        raise VerifyError("socket identity or invalidation evidence differs")
    required_source = (
        'row["flags"] == "00010000"',
        'row["type"] == "0001"',
        'row["state"] == "01"',
        '"socket:[%d]" % listener["inode"]',
        "if len(listeners) != 1:",
    )
    if any(token not in authority for token in required_source) or any(
        token not in collector for token in required_source
    ):
        raise VerifyError("unique kernel-listener/MainPID-FD logic absent")
    prohibited_source = (
        '"socket:[%d]" % socket_state.st_ino',
        "socket_identity not in fd_targets",
    )
    if any(token in authority for token in prohibited_source) or any(
        token in collector for token in prohibited_source
    ):
        raise VerifyError("filesystem inode is incorrectly used as kernel identity")
    if (
        (BUNDLE / "systemd/ra-docker-mysql-tpch-v1.service").exists()
        or "--service-type=oneshot" not in materializer
        or "--remain-after-exit" not in materializer
        or 'PROHIBITED_OPTIONS = ("--wait", "--collect")' not in materializer
        or (runtime.get("database_counter_transport") or {}).get(
            "missing_or_timeout_result"
        )
        not in ("FAIL", None)
    ):
        raise VerifyError("transient oneshot or counter fail-closed policy differs")
    for path in [BUNDLE, *BUNDLE.rglob("*")]:
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise VerifyError("sealed immutable mode differs: " + str(path))
    return {
        "schema_version": "mysql_tpch_self_contained_bundle_verification_v1",
        "status": "PASS_SELF_CONTAINED_SEALED_BUNDLE",
        "bundle_id": bundle_id,
        "bundle_manifest_sha256": manifest_sha,
        "verified_files": len(manifest["files"]),
        "pytest_regression_count": 39,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions": 0,
    }


def main() -> int:
    print(json.dumps(verify(), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (VerifyError, OSError, UnicodeError, ValueError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
