#!/usr/bin/env python3
"""Build immutable XP v18 with the plan executor binding fixed."""
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
PARENT_ID = "46c5207d29682d977250fb1be6f3ed5791c6f8ff6e5f162c49591bb311e9c8d4"
PARENT_MANIFEST_SHA256 = "e10c95fd9697915b26bf79fb3a1997cabdede171feb1f41cf985412223640829"
PARENT = ROOT / "artifacts/mysql_xp_node02_successor_v17" / PARENT_ID
OUTPUT_ROOT = ROOT / "artifacts/mysql_xp_node02_successor_v18"
SERVICE = "launch/mysql_xp_node02_adopted_service_v1.py"
TEST = "tests/test_mysql_xp_systemd_compatibility_v11.py"
MATERIALIZER = "test_support/mysql_xp_qualification_bootstrap_materials_v5.py"
BUILDER = "build_mysql_xp_node02_plan_executor_successor_v18.py"
REMEDIATION = "xp_node02_plan_executor_remediation_v1.json"
INVALIDATION = "invalidated_46c_plan_executor_attempt.json"
INVALID_DEV_ID = "196d4615c4a74187f73f8820333abac78ab4503cf4572430555a508e19181f8d"
INVALID_DEV = "invalidated_196d_materializer_supersedes_binding.json"
INVALID_CGROUP_PROJECTION_ID = "e1841a237ec63bb97b5162c4ac8c503236c56927ce2799cde136a44bacca6db0"
INVALID_CGROUP_PROJECTION = "invalidated_e1841_all_controller_unit_path_assumption.json"
ACTIVE_DEVICE_BINDINGS = (
    "authority/node02_dedicated_lane_binding_v1.json",
    "build_mysql_xp_formal10_launch_v3.py",
    "build_mysql_xp_formal10_launch_v4.py",
    "build_mysql_xp_formal10_launch_v5.py",
    "build_mysql_xp_formal10_launch_v6.py",
    "build_mysql_xp_formal10_launch_v7.py",
    "build_mysql_xp_formal10_launch_v8.py",
    "distributed_runtime_contract.json",
    "launch/mysql_xp_capture_transition_v1.py",
    "launch/mysql_xp_node02_adopted_service_v1.py",
    "qualification/mysql_distributed_live_readback_qualification_v1.py",
    "test_support/mysql_xp_qualification_bootstrap_materials_v5.py",
    "tests/test_mysql_xp_node02_successor_v10.py",
    "xp_executable_launch_contract_v3.json",
    "xp_node02_executable_launch_contract_v4.json",
)


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


def load(payload: bytes, label: str) -> dict[str, Any]:
    value = json.loads(payload)
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
        '            args.authorization_expires_at_utc,\n'
        '        )\n'
        '        if args.plan:',
        '            args.authorization_expires_at_utc,\n'
        '            args.executor_unit,\n'
        '        )\n'
        '        if args.plan:',
        1, "plan executor-unit binding",
    )
    return payload


