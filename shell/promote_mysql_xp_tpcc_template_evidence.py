#!/usr/bin/env python3
"""Promote verified XP and TPC-C template evidence into the draft readiness manifest.

Default mode is dry-run.  Only ``--execute`` writes, and only the two scenarios'
``environment.current_state``, ``environment.deployment_evidence`` and
``template`` objects may change.  No environment, R0, T, lease, READY or launch
gate is inferred by this tool.
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
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DRAFT = ROOT / "config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft"
READINESS = DRAFT / "readiness_manifest.json"
XP_EVIDENCE = DRAFT / "evidence/xp_template_deployment_20260723.json"
TPCC_EVIDENCE = DRAFT / "evidence/tpcc_e1_template_deployment_20260723.json"
XP_VERIFIER = ROOT / "shell/verify_mysql_xp_canonical_template_evidence.py"
TPCC_VERIFIER = ROOT / "shell/verify_mysql_tpcc_e1_canonical_evidence.py"
AUDIT = DRAFT / "evidence/xp_tpcc_template_readiness_promotion_audit.json"
PROMOTER = ROOT / "shell/promote_mysql_xp_tpcc_template_evidence.py"
SCENARIOS = {
    "hybench_xp_io": {
        "benchmark": "hybench_xp",
        "schema": "mysql_xp_canonical_template_deployment_evidence_v1",
        "host": "192.0.2.4",
        "device": "/dev/nvme0n1p1",
        "mount": "/mnt/mysql_nvme0n1p1",
        "evidence": XP_EVIDENCE,
        "verifier": XP_VERIFIER,
    },
    "tpcc_cpu": {
        "benchmark": "tpcc",
        "schema": "mysql_tpcc_e1_canonical_template_deployment_evidence_v1",
        "host": "192.0.2.7",
        "device": "/dev/nvme3n1",
        "mount": "/mnt/mysql_tpcc_nvme3n1",
        "evidence": TPCC_EVIDENCE,
        "verifier": TPCC_VERIFIER,
    },
}
SCOPE = {
    "template_deployment_only": True,
    "r0": False,
    "sgd": False,
    "accepted_iteration": False,
    "readiness_changed": False,
    "ready": False,
    "launch_authorized": False,
}
ALLOWED_PATHS = {
    f"scenarios.{scenario}.{suffix}"
    for scenario in SCENARIOS
    for suffix in (
        "environment.current_state", "environment.deployment_evidence",
        "template.ready", "template.manifest", "template.deployment_verification",
    )
}


class PromotionError(RuntimeError):
    pass


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def load_object(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PromotionError(f"{label} root must be an object")
    return value


def verify_sidecar(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise PromotionError(f"canonical evidence missing or symlinked: {path}")
    digest = sha256_file(path)
    sidecar = path.with_name(path.name + ".sha256")
    expected = f"{digest}  {path.name}\n"
    if (
        not sidecar.is_file() or sidecar.is_symlink()
        or sidecar.read_text(encoding="ascii") != expected
    ):
        raise PromotionError(f"canonical sidecar mismatch: {sidecar}")
    return digest


def pointer(path: Path, digest: str | None = None) -> dict[str, str]:
    return {
        "path": str(path.relative_to(ROOT)),
        "sha256": digest or sha256_file(path),
    }


def validate_canonical(
    scenario: str, spec: Mapping[str, Any], readiness: Mapping[str, Any]
) -> tuple[dict[str, Any], str]:
    path = Path(spec["evidence"])
    digest = verify_sidecar(path)
    document = load_object(path, f"{scenario} canonical evidence")
    expected = readiness["scenarios"][scenario]
    if (
        document.get("schema_version") != spec["schema"]
        or document.get("artifact_type") != "mysql_template_deployment_evidence"
        or document.get("status") != "TEMPLATE_DEPLOYMENT_VERIFIED_NOT_READY"
        or document.get("scenario") != scenario
        or document.get("benchmark") != spec["benchmark"]
        or document.get("engine") != "mysql"
        or document.get("host") != spec["host"]
        or document.get("device") != spec["device"]
        or document.get("mount") != spec["mount"]
        or document.get("formal_scope") != SCOPE
    ):
        raise PromotionError(f"{scenario} canonical identity/scope mismatch")
    environment = expected.get("environment") or {}
    if any(
        environment.get(name) != spec[name]
        for name in ("device", "mount")
    ) or environment.get("host_address") != spec["host"]:
        raise PromotionError(f"{scenario} readiness allocation differs from canonical evidence")
    identity = document.get("template_identity")
    if not isinstance(identity, Mapping) or not identity:
        raise PromotionError(f"{scenario} lacks template identity")
    if document.get("exact_row_counts") in (None, {}) or not document.get("template_path"):
        raise PromotionError(f"{scenario} lacks row-count/template-path closure")
    return document, digest


def run_verifier(path: Path, evidence: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise PromotionError(f"verifier missing or symlinked: {path}")
    command = [sys.executable, str(path), "--evidence", str(evidence)]
    result = subprocess.run(
        command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False,
    )
    output = result.stdout.strip()
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise PromotionError(f"verifier returned non-JSON output: {output}") from exc
    if result.returncode != 0 or not isinstance(payload, Mapping) or payload.get("status") != "PASS":
        raise PromotionError(f"verifier failed rc={result.returncode}: {output}")
    return {
        "command": command,
        "exit_code": result.returncode,
        "result": copy.deepcopy(dict(payload)),
        "verifier": pointer(path),
    }


def changed_paths(before: Any, after: Any, prefix: str = "") -> set[str]:
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        result: set[str] = set()
        for key in set(before) | set(after):
            child = f"{prefix}.{key}" if prefix else str(key)
            result |= changed_paths(before.get(key), after.get(key), child)
        return result
    return set() if before == after else {prefix}


def atomic_publish(outputs: Mapping[Path, bytes]) -> None:
    staged: dict[Path, Path] = {}
    try:
        for target, payload in outputs.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, raw = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
            temporary = Path(raw)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            staged[target] = temporary
        # Readiness is the final commit marker.
        for target in sorted(outputs, key=lambda item: (item == READINESS, str(item))):
            os.replace(staged.pop(target), target)
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def wait_for_inputs(seconds: int) -> None:
    deadline = time.monotonic() + seconds
    required = [
        path
        for spec in SCENARIOS.values()
        for path in (Path(spec["evidence"]), Path(spec["evidence"]).with_name(Path(spec["evidence"]).name + ".sha256"), Path(spec["verifier"]))
    ]
    while True:
        missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
        if not missing:
            return
        if time.monotonic() >= deadline:
            raise PromotionError(f"canonical evidence not ready; no files written: {missing}")
        time.sleep(min(5, max(0.0, deadline - time.monotonic())))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--wait-seconds", type=int, default=0)
    args = parser.parse_args(argv)
    if args.wait_seconds < 0:
        raise PromotionError("--wait-seconds must be non-negative")
    wait_for_inputs(args.wait_seconds)

    readiness_bytes = READINESS.read_bytes()
    readiness = load_object(READINESS, "readiness manifest")
    if readiness.get("status") != "NOT_READY" or readiness.get("launch_authorized") is not False:
        raise PromotionError("readiness must remain NOT_READY and launch-disabled")
    publication = (readiness.get("global_requirements") or {}).get("bundle_publication") or {}
    if publication.get("ready") is not False or publication.get("ready_file_authorizes_launch") is not False:
        raise PromotionError("published/authorized bundle cannot be changed")
    if set(SCENARIOS) - set(readiness.get("scenarios") or {}):
        raise PromotionError("readiness lacks XP/TPC-C scenarios")

    evidence: dict[str, tuple[dict[str, Any], str]] = {
        scenario: validate_canonical(scenario, spec, readiness)
        for scenario, spec in SCENARIOS.items()
    }
    desired_pointers = {
        scenario: pointer(Path(SCENARIOS[scenario]["evidence"]), evidence[scenario][1])
        for scenario in SCENARIOS
    }
    already_promoted = all(
        readiness["scenarios"][scenario].get("environment", {}).get("deployment_evidence")
            == desired_pointers[scenario]
        and readiness["scenarios"][scenario].get("template") == {
            "ready": True,
            "manifest": desired_pointers[scenario],
            "deployment_verification": desired_pointers[scenario],
        }
        and readiness["scenarios"][scenario].get("environment", {}).get("current_state")
            == "canonical_template_deployment_verified"
        for scenario in SCENARIOS
    )

    if already_promoted:
        verify_sidecar(AUDIT)
        audit = load_object(AUDIT, "promotion audit")
        if (
            audit.get("promoted_evidence") != desired_pointers
            or audit.get("ready_or_launch_authorization_set") is not False
        ):
            raise PromotionError("existing promotion audit does not bind current readiness/evidence")
        for scenario, spec in SCENARIOS.items():
            record = (audit.get("verifier_results") or {}).get(scenario) or {}
            if (
                record.get("exit_code") != 0
                or (record.get("result") or {}).get("status") != "PASS"
                or record.get("verifier") != pointer(Path(spec["verifier"]))
            ):
                raise PromotionError(f"existing {scenario} verifier audit is stale")
        proposed = readiness_bytes
        current_readiness_sha = sha256_bytes(readiness_bytes)
        if (
            audit.get("promoter") == pointer(PROMOTER)
            and audit.get("after_readiness_sha256") == current_readiness_sha
        ):
            audit_payload = AUDIT.read_bytes()
        else:
            audit = copy.deepcopy(audit)
            previous_after = audit.get("after_readiness_sha256")
            verifier = ROOT / "shell/verify_mysql_sgd_v2_readiness.py"
            check = subprocess.run(
                [sys.executable, str(verifier), "--json"], cwd=ROOT,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                check=False,
            )
            try:
                check_payload = json.loads(check.stdout)
            except json.JSONDecodeError as exc:
                raise PromotionError("readiness verifier returned non-JSON output") from exc
            if check.returncode != 1 or check_payload.get("status") != "NOT_READY":
                raise PromotionError("current readiness did not validate as NOT_READY")
            audit["superseded_after_readiness_sha256"] = previous_after
            audit["after_readiness_sha256"] = current_readiness_sha
            audit["audit_refresh_reason"] = (
                "readiness evidence chain changed after template promotion; "
                "template projection and NOT_READY status revalidated"
            )
            audit["readiness_reverification"] = {
                "verifier": pointer(verifier),
                "exit_code": check.returncode,
                "status": check_payload.get("status"),
                "passed": check_payload.get("passed"),
                "failed": check_payload.get("failed"),
            }
            audit["promoter"] = pointer(PROMOTER)
            audit_payload = canonical_json(audit)
        changes: set[str] = set()
        verifier_results = audit["verifier_results"]
    else:
        verifier_results = {
            scenario: run_verifier(Path(spec["verifier"]), Path(spec["evidence"]))
            for scenario, spec in SCENARIOS.items()
        }
        # Verifiers may inspect remote state. Refuse promotion if local inputs moved meanwhile.
        for scenario, spec in SCENARIOS.items():
            if verify_sidecar(Path(spec["evidence"])) != evidence[scenario][1]:
                raise PromotionError(f"{scenario} evidence changed during verification")
        updated = copy.deepcopy(readiness)
        for scenario in SCENARIOS:
            item = updated["scenarios"][scenario]
            item["environment"]["current_state"] = "canonical_template_deployment_verified"
            item["environment"]["deployment_evidence"] = desired_pointers[scenario]
            item["template"] = {
                "ready": True,
                "manifest": desired_pointers[scenario],
                "deployment_verification": desired_pointers[scenario],
            }
        changes = changed_paths(readiness, updated)
        if not changes or any(
            not any(path == allowed or path.startswith(allowed + ".") for allowed in ALLOWED_PATHS)
            for path in changes
        ):
            raise PromotionError(f"promotion changed forbidden or incomplete fields: {sorted(changes)}")
        if (
            updated.get("status") != readiness.get("status")
            or updated.get("launch_authorized") != readiness.get("launch_authorized")
            or updated["global_requirements"] != readiness["global_requirements"]
        ):
            raise PromotionError("global readiness/authorization gates changed")
        proposed = canonical_json(updated)
        audit_document = {
            "schema_version": 1,
            "scope": "xp_tpcc_canonical_template_evidence_only",
            "before_readiness_sha256": sha256_bytes(readiness_bytes),
            "after_readiness_sha256": sha256_bytes(proposed),
            "changed_json_paths": sorted(changes),
            "promoted_evidence": desired_pointers,
            "promoter": pointer(PROMOTER),
            "verifier_results": verifier_results,
            "status_unchanged": "NOT_READY",
            "launch_authorized_unchanged": False,
            "bundle_publication_unchanged": True,
            "other_scenarios_unchanged": True,
            "ready_or_launch_authorization_set": False,
        }
        audit_payload = canonical_json(audit_document)

    readiness_sidecar = f"{sha256_bytes(proposed)}  {READINESS.name}\n".encode()
    audit_sidecar = f"{sha256_bytes(audit_payload)}  {AUDIT.name}\n".encode()
    outputs = {
        AUDIT: audit_payload,
        AUDIT.with_name(AUDIT.name + ".sha256"): audit_sidecar,
        READINESS: proposed,
        READINESS.with_name(READINESS.name + ".sha256"): readiness_sidecar,
    }
    report = {
        "status": "PASS",
        "mode": "execute" if args.execute else "dry-run",
        "already_promoted": already_promoted,
        "changed_json_paths": sorted(changes),
        "before_readiness_sha256": sha256_bytes(readiness_bytes),
        "replacement_sha256": {
            str(READINESS.relative_to(ROOT)): sha256_bytes(proposed),
            str(AUDIT.relative_to(ROOT)): sha256_bytes(audit_payload),
        },
        "verifier_results": verifier_results,
        "readiness_status": readiness["status"],
        "launch_authorized": readiness["launch_authorized"],
    }
    if args.execute:
        atomic_publish(outputs)
        if any(path.read_bytes() != payload for path, payload in outputs.items()):
            raise PromotionError("post-publication byte verification failed")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, PromotionError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        raise SystemExit(2)
