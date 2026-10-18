#!/usr/bin/env python3
"""Exact, read-only identity verifier for the canonical MySQL XP template."""
from __future__ import print_function

import hashlib
import json
import os
import stat
from pathlib import Path

MANIFEST_SHA256 = "fd7c9918890fe6eea33f900da05a8d2825e204bc3f3cbf90505ddc43b98b52fa"
TREE_SHA256 = "c512bc478abb3e210ddafe3e5f69cf2058f8bfdbd7e90afa251b9b9f156c10ac"
DATABASE_SIZE_BYTES = 11033004101
REGULAR_FILE_COUNT = 173
DIRECTORY_COUNT = 6
SYMLINK_COUNT = 1
MYSQL_SOCK_TARGET = "/var/run/mysqld/mysqld.sock"
IMAGE_ID = "sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
ROWS = {
    "checking": 6000000, "checkingAccount": 3020000,
    "company": 20000, "customer": 3000000,
    "loanapps": 6000000, "loantrans": 6000000,
    "savingAccount": 3020000, "transfer": 60000000,
}
TABLE_SHA256 = {
    "hybench_sf10x/checking.ibd": "a206da6ff69e07ea07178844868790b3a2f443a48b30615908df97b0ae18a44b",
    "hybench_sf10x/checkingAccount.ibd": "eb923e73df5c6ac476adb53b0da5196bbcf8f2ab6d7babbb70c794167254b346",
    "hybench_sf10x/company.ibd": "509bff481f779a72157caf4925323a0587132319c9bd2cbe7a9f7d80a5b394b6",
    "hybench_sf10x/customer.ibd": "7a5490d398aef545ba3b79a727f02af82df3372df37101cea193a42f41caef55",
    "hybench_sf10x/loanapps.ibd": "cd5e48ad7befbb0fed0c6df3fb984fad0242136e94238044378128d3c6f78237",
    "hybench_sf10x/loantrans.ibd": "2435b5b146f5b7d0f03942fff58725ef4372152fb77649c8bf8ca2fe574e6d7e",
    "hybench_sf10x/savingAccount.ibd": "dda7343135ab0f642e15d43f49bf1926886ae9e18dadcd78edca7e8483060422",
    "hybench_sf10x/transfer.ibd": "8ad64a73d72751f79c9c4f27a6ffb7f8f26b1641aaf3d9ba3217588ac43181b6",
}

class TemplateError(RuntimeError):
    pass

def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

def expected_identity():
    return {
        "manifest_sha256": MANIFEST_SHA256,
        "tree_sha256": TREE_SHA256,
        "database_size_bytes": DATABASE_SIZE_BYTES,
        "regular_file_count": REGULAR_FILE_COUNT,
        "directory_count": DIRECTORY_COUNT,
        "symlink_count": SYMLINK_COUNT,
        "table_ibd_sha256": dict(TABLE_SHA256),
    }

def scan_datadir(root):
    root = Path(root)
    digest = hashlib.sha256(); total = 0
    counts = {"regular_file_count": 0, "directory_count": 0, "symlink_count": 0}
    tables = {}; symlinks = []
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        relative_text = path.relative_to(root).as_posix(); relative = relative_text.encode("utf-8")
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            target = os.readlink(str(path)); digest.update(b"L\0" + relative + b"\0" + target.encode("utf-8") + b"\0")
            counts["symlink_count"] += 1; symlinks.append({"path": relative_text, "target": target}); continue
        if stat.S_ISDIR(mode):
            digest.update(b"D\0" + relative + b"\0"); counts["directory_count"] += 1; continue
        if not stat.S_ISREG(mode): raise TemplateError("unsupported datadir entry: " + relative_text)
        size = path.stat().st_size; total += size; counts["regular_file_count"] += 1
        digest.update(b"F\0" + relative + b"\0" + str(size).encode("ascii") + b"\0")
        table_digest = hashlib.sha256() if relative_text in TABLE_SHA256 else None
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                digest.update(block)
                if table_digest is not None: table_digest.update(block)
        if table_digest is not None: tables[relative_text] = table_digest.hexdigest()
    result = dict(counts); result.update({"database_size_bytes": total, "tree_sha256": digest.hexdigest(), "table_ibd_sha256": tables, "symlinks": symlinks})
    return result

