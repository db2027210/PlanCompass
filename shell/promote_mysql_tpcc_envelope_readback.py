#!/usr/bin/env python3
"""Promote only the verified TPC-C MySQL preregistered envelope into readiness.

The default is a no-write dry-run. ``--execute`` atomically publishes the
readiness manifest, exact sidecars, and an audit. This does not freeze the
preregistered profile as a formal R0 value and cannot promote PostgreSQL,
cross-engine equality, static isolation, R0, READY, or launch authorization.
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
SOURCE = ROOT / "history/mysql_sgd_env_agents_20260723/tpcc/formal_mysql_normalized_envelope_readback.json"
VERIFIER = ROOT / "shell/verify_mysql_xp_tpcc_envelope_evidence.py"
READINESS_VERIFIER = ROOT / "shell/verify_mysql_sgd_v2_readiness.py"
PROMOTER = ROOT / "shell/promote_mysql_tpcc_envelope_readback.py"
AUDIT = DRAFT / "evidence/tpcc_mysql_envelope_readiness_promotion_audit.json"
SCENARIO = "tpcc_cpu"
FREEZE_ID = "frozen_paired_pg_mysql_resource_envelopes_20260723_v1"
PROFILE_NAME = "tpcc_cpu_r0_preregistered"
ALLOWED = {
    f"scenarios.{SCENARIO}.mysql_envelope.freeze_id",
    f"scenarios.{SCENARIO}.mysql_envelope.normalized",
    f"scenarios.{SCENARIO}.mysql_envelope.readback",
}
PROTECTED = {
    "postgresql_envelope_unchanged": True,
    "postgresql_envelope_unpromoted": True,
    "cross_engine_envelope_gate_false": True,
    "static_isolation_unchanged": True,
    "dynamic_interference_unchanged": True,
    "r0_calibration_unchanged": True,
    "formal_value_frozen_false": True,
    "global_status_not_ready": True,
    "launch_authorized_false": True,
}


class PromotionError(RuntimeError):
    pass


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PromotionError(f"JSON root must be object: {path}")
    return value


def canonical(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def sidecar(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise PromotionError(f"missing or symlinked input: {path}")
    value = sha(path)
    check = path.with_name(path.name + ".sha256")
    if (
        not check.is_file() or check.is_symlink()
        or check.read_text(encoding="ascii") != f"{value}  {path.name}\n"
    ):
        raise PromotionError(f"sidecar mismatch: {check}")
    return value


def paired_checksum() -> None:
    expected = f"{sha(PAIRED)}  {PAIRED.name}"
    if expected not in (PAIRED.parent / "checksums.sha256").read_text(encoding="ascii").splitlines():
        raise PromotionError("paired profile checksum mismatch")


def pointer(path: Path, value: str | None = None) -> dict[str, str]:
    return {"path": str(path.relative_to(ROOT)), "sha256": value or sha(path)}


def changed(before: Any, after: Any, prefix: str = "") -> set[str]:
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        result: set[str] = set()
        for key in set(before) | set(after):
            child = f"{prefix}.{key}" if prefix else str(key)
            result |= changed(before.get(key), after.get(key), child)
        return result
    return set() if before == after else {prefix}


def run_json(command: list[str], expected_rc: int) -> dict[str, Any]:
    result = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, check=False)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PromotionError(f"non-JSON verifier output: {result.stdout.strip()}") from exc
    if result.returncode != expected_rc:
        raise PromotionError(f"verifier rc={result.returncode}: {result.stdout.strip()}")
    return payload


def proposed_readiness_check(payload: bytes) -> dict[str, Any]:
    fd, raw = tempfile.mkstemp(prefix=".tpcc-envelope-readiness-", suffix=".json", dir=DRAFT)
    path = Path(raw)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        result = run_json([sys.executable, str(READINESS_VERIFIER), "--manifest", str(path), "--json"], 1)
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
    source_sha = sidecar(SOURCE)
    sidecar(READINESS)
    paired_checksum()
    source = load(SOURCE)
    paired = load(PAIRED)
    profile = paired["profiles"][PROFILE_NAME]
    scenario_profile = paired["scenarios"][SCENARIO]
    if scenario_profile.get("formal_value_frozen") is not False:
        raise PromotionError("TPC-C preregistered profile is not explicitly non-frozen")
    if (
        source.get("schema_version") != "mysql_normalized_resource_envelope_readback_v1"
        or source.get("status") != "MYSQL_NORMALIZED_ENVELOPE_VERIFIED_NOT_READY"
        or source.get("scenario") != SCENARIO or source.get("engine") != "mysql"
        or source.get("profile") != PROFILE_NAME or source.get("freeze_id") != FREEZE_ID
        or source.get("normalized_envelope") != profile
        or source.get("formal_scope") != {
            "environment_readback_only": True, "r0": False, "sgd": False,
            "accepted_iteration": False, "readiness_changed": False,
            "ready": False, "launch_authorized": False,
        }
    ):
        raise PromotionError("TPC-C normalized evidence identity/scope mismatch")
    static = source.get("static_isolation") or {}
    if (
        static.get("postgresql_profile_matches_tpcc") is not False
        or static.get("cross_engine_envelope_equality") is not False
        or static.get("formal_static_isolation_gate_passed") is not False
    ):
        raise PromotionError("TPC-C source does not preserve failed paired/static gates")
    if readiness.get("status") != "NOT_READY" or readiness.get("launch_authorized") is not False:
        raise PromotionError("readiness is not fail-closed")
    scenario = readiness["scenarios"][SCENARIO]
    if scenario.get("postgresql_envelope") != {"freeze_id": None, "normalized": None, "readback": None}:
        raise PromotionError("TPC-C PostgreSQL envelope is not unpromoted")
    if scenario.get("static_isolation") != {"evidence": None, "passed": False}:
        raise PromotionError("TPC-C static-isolation gate changed")
    for gate in ("dynamic_interference", "r0_calibration"):
        if scenario.get(gate, {}).get("passed") is not False:
            raise PromotionError(f"TPC-C {gate} gate changed")

    evidence = run_json([sys.executable, str(VERIFIER), "--allow-unpromoted-readiness"], 0)
    if (
        evidence.get("status") != "PASS"
        or evidence.get("tpcc_mysql_normalized_envelope") is not True
        or evidence.get("tpcc_formal_value_frozen") is not False
        or evidence.get("tpcc_formal_static_gate") is not False
        or evidence.get("tpcc_cross_engine_equality") is not False
    ):
        raise PromotionError("TPC-C canonical verifier did not preserve fail-closed scope")

    desired = {"freeze_id": FREEZE_ID, "normalized": profile, "readback": pointer(SOURCE, source_sha)}
    updated = copy.deepcopy(readiness)
    updated["scenarios"][SCENARIO]["mysql_envelope"] = desired
    paths = changed(readiness, updated)
    already = not paths
    if paths != ALLOWED and not already:
        raise PromotionError(f"forbidden or incomplete changed paths: {sorted(paths)}")
    for field in ("postgresql_envelope", "static_isolation", "dynamic_interference", "r0_calibration"):
        if updated["scenarios"][SCENARIO][field] != readiness["scenarios"][SCENARIO][field]:
            raise PromotionError(f"protected field changed: {field}")
    proposed = canonical(updated)
    readiness_result = proposed_readiness_check(proposed)

    if already:
        sidecar(AUDIT)
        audit = load(AUDIT)
        if (
            audit.get("scope") != "tpcc_cpu_mysql_preregistered_envelope_only"
            or audit.get("promoted_mysql_envelope") != desired
            or audit.get("protected_fields_unchanged") != PROTECTED
        ):
            raise PromotionError("existing TPC-C promotion audit is stale")
        if (
            audit.get("after_readiness_sha256") == sha_bytes(proposed)
            and audit.get("promoter") == pointer(PROMOTER)
            and audit.get("evidence_verifier") == pointer(VERIFIER)
            and audit.get("readiness_verifier") == pointer(READINESS_VERIFIER)
        ):
            audit_payload = AUDIT.read_bytes()
        else:
            audit = copy.deepcopy(audit)
            audit["superseded_after_readiness_sha256"] = audit.get("after_readiness_sha256")
            audit["after_readiness_sha256"] = sha_bytes(proposed)
            audit["audit_refresh_reason"] = (
                "unrelated readiness evidence changed; TPC-C envelope projection and fail-closed gates revalidated"
            )
            audit["promoter"] = pointer(PROMOTER)
            audit["evidence_verifier"] = pointer(VERIFIER)
            audit["evidence_verifier_result"] = evidence
            audit["readiness_verifier"] = pointer(READINESS_VERIFIER)
            audit["readiness_reverification"] = {
                "status": readiness_result.get("status"), "ready": readiness_result.get("ready"),
                "passed": readiness_result.get("passed"), "failed": readiness_result.get("failed"),
            }
            audit_payload = canonical(audit)
    else:
        audit_payload = canonical({
            "schema_version": 1,
            "scope": "tpcc_cpu_mysql_preregistered_envelope_only",
            "before_readiness_sha256": sha_bytes(before_bytes),
            "after_readiness_sha256": sha_bytes(proposed),
            "changed_json_paths": sorted(paths),
            "already_promoted": False,
            "promoted_mysql_envelope": desired,
            "profile_status": {"profile": PROFILE_NAME, "formal_value_frozen": False, "r0_passed": False},
            "promoter": pointer(PROMOTER),
            "evidence_verifier": pointer(VERIFIER),
            "evidence_verifier_result": evidence,
            "readiness_verifier": pointer(READINESS_VERIFIER),
            "readiness_reverification": {
                "status": readiness_result.get("status"), "ready": readiness_result.get("ready"),
                "passed": readiness_result.get("passed"), "failed": readiness_result.get("failed"),
            },
            "protected_fields_unchanged": PROTECTED,
        })
    outputs = {
        READINESS: proposed,
        READINESS.with_name(READINESS.name + ".sha256"): f"{sha_bytes(proposed)}  {READINESS.name}\n".encode(),
        AUDIT: audit_payload,
        AUDIT.with_name(AUDIT.name + ".sha256"): f"{sha_bytes(audit_payload)}  {AUDIT.name}\n".encode(),
    }
    if args.execute:
        atomic_publish(outputs)
        if any(path.read_bytes() != payload for path, payload in outputs.items()):
            raise PromotionError("post-publication byte verification failed")
    print(json.dumps({
        "status": "PASS", "mode": "execute" if args.execute else "dry-run",
        "already_promoted": already, "changed_json_paths": sorted(paths),
        "formal_value_frozen": False, "r0_passed": False,
        "readiness_status": "NOT_READY", "launch_authorized": False,
        "replacement_sha256": {str(path.relative_to(ROOT)): sha_bytes(payload) for path, payload in outputs.items()},
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, PromotionError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        raise SystemExit(2)
