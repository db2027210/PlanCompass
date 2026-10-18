#!/usr/bin/env python3
"""Promote only the verified XP MySQL envelope into draft readiness.

Default mode is dry-run.  ``--execute`` atomically updates the readiness
manifest, its sidecar, and a promotion audit.  PostgreSQL, cross-engine,
static-isolation, dynamic-interference, R0, READY, and launch gates are never
promoted by this tool.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DRAFT = ROOT / "config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft"
READINESS = DRAFT / "readiness_manifest.json"
PAIRED = ROOT / "config/formal/frozen_paired_pg_mysql_resource_envelopes_20260723_v1/paired_resource_envelopes.json"
SOURCE = ROOT / "history/mysql_sgd_env_agents_20260723/xp/formal_mysql_normalized_envelope_readback.json"
STATIC = DRAFT / "evidence/xp_static_isolation_audit_20260723.json"
VERIFIER = ROOT / "shell/verify_mysql_xp_tpcc_envelope_evidence.py"
READINESS_VERIFIER = ROOT / "shell/verify_mysql_sgd_v2_readiness.py"
PROMOTER = ROOT / "shell/promote_mysql_xp_envelope_readback.py"
AUDIT = DRAFT / "evidence/xp_mysql_envelope_readiness_promotion_audit.json"
SCENARIO = "hybench_xp_io"
FREEZE_ID = "frozen_paired_pg_mysql_resource_envelopes_20260723_v1"
ALLOWED = {
    f"scenarios.{SCENARIO}.mysql_envelope.freeze_id",
    f"scenarios.{SCENARIO}.mysql_envelope.readback",
    f"scenarios.{SCENARIO}.mysql_envelope.normalized",
}
PROTECTED_ASSERTIONS = {
    "postgresql_envelope_unchanged": True,
    "postgresql_envelope_unpromoted": True,
    "cross_engine_envelope_gate_false": True,
    "static_isolation_unchanged": True,
    "dynamic_interference_unchanged": True,
    "r0_calibration_unchanged": True,
    "global_status_not_ready": True,
    "launch_authorized_false": True,
}


class PromotionError(RuntimeError):
    pass


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PromotionError(f"JSON root must be an object: {path}")
    return value


def canonical(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def verify_sidecar(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise PromotionError(f"missing or symlinked evidence: {path}")
    value = digest(path)
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.is_file() or sidecar.is_symlink():
        raise PromotionError(f"missing sidecar: {sidecar}")
    if sidecar.read_text(encoding="ascii") != f"{value}  {path.name}\n":
        raise PromotionError(f"sidecar mismatch: {sidecar}")
    return value


def verify_paired_checksum() -> str:
    value = digest(PAIRED)
    checksum = PAIRED.parent / "checksums.sha256"
    expected = f"{value}  {PAIRED.name}"
    if expected not in checksum.read_text(encoding="ascii").splitlines():
        raise PromotionError("paired profile checksum mismatch")
    return value


def pointer(path: Path, value: str | None = None) -> dict[str, str]:
    return {"path": str(path.relative_to(ROOT)), "sha256": value or digest(path)}


def changes(before: Any, after: Any, prefix: str = "") -> set[str]:
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        result: set[str] = set()
        for key in set(before) | set(after):
            child = f"{prefix}.{key}" if prefix else str(key)
            result |= changes(before.get(key), after.get(key), child)
        return result
    return set() if before == after else {prefix}


def run_json(command: list[str], expected_rc: int) -> dict[str, Any]:
    result = subprocess.run(
        command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PromotionError(f"non-JSON verifier output: {result.stdout.strip()}") from exc
    if result.returncode != expected_rc:
        raise PromotionError(f"verifier rc={result.returncode}: {result.stdout.strip()}")
    return payload


def validate_inputs(readiness: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
    source_sha = verify_sidecar(SOURCE)
    verify_sidecar(STATIC)
    verify_sidecar(READINESS)
    verify_paired_checksum()
    source = load(SOURCE)
    paired = load(PAIRED)
    profile = paired["profiles"]["io"]
    if (
        source.get("schema_version") != "mysql_normalized_resource_envelope_readback_v1"
        or source.get("status") != "MYSQL_NORMALIZED_ENVELOPE_VERIFIED_NOT_READY"
        or source.get("scenario") != SCENARIO
        or source.get("engine") != "mysql"
        or source.get("profile") != "io"
        or source.get("freeze_id") != FREEZE_ID
        or source.get("normalized_envelope") != profile
        or source.get("requested_effective_equal") is not True
        or source.get("qualification") != {
            "all_eight_fields_have_live_effective_cgroup_readback": True,
            "matches_frozen_mysql_io_profile": True,
            "postgresql_counterpart_envelope_proven": False,
            "cross_engine_envelope_equality_proven": False,
            "formal_static_isolation_gate_passed": False,
        }
    ):
        raise PromotionError("XP normalized evidence identity/scope mismatch")
    scope = source.get("formal_scope") or {}
    if scope != {
        "environment_readback_only": True, "r0": False, "sgd": False,
        "accepted_iteration": False, "readiness_changed": False,
        "ready": False, "launch_authorized": False,
    }:
        raise PromotionError("XP normalized evidence scope is not fail-closed")
    if readiness.get("status") != "NOT_READY" or readiness.get("launch_authorized") is not False:
        raise PromotionError("readiness is not fail-closed")
    scenario = readiness["scenarios"][SCENARIO]
    if scenario.get("postgresql_envelope") != {"freeze_id": None, "normalized": None, "readback": None}:
        raise PromotionError("XP PostgreSQL envelope must remain unpromoted")
    if scenario.get("static_isolation") != {"evidence": None, "passed": False}:
        raise PromotionError("XP static-isolation gate must remain false")
    return source, dict(profile), source_sha


def verify_proposed(payload: bytes) -> dict[str, Any]:
    fd, raw = tempfile.mkstemp(prefix=".xp-envelope-readiness-", suffix=".json", dir=DRAFT)
    path = Path(raw)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        result = run_json(
            [sys.executable, str(READINESS_VERIFIER), "--manifest", str(path), "--json"], 1
        )
    finally:
        path.unlink(missing_ok=True)
    if result.get("status") != "NOT_READY" or result.get("ready") is not False:
        raise PromotionError("proposed readiness did not remain NOT_READY")
    return result


def atomic_publish(outputs: Mapping[Path, bytes]) -> None:
    staged: dict[Path, Path] = {}
    try:
        for target, payload in outputs.items():
            fd, raw = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
            temporary = Path(raw)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload); handle.flush(); os.fsync(handle.fileno())
            staged[target] = temporary
        for target in sorted(outputs, key=lambda item: (item == READINESS, str(item))):
            os.replace(staged.pop(target), target)
            directory = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)

    before_bytes = READINESS.read_bytes()
    readiness = load(READINESS)
    _, profile, source_sha = validate_inputs(readiness)
    evidence_result = run_json(
        [sys.executable, str(VERIFIER), "--allow-unpromoted-readiness"], 0
    )
    if evidence_result.get("status") != "PASS" or evidence_result.get("xp_mysql_normalized_envelope") is not True:
        raise PromotionError("XP envelope evidence verifier did not pass")

    desired = {
        "freeze_id": FREEZE_ID,
        "normalized": profile,
        "readback": pointer(SOURCE, source_sha),
    }
    updated = copy.deepcopy(readiness)
    updated["scenarios"][SCENARIO]["mysql_envelope"] = desired
    changed = changes(readiness, updated)
    already = not changed
    if changed != ALLOWED and not already:
        raise PromotionError(f"forbidden or incomplete changed paths: {sorted(changed)}")
    for field in ("postgresql_envelope", "static_isolation", "dynamic_interference", "r0_calibration"):
        if updated["scenarios"][SCENARIO][field] != readiness["scenarios"][SCENARIO][field]:
            raise PromotionError(f"forbidden XP gate changed: {field}")
    if updated.get("status") != "NOT_READY" or updated.get("launch_authorized") is not False:
        raise PromotionError("global readiness gate changed")
    proposed = canonical(updated)
    readiness_result = verify_proposed(proposed)

    if already:
        verify_sidecar(AUDIT)
        audit = load(AUDIT)
        protected = audit.get("protected_fields_unchanged") or {}
        if (
            audit.get("scope") != "hybench_xp_io_mysql_envelope_only"
            or audit.get("promoted_mysql_envelope") != desired
            or not (protected.get("static_isolation") is True or protected.get("static_isolation_unchanged") is True)
            or not (protected.get("dynamic_interference") is True or protected.get("dynamic_interference_unchanged") is True)
            or not (protected.get("r0_calibration") is True or protected.get("r0_calibration_unchanged") is True)
        ):
            raise PromotionError("existing promotion audit is stale or incomplete")
        if (
            audit.get("after_readiness_sha256") == digest_bytes(proposed)
            and audit.get("promoter") == pointer(PROMOTER)
            and audit.get("evidence_verifier") == pointer(VERIFIER)
            and audit.get("readiness_verifier") == pointer(READINESS_VERIFIER)
            and audit.get("protected_fields_unchanged") == PROTECTED_ASSERTIONS
        ):
            audit_payload = AUDIT.read_bytes()
        else:
            audit = copy.deepcopy(audit)
            audit["superseded_after_readiness_sha256"] = audit.get("after_readiness_sha256")
            audit["after_readiness_sha256"] = digest_bytes(proposed)
            audit["audit_refresh_reason"] = (
                "unrelated readiness evidence changed; XP envelope projection and fail-closed gates revalidated"
            )
            audit["promoter"] = pointer(PROMOTER)
            audit["evidence_verifier"] = pointer(VERIFIER)
            audit["readiness_verifier"] = pointer(READINESS_VERIFIER)
            audit["evidence_verifier_result"] = evidence_result
            audit["readiness_reverification"] = {
                "status": readiness_result.get("status"),
                "ready": readiness_result.get("ready"),
                "passed": readiness_result.get("passed"),
                "failed": readiness_result.get("failed"),
            }
            audit["protected_fields_unchanged"] = PROTECTED_ASSERTIONS
            audit_payload = canonical(audit)
    else:
        audit = {
            "schema_version": 1,
            "scope": "hybench_xp_io_mysql_envelope_only",
            "before_readiness_sha256": digest_bytes(before_bytes),
            "after_readiness_sha256": digest_bytes(proposed),
            "changed_json_paths": sorted(changed),
            "already_promoted": False,
            "promoted_mysql_envelope": desired,
            "promoter": pointer(PROMOTER),
            "evidence_verifier": pointer(VERIFIER),
            "evidence_verifier_result": evidence_result,
            "readiness_verifier": pointer(READINESS_VERIFIER),
            "readiness_reverification": {
                "status": readiness_result.get("status"),
                "ready": readiness_result.get("ready"),
                "passed": readiness_result.get("passed"),
                "failed": readiness_result.get("failed"),
            },
            "protected_fields_unchanged": PROTECTED_ASSERTIONS,
        }
        audit_payload = canonical(audit)
    outputs = {
        READINESS: proposed,
        READINESS.with_name(READINESS.name + ".sha256"):
            f"{digest_bytes(proposed)}  {READINESS.name}\n".encode(),
        AUDIT: audit_payload,
        AUDIT.with_name(AUDIT.name + ".sha256"):
            f"{digest_bytes(audit_payload)}  {AUDIT.name}\n".encode(),
    }
    if args.execute:
        atomic_publish(outputs)
        if any(path.read_bytes() != payload for path, payload in outputs.items()):
            raise PromotionError("post-publication byte verification failed")
    print(json.dumps({
        "status": "PASS", "mode": "execute" if args.execute else "dry-run",
        "already_promoted": already, "changed_json_paths": sorted(changed),
        "readiness_status": "NOT_READY", "launch_authorized": False,
        "replacement_sha256": {str(path.relative_to(ROOT)): digest_bytes(payload) for path, payload in outputs.items()},
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, PromotionError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        raise SystemExit(2)
