#!/usr/bin/env python3
"""Build immutable JOB control successor closing publication/readback gaps."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/mysql_job_v13_qualification_r0_closure_v2"
PARENT_ID = "20d94ca14eaae9ad880f8fba8d32df04b39df44a523371d3c3d583b6bb4c7bbc"
PARENT_MANIFEST_SHA256 = "703280c260fea860b962a1834057e83da7a0e4986d65d8816312e1a4ade3aac0"
PARENT = ROOT / "artifacts/mysql_job_v13_qualification_r0_closure_v1" / PARENT_ID
JOB_ID = "6b4844bcdcc7ada8dfd76c6676f87c98dc382aa76979504d3edc65baaab52dd7"
XP_V20_ID = "f2dd55ea6b5ea4db704aa533bddc9161b378c30735b9606a092efccc1315179f"
XP_V20 = ROOT / "artifacts/mysql_xp_node02_successor_v20" / XP_V20_ID
FILES = {
    "qualification/mysql_job_xp_shared_qualification_dispatch_v1.py": ROOT / "shell/mysql_job_xp_shared_qualification_dispatch_v1.py",
    "qualification/mysql_xp_successor_binding_v1.py": ROOT / "shell/mysql_xp_successor_binding_v1.py",
    "qualification/mysql_job_v13_fresh_r0_runner_v1.py": ROOT / "shell/mysql_job_v13_fresh_r0_runner_v1.py",
    "controller/mysql_job_v13_qualification_authority_v2.py": ROOT / "shell/mysql_job_v13_qualification_authority_v2.py",
    "controller/mysql_job_xp_four_flow_controller_v1.py": ROOT / "shell/mysql_job_xp_four_flow_controller_v1.py",
    "controller/mysql_xp_successor_qualification_launch_adapter_v2.py": ROOT / "shell/mysql_xp_v18_qualification_launch_adapter_v1.py",
    "controller/mysql_xp_successor_binding_v1.py": ROOT / "shell/mysql_xp_successor_binding_v1.py",
    "controller/render_mysql_job_xp_successor_short_ttl_v2.py": ROOT / "shell/render_mysql_job_xp_successor_short_ttl_v2.py",
    "publisher/mysql_content_addressed_bundle_publish_v1.py": ROOT / "shell/mysql_content_addressed_bundle_publish_v1.py",
    "templates/mysql_job_v13_xp_successor_short_ttl_commands_v2.json": ROOT / "templates/mysql_job_v13_xp_successor_short_ttl_commands_v2.json",
    "tests/test_mysql_job_v14_qualification_r0_closure.py": ROOT / "tests/test_mysql_job_v14_qualification_r0_closure.py",
    "tests/test_mysql_job_v15_control_successor.py": ROOT / "tests/test_mysql_job_v15_control_successor.py",
    "builder/build_mysql_job_v13_qualification_r0_closure_v2.py": Path(__file__).resolve(),
}


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_manifest(bundle: Path, expected_id: str, expected_sha: str | None = None) -> dict[str, Any]:
    path = bundle / "bundle_manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    unsigned = {key: item for key, item in value.items() if key != "bundle_id"}
    if (
        bundle.is_symlink() or not bundle.is_dir()
        or value.get("bundle_id") != expected_id or bundle.name != expected_id
        or hashlib.sha256(canonical(unsigned)).hexdigest() != expected_id
        or (expected_sha is not None and sha256(path) != expected_sha)
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("postgresql_mutation_prohibited") is not True
        or value.get("accepted_round_authorized") is not False
    ):
        raise BuildError("predecessor/target manifest identity differs")
    return value


def verify_inputs() -> None:
    parent = verify_manifest(PARENT, PARENT_ID, PARENT_MANIFEST_SHA256)
    actual = {
        path.relative_to(PARENT).as_posix(): sha256(path)
        for path in PARENT.rglob("*") if path.is_file()
        and path.name not in {"bundle_manifest.json", "bundle_manifest.json.sha256"}
    }
    if actual != parent.get("files"):
        raise BuildError("20d94 predecessor exact closure differs")
    verify_manifest(XP_V20, XP_V20_ID, "3df7740447d613a653ef500100a54edf755ab05b8728336460ac96b5105ee17f")
    for relative, source in FILES.items():
        if source.is_symlink() or not source.is_file():
            raise BuildError("successor source absent: " + relative)


def manifest(files: dict[str, str]) -> dict[str, Any]:
    return {
        "schema_version": "mysql_job_v13_qualification_r0_control_successor_v2",
        "status": "SEALED_CONTROL_SUCCESSOR_NOT_AUTHORITY_NOT_EXECUTED",
        "predecessor_support_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
        "target_job_bundle_id": JOB_ID,
        "current_verified_xp_bundle_id": XP_V20_ID,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "closed_audit_gaps": {
            "xp_version_text_uses_renderer_binding": True,
            "only_two_external_xp_tokens_precreated": True,
            "job_tokens_renderer_created_exclusively": True,
            "fresh_job_collect_only_and_sidecar_required": True,
            "fresh_xp_collect_only_and_sidecar_required": True,
            "controller_to_incoming_transfer_explicit": True,
            "node02_transactional_publication_explicit": True,
        },
        "xp_successor_binding_policy": {
            "known_v18_v20_exact_ids_pinned": True,
            "v21_identity_hardcoded": False,
            "v21_must_be_content_addressed_direct_successor_of_final_v20": True,
            "v21_critical_collector_authority_service_files_manifest_bound": True,
            "v22_or_later_requires_new_control_successor": True,
        },
        "support_destination_root": "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/runtime_algorithm_c_successor_20260729/bundles",
        "qualification_credit": 0,
        "r0_credit": 0,
        "candidate_credit": 0,
        "top5_replay_credit": 0,
        "accepted_rounds_credit": 0,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions_performed": 0,
        "files": files,
    }


def seal(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o444 if path.is_file() else 0o555)
    os.chmod(root, 0o555)


def build() -> dict[str, Any]:
    verify_inputs()
    files = {relative: sha256(source) for relative, source in sorted(FILES.items())}
    unsigned = manifest(files)
    bundle_id = hashlib.sha256(canonical(unsigned)).hexdigest()
    value = dict(unsigned, bundle_id=bundle_id)
    destination = OUTPUT / bundle_id
    if destination.exists():
        current = json.loads((destination / "bundle_manifest.json").read_text(encoding="utf-8"))
        if current != value:
            raise BuildError("existing v2 bundle differs")
        return value
    OUTPUT.mkdir(parents=True, exist_ok=True)
    destination.mkdir(mode=0o700)
    try:
        for relative, source in FILES.items():
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        payload = canonical(value)
        (destination / "bundle_manifest.json").write_bytes(payload)
        (destination / "bundle_manifest.json.sha256").write_text(
            "%s  bundle_manifest.json\n" % hashlib.sha256(payload).hexdigest(), encoding="ascii"
        )
        seal(destination)
    except Exception:
        for path in sorted(destination.rglob("*"), reverse=True):
            try:
                os.chmod(path, 0o600 if path.is_file() else 0o700)
            except OSError:
                pass
        try:
            os.chmod(destination, 0o700)
        except OSError:
            pass
        raise
    return value


def main() -> int:
    result = build()
    print(json.dumps({
        "bundle_id": result["bundle_id"],
        "path": str(OUTPUT / result["bundle_id"]),
        "status": result["status"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BuildError, OSError, ValueError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)