def verify_parent() -> dict[str, Any]:
    manifest_path = PARENT / "bundle_manifest.json"
    manifest = load(manifest_path.read_bytes(), "parent manifest")
    if (
        manifest.get("bundle_id") != PARENT_ID
        or sha_file(manifest_path) != PARENT_MANIFEST_SHA256
        or relative_files(PARENT) != manifest.get("files")
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
    for relative in ACTIVE_DEVICE_BINDINGS:
        if "259:1" in content[relative].decode("utf-8"):
            raise BuildError(relative + " regressed to stale throttle identity")
    content[SERVICE] = patch_service(content[SERVICE])
    content[SERVICE] = replace_exact(
        content[SERVICE],
        '        != "mysql_xp_node02_successor_bundle_v17"',
        '        != "mysql_xp_node02_successor_bundle_v18"',
        1, "successor service schema binding",
    )
    content[TEST] = replace_exact(
        content[TEST],
        'manifest["schema_version"]=="mysql_xp_node02_successor_bundle_v17"',
        'manifest["schema_version"]=="mysql_xp_node02_successor_bundle_v18"',
        1, "bundle schema regression",
    )
    content[MATERIALIZER] = replace_exact(
        content[MATERIALIZER],
        'manifest.get("supersedes_bundle_id")\n'
        '        != "311c3680d594406e4c692a18fd0ca1defca6a74aa59ce276907f7e8ff7cfb5d2"',
        'manifest.get("supersedes_bundle_id")\n'
        '        != "46c5207d29682d977250fb1be6f3ed5791c6f8ff6e5f162c49591bb311e9c8d4"',
        1, "materializer v18 predecessor binding",
    )
    content[BUILDER] = HERE.read_bytes()
    content[REMEDIATION] = canonical({
        "schema_version": "mysql_xp_node02_plan_executor_remediation_v1",
        "status": "OFFLINE_PLAN_EXECUTOR_REMEDIATED_NEW_QUALIFICATION_REQUIRED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "service_expected_schema": "mysql_xp_node02_successor_bundle_v18",
        "parent_manifest_schema": "mysql_xp_node02_successor_bundle_v17",
        "live_partition_identity_preserved": "/dev/nvme0n1p1@259:2",
        "live_throttle_parent_identity": "/dev/nvme0n1@259:0",
        "plan_executor_unit_forwarded": True,
        "database_cpu_quota_cores": 5.3,
        "database_io_read_bps": 29360128,
        "database_io_write_bps": 524288,
        "active_parameter_count": 7,
        "required_nonempty_subsets": 127,
        "fixed_real_replay_top_k": 5,
        "fresh_qualification_required": True,
        "fresh_r0_required": True,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    })
    content[INVALIDATION] = canonical({
        "schema_version": "mysql_xp_plan_executor_invalidation_v1",
        "invalidated_bundle_id": PARENT_ID,
        "invalidated_attempt_id":
            "xp-formal-qual-20260802T182103Z-630c9c6a348adb6e92b035e4",
        "reason": "the plan call omitted the required executor_unit positional argument",
        "data_plane_started": False,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    })
    content[INVALID_DEV] = canonical({
        "schema_version": "mysql_xp_v13_development_materializer_binding_invalidation_v1",
        "invalidated_bundle_id": INVALID_DEV_ID,
        "reason": "test-support materializer retained the pre-v12 supersedes_bundle_id assertion",
        "remote_publication": False,
        "database_or_client_started": False,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    })
    content[INVALID_CGROUP_PROJECTION] = canonical({
        "schema_version": "mysql_xp_v13_all_controller_unit_path_invalidation_v1",
        "invalidated_bundle_id": INVALID_CGROUP_PROJECTION_ID,
        "invalidated_attempt_id":
            "xp-formal-qual-20260802T170931Z-be9aa3ef7e9e191c5b62322b",
        "reason": "node02 systemd 219 assigns active controllers to the transient unit while passive perf_event/cpuset/freezer/hugetlb/net_cls controllers remain at root",
        "database_or_client_started": False,
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
        "schema_version": "mysql_xp_node02_successor_bundle_v18",
        "status": "SEALED_PLAN_EXECUTOR_SUCCESSOR_NOT_LAUNCHED",
        "builder_revision": "v18_plan_executor_binding",
        "builder_sha256": sha_file(HERE),
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
        "supersedes_bundle_id": PARENT_ID,
        "invalid_development_bundle_ids": sorted(set(
            list(parent.get("invalid_development_bundle_ids") or [])
            + [INVALID_DEV_ID, INVALID_CGROUP_PROJECTION_ID]
        )),
        "service_sha256": files[SERVICE],
        "collector_sha256": files[
            "qualification/mysql_distributed_live_readback_qualification_v1.py"
        ],
        "distributed_runtime_contract_sha256": files[
            "distributed_runtime_contract.json"
        ],
        "launch_contract_sha256": files["xp_executable_launch_contract_v3.json"],
        "node02_lane": dict(parent["node02_lane"], storage={
            "block_device": "/dev/nvme0n1p1",
            "dedicated_to_mysql_xp": True,
            "major_minor": "259:2",
        }),
        "live_partition_identity_contract": {
            "device": "/dev/nvme0n1p1",
            "major_minor": "259:2",
            "docker_data_root_must_resolve_to_same_device": True,
            "postgresql_device_prohibited": True,
        },
        "live_throttle_parent_identity_contract": {
            "device": "/dev/nvme0n1",
            "major_minor": "259:0",
            "partition": "/dev/nvme0n1p1",
            "partition_major_minor": "259:2",
            "same_sysfs_parent_required": True,
        },
        "optional_cgroup_namespace_contract": {
            "mandatory_namespaces": ["mnt", "pid", "net", "uts", "ipc", "user"],
            "cgroup_namespace_optional_but_symmetric": True,
            "missing_fallback": "/proc/self/cgroup_plus_systemd_ControlGroup",
        },
        "cgroup_v1_executor_projection_contract": {
            "exact_unit_controllers": [
                "name=systemd", "cpu", "cpuacct", "devices", "memory", "pids", "blkio"
            ],
            "passive_root_controller_paths_allowed": True,
            "only_allowed_paths": ["/", "/system.slice/<sealed_transient_unit>"],
            "container_or_nsenter_ancestry_prohibited": True,
        },
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
        stage = Path(tempfile.mkdtemp(prefix=".xp-v18.", dir=OUTPUT_ROOT))
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

    actual = relative_files(destination)
    unsigned = {key: value for key, value in manifest.items() if key != "bundle_id"}
    if (actual != files or destination.name != bundle_id
            or sha_bytes(canonical(unsigned)) != bundle_id):
        raise BuildError("published successor identity differs")
    for path in (destination, *destination.rglob("*")):
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError("published successor is mutable")
    print(json.dumps({
        "status": "PASS_IMMUTABLE_XP_V18_PLAN_EXECUTOR_SUCCESSOR",
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
