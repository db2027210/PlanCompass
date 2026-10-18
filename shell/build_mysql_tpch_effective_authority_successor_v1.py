#!/usr/bin/env python3
"""Build the immutable TPC-H effective-authority-first successor locally.

No network, Docker, database, or workload operation is performed.  The exact
df893 closure is copied, its collector is replaced, and publication is
content-addressed, atomic, non-overwriting, and idempotent.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BUILDER = Path(__file__).resolve()
PREDECESSOR_ID = (
    "df8939b1d7e22d22d72c3c3e6cf54434bee2c71959e8a28446d847d5b221520d"
)
PREDECESSOR_MANIFEST_SHA256 = (
    "fb52d46eb2080baa7b7d7637f21d3a60b0de646f54d336950f0d34c2ab60c7ab"
)
PREDECESSOR = (
    ROOT / "artifacts/mysql_tpch_formal10_successor_v2" / PREDECESSOR_ID
)
COLLECTOR_RELATIVE = (
    "qualification/mysql_distributed_live_readback_qualification_v1.py"
)
INVALIDATION = (
    ROOT
    / "preflight/"
    "mysql_tpch_df893_empty_hostconfig_projection_invalidation_20260731.json"
)
TEST = ROOT / "shell/test_mysql_tpch_effective_authority_successor_v1.py"
DEFAULT_OUTPUT_ROOT = (
    ROOT / "artifacts/mysql_tpch_effective_authority_successor_v1"
)
HEX64 = re.compile(r"[0-9a-f]{64}$")


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_object(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise BuildError(label + " is absent or symlinked")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildError(label + " is invalid") from exc
    if not isinstance(value, dict):
        raise BuildError(label + " must be an object")
    return value


def relative_files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError("symlink in bundle closure: " + str(path))
        if path.is_file():
            result[path.relative_to(root).as_posix()] = sha256(path)
        elif not path.is_dir():
            raise BuildError("special file in bundle closure: " + str(path))
    return result


def verify_predecessor() -> dict[str, Any]:
    manifest_path = PREDECESSOR / "bundle_manifest.json"
    manifest = load_object(manifest_path, "df893 manifest")
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    sidecar = PREDECESSOR / "bundle_manifest.json.sha256"
    if (
        PREDECESSOR.is_symlink()
        or PREDECESSOR.name != PREDECESSOR_ID
        or sha256(manifest_path) != PREDECESSOR_MANIFEST_SHA256
        or bundle_id != PREDECESSOR_ID
        or hashlib.sha256(canonical(unsigned)).hexdigest() != PREDECESSOR_ID
        or sidecar.is_symlink()
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii")
        != PREDECESSOR_MANIFEST_SHA256 + "  bundle_manifest.json\n"
        or manifest.get("schema_version")
        != "mysql_tpch_distributed_formal10_successor_v2"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("postgresql_mutation_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("df893 immutable identity or authority differs")
    actual = relative_files(PREDECESSOR)
    actual.pop("bundle_manifest.json")
    actual.pop("bundle_manifest.json.sha256")
    if actual != manifest.get("files"):
        raise BuildError("df893 exact file closure differs")
    return manifest


def _replace_once(payload: bytes, old: bytes, new: bytes, label: str) -> bytes:
    if payload.count(old) != 1:
        raise BuildError("collector patch point differs: " + label)
    return payload.replace(old, new, 1)


def collector_payload() -> bytes:
    """Return df893's collector with one fail-closed TPC-H policy repair."""
    source = PREDECESSOR / COLLECTOR_RELATIVE
    before = sha256(source)
    payload = source.read_bytes()
    if sha256(source) != before:
        raise BuildError("df893 collector changed during read")

    old_return = b'''    return {
        "schema_version": value["schema_version"],
        "authority_sha256": recorded_sha,
        "cgroup_path": expected_cgroup,
        "device_major_minor": value["device_major_minor"],
        "read_bps": value["read_bps"],
        "write_bps": value["write_bps"],
        "docker_hostconfig_is_declaration_not_effective_authority": True,
    }

def validate_container_readback(container_row, runtime):
'''
    new_return = b'''    expected_attempt = defaults_file.parent.parent.name
    if (
        value.get("bundle_id") != bundle_id
        or value.get("attempt_token") != expected_attempt
    ):
        raise QualificationError(
            "effective cgroup-v1 manifest/attempt binding differs"
        )
    return {
        "schema_version": value["schema_version"],
        "authority_sha256": recorded_sha,
        "bundle_id": bundle_id,
        "attempt_token": expected_attempt,
        "database_container_id": container_id,
        "database_pid": pid,
        "same_live_identity_verified": True,
        "cgroup_path": expected_cgroup,
        "device_major_minor": value["device_major_minor"],
        "read_bps": value["read_bps"],
        "write_bps": value["write_bps"],
        "docker_hostconfig_is_declaration_not_effective_authority": True,
    }


def _validate_docker_blkio_projection(
    read_rows,
    write_rows,
    device,
    major_minor,
    read_bps,
    write_bps,
    container_id,
    pid,
    bundle_id,
    attempt_token,
    effective_authority,
):
    """Accept []/[] only behind the exact same-live effective authority."""
    read_empty = read_rows == []
    write_empty = write_rows == []
    if read_empty or write_empty:
        if not (read_empty and write_empty):
            raise QualificationError(
                "asymmetric empty Docker blkio projection differs"
            )
        expected_cgroup = "/sys/fs/cgroup/blkio/docker/" + str(container_id)
        if (
            not isinstance(effective_authority, dict)
            or effective_authority.get("schema_version")
            != "mysql_tpch_blkio_v1_throttle_authority_v1"
            or re.fullmatch(
                r"[0-9a-f]{64}",
                str(effective_authority.get("authority_sha256") or ""),
            )
            is None
            or effective_authority.get("bundle_id") != bundle_id
            or effective_authority.get("attempt_token") != attempt_token
            or effective_authority.get("database_container_id") != container_id
            or effective_authority.get("database_pid") != pid
            or effective_authority.get("same_live_identity_verified") is not True
            or effective_authority.get("cgroup_path") != expected_cgroup
            or effective_authority.get("device_major_minor") != major_minor
            or effective_authority.get("read_bps") != int(read_bps)
            or effective_authority.get("write_bps") != int(write_bps)
            or effective_authority.get(
                "docker_hostconfig_is_declaration_not_effective_authority"
            )
            is not True
        ):
            raise QualificationError(
                "empty Docker blkio projection lacks exact effective authority"
            )
        return "empty_arrays_with_exact_effective_cgroup_v1_authority"
    if not _exact_rate_binding(read_rows, device, major_minor, read_bps):
        raise QualificationError("container read BPS binding differs")
    if not _exact_rate_binding(write_rows, device, major_minor, write_bps):
        raise QualificationError("container write BPS binding differs")
    return "exact_nonempty_docker_declaration"


def validate_container_readback(
    container_row,
    runtime,
    effective_blkio_authority=None,
    bundle_id=None,
    attempt_token=None,
):
'''
    payload = _replace_once(
        payload, old_return, new_return, "authority result and projection helper"
    )
    payload = _replace_once(
        payload,
        b"def validate_tpch_effective_blkio_authority(\n"
        b"    container_row, runtime, defaults_file\n"
        b"):\n",
        b"def validate_tpch_effective_blkio_authority(\n"
        b"    container_row, runtime, defaults_file, bundle_id\n"
        b"):\n",
        "manifest-bound authority signature",
    )
    old_rates = b'''    if not _exact_rate_binding(
        host_config.get("BlkioDeviceReadBps"),
        throttle_device,
        throttle_major_minor,
        envelope["io_read_bps"],
    ):
        raise QualificationError("container read BPS binding differs")
    if not _exact_rate_binding(
        host_config.get("BlkioDeviceWriteBps"),
        throttle_device,
        throttle_major_minor,
        envelope["io_write_bps"],
    ):
        raise QualificationError("container write BPS binding differs")
'''
    new_rates = b'''    docker_blkio_projection = _validate_docker_blkio_projection(
        host_config.get("BlkioDeviceReadBps"),
        host_config.get("BlkioDeviceWriteBps"),
        throttle_device,
        throttle_major_minor,
        envelope["io_read_bps"],
        envelope["io_write_bps"],
        container_row.get("Id"),
        pid,
        bundle_id,
        attempt_token,
        effective_blkio_authority,
    )
'''
    payload = _replace_once(payload, old_rates, new_rates, "Docker blkio policy")
    payload = _replace_once(
        payload,
        b'''        "effective_cgroup_v1_throttle": effective_throttle,
        "host_config": {
''',
        b'''        "effective_cgroup_v1_throttle": effective_throttle,
        "docker_blkio_projection": docker_blkio_projection,
        "host_config": {
''',
        "readback evidence mode",
    )
    old_order = b'''    database_row = inspect_container(container)
    container_readback = validate_container_readback(database_row, runtime)
    container_readback["effective_blkio_authority"] = (
        validate_tpch_effective_blkio_authority(
            database_row, runtime, defaults_file
        )
    )
'''
    new_order = b'''    database_row = inspect_container(container)
    effective_blkio_authority = validate_tpch_effective_blkio_authority(
        database_row, runtime, defaults_file, manifest["bundle_id"]
    )
    attempt_token = Path(defaults_file).parent.parent.name
    container_readback = validate_container_readback(
        database_row,
        runtime,
        effective_blkio_authority,
        manifest["bundle_id"],
        attempt_token,
    )
    container_readback["effective_blkio_authority"] = (
        effective_blkio_authority
    )
'''
    payload = _replace_once(payload, old_order, new_order, "authority-first order")
    return payload


