#!/usr/bin/env python3
"""Independently verify canonical XP template evidence and both source bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = ROOT / (
    "config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft/"
    "evidence/xp_template_deployment_20260723.json"
)
EXPECTED_ROWS = {
    "checking": 6000000,
    "checkingAccount": 3020000,
    "company": 20000,
    "customer": 3000000,
    "loanapps": 6000000,
    "loantrans": 6000000,
    "savingAccount": 3020000,
    "transfer": 60000000,
}


class VerificationError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise VerificationError(f"{label} must be an object")
    return value


def _repo_path(raw: Any, label: str) -> Path:
    text = str(raw or "")
    candidate = (ROOT / text).resolve()
    try:
        candidate.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise VerificationError(f"{label} escapes the repository") from exc
    if not candidate.is_file() or candidate.is_symlink():
        raise VerificationError(f"{label} is absent or a symlink: {candidate}")
    return candidate


def _require_sha(path: Path, expected: Any, label: str) -> None:
    actual = _sha256(path)
    if actual != str(expected):
        raise VerificationError(f"{label} SHA mismatch: {actual}")


def _verify_checksum_manifest(path: Path) -> int:
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or len(digest) != 64 or not name:
            raise VerificationError("SQL-count checksum manifest is malformed")
        child = (path.parent / name).resolve()
        if child.parent != path.parent.resolve() or not child.is_file() or child.is_symlink():
            raise VerificationError(f"invalid SQL-count bundle member: {name}")
        _require_sha(child, digest, f"SQL-count bundle member {name}")
        count += 1
    if count == 0:
        raise VerificationError("SQL-count checksum manifest is empty")
    return count


def verify(path: Path = DEFAULT_EVIDENCE) -> dict[str, Any]:
    evidence_path = Path(path).resolve()
    document = _mapping(json.loads(evidence_path.read_text(encoding="utf-8")), "evidence")
    sidecar = evidence_path.with_name(evidence_path.name + ".sha256")
    expected_sidecar = f"{_sha256(evidence_path)}  {evidence_path.name}\n"
    if not sidecar.is_file() or sidecar.is_symlink() or sidecar.read_text(encoding="ascii") != expected_sidecar:
        raise VerificationError("canonical evidence sidecar is absent or mismatched")
    if document.get("schema_version") != "mysql_xp_canonical_template_deployment_evidence_v1":
        raise VerificationError("canonical evidence schema mismatch")
    if document.get("status") != "TEMPLATE_DEPLOYMENT_VERIFIED_NOT_READY":
        raise VerificationError("canonical evidence status mismatch")
    scope = _mapping(document.get("formal_scope"), "formal_scope")
    if scope != {
        "template_deployment_only": True,
        "r0": False,
        "sgd": False,
        "accepted_iteration": False,
        "readiness_changed": False,
        "ready": False,
        "launch_authorized": False,
    }:
        raise VerificationError("formal scope must remain non-R0/non-SGD/non-READY")

    sources = _mapping(document.get("source_evidence"), "source_evidence")
    post_ref = _mapping(sources.get("post_copy"), "post_copy source")
    post_path = _repo_path(post_ref.get("path"), "post-copy evidence")
    _require_sha(post_path, post_ref.get("sha256"), "post-copy evidence")
    post_sidecar = _repo_path(post_ref.get("sidecar_path"), "post-copy sidecar")
    if post_sidecar.read_text(encoding="ascii") != f"{post_ref['sha256']}  {post_path.name}\n":
        raise VerificationError("post-copy sidecar mismatch")
    post = _mapping(json.loads(post_path.read_text(encoding="utf-8")), "post-copy evidence")
    if post.get("status") != "PASS" or post.get("errors") != [] or post.get("read_only") is not True:
        raise VerificationError("post-copy evidence is not a clean read-only PASS")

    sql_ref = _mapping(sources.get("sql_count_bundle"), "SQL-count source")
    checksum_path = _repo_path(sql_ref.get("checksums_path"), "SQL-count checksums")
    _require_sha(checksum_path, sql_ref.get("checksums_sha256"), "SQL-count checksums")
    member_count = _verify_checksum_manifest(checksum_path)
    verdict_path = _repo_path(sql_ref.get("verdict_path"), "SQL-count verdict")
    summary_path = _repo_path(sql_ref.get("summary_path"), "SQL-count summary")
    _require_sha(verdict_path, sql_ref.get("verdict_sha256"), "SQL-count verdict")
    _require_sha(summary_path, sql_ref.get("summary_sha256"), "SQL-count summary")
    verdict = _mapping(json.loads(verdict_path.read_text(encoding="utf-8")), "SQL-count verdict")
    summary = _mapping(json.loads(summary_path.read_text(encoding="utf-8")), "SQL-count summary")
    if verdict.get("passed") is not True or verdict.get("actual") != EXPECTED_ROWS:
        raise VerificationError("SQL-count verdict rows differ from the frozen eight-table contract")
    if verdict.get("expected") != EXPECTED_ROWS or verdict.get("actual_total") != 87060000 or verdict.get("expected_total") != 87060000:
        raise VerificationError("SQL-count verdict total/expected rows mismatch")
    if summary.get("status") != "SQL_ROWCOUNT_CLOSURE_PASS" or summary.get("total_rows") != 87060000:
        raise VerificationError("SQL-count summary is not a closure PASS")
    if any(summary.get(key) is not False for key in ("r0", "sgd", "accepted", "readiness_changed", "pg_mutation_performed")):
        raise VerificationError("SQL-count source exceeds template-only scope")

    before = checksum_path.parent / "template_v2_before.txt"
    after = checksum_path.parent / "template_v2_after.txt"
    if before.read_bytes() != after.read_bytes():
        raise VerificationError("canonical template changed across SQL-count closure")
    recovery = (checksum_path.parent / "recovery.log").read_text(encoding="utf-8")
    for required in (
        "clean_shutdown=1", "temporary_container_removed=true",
        "network_removed=true", "credential_material_used=false",
        "pg_mutation_performed=false", "r0=false", "sgd=false",
    ):
        if required not in recovery:
            raise VerificationError(f"recovery proof lacks {required}")

    identity = _mapping(document.get("template_identity"), "template_identity")
    unchanged = _mapping(document.get("template_unchanged"), "template_unchanged")
    if (
        document.get("exact_row_counts") != EXPECTED_ROWS
        or document.get("table_count") != 8
        or document.get("total_rows") != 87060000
        or identity.get("database_manifest_sha256") != post.get("manifest_sha256")
        or identity.get("template_tree_sha256") != post.get("template_snapshot_sha256")
        or identity.get("database_size_bytes") != post.get("database_size_bytes")
        or unchanged.get("before_inventory_sha256") != _sha256(before)
        or unchanged.get("after_inventory_sha256") != _sha256(after)
        or unchanged.get("passed") is not True
    ):
        raise VerificationError("canonical evidence does not exactly reproduce its sources")
    return {
        "status": "PASS",
        "evidence": str(evidence_path),
        "evidence_sha256": _sha256(evidence_path),
        "post_copy_sha256": _sha256(post_path),
        "sql_count_checksums_sha256": _sha256(checksum_path),
        "sql_count_bundle_member_count": member_count,
        "table_count": 8,
        "total_rows": 87060000,
        "ready": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    args = parser.parse_args()
    try:
        result = verify(args.evidence)
    except (OSError, UnicodeError, json.JSONDecodeError, VerificationError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
