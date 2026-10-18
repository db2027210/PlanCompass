from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "shell/runtime_tree_content_identity_v3.py"
COLLECTOR_PATH = ROOT / "shell/collect_remote_runtime_tree_manifest.py"


def module_from(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TREE = module_from(MODULE_PATH, "runtime_tree_content_identity_v3")


def fixture(root: Path) -> list[dict]:
    (root / "bin").mkdir(parents=True)
    (root / "empty").mkdir()
    (root / "empty").chmod(0o750)
    program = root / "bin/runtime"
    program.write_bytes(b"runtime\n")
    program.chmod(0o755)
    (root / "current").symlink_to("bin/runtime")
    return TREE.inventory_tree(root)


def test_v3_sources_parse_with_python36_grammar() -> None:
    for path in (MODULE_PATH, COLLECTOR_PATH):
        ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
            feature_version=(3, 6),
        )


def test_empty_directory_and_mode_are_in_portable_identity(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    entries = fixture(source)
    manifest = TREE.portable_manifest(
        entries, {"source_root": "/excluded/from/hash"}
    )
    empty = next(
        row
        for row in manifest["content_identity"]["entries"]
        if row["path"] == "empty"
    )
    assert empty == {"path": "empty", "type": "directory", "mode": 0o750}
    assert manifest["content_identity"]["directory_count"] == 2
    assert TREE.verify_tree_manifest(source, manifest)[
        "directory_path_and_mode_verified"
    ] is True

    relocated = tmp_path / "relocated"
    fixture(relocated)
    assert (
        TREE.verify_tree_manifest(relocated, manifest)["tree_content_sha256"]
        == manifest["tree_content_sha256"]
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "extra_empty_directory",
        "missing_empty_directory",
        "directory_mode",
        "file_mode",
        "file_content",
        "symlink_target",
    ),
)
def test_every_material_tree_difference_fails_closed(
    tmp_path: Path, mutation: str,
) -> None:
    source = tmp_path / "source"
    manifest = TREE.portable_manifest(fixture(source), {"source": "test"})
    if mutation == "extra_empty_directory":
        (source / "additional-empty").mkdir()
    elif mutation == "missing_empty_directory":
        (source / "empty").rmdir()
    elif mutation == "directory_mode":
        (source / "empty").chmod(0o755)
    elif mutation == "file_mode":
        (source / "bin/runtime").chmod(0o700)
    elif mutation == "file_content":
        (source / "bin/runtime").write_bytes(b"changed\n")
    else:
        (source / "current").unlink()
        (source / "current").symlink_to("wrong")
    with pytest.raises(TREE.RuntimeTreeError, match="live runtime tree differs"):
        TREE.verify_tree_manifest(source, manifest)


def test_symlink_mode_is_not_an_authority_field(tmp_path: Path) -> None:
    entries = fixture(tmp_path / "source")
    link = next(row for row in entries if row["type"] == "symlink")
    assert set(link) == {"path", "type", "target"}
    with pytest.raises(TREE.RuntimeTreeError, match="symlink metadata"):
        TREE.content_identity([dict(link, mode=0o777)])


def test_cli_dispatch_and_unknown_schema_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "source"
    manifest = TREE.portable_manifest(fixture(source), {"source": "test"})
    authority = tmp_path / "authority.json"
    authority.write_bytes(TREE.canonical(manifest))
    completed = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            "--authority-manifest",
            str(authority),
            "--runtime-root",
            str(source),
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["tree_content_sha256"] == (
        manifest["tree_content_sha256"]
    )
    authority.write_bytes(
        TREE.canonical({"schema_version": "unknown_authority"})
    )
    rejected = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            "--authority-manifest",
            str(authority),
            "--runtime-root",
            str(source),
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert rejected.returncode != 0
    assert "schema is unsupported" in rejected.stderr
