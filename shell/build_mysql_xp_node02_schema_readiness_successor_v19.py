#!/usr/bin/env python3
"""Build immutable XP v19 with successor-schema and slow-start fixes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PARENT_ID = "f43f33b5d60cf6de2fe63f96852e6a46ea3b132b5d52039a1e60021771dc9262"
PARENT_MANIFEST_SHA256 = "a3d1f3750fc25b20568d7279ae20d2973384ef348992198ac4bd96aea797e1e0"
PARENT = ROOT / "artifacts/mysql_xp_node02_successor_v18" / PARENT_ID
OUTPUT_ROOT = ROOT / "artifacts/mysql_xp_node02_successor_v19"
SERVICE = "launch/mysql_xp_node02_adopted_service_v1.py"
AUTHORITY = "qualification/mysql_xp_qualification_authority_v1.py"
COLLECTOR = "qualification/mysql_distributed_live_readback_qualification_v1.py"
MATERIALIZER = "launch/mysql_xp_node02_formal10_materializer_v1.py"
BOOTSTRAP_MATERIALS = "test_support/mysql_xp_qualification_bootstrap_materials_v5.py"
COMPAT_TEST = "tests/test_mysql_xp_systemd_compatibility_v11.py"
REGRESSION_TEST = "tests/test_mysql_xp_schema_readiness_v19.py"
BUILDER = "build_mysql_xp_node02_schema_readiness_successor_v19.py"
REMEDIATION = "xp_node02_schema_readiness_remediation_v1.json"
INVALIDATION = "invalidated_f43_bootstrap_timeout_attempt.json"


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError(label + " is not an object")
    return value


def relative_files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError("symlink prohibited: " + str(path))
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in (
            "bundle_manifest.json", "bundle_manifest.json.sha256"
        ):
            result[relative] = sha_file(path)
    return result


def replace_exact(payload: bytes, old: str, new: str, count: int, label: str) -> bytes:
    text = payload.decode("utf-8")
    if text.count(old) != count:
        raise BuildError(label + " predecessor cardinality differs")
    return text.replace(old, new).encode("utf-8")


def patch_service(payload: bytes) -> bytes:
    payload = replace_exact(
        payload,
        "DATADIR_REGULAR_MODE = 0o600\n",
        "DATADIR_REGULAR_MODE = 0o600\n"
        "DATABASE_SOCKET_READINESS_TIMEOUT_SECONDS = 600\n"
        "DATABASE_SOCKET_READINESS_POLL_SECONDS = 1\n",
        1, "readiness constants",
    )
    old = '''    readiness = None
    for _unused in range(120):
        readiness = _run(
            _docker_argv(
                "exec", plan["database"]["name"],
                "mysql", "--user=root", "--protocol=socket", "--batch", "--raw",
                "--skip-column-names", "--execute", "SELECT 1",
            ),
            check=False,
        )
        if readiness.returncode == 0 and readiness.stdout.strip() == "1":
            break
        time.sleep(1)
    else:
        raise ServiceError("database socket bootstrap readiness failed")
'''
    new = '''    _wait_for_database_socket(plan)
'''
    payload = replace_exact(payload, old, new, 1, "bounded readiness call")
    marker = "def bootstrap(plan, copy_authority, output, main_agent_token):\n"
    helper = '''def _wait_for_database_socket(plan):
    """Wait for an exact socket query while preserving fail-closed startup."""
    for _unused in range(DATABASE_SOCKET_READINESS_TIMEOUT_SECONDS):
        readiness = _run(
            _docker_argv(
                "exec", plan["database"]["name"],
                "mysql", "--user=root", "--protocol=socket", "--batch", "--raw",
                "--skip-column-names", "--execute", "SELECT 1",
            ),
            check=False,
        )
        if readiness.returncode == 0 and readiness.stdout.strip() == "1":
            return readiness
        time.sleep(DATABASE_SOCKET_READINESS_POLL_SECONDS)
    raise ServiceError("database socket bootstrap readiness failed")


'''
    payload = replace_exact(payload, marker, helper + marker, 1, "readiness helper")
    return replace_exact(
        payload,
        '        != "mysql_xp_node02_successor_bundle_v18"',
        '        != "mysql_xp_node02_successor_bundle_v19"',
        1, "service outer schema",
    )


def patch_successor_schemas(payload: bytes, label: str) -> bytes:
    old = ('        or manifest.get("schema_version")\n'
           '        != "mysql_xp_distributed_formal10_launch_v3"')
    new = ('        or manifest.get("schema_version") not in (\n'
           '            "mysql_xp_distributed_formal10_launch_v3",\n'
           '            "mysql_xp_node02_successor_bundle_v18",\n'
           '            "mysql_xp_node02_successor_bundle_v19",\n'
           '        )')
    return replace_exact(payload, old, new, 1, label)


def verify_parent() -> dict[str, Any]:
    manifest_path = PARENT / "bundle_manifest.json"
    manifest = load(manifest_path, "parent manifest")
    if (
        manifest.get("bundle_id") != PARENT_ID
        or sha_file(manifest_path) != PARENT_MANIFEST_SHA256
        or relative_files(PARENT) != manifest.get("files")
        or manifest.get("schema_version") != "mysql_xp_node02_successor_bundle_v18"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("parent identity, closure, scope, or authority differs")
    return manifest


def freeze(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def build() -> Path:
    parent = verify_parent()
    content = {
        relative: (PARENT / relative).read_bytes()
        for relative in sorted(parent["files"])
    }
    content[SERVICE] = patch_service(content[SERVICE])
    content[AUTHORITY] = patch_successor_schemas(
        content[AUTHORITY], "qualification authority outer schemas"
    )
    content[MATERIALIZER] = patch_successor_schemas(
        content[MATERIALIZER], "formal10 materializer outer schemas"
    )
    content[COLLECTOR] = replace_exact(
        content[COLLECTOR],
        '        "mysql_xp_distributed_formal10_launch_v3",\n',
        '        "mysql_xp_distributed_formal10_launch_v3",\n'
        '        "mysql_xp_node02_successor_bundle_v18",\n'
        '        "mysql_xp_node02_successor_bundle_v19",\n',
        1, "live collector outer schemas",
    )
    content[BOOTSTRAP_MATERIALS] = replace_exact(
        content[BOOTSTRAP_MATERIALS],
        '        != "46c5207d29682d977250fb1be6f3ed5791c6f8ff6e5f162c49591bb311e9c8d4"',
        '        != "' + PARENT_ID + '"',
        1, "bootstrap-material predecessor binding",
    )
    content[COMPAT_TEST] = replace_exact(
        content[COMPAT_TEST],
        'manifest["schema_version"]=="mysql_xp_node02_successor_bundle_v18"',
        'manifest["schema_version"]=="mysql_xp_node02_successor_bundle_v19"',
        1, "compatibility-test schema",
    )
    content[REGRESSION_TEST] = (ROOT / REGRESSION_TEST).read_bytes()
    content[BUILDER] = HERE.read_bytes()
    content[REMEDIATION] = canonical({
        "schema_version": "mysql_xp_node02_schema_readiness_remediation_v1",
        "status": "OFFLINE_SCHEMA_AND_SLOW_START_REMEDIATED_NEW_QUALIFICATION_REQUIRED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "parent_bundle_id": PARENT_ID,
        "accepted_outer_manifest_schemas": [
            "mysql_xp_distributed_formal10_launch_v3",
            "mysql_xp_node02_successor_bundle_v18",
            "mysql_xp_node02_successor_bundle_v19",
        ],
        "bootstrap_socket_query": "SELECT 1",
        "bootstrap_readiness_timeout_seconds": 600,
        "bootstrap_readiness_poll_seconds": 1,
        "health_check_bypass_prohibited": True,
        "fresh_qualification_required": True,
        "fresh_r0_required": True,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    })
    content[INVALIDATION] = canonical({
        "schema_version": "mysql_xp_v18_bootstrap_timeout_invalidation_v1",
        "invalidated_bundle_id": PARENT_ID,
        "invalidated_attempt_id":
            "xp-formal-qual-20260803T011529Z-696b0d2b9556ee87a6b51737",
        "reason": "database socket readiness exceeded the fixed 120-second bootstrap budget under the sealed 524288-B/s write cap",
        "database_container_left_running": True,
        "client_container_left_created_not_started": True,
        "adopted_service_published": False,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    })

    files = {relative: sha_bytes(payload)
             for relative, payload in sorted(content.items())}
    identity = {key: value for key, value in parent.items()
                if key not in ("bundle_id", "files")}
    identity.update({
        "schema_version": "mysql_xp_node02_successor_bundle_v19",
        "status": "SEALED_SCHEMA_AND_SLOW_START_SUCCESSOR_NOT_LAUNCHED",
        "builder_revision": "v19_successor_schema_and_slow_start",
        "builder_sha256": sha_file(HERE),
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
        "supersedes_bundle_id": PARENT_ID,
        "service_sha256": files[SERVICE],
        "collector_sha256": files[COLLECTOR],
        "fresh_r0_required": True,
        "historical_accepted_round_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "offline_build_only": True,
        "remote_actions": 0,
        "files": files,
    })
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity, bundle_id=bundle_id)

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    destination = OUTPUT_ROOT / bundle_id
    if not destination.exists():
        stage = Path(tempfile.mkdtemp(prefix=".xp-v19.", dir=OUTPUT_ROOT))
        try:
            for relative, payload in sorted(content.items()):
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            raw = canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii"
            )
            freeze(stage)
            os.replace(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)

    if relative_files(destination) != files:
        raise BuildError("published successor closure differs")
    unsigned = dict(load(destination / "bundle_manifest.json", "manifest"))
    actual_id = unsigned.pop("bundle_id", None)
    if actual_id != destination.name or sha_bytes(canonical(unsigned)) != destination.name:
        raise BuildError("published successor content identity differs")
    for path in (destination, *destination.rglob("*")):
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError("published successor is mutable")
    print(json.dumps({
        "status": "PASS_IMMUTABLE_XP_V19_SCHEMA_SLOW_START_SUCCESSOR",
        "bundle_id": bundle_id,
        "bundle": str(destination),
        "verified_files": len(files),
        "fresh_qualification_required": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }, sort_keys=True))
    return destination


if __name__ == "__main__":
    try:
        build()
    except (BuildError, OSError, ValueError, KeyError) as exc:
        raise SystemExit("NOT_READY: " + str(exc))