def copy_file(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise BuildError("regular source required: " + str(source))
    before = sha256(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    if sha256(source) != before or sha256(destination) != before:
        raise BuildError("source changed while copying: " + str(source))
    os.chmod(destination, stat.S_IMODE(source.stat().st_mode) | 0o600)


def freeze(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def thaw_directories(root: Path) -> None:
    if not root.exists():
        return
    os.chmod(root, 0o755)
    for path in root.rglob("*"):
        if path.is_dir():
            os.chmod(path, 0o755)


def fsync_tree(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    for path in sorted(
        [root, *[item for item in root.rglob("*") if item.is_dir()]],
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def verify_bundle(bundle: Path, expected_id: str | None = None) -> dict[str, Any]:
    manifest_path = bundle / "bundle_manifest.json"
    manifest = load_object(manifest_path, "successor manifest")
    identity = dict(manifest)
    bundle_id = identity.pop("bundle_id", None)
    actual = relative_files(bundle)
    actual.pop("bundle_manifest.json", None)
    actual.pop("bundle_manifest.json.sha256", None)
    sidecar = bundle / "bundle_manifest.json.sha256"
    if (
        HEX64.fullmatch(str(bundle_id or "")) is None
        or (expected_id is None and bundle.name != bundle_id)
        or (expected_id is not None and bundle_id != expected_id)
        or hashlib.sha256(canonical(identity)).hexdigest() != bundle_id
        or sidecar.is_symlink()
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii")
        != sha256(manifest_path) + "  bundle_manifest.json\n"
        or actual != manifest.get("files")
        or manifest.get("schema_version")
        != "mysql_tpch_distributed_formal10_successor_v2"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("postgresql_mutation_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or manifest.get("predecessor_bundle_id") != PREDECESSOR_ID
        or manifest.get("effective_authority_first_empty_projection") is not True
    ):
        raise BuildError("successor identity, closure, or authority differs")
    for path in [bundle, *bundle.rglob("*")]:
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError("successor immutable mode differs: " + str(path))
    return manifest


def publish(output_root: Path) -> Path:
    predecessor = verify_predecessor()
    for source in (BUILDER, INVALIDATION, TEST):
        if source.is_symlink() or not source.is_file():
            raise BuildError("successor input is absent: " + str(source))
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".tpch-effective-authority.", dir=output_root))
    try:
        for relative, expected in sorted(predecessor["files"].items()):
            source = PREDECESSOR / relative
            if sha256(source) != expected:
                raise BuildError("df893 file differs: " + relative)
            if relative == COLLECTOR_RELATIVE:
                continue
            copy_file(source, stage / relative)
        collector = stage / COLLECTOR_RELATIVE
        collector.parent.mkdir(parents=True, exist_ok=True)
        collector.write_bytes(collector_payload())
        os.chmod(collector, 0o755)
        copy_file(
            INVALIDATION,
            stage / "invalidated_df893_empty_hostconfig_projection.json",
        )
        copy_file(
            TEST,
            stage / "offline_tests/test_effective_authority_projection_v1.py",
        )
        copy_file(
            BUILDER,
            stage / "builder/build_mysql_tpch_effective_authority_successor_v1.py",
        )
        files = relative_files(stage)
        identity = dict(predecessor)
        identity.pop("bundle_id", None)
        identity.pop("files", None)
        identity.update(
            {
                "predecessor_bundle_id": PREDECESSOR_ID,
                "predecessor_manifest_sha256": PREDECESSOR_MANIFEST_SHA256,
                "builder_sha256": sha256(BUILDER),
                "shared_collector_source_sha256": sha256(collector),
                "invalidated_df893_empty_hostconfig_projection_sha256":
                    sha256(INVALIDATION),
                "effective_authority_first_empty_projection": True,
                "docker_empty_projection_policy": {
                    "accepted_shape": {
                        "BlkioDeviceReadBps": [],
                        "BlkioDeviceWriteBps": [],
                    },
                    "requires_manifest_attempt_same_live_identity": True,
                    "requires_cgroup_v1_path_and_sidecar": True,
                    "requires_exact_device_major_minor": "259:1",
                    "requires_exact_read_bps": 100663296,
                    "requires_exact_write_bps": 100663296,
                    "asymmetric_empty_rejected": True,
                    "wrong_nonempty_declaration_rejected": True,
                },
                "component_hashes": {
                    **(predecessor.get("component_hashes") or {}),
                    "collector": sha256(collector),
                },
                "invalidated_predecessor_bundle_ids": list(
                    dict.fromkeys(
                        [
                            *(predecessor.get("invalidated_predecessor_bundle_ids")
                              or []),
                            PREDECESSOR_ID,
                        ]
                    )
                ),
                "fresh_r0_required": True,
                "formal_launch_authorized": False,
                "accepted_round_authorized": False,
                "offline_build_only": True,
                "remote_actions": 0,
                "publication": {
                    "content_addressed": True,
                    "same_filesystem": True,
                    "frozen_before_visibility": True,
                    "atomic_directory_rename": True,
                    "non_overwriting": True,
                    "concurrent_identical_publish_idempotent": True,
                    "fsync_before_and_after_rename": True,
                },
                "files": dict(sorted(files.items())),
            }
        )
        bundle_id = hashlib.sha256(canonical(identity)).hexdigest()
        manifest = {**identity, "bundle_id": bundle_id}
        payload = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(payload)
        (stage / "bundle_manifest.json.sha256").write_text(
            hashlib.sha256(payload).hexdigest() + "  bundle_manifest.json\n",
            encoding="ascii",
        )
        freeze(stage)
        verify_bundle(stage, bundle_id)
        fsync_tree(stage)
        final = output_root / bundle_id
        if final.exists() or final.is_symlink():
            verify_bundle(final, bundle_id)
            thaw_directories(stage)
            shutil.rmtree(stage)
            return final
        try:
            os.rename(stage, final)
        except OSError as exc:
            if exc.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                raise
            verify_bundle(final, bundle_id)
            thaw_directories(stage)
            shutil.rmtree(stage)
            return final
        descriptor = os.open(output_root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        verify_bundle(final, bundle_id)
        return final
    except BaseException:
        if stage.exists():
            thaw_directories(stage)
            shutil.rmtree(stage)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--verify-bundle", type=Path)
    args = parser.parse_args()
    if args.verify_bundle:
        bundle = args.verify_bundle.resolve(strict=True)
        manifest = verify_bundle(bundle)
    else:
        bundle = publish(args.output_root)
        manifest = verify_bundle(bundle)
    print(
        json.dumps(
            {
                "status": "BUILT_STATIC_PASS_RUNTIME_EVIDENCE_PENDING",
                "bundle": str(bundle),
                "bundle_id": manifest["bundle_id"],
                "bundle_manifest_sha256": sha256(bundle / "bundle_manifest.json"),
                "formal_scope": "mysql_only",
                "formal_launch_authorized": False,
                "accepted_round_authorized": False,
                "remote_actions": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
