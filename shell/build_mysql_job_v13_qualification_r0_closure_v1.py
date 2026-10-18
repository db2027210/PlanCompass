#!/usr/bin/env python3
"""Build the immutable JOB v13 four-flow/fresh-R0 support closure.

This bundle augments an already-live JOB v13 attempt.  It grants no launch
authority, changes no resource envelope, and produces no experimental credit.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/mysql_job_v13_qualification_r0_closure_v1"
JOB_ID = "6b4844bcdcc7ada8dfd76c6676f87c98dc382aa76979504d3edc65baaab52dd7"
XP_ID = "f43f33b5d60cf6de2fe63f96852e6a46ea3b132b5d52039a1e60021771dc9262"
XP_V19_ID = "e91031f5af956af87904a240fcf6ce6673d2511dfc66f1a6337f36698133050d"
XP_V20_ID = "f2dd55ea6b5ea4db704aa533bddc9161b378c30735b9606a092efccc1315179f"
JOB_COLLECTOR_SHA256 = "02ef703b51f923bdc3cbe0127830e929872eb212468e1b6dcbd1577ee00bb7ad"
XP_COLLECTOR_SHA256 = "6aae48e139c667cfe1efaaa6cd0a6a672b72a8d3b3f024cee19fd32bb2f69315"
XP_V19_COLLECTOR_SHA256 = "522f01b5f2b395d70d5f46451a66e6aa64f2288ed474f4faa2fc9337a2906d25"
XP_V20_COLLECTOR_SHA256 = "1e1c713791e4b801444501fd43f52cb7bdef350331d3cea14a46628446ab509e"
INVALIDATED_SUPPORT_ID = "d3f9640d3df4608897bead538ba33f13e084df139371648b58f09a1581d2004b"
FILES = {
    "qualification/mysql_job_xp_shared_qualification_dispatch_v1.py":
        ROOT / "shell/mysql_job_xp_shared_qualification_dispatch_v1.py",
    "controller/mysql_job_v13_qualification_authority_v2.py":
        ROOT / "shell/mysql_job_v13_qualification_authority_v2.py",
    "controller/mysql_job_xp_four_flow_controller_v1.py":
        ROOT / "shell/mysql_job_xp_four_flow_controller_v1.py",
    "controller/mysql_xp_v18_qualification_launch_adapter_v1.py":
        ROOT / "shell/mysql_xp_v18_qualification_launch_adapter_v1.py",
    "controller/mysql_xp_v18_v19_v20_qualification_launch_adapter_v1.py":
        ROOT / "shell/mysql_xp_v18_qualification_launch_adapter_v1.py",
    "publisher/mysql_content_addressed_bundle_publish_v1.py":
        ROOT / "shell/mysql_content_addressed_bundle_publish_v1.py",
    "templates/mysql_job_v13_xp_v20_short_ttl_commands_v1.json":
        ROOT / "templates/mysql_job_v13_xp_v20_short_ttl_commands_v1.json",
    "qualification/mysql_job_v13_fresh_r0_runner_v1.py":
        ROOT / "shell/mysql_job_v13_fresh_r0_runner_v1.py",
    "tests/test_mysql_job_v14_qualification_r0_closure.py":
        ROOT / "tests/test_mysql_job_v14_qualification_r0_closure.py",
    "builder/build_mysql_job_v13_qualification_r0_closure_v1.py": Path(__file__).resolve(),
}


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


def verify_targets() -> None:
    targets = (
        (ROOT / "artifacts/mysql_job_raw_all_subset_three_level_successor_v13" / JOB_ID,
         "qualification/mysql_job_distributed_live_readback_qualification_v2.py",
         JOB_ID, JOB_COLLECTOR_SHA256),
        (ROOT / "artifacts/mysql_xp_node02_successor_v18" / XP_ID,
         "qualification/mysql_distributed_live_readback_qualification_v1.py",
         XP_ID, XP_COLLECTOR_SHA256),
        (ROOT / "artifacts/mysql_xp_node02_successor_v19" / XP_V19_ID,
         "qualification/mysql_distributed_live_readback_qualification_v1.py",
         XP_V19_ID, XP_V19_COLLECTOR_SHA256),
        (ROOT / "artifacts/mysql_xp_node02_successor_v20" / XP_V20_ID,
         "qualification/mysql_distributed_live_readback_qualification_v1.py",
         XP_V20_ID, XP_V20_COLLECTOR_SHA256),
    )
    for bundle, collector, identity, expected_collector in targets:
        manifest_path = bundle / "bundle_manifest.json"
        if bundle.is_symlink() or not bundle.is_dir() or not manifest_path.is_file():
            raise BuildError("target bundle absent: " + identity)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("bundle_id") != identity
            or manifest.get("formal_scope") != "mysql_only"
            or manifest.get("cross_engine_comparison_prohibited") is not True
            or sha_file(bundle / collector) != expected_collector
        ):
            raise BuildError("target bundle or collector identity differs: " + identity)


def manifest(files: dict[str, str]) -> dict[str, Any]:
    return {
        "schema_version": "mysql_job_v13_qualification_r0_support_bundle_v1",
        "status": "SEALED_SUPPORT_CLOSURE_NOT_AUTHORITY_NOT_EXECUTED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "target_job_bundle_id": JOB_ID,
        "target_xp_bundle_id": XP_V20_ID,
        "compatible_xp_bundle_ids": [XP_ID, XP_V19_ID, XP_V20_ID],
        "target_resource_envelopes_changed": False,
        "target_live_attempt_replacement_required": False,
        "invalidated_support_bundles": {
            INVALIDATED_SUPPORT_ID: (
                "fresh-R0 canonical JSON and inspect identity hashing differed "
                "from the sealed JOB v13 handoff authority"
            )
        },
        "invalidated_xp_v20_intermediate_bundle_ids": [
            "7cd04df600470bf7afde0d2e3740c116ecbab072641fe8813f5606ce0352d3b7",
            "ebb17fef3e6a86b380401babe1e04835ec4b887fb61546527787d42970e17149"
        ],
        "superseded_support_bundle_ids": [
            "2d19be58c02dac9cb6ed3725f5ddbc718c0fb3bf497b1caa4e8276dfab3c109c",
            "7a6a46338c8cb5d2b839c77fd8bec7e3a7274cb334bbe991575a414d52874b2a"
        ],
        "support_tests_require_exact_target_bundles_for_dispatch_checks": True,
        "transactional_publisher_test_self_contained": True,
        "shared_collector_contract": {
            "dispatch_records_one_identity_for_four_flows": True,
            "job_backend_sha256": JOB_COLLECTOR_SHA256,
            "xp_backend_sha256": XP_COLLECTOR_SHA256,
            "xp_v19_backend_sha256": XP_V19_COLLECTOR_SHA256,
            "xp_v20_backend_sha256": XP_V20_COLLECTOR_SHA256,
            "backend_source_identity_remains_hash_bound": True,
        },
        "four_flow_protocol": {
            "sequence": ["job_solo", "xp_solo", ["job_paired", "xp_paired"], "compare_bidirectional"],
            "paired_start_concurrent": True,
            "all_attempt_ids_unique": True,
            "all_authority_tokens_unique": True,
            "reciprocal_peer_binding_required": True,
        },
        "fresh_r0_protocol": {
            "same_live_job_planner_required": True,
            "pass_four_flow_gate_required": True,
            "fresh_handoff_required": True,
            "duration_seconds": 180,
            "minimum_samples": 181,
            "cpu_ratio_range": [0.30, 0.50],
            "memory_ratio_range": [0.85, 0.95],
            "io_ratio_range": [0.30, 0.50],
            "original_gradient_inputs_created": False,
        },
        "qualification_credit": 0,
        "r0_credit": 0,
        "candidate_credit": 0,
        "top5_replay_credit": 0,
        "accepted_rounds_credit": 0,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "files": files,
    }


def verify_existing(destination: Path, expected: dict[str, Any]) -> None:
    path = destination / "bundle_manifest.json"
    if not path.is_file() or json.loads(path.read_text(encoding="utf-8")) != expected:
        raise BuildError("existing support bundle identity differs")
    for relative, digest in expected["files"].items():
        candidate = destination / relative
        if candidate.is_symlink() or not candidate.is_file() or sha_file(candidate) != digest:
            raise BuildError("existing support bundle closure differs: " + relative)


def seal(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o444 if path.is_file() else 0o555)
    os.chmod(root, 0o555)


def build() -> dict[str, Any]:
    verify_targets()
    for relative, source in FILES.items():
        if source.is_symlink() or not source.is_file():
            raise BuildError("support source absent: " + relative)
    files = {relative: sha_file(source) for relative, source in sorted(FILES.items())}
    unsigned = manifest(files)
    bundle_id = sha_bytes(canonical(unsigned))
    sealed = dict(unsigned, bundle_id=bundle_id)
    destination = OUTPUT / bundle_id
    if destination.exists():
        verify_existing(destination, sealed)
        return sealed
    OUTPUT.mkdir(parents=True, exist_ok=True)
    try:
        destination.mkdir(mode=0o700)
        for relative, source in FILES.items():
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        payload = canonical(sealed)
        (destination / "bundle_manifest.json").write_bytes(payload)
        (destination / "bundle_manifest.json.sha256").write_text(
            "%s  bundle_manifest.json\n" % sha_bytes(payload), encoding="ascii"
        )
        seal(destination)
    except Exception:
        # A failed unpublished staging directory carries no authority.  Make
        # it writable so the next operator can inspect/remove it explicitly.
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
    return sealed


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
