#!/usr/bin/env python3
"""Read-only, fail-closed verification for the frozen MySQL HyBench XP copy.

The verifier never changes the template.  It validates the authoritative
manifest and sidecar, the eight-table row-count contract, sealed permissions,
and the byte-level ``sha256_tree_v1`` datadir digest used by the formal engine
gate.  Re-running it is safe and produces no files.
"""

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


AUTHORITATIVE_MANIFEST_SHA256 = (
    "fd7c9918890fe6eea33f900da05a8d2825e204bc3f3cbf90505ddc43b98b52fa"
)
EXPECTED_ROWS = {
    "checking": 6_000_000,
    "checkingAccount": 3_020_000,
    "company": 20_000,
    "customer": 3_000_000,
    "loanapps": 6_000_000,
    "loantrans": 6_000_000,
    "savingAccount": 3_020_000,
    "transfer": 60_000_000,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_size_and_sha256(root: Path) -> Tuple[int, str, Dict[str, Any]]:
    """Implement the formal engine gate's exact sha256_tree_v1 algorithm."""
    digest = hashlib.sha256()
    total = 0
    counts = {"directories": 0, "regular_files": 0, "symlinks": 0}
    table_sha256: Dict[str, str] = {}
    entries = sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
    for path in entries:
        relative_text = path.relative_to(root).as_posix()
        relative = relative_text.encode("utf-8")
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            target = os.readlink(path).encode("utf-8")
            digest.update(b"L\0" + relative + b"\0" + target + b"\0")
            counts["symlinks"] += 1
            continue
        if stat.S_ISDIR(mode):
            digest.update(b"D\0" + relative + b"\0")
            counts["directories"] += 1
            continue
        if not stat.S_ISREG(mode):
            raise ValueError(f"non-regular datadir entry: {relative_text}")
        size = path.stat().st_size
        total += size
        counts["regular_files"] += 1
        digest.update(b"F\0" + relative + b"\0" + str(size).encode("ascii") + b"\0")
        file_digest = hashlib.sha256() if relative_text.startswith("hybench_sf10x/") else None
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                digest.update(block)
                if file_digest is not None:
                    file_digest.update(block)
        if file_digest is not None and path.suffix == ".ibd":
            table_sha256[relative_text] = file_digest.hexdigest()
    details: Dict[str, Any] = dict(counts)
    details["table_ibd_sha256"] = table_sha256
    return total, digest.hexdigest(), details


def _regular_non_symlink(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except FileNotFoundError:
        return False


def _writable_entries(root: Path) -> List[str]:
    result: List[str] = []
    candidates = [root, *root.rglob("*")]
    for path in candidates:
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            continue
        if mode & 0o222:
            result.append("." if path == root else path.relative_to(root).as_posix())
    return sorted(result)


def verify(
    template_root: Path,
    *,
    manifest_path: Optional[Path] = None,
    expected_manifest_sha256: str = AUTHORITATIVE_MANIFEST_SHA256,
) -> Dict[str, Any]:
    root = template_root.absolute()
    manifest = (manifest_path or root / "database_manifest.json").absolute()
    sidecar = manifest.with_name(manifest.name + ".sha256")
    datadir = root / "datadir"
    errors: List[str] = []

    if not root.is_dir() or root.is_symlink():
        errors.append("template root must be a non-symlink directory")
    if not datadir.is_dir() or datadir.is_symlink():
        errors.append("datadir must be a non-symlink directory")
    if not _regular_non_symlink(manifest):
        errors.append("database_manifest.json must be a regular non-symlink file")

    manifest_sha256: Optional[str] = None
    document: Dict[str, Any] = {}
    if _regular_non_symlink(manifest):
        manifest_sha256 = sha256_file(manifest)
        if manifest_sha256 != expected_manifest_sha256:
            errors.append(
                f"manifest SHA-256 mismatch: observed={manifest_sha256} "
                f"expected={expected_manifest_sha256}"
            )
        try:
            loaded = json.loads(manifest.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("manifest root is not an object")
            document = loaded
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"invalid manifest JSON: {exc}")

    expected_sidecar = f"{expected_manifest_sha256}  {manifest.name}\n"
    if not _regular_non_symlink(sidecar):
        errors.append(f"missing regular non-symlink sidecar: {sidecar.name}")
    else:
        try:
            if sidecar.read_text(encoding="ascii") != expected_sidecar:
                errors.append("manifest sidecar content mismatch")
        except (OSError, UnicodeError) as exc:
            errors.append(f"invalid manifest sidecar: {exc}")

    expected_fields = {
        "schema_version": 1,
        "freeze_id": "frozen_formal_config_20260721_v2",
        "global_campaign_id": "six_session_20260721_v2",
        "engine": "mysql",
        "template_id": "mysql80_hybench10x_v1",
        "database_version": "8.0.45",
        "dataset_version": "hybench-10x-v1",
        "scale_factor": "10x",
        "workload": "hybench",
        "database": "hybench_sf10x",
        "status": "complete",
        "template_snapshot_sha256_algorithm": "sha256_tree_v1",
        "clean_database_shutdown_before_snapshot": True,
    }
    for field, expected in expected_fields.items():
        if document.get(field) != expected:
            errors.append(f"manifest field mismatch: {field}")

    for field in ("row_counts", "row_count_by_table"):
        if document.get(field) != EXPECTED_ROWS:
            errors.append(f"manifest {field} does not equal the frozen eight-table contract")
    load_audit = document.get("load_audit")
    if not isinstance(load_audit, dict):
        errors.append("manifest load_audit is missing")
    else:
        for table, rows in EXPECTED_ROWS.items():
            audit = load_audit.get(table)
            if not isinstance(audit, dict):
                errors.append(f"load_audit missing table: {table}")
                continue
            if audit.get("loaded_rows") != rows or audit.get("final_count_rows") != rows:
                errors.append(f"load_audit row mismatch: {table}")
            if audit.get("warning_count") != 0 or audit.get("closure_passed") is not True:
                errors.append(f"load_audit closure mismatch: {table}")

    for table in EXPECTED_ROWS:
        path = datadir / "hybench_sf10x" / f"{table}.ibd"
        if not _regular_non_symlink(path):
            errors.append(f"missing table file: hybench_sf10x/{table}.ibd")

    tree_size: Optional[int] = None
    tree_sha256: Optional[str] = None
    tree_details: Dict[str, Any] = {}
    if datadir.is_dir() and not datadir.is_symlink():
        try:
            tree_size, tree_sha256, tree_details = tree_size_and_sha256(datadir)
            if tree_size != document.get("database_size_bytes"):
                errors.append(
                    f"database size mismatch: observed={tree_size} "
                    f"expected={document.get('database_size_bytes')}"
                )
            if tree_sha256 != document.get("template_snapshot_sha256"):
                errors.append(
                    f"datadir tree SHA-256 mismatch: observed={tree_sha256} "
                    f"expected={document.get('template_snapshot_sha256')}"
                )
        except (OSError, ValueError) as exc:
            errors.append(f"datadir traversal failed: {exc}")

    if root.is_dir() and not root.is_symlink():
        writable = _writable_entries(root)
        if writable:
            preview = writable[:20]
            errors.append(f"template contains writable paths: {preview}")

    return {
        "schema_version": "mysql_xp_template_copy_verification_v1",
        "status": "PASS" if not errors else "FAIL",
        "read_only": True,
        "template_root": str(root),
        "manifest_path": str(manifest),
        "manifest_sha256": manifest_sha256,
        "expected_manifest_sha256": expected_manifest_sha256,
        "sidecar_path": str(sidecar),
        "expected_sidecar_line": expected_sidecar.rstrip("\n"),
        "eight_table_rows": EXPECTED_ROWS,
        "total_rows": sum(EXPECTED_ROWS.values()),
        "database_size_bytes": tree_size,
        "template_snapshot_sha256": tree_sha256,
        "tree_inventory": tree_details,
        "errors": errors,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--expected-manifest-sha256",
        default=AUTHORITATIVE_MANIFEST_SHA256,
        help="Override only for testing or a separately frozen manifest version.",
    )
    args = parser.parse_args(argv)
    result = verify(
        args.template_root,
        manifest_path=args.manifest,
        expected_manifest_sha256=args.expected_manifest_sha256,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
