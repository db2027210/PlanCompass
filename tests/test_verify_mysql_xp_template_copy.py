from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from shell.verify_mysql_xp_template_copy import EXPECTED_ROWS, tree_size_and_sha256, verify


@pytest.fixture(autouse=True)
def _restore_fixture_permissions(tmp_path: Path):
    yield
    for current, directories, files in os.walk(tmp_path):
        Path(current).chmod(0o700)
        for name in directories:
            (Path(current) / name).chmod(0o700)
        for name in files:
            path = Path(current) / name
            if not path.is_symlink():
                path.chmod(0o600)


def _seal(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            continue
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def _fixture(tmp_path: Path) -> tuple[Path, Path, str]:
    root = tmp_path / "template_v2"
    schema = root / "datadir" / "hybench_sf10x"
    schema.mkdir(parents=True)
    for index, table in enumerate(EXPECTED_ROWS):
        (schema / f"{table}.ibd").write_bytes(bytes([index]) * (index + 1))
    (root / "datadir" / "mysql.sock").symlink_to("/var/run/mysqld/mysqld.sock")
    size, tree_sha, _ = tree_size_and_sha256(root / "datadir")
    manifest = {
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
        "template_snapshot_sha256": tree_sha,
        "database_size_bytes": size,
        "clean_database_shutdown_before_snapshot": True,
        "row_counts": EXPECTED_ROWS,
        "row_count_by_table": EXPECTED_ROWS,
        "load_audit": {
            table: {
                "loaded_rows": rows,
                "final_count_rows": rows,
                "warning_count": 0,
                "closure_passed": True,
            }
            for table, rows in EXPECTED_ROWS.items()
        },
    }
    manifest_path = root / "database_manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    (root / "database_manifest.json.sha256").write_text(
        f"{manifest_sha}  database_manifest.json\n", encoding="ascii"
    )
    _seal(root)
    return root, manifest_path, manifest_sha


def test_complete_sealed_copy_passes_and_is_reentrant(tmp_path: Path) -> None:
    root, _, manifest_sha = _fixture(tmp_path)
    first = verify(root, expected_manifest_sha256=manifest_sha)
    second = verify(root, expected_manifest_sha256=manifest_sha)
    assert first == second
    assert first["status"] == "PASS"
    assert first["total_rows"] == 87_060_000
    assert set(first["tree_inventory"]["table_ibd_sha256"]) == {
        f"hybench_sf10x/{table}.ibd" for table in EXPECTED_ROWS
    }


def test_missing_sidecar_fails_closed(tmp_path: Path) -> None:
    root, _, manifest_sha = _fixture(tmp_path)
    sidecar = root / "database_manifest.json.sha256"
    root.chmod(0o755)
    sidecar.unlink()
    root.chmod(0o555)
    result = verify(root, expected_manifest_sha256=manifest_sha)
    assert result["status"] == "FAIL"
    assert any("sidecar" in error for error in result["errors"])


def test_partial_or_tampered_copy_fails_without_writing(tmp_path: Path) -> None:
    root, _, manifest_sha = _fixture(tmp_path)
    table = root / "datadir" / "hybench_sf10x" / "transfer.ibd"
    table.chmod(0o644)
    before = table.read_bytes()
    table.write_bytes(before + b"tampered")
    result = verify(root, expected_manifest_sha256=manifest_sha)
    assert result["status"] == "FAIL"
    assert table.read_bytes() == before + b"tampered"
    assert any("tree SHA-256 mismatch" in error for error in result["errors"])
    assert any("writable paths" in error for error in result["errors"])


def test_row_contract_mismatch_fails(tmp_path: Path) -> None:
    root, manifest_path, _ = _fixture(tmp_path)
    root.chmod(0o755)
    manifest_path.chmod(0o644)
    document = json.loads(manifest_path.read_text())
    document["row_counts"]["transfer"] -= 1
    manifest_path.write_text(json.dumps(document, sort_keys=True) + "\n")
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    sidecar = root / "database_manifest.json.sha256"
    sidecar.chmod(0o644)
    sidecar.write_text(f"{manifest_sha}  database_manifest.json\n", encoding="ascii")
    _seal(root)
    result = verify(root, expected_manifest_sha256=manifest_sha)
    assert result["status"] == "FAIL"
    assert any("row_counts" in error for error in result["errors"])