def verify_template(template_root):
    root = Path(template_root).absolute(); manifest = root / "database_manifest.json"; sidecar = root / "database_manifest.json.sha256"; datadir = root / "datadir"
    errors = []
    if not root.is_dir() or root.is_symlink(): errors.append("template_root")
    if not datadir.is_dir() or datadir.is_symlink(): errors.append("datadir")
    manifest_sha = sha256_file(manifest) if manifest.is_file() and not manifest.is_symlink() else None
    if manifest_sha != MANIFEST_SHA256: errors.append("manifest_sha256")
    try: document = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception: document = {}; errors.append("manifest_json")
    if not sidecar.is_file() or sidecar.is_symlink() or sidecar.read_text(encoding="ascii") != MANIFEST_SHA256 + "  database_manifest.json\n": errors.append("manifest_sidecar")
    exact = {"schema_version": 1, "freeze_id": "frozen_formal_config_20260721_v2", "global_campaign_id": "six_session_20260721_v2", "engine": "mysql", "template_id": "mysql80_hybench10x_v1", "database_version": "8.0.45", "dataset_version": "hybench-10x-v1", "scale_factor": "10x", "workload": "hybench", "database": "hybench_sf10x", "status": "complete", "template_snapshot_sha256_algorithm": "sha256_tree_v1", "template_snapshot_sha256": TREE_SHA256, "database_size_bytes": DATABASE_SIZE_BYTES, "clean_database_shutdown_before_snapshot": True}
    for key, value in exact.items():
        if document.get(key) != value: errors.append("manifest_field:" + key)
    if (document.get("image") or {}).get("image_id") != IMAGE_ID: errors.append("manifest_image")
    if document.get("row_counts") != ROWS or document.get("row_count_by_table") != ROWS: errors.append("manifest_rows")
    audit = document.get("load_audit") or {}
    for table, rows in ROWS.items():
        row = audit.get(table) or {}
        if row.get("loaded_rows") != rows or row.get("final_count_rows") != rows or row.get("warning_count") != 0 or row.get("closure_passed") is not True: errors.append("load_audit:" + table)
    scan = scan_datadir(datadir) if datadir.is_dir() and not datadir.is_symlink() else {}
    expected = expected_identity()
    for key in ("tree_sha256", "database_size_bytes", "regular_file_count", "directory_count", "symlink_count", "table_ibd_sha256"):
        if scan.get(key) != expected.get(key): errors.append("datadir:" + key)
    if scan.get("symlinks") != [{"path": "mysql.sock", "target": MYSQL_SOCK_TARGET}]: errors.append("datadir:symlink_identity")
    writable = []
    if root.is_dir() and not root.is_symlink():
        for path in [root] + list(root.rglob("*")):
            if not path.is_symlink() and path.lstat().st_mode & 0o222: writable.append("." if path == root else path.relative_to(root).as_posix())
    if writable: errors.append("writable_entries")
    return {"schema_version": "mysql_xp_canonical_template_identity_v2", "status": "PASS_EXACT_CANONICAL_TEMPLATE" if not errors else "FAIL", "template_root": str(root), "manifest_sha256": manifest_sha, "tree_sha256": scan.get("tree_sha256"), "database_size_bytes": scan.get("database_size_bytes"), "regular_file_count": scan.get("regular_file_count"), "directory_count": scan.get("directory_count"), "symlink_count": scan.get("symlink_count"), "symlinks": scan.get("symlinks"), "table_ibd_sha256": scan.get("table_ibd_sha256"), "row_counts": document.get("row_counts"), "total_rows": sum(ROWS.values()), "read_only": not writable, "errors": errors, "formal_launch_authorized": False, "accepted_round_authorized": False, "accepted_rounds_credit": 0}

def require_template(template_root):
    result = verify_template(template_root)
    if result["status"] != "PASS_EXACT_CANONICAL_TEMPLATE": raise TemplateError("canonical template differs: " + ",".join(result["errors"]))
    return result
