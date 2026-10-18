#!/usr/bin/env python3
"""Build immutable JOB control v3 bound to the sealed XP v23 chain."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/mysql_job_v13_qualification_r0_closure_v3"
PARENT_ID = "7801df9f783c61fb5a72341c8bfc6ac04c28bd6f7729d12f7a5e987d28e3b1b3"
PARENT_MANIFEST_SHA256 = "ce1f9cd0c4bc6d493c7775d89933853bc8abbf4ef318e9c9a1426ff64768e650"
PARENT = ROOT / "artifacts/mysql_job_v13_qualification_r0_closure_v3" / PARENT_ID
JOB_ID = "6b4844bcdcc7ada8dfd76c6676f87c98dc382aa76979504d3edc65baaab52dd7"
XP_V19_ID = "e91031f5af956af87904a240fcf6ce6673d2511dfc66f1a6337f36698133050d"
XP_V20_ID = "f2dd55ea6b5ea4db704aa533bddc9161b378c30735b9606a092efccc1315179f"
XP_V21_ID = "f6528795e2eabc84c7d29ff8d387bc50ffd0bbb055e4fc0987da5aa8626717c2"
XP_V22_ID = "21d1a853548ebdff573d18a82c7a124b810b5656e3e3a330146f87d3e0b460ad"
XP_V23_ID = "ba68d4a57f7ebb03f8e9c662250492aac7341757c66c56256665c41b5ee3a77e"
XP_MANIFEST_SHA256 = {
    XP_V20_ID: "3df7740447d613a653ef500100a54edf755ab05b8728336460ac96b5105ee17f",
    XP_V21_ID: "8f0cde3c6f5266d2bcd716e84cf5e3ae57412baf4dd3fd0ea60f3a723829615e",
    XP_V22_ID: "37863284416171c00a1a9f2e4b036455abf3667a183e3df3eb8aa2b01d36c115",
    XP_V23_ID: "e03f095d21876c687e9b916b8b2b5e4dca6999d5ece745d3b6261becfc5eecf0",
}
XP_ROOTS = {
    XP_V20_ID: ROOT / "artifacts/mysql_xp_node02_successor_v20" / XP_V20_ID,
    XP_V21_ID: ROOT / "artifacts/mysql_xp_node02_successor_v21" / XP_V21_ID,
    XP_V22_ID: ROOT / "artifacts/mysql_xp_node02_successor_v22" / XP_V22_ID,
    XP_V23_ID: ROOT / "artifacts/mysql_xp_node02_successor_v23" / XP_V23_ID,
}
XP_CRITICAL = {
    "qualification/mysql_distributed_live_readback_qualification_v1.py": "e993fa1dd8fe45af42a18980e4bd0623c99409248bb1a62d627da40439b88ca3",
    "qualification/mysql_xp_qualification_authority_v1.py": "aade726a7b4b9e593c8e32c15366c7535ee604f666fcef625609d6918167fe32",
    "launch/mysql_xp_node02_adopted_service_v1.py": "2626612ec569e0dca322b1e594c9054e2b1e7eacffa9713f9a86b899792a3731",
    "launch/mysql_xp_node02_formal10_materializer_v1.py": "e81e4feed8345fcf2c625a4027839e2f354bd181b49a30a881491e0db00e6061",
    "test_support/mysql_xp_qualification_bootstrap_materials_v5.py": "dc6bbf8c9604bc65dbe739893139d5859c4f76f8ebde59c7065d6d79e65dc596",
    "xp_node02_qualification_ttl_remediation_v1.json": "c6cad35539ee2c60908bfe67c4a8ffb338f45ccadc3d5bc2edfddae7f13aaa6c",
    "xp_node02_authority_ttl_parity_remediation_v1.json": "ca6c2458d01a086f9e7bff0ee0c27c7fed15de679bb5c0b50d748b144e460bed",
}
FILES = {
    "qualification/mysql_job_xp_shared_qualification_dispatch_v1.py": ROOT / "shell/mysql_job_xp_shared_qualification_dispatch_v1.py",
    "qualification/mysql_xp_successor_binding_v1.py": ROOT / "shell/mysql_xp_successor_binding_v1.py",
    "qualification/mysql_job_v13_fresh_r0_runner_v1.py": ROOT / "shell/mysql_job_v13_fresh_r0_runner_v1.py",
    "controller/mysql_job_v13_qualification_authority_v2.py": ROOT / "shell/mysql_job_v13_qualification_authority_v2.py",
    "controller/mysql_job_xp_four_flow_controller_v1.py": ROOT / "shell/mysql_job_xp_four_flow_controller_v1.py",
    "controller/mysql_xp_successor_qualification_launch_adapter_v2.py": ROOT / "shell/mysql_xp_v18_qualification_launch_adapter_v1.py",
    "controller/mysql_xp_successor_binding_v1.py": ROOT / "shell/mysql_xp_successor_binding_v1.py",
    "controller/render_mysql_job_xp_successor_short_ttl_v3.py": ROOT / "shell/render_mysql_job_xp_successor_short_ttl_v2.py",
    "publisher/mysql_content_addressed_bundle_publish_v1.py": ROOT / "shell/mysql_content_addressed_bundle_publish_v1.py",
    "templates/mysql_job_v13_xp_successor_short_ttl_commands_v3.json": ROOT / "templates/mysql_job_v13_xp_successor_short_ttl_commands_v2.json",
    "tests/test_mysql_job_v14_qualification_r0_closure.py": ROOT / "tests/test_mysql_job_v14_qualification_r0_closure.py",
    "tests/test_mysql_job_v15_control_successor.py": ROOT / "tests/test_mysql_job_v15_control_successor.py",
    "builder/build_mysql_job_v13_qualification_r0_closure_v3.py": Path(__file__).resolve(),
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


def verify_manifest(bundle: Path, expected_id: str, expected_sha: str) -> dict[str, Any]:
    path = bundle / "bundle_manifest.json"
    sidecar = bundle / "bundle_manifest.json.sha256"
    value = json.loads(path.read_text(encoding="utf-8"))
    unsigned = {key: item for key, item in value.items() if key != "bundle_id"}
    if (
        bundle.is_symlink() or not bundle.is_dir()
        or value.get("bundle_id") != expected_id or bundle.name != expected_id
        or hashlib.sha256(canonical(unsigned)).hexdigest() != expected_id
        or sha256(path) != expected_sha
        or sidecar.read_text(encoding="ascii") != "%s  bundle_manifest.json\n" % expected_sha
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
        raise BuildError("v2 predecessor exact closure differs")
    predecessors = {
        XP_V20_ID: (XP_V19_ID, "9e58aabf733881726755251a5b1b3f9a3e5e79ff2821b20cbe6ae56b7e91b872"),
        XP_V21_ID: (XP_V20_ID, XP_MANIFEST_SHA256[XP_V20_ID]),
        XP_V22_ID: (XP_V21_ID, XP_MANIFEST_SHA256[XP_V21_ID]),
        XP_V23_ID: (XP_V22_ID, XP_MANIFEST_SHA256[XP_V22_ID]),
    }
    schemas = {XP_V20_ID: 20, XP_V21_ID: 21, XP_V22_ID: 22, XP_V23_ID: 23}
    for bundle_id in (XP_V20_ID, XP_V21_ID, XP_V22_ID, XP_V23_ID):
        row = verify_manifest(XP_ROOTS[bundle_id], bundle_id, XP_MANIFEST_SHA256[bundle_id])
        predecessor_id, predecessor_sha = predecessors[bundle_id]
        if (
            row.get("schema_version") != "mysql_xp_node02_successor_bundle_v%d" % schemas[bundle_id]
            or row.get("predecessor_bundle_id") != predecessor_id
            or row.get("predecessor_manifest_sha256") != predecessor_sha
        ):
            raise BuildError("XP v23 sealed predecessor chain differs")
    v23 = json.loads((XP_ROOTS[XP_V23_ID] / "bundle_manifest.json").read_text(encoding="utf-8"))
    if (
        v23.get("status") != "SEALED_GENERATOR_SERVICE_60_MINUTE_TTL_PARITY_NOT_LAUNCHED"
        or v23.get("qualification_material_valid_minutes_min") != 10
        or v23.get("qualification_material_valid_minutes_max") != 60
        or v23.get("materials_generator_sha256") != XP_CRITICAL["test_support/mysql_xp_qualification_bootstrap_materials_v5.py"]
        or v23.get("test_support_materializer") != "test_support/mysql_xp_qualification_bootstrap_materials_v5.py"
    ):
        raise BuildError("XP v23 TTL/materials identity differs")
    for relative, expected in XP_CRITICAL.items():
        if v23.get("files", {}).get(relative) != expected or sha256(XP_ROOTS[XP_V23_ID] / relative) != expected:
            raise BuildError("XP v23 critical identity differs: " + relative)
    for relative, source in FILES.items():
        if source.is_symlink() or not source.is_file():
            raise BuildError("successor source absent: " + relative)


def manifest(files: dict[str, str]) -> dict[str, Any]:
    return {
        "schema_version": "mysql_job_v13_qualification_r0_control_successor_v4",
        "status": "SEALED_POST_VERIFY_SHARED_DISPATCH_SUCCESSOR_NOT_AUTHORITY_NOT_EXECUTED",
        "predecessor_support_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
        "target_job_bundle_id": JOB_ID,
        "current_verified_xp_bundle_id": XP_V23_ID,
        "xp_sealed_chain": [
            {"bundle_id": bundle_id, "manifest_sha256": XP_MANIFEST_SHA256[bundle_id]}
            for bundle_id in (XP_V20_ID, XP_V21_ID, XP_V22_ID, XP_V23_ID)
        ],
        "xp_v23_critical_files": XP_CRITICAL,
        "xp_material_valid_minutes": {"minimum": 10, "maximum": 60},
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "retained_v2_fixes": {
            "only_two_external_xp_tokens_precreated": True,
            "job_tokens_renderer_created_exclusively": True,
            "fresh_job_collect_only_and_sidecar_required": True,
            "fresh_xp_collect_only_and_sidecar_required": True,
            "controller_to_incoming_transfer_explicit": True,
            "node02_transactional_publication_explicit": True,
        },
        "shared_dispatcher_self_hash_substitution": {
            "backend_exact_bundle_closure_verified_first": True,
            "backend_sha256_restored_during_every_verify_bundle_call": True,
            "shared_identity_enabled_only_after_successful_verify_bundle": True,
            "shared_identity_substitution_target": "qualification evidence collector_sha256 self-file call only",
            "real_job_6b4844_verify_bundle_regression": "PASS",
            "real_xp_ba68d4_verify_bundle_regression": "PASS",
        },
        "xp_successor_binding_policy": {
            "exact_v23_required": True,
            "exact_v23_to_v22_to_v21_to_v20_sealed_chain_required": True,
            "seven_v23_critical_identities_pinned": True,
            "v24_or_later_requires_new_control_successor": True,
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
            raise BuildError("existing v3 bundle differs")
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
