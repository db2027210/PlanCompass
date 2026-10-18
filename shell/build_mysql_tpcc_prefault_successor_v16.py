#!/usr/bin/env python3
"""Build the immutable TPC-C v16 prefault/materializer successor.

V15 correctly records every client mount backed by the target device during
prefault.  The sealed MySQL image declares an otherwise unused anonymous
``/var/lib/mysql`` volume, so that destination is present in the truthful
prefault evidence.  V15's materializer omitted this fixed image-declared
destination from its allow-list and therefore rejected its own evidence.

This offline-only successor adds that one destination to the allow-list.  It
does not change the database/client resource envelopes, workload, Algorithm C,
or any launch/accepted-round authority.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "c95c59a40423dfe1cc47c09a357ad78aafd4cafd69056c51ba253254dedc22ec"
PARENT = ROOT / "artifacts/mysql_tpcc_typed_envelope_successor_v15" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpcc_prefault_successor_v16"
SCHEMA = "mysql_tpcc_prefault_successor_bundle_v16"
MATERIALIZER = "authority/mysql_tpcc_materialize_phase_command_v2.py"
SERVICE_V1 = "authority/mysql_tpcc_node04_adopted_service_v1.py"
BUILDER = "authority/build_mysql_tpcc_prefault_successor_v16.py"
INVALIDATION = "authority/mysql_tpcc_v15_prefault_allowlist_invalidation_20260801.json"
NETWORK_INVALIDATION = "authority/mysql_tpcc_v16_unreachable_publish_invalidation_20260801.json"
TEST = "tests/test_mysql_tpcc_prefault_successor_v16.py"


class BuildError(RuntimeError):
    pass


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_parent() -> dict:
    path = PARENT / "bundle_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    if (
        manifest.get("bundle_id") != PARENT_ID
        or sha_bytes(canonical(unsigned)) != PARENT_ID
        or manifest.get("schema_version")
        != "mysql_tpcc_typed_envelope_successor_bundle_v15"
        or manifest.get("scenario") != "tpcc"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("v15 parent identity/scope differs")
    for relative, expected in (manifest.get("files") or {}).items():
        candidate = PARENT / relative
        if candidate.is_symlink() or not candidate.is_file() or sha_path(candidate) != expected:
            raise BuildError("v15 parent closure differs: " + relative)
    return manifest


def patch_materializer(data: bytes) -> bytes:
    source = data.decode("utf-8")
    old = '''    allowed_destinations = {
        "/opt/mysql-tpcc-bundle", "/opt/mysql-tpcc-jdk",
        "/opt/mysql-tpcc-python", "/opt/mysql-tpcc-config",
        "/measurement/config",
    }'''
    new = '''    allowed_destinations = {
        "/opt/mysql-tpcc-bundle", "/opt/mysql-tpcc-jdk",
        "/opt/mysql-tpcc-python", "/opt/mysql-tpcc-config",
        "/measurement/config",
        # The sealed MySQL image declares this anonymous client volume.
        # Prefault reads it but never writes it; the workload does not use it.
        "/var/lib/mysql",
    }'''
    if source.count(old) != 1:
        raise BuildError("v15 prefault allow-list patch cardinality differs")
    return source.replace(old, new).encode("utf-8")


def patch_service(data: bytes) -> bytes:
    source = data.decode("utf-8")
    old_create = '''        "--device-write-bps", f"{DEVICE}:{IO_BPS}",
        "--publish", f"127.0.0.1:{PORT}:3306",
        "--mount", f"type=bind,src={datadir},dst=/var/lib/mysql",'''
    new_create = '''        "--device-write-bps", f"{DEVICE}:{IO_BPS}",
        "--network", "host",
        "--mount", f"type=bind,src={datadir},dst=/var/lib/mysql",'''
    if source.count(old_create) != 1:
        raise BuildError("v16 database network create patch cardinality differs")
    source = source.replace(old_create, new_create)
    old_args = '''        "--datadir=/var/lib/mysql",
        "--innodb-buffer-pool-size=4294967296",
    ]'''
    new_args = '''        "--datadir=/var/lib/mysql",
        "--innodb-buffer-pool-size=4294967296",
        "--bind-address=127.0.0.1",
        f"--port={PORT}",
    ]'''
    if source.count(old_args) != 1:
        raise BuildError("v16 mysqld loopback port patch cardinality differs")
    source = source.replace(old_args, new_args)
    old_verify = '''        ports = (host.get("PortBindings") or {}).get("3306/tcp") or []
        if (
            not isinstance(mount, Mapping)
            or mount.get("RW") is not True
            or mount.get("Source") != plan["database"]["datadir"]
            or (
                not trusted_v1_blkio
                and read_limits != [{"Path": DEVICE, "Rate": IO_BPS}]
            )
            or (
                not trusted_v1_blkio
                and write_limits != [{"Path": DEVICE, "Rate": IO_BPS}]
            )
            or ports
            != [{"HostIp": "127.0.0.1", "HostPort": str(PORT)}]
        ):
            raise ServiceError("database datadir/IO/port envelope differs")'''
    new_verify = '''        command = config.get("Cmd") or []
        if (
            not isinstance(mount, Mapping)
            or mount.get("RW") is not True
            or mount.get("Source") != plan["database"]["datadir"]
            or (
                not trusted_v1_blkio
                and read_limits != [{"Path": DEVICE, "Rate": IO_BPS}]
            )
            or (
                not trusted_v1_blkio
                and write_limits != [{"Path": DEVICE, "Rate": IO_BPS}]
            )
            or host.get("NetworkMode") != "host"
            or (host.get("PortBindings") or {}) != {}
            or "--bind-address=127.0.0.1" not in command
            or f"--port={PORT}" not in command
        ):
            raise ServiceError("database datadir/IO/host-network port envelope differs")'''
    if source.count(old_verify) != 1:
        raise BuildError("v16 database network readback patch cardinality differs")
    return source.replace(old_verify, new_verify).encode("utf-8")


def test_source() -> bytes:
    return b'''from __future__ import annotations\nimport hashlib,json,py_compile,stat\nfrom pathlib import Path\nROOT=Path(__file__).resolve().parents[1]\ndef sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()\ndef test_v16_identity_and_prefault_allowlist():\n m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")\n raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\\n").encode()\n assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()\n assert m["schema_version"]=="mysql_tpcc_prefault_successor_bundle_v16"\n assert m["scenario"]=="tpcc" and m["formal_scope"]=="mysql_only"\n assert m["cross_engine_comparison_prohibited"] is True\n assert m["formal_launch_authorized"] is False and m["accepted_round_authorized"] is False\n assert m["database_network"]=="host_loopback_mysql_65106"\n assert stat.S_IMODE(ROOT.stat().st_mode)==0o555\n for directory in (item for item in ROOT.rglob("*") if item.is_dir()):assert stat.S_IMODE(directory.stat().st_mode)==0o555\n for relative,expected in m["files"].items():\n  path=ROOT/relative\n  assert sha(path)==expected and stat.S_IMODE(path.stat().st_mode)&0o004\n materializer=(ROOT/"authority/mysql_tpcc_materialize_phase_command_v2.py").read_text()\n assert materializer.count('"/var/lib/mysql"')==1\n assert "The sealed MySQL image declares this anonymous client volume" in materializer\n service=(ROOT/"authority/mysql_tpcc_node04_adopted_service_v1.py").read_text()\n assert '"--network", "host"' in service\n assert '"--publish"' not in service\n assert '"--bind-address=127.0.0.1"' in service and 'f"--port={PORT}"' in service\n assert 'database datadir/IO/host-network port envelope differs' in service\n py_compile.compile(str(ROOT/"authority/mysql_tpcc_materialize_phase_command_v2.py"),doraise=True,cfile=str(Path("/tmp")/(ROOT.name+"-materializer.pyc")))\n py_compile.compile(str(ROOT/"authority/mysql_tpcc_node04_adopted_service_v1.py"),doraise=True,cfile=str(Path("/tmp")/(ROOT.name+"-service.pyc")))\n'''


def freeze(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink in v16 bundle")
        mode = stat.S_IMODE(path.stat().st_mode)
        if path.is_dir():
            # The formal classpath gate runs as the unprivileged client and
            # requires every bundle directory to be read-only traversable.
            os.chmod(path, 0o555)
        else:
            os.chmod(path, (mode & ~0o222) | 0o444)
    os.chmod(root, 0o555)


def build() -> Path:
    parent = load_parent()
    payload = {
        relative: (PARENT / relative).read_bytes()
        for relative in sorted(parent["files"])
    }
    modes = {
        relative: stat.S_IMODE((PARENT / relative).stat().st_mode)
        for relative in parent["files"]
    }
    payload[MATERIALIZER] = patch_materializer(payload[MATERIALIZER])
    payload[SERVICE_V1] = patch_service(payload[SERVICE_V1])
    for relative, data in list(payload.items()):
        if relative.startswith("tests/") and relative.endswith(".py"):
            payload[relative] = data.replace(
                b"mysql_tpcc_typed_envelope_successor_bundle_v15", SCHEMA.encode()
            )
    payload[BUILDER] = Path(__file__).read_bytes()
    modes[BUILDER] = 0o444
    payload[INVALIDATION] = canonical({
        "schema_version": "mysql_tpcc_v15_prefault_allowlist_invalidation_v1",
        "invalidated_bundle_id": PARENT_ID,
        "reason": "truthful prefault evidence includes the sealed image-declared /var/lib/mysql client volume, but the v15 materializer omitted it from its allow-list",
        "replacement_behavior": "allow that exact destination for read-only prefault evidence validation",
        "resource_envelope_unchanged": True,
        "workload_and_algorithm_c_unchanged": True,
        "fresh_solo_paired_and_r0_required": True,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "postgresql_mutation": False,
    })
    modes[INVALIDATION] = 0o444
    payload[NETWORK_INVALIDATION] = canonical({
        "schema_version": "mysql_tpcc_v16_unreachable_publish_invalidation_v1",
        "invalidated_bundle_ids": [
            "e1a5732d7041370844f1e2fa3c99b3a2f1b4f0bbf96f39ccc39eb9ee558d5073",
            "8ee51b92f74026541b031542e6b302e8d334738524aca606ac3053feb3d64ab7",
        ],
        "reason": "dedicated dockerd is sealed with bridge=none, iptables=false and userland-proxy=false, so Docker loopback publish cannot create a reachable 65106 listener",
        "replacement_behavior": "database uses host network and mysqld binds only 127.0.0.1:65106; client remains host-networked",
        "resource_envelope_unchanged": True,
        "workload_and_algorithm_c_unchanged": True,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_round_credit": 0,
        "postgresql_mutation": False,
    })
    modes[NETWORK_INVALIDATION] = 0o444
    payload[TEST] = test_source()
    modes[TEST] = 0o444
    files = {relative: sha_bytes(data) for relative, data in sorted(payload.items())}
    unsigned = {key: value for key, value in parent.items() if key not in {"bundle_id", "files"}}
    unsigned.update({
        "schema_version": SCHEMA,
        "status": "SEALED_PREFAULT_MATERIALIZER_SUCCESSOR_NOT_LAUNCHED",
        "base_bundle_id": PARENT_ID,
        "base_manifest_sha256": sha_path(PARENT / "bundle_manifest.json"),
        "prefault_image_volume_destination": "/var/lib/mysql",
        "bundle_access_mode": "read_only_world_readable_traversable",
        "database_network": "host_loopback_mysql_65106",
        "resource_envelope_unchanged": True,
        "workload_and_algorithm_c_unchanged": True,
        "immutable": True,
        "offline_build_only": True,
        "remote_actions": 0,
        "qualification_authorized": False,
        "typed_capture_authorized": False,
        "formal_r0_authorized": False,
        "debug_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "fresh_solo_paired_and_r0_required": True,
        "files": files,
    })
    bundle_id = sha_bytes(canonical(unsigned))
    manifest = {**unsigned, "bundle_id": bundle_id}
    target = OUT / bundle_id
    if target.exists():
        if json.loads((target / "bundle_manifest.json").read_text()) != manifest:
            raise BuildError("existing v16 target differs")
        return target
    OUT.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="." + bundle_id + ".", dir=str(OUT)))
    try:
        for relative, data in payload.items():
            path = stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            os.chmod(path, modes.get(relative, 0o444) & ~0o222)
        raw = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(raw)
        (stage / "bundle_manifest.json.sha256").write_text(
            sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii"
        )
        freeze(stage)
        os.rename(stage, target)
    finally:
        if stage.exists():
            os.chmod(stage, 0o755)
            shutil.rmtree(stage)
    return target


if __name__ == "__main__":
    print(build())
