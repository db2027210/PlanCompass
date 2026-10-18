#!/usr/bin/env python3
"""Transactionally seal the offline JOB Algorithm-C repair successor."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import stat


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "6b4844bcdcc7ada8dfd76c6676f87c98dc382aa76979504d3edc65baaab52dd7"
PARENT_MANIFEST_SHA256 = "3ee84d8e47827ff1a4221e3f8828d41e85f3d6aca2e15cf69dc610aa460f2ce6"
PARENT = ROOT / "artifacts/mysql_job_raw_all_subset_three_level_successor_v13" / PARENT_ID
OUTPUT = ROOT / "artifacts/mysql_job_raw_all_subset_three_level_successor_v14"
SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v14"
TEST = "tests/test_mysql_job_algorithm_repair_successor_v14.py"
BUILDER = "builder/build_mysql_job_algorithm_repair_successor_v14.py"
AUDIT = "authority/mysql_job_v6_algorithm_root_cause_and_repair_v14.json"
SOURCE_OVERRIDES = {
    "qualification/experience_table_subset_optimizer.py": "experience_table_subset_optimizer.py",
    "qualification/mysql_parameter_optimizer.py": "mysql_parameter_optimizer.py",
    "qualification/formal_mysql_sgd_checkpoint.py": "formal_mysql_sgd_checkpoint.py",
}
CONTRACT = {
    "formal_raw_all_nonempty_subsets_before_top5": True,
    "plan_clustering_diagnostic_only": True,
    "missing_l2_l3_independent": True,
    "original_gradient_confidence_required": True,
    "fixed_top5_only": True,
    "candidate_estimator_identity_required": True,
    "canonical_plan_bundle_required_before_seal": True,
    "all_unresponsive_candidates_fail_closed": True,
    "raw_gradient_event_revalidated_before_seal": True,
    "all_baseline_winners_fail_closed": True,
    "canonical_plan_sha_required_at_checkpoint": True,
    "replay_fingerprint_attached_before_outer_gate": True,
    "complete_l1_l2_l3_required_for_outer_acceptance": True,
    "qps_candidate_input_prohibited": True,
}


class BuildError(RuntimeError):
    pass


def canonical(value) -> bytes:
    return (json.dumps(
        value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False,
    ) + "\n").encode("utf-8")


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def verify_parent():
    manifest_path = PARENT / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        PARENT.name != PARENT_ID
        or manifest.get("bundle_id") != PARENT_ID
        or digest(manifest_path) != PARENT_MANIFEST_SHA256
        or manifest.get("schema_version")
        != "mysql_job_raw_all_subset_three_level_successor_bundle_v13"
        or not isinstance(manifest.get("files"), dict)
    ):
        raise BuildError("v13 parent identity differs")
    for relative, expected in manifest["files"].items():
        path = PARENT / relative
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise BuildError("v13 parent closure differs: " + relative)
    return manifest


def root_cause_audit():
    return {
        "schema_version": "mysql_job_v6_algorithm_root_cause_and_repair_v14",
        "status": "ROOT_CAUSE_CLOSED_OFFLINE_NEW_CAMPAIGN_REQUIRED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "source_campaign": "mysql_algorithm_c_job_memory_v3.gradient_debug.20260728T085336Z",
        "source_checkpoint_sha256": "2f686b6602e31a1db02d16bc064101f9305d0dcd65ab166a291a8922b3218e08",
        "source_reconciliation_sha256": "96beed0c59d0f86ac8c4b517ce94a5af460be2b14089a3d40583a3ef6813df82",
        "source_replay_authority_reconciled": True,
        "source_rerun_authorized": False,
        "source_accepted_rounds_credit": 0,
        "findings": [
            {
                "symptom": "all_15_subset_winners_equal_baseline",
                "root_cause": (
                    "strict-improvement incumbent selection returned the baseline when "
                    "candidate resource/plan estimation was flat; sealing had no explicit "
                    "all-baseline-winner rejection"
                ),
                "repair": "reject zero nonbaseline winners before global ranking can seal",
            },
            {
                "symptom": "sealed_canonical_plan_bundle_sha256_null",
                "root_cause": (
                    "the checkpoint candidate signature accepted a missing canonical plan digest"
                ),
                "repair": "require a lowercase 64-hex canonical plan SHA-256 at checkpoint seal",
            },
            {
                "symptom": "replay_l2_l3_missing",
                "root_cause": (
                    "three-level fingerprint code existed, but replay orchestration did not "
                    "build and attach it before the outer acceptance gate"
                ),
                "repair": (
                    "attach the fingerprint from raw replay evidence; missing levels remain "
                    "independent candidates but are ineligible for outer acceptance"
                ),
            },
            {
                "symptom": "gradient_summary_not_revalidated_at_seal",
                "root_cause": "the ranking row did not preserve its raw gradient events",
                "repair": (
                    "preserve every event and recompute g_j=sum(dScore/dU*T*confidence) "
                    "plus the g_j/eta-derived parameter proposal before sealing"
                ),
            },
        ],
        "algorithm": {
            "gradient_formula": "g_j = sum_r(dScore/dU_r * T[j,r] * confidence)",
            "parameter_update": "proposed = before * (1 + eta * g_j)",
            "replacement_search_algorithm_used": False,
            "candidate_count": 15,
            "candidate_order": "global_final_candidate_score_descending",
            "fixed_top5": True,
            "qps_role": "outer_acceptance_after_real_replay_only",
        },
        "old_campaign_action": "NO_RERUN_NO_CREDIT",
        "successor_action": "FRESH_R0_THEN_NEW_DEBUG_ROUND_AFTER_ALL_GATES",
        "remote_actions": 0,
    }


def freeze(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink prohibited in sealed successor")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def verify_bundle(path: Path, expected_id: str | None = None):
    manifest_path = path / "bundle_manifest.json"
    sidecar = path / "bundle_manifest.json.sha256"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    identity = dict(manifest)
    identity.pop("bundle_id", None)
    actual_paths = {
        item.relative_to(path).as_posix()
        for item in path.rglob("*") if item.is_file()
    }
    expected_paths = set(manifest.get("files") or {}) | {
        "bundle_manifest.json", "bundle_manifest.json.sha256",
    }
    if (
        path.name != (expected_id or manifest.get("bundle_id"))
        or manifest.get("bundle_id") != path.name
        or manifest.get("schema_version") != SCHEMA
        or sha_bytes(canonical(identity)) != path.name
        or sidecar.read_text(encoding="ascii")
        != f"{digest(manifest_path)}  bundle_manifest.json\n"
        or actual_paths != expected_paths
        or manifest.get("algorithm_c_contract") != CONTRACT
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or manifest.get("accepted_rounds_credit") != 0
    ):
        raise BuildError("v14 immutable identity differs")
    for relative, expected in manifest["files"].items():
        candidate = path / relative
        if candidate.is_symlink() or not candidate.is_file() or digest(candidate) != expected:
            raise BuildError("v14 file identity differs: " + relative)
    return manifest


def publish() -> Path:
    parent = verify_parent()
    payload: dict[str, bytes] = {}
    modes: dict[str, int] = {}
    for relative in parent["files"]:
        if (
            relative == "closure.json"
            or relative in SOURCE_OVERRIDES
            or relative.startswith("tests/")
        ):
            continue
        source = PARENT / relative
        payload[relative] = source.read_bytes()
        modes[relative] = stat.S_IMODE(source.stat().st_mode)
    for relative, source_name in SOURCE_OVERRIDES.items():
        payload[relative] = (ROOT / source_name).read_bytes()
        modes[relative] = 0o444
    payload[TEST] = (ROOT / TEST).read_bytes()
    modes[TEST] = 0o444
    payload[BUILDER] = Path(__file__).read_bytes()
    modes[BUILDER] = 0o555
    payload[AUDIT] = canonical(root_cause_audit())
    modes[AUDIT] = 0o444

    closure = json.loads((PARENT / "closure.json").read_text(encoding="utf-8"))
    closure.update({
        "schema_version": "mysql_job_module_closure_v14",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_bundle_manifest_sha256": PARENT_MANIFEST_SHA256,
        "algorithm_c_contract": CONTRACT,
        "algorithm_root_cause_audit": AUDIT,
        "self_contained_bundle_test": TEST,
        "old_campaign_rerun_authorized": False,
        "accepted_rounds_credit": 0,
    })
    closure["files"] = {
        relative: sha_bytes(value) for relative, value in sorted(payload.items())
    }
    payload["closure.json"] = canonical(closure)
    modes["closure.json"] = 0o444

    identity = {key: value for key, value in parent.items() if key not in {"bundle_id", "files"}}
    identity.update({
        "schema_version": SCHEMA,
        "status": "SEALED_JOB_ALGORITHM_REPAIR_OFFLINE_NOT_R0_OR_FORMAL_AUTHORITY",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
        "algorithm_c_contract": CONTRACT,
        "algorithm_root_cause_audit": AUDIT,
        "self_contained_bundle_test": TEST,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "fresh_r0_required": True,
        "old_top5_rerun_authorized": False,
        "offline_build_only": True,
        "remote_actions": 0,
        "files": {relative: sha_bytes(value) for relative, value in sorted(payload.items())},
    })
    bundle_id = sha_bytes(canonical(identity))
    manifest = {**identity, "bundle_id": bundle_id}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    final = OUTPUT / bundle_id
    if final.exists() or final.is_symlink():
        verify_bundle(final, bundle_id)
        return final
    stage = OUTPUT / f".stage-{bundle_id}-{secrets.token_hex(6)}"
    stage.mkdir(mode=0o700)
    try:
        for relative, value in payload.items():
            path = stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
            os.chmod(path, modes[relative])
        manifest_payload = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(manifest_payload)
        os.chmod(stage / "bundle_manifest.json", 0o444)
        (stage / "bundle_manifest.json.sha256").write_text(
            f"{sha_bytes(manifest_payload)}  bundle_manifest.json\n", encoding="ascii",
        )
        os.chmod(stage / "bundle_manifest.json.sha256", 0o444)
        os.rename(stage, final)
    finally:
        if stage.exists():
            for path in sorted(stage.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            stage.rmdir()
    freeze(final)
    verify_bundle(final, bundle_id)
    return final


if __name__ == "__main__":
    bundle = publish()
    print(json.dumps({
        "status": "SEALED_LOCAL_ONLY_NOT_LAUNCHED",
        "bundle": str(bundle),
        "bundle_id": bundle.name,
        "bundle_manifest_sha256": digest(bundle / "bundle_manifest.json"),
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
    }, sort_keys=True))
