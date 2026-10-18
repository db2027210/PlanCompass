from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "shell/runtime_tree_content_identity_v2.py"
COLLECTOR_PATH = ROOT / "shell/collect_remote_runtime_tree_manifest.py"
PYTHON_AUTHORITY = (
    ROOT / "preflight/runtime_manifests_20260729/"
    "node04_python311-f14659a7a7507b4b5496535d2b34af9c93214e2769e3f44aef0076abcd2e3950.json"
)
JDK_AUTHORITY = (
    ROOT / "preflight/runtime_manifests_20260729/"
    "node04_jdk23-bee22f03ea1a53d83e8cf1bfe983526376924de5641fd89bc556d8094f069169.json"
)
XP_JDK_AUTHORITY = (
    ROOT / "artifacts/mysql_xp_distributed_successor_v1/"
    "a475a613e03fc8f8f700b7dc65bf8842566187a761c5021c5634e3003e7fc2b3/"
    "toolchain/jdk-23.0.2-tree-manifest.json"
)


def module_from(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TREE = module_from(MODULE_PATH, "runtime_tree_content_identity_v2")


def publication(root: str, entries: list[dict]) -> dict:
    identity = TREE.content_identity(entries)["identity"]
    inventory = {
        "schema_version": TREE.INVENTORY_SCHEMA,
        "root": root,
        "file_count": identity["file_count"],
        "symlink_count": identity["symlink_count"],
        "total_file_bytes": identity["total_file_bytes"],
        "entries": entries,
    }
    legacy = hashlib.sha256(
        (
            json.dumps(
                inventory, ensure_ascii=False, sort_keys=True, indent=2
            )
            + "\n"
        ).encode()
    ).hexdigest()
    return {
        "schema_version": TREE.PUBLICATION_SCHEMA,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "launch_authorized": False,
        "remote_mutation_performed": False,
        "host": "test@source",
        "remote_root": root,
        "tree_sha256": legacy,
        "inventory": inventory,
    }


def write_fixture(root: Path) -> list[dict]:
    root.mkdir()
    executable = root / "bin"
    executable.mkdir()
    program = executable / "runtime"
    program.write_bytes(b"runtime\n")
    program.chmod(0o755)
    (root / "current").symlink_to("bin/runtime")
    return [
        {
            "path": "bin/runtime",
            "type": "file",
            "size": 8,
            "mode": 0o755,
            "sha256": hashlib.sha256(b"runtime\n").hexdigest(),
        },
        {"path": "current", "type": "symlink", "target": "bin/runtime"},
    ]


def test_sources_parse_with_python36_grammar() -> None:
    for path in (MODULE_PATH, COLLECTOR_PATH):
        ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
            feature_version=(3, 6),
        )


def test_identity_is_path_independent_but_legacy_digest_is_not(
    tmp_path: Path,
) -> None:
    entries = write_fixture(tmp_path / "installed")
    first = publication("/source/one", entries)
    second = publication("/different/source", entries)
    first_result = TREE.validate_publication(first)
    second_result = TREE.validate_publication(second)
    assert (
        first_result["tree_content_sha256"]
        == second_result["tree_content_sha256"]
    )
    assert (
        first_result["legacy_location_bound_tree_sha256"]
        != second_result["legacy_location_bound_tree_sha256"]
    )
    readback = TREE.verify_tree(tmp_path / "installed", first)
    assert readback["status"] == "PASS_EXACT_PATH_INDEPENDENT_TREE_READBACK"
    assert readback["directory_entry_set_verified"] is True
    assert readback["file_sha256_size_mode_verified"] is True
    assert readback["symlink_target_verified"] is True


@pytest.mark.parametrize(
    "mutation", ["content", "mode", "link", "extra", "extra_directory"]
)
def test_live_verification_rejects_every_material_difference(
    tmp_path: Path, mutation: str
) -> None:
    installed = tmp_path / "installed"
    authority = publication("/source", write_fixture(installed))
    if mutation == "content":
        (installed / "bin/runtime").write_bytes(b"changed\n")
    elif mutation == "mode":
        (installed / "bin/runtime").chmod(0o700)
    elif mutation == "link":
        (installed / "current").unlink()
        (installed / "current").symlink_to("wrong")
    elif mutation == "extra":
        (installed / "extra").write_text("not authorized", encoding="utf-8")
    else:
        (installed / "empty-extra-directory").mkdir()
    with pytest.raises(TREE.RuntimeTreeError, match="live runtime"):
        TREE.verify_tree(installed, authority)


def test_unsafe_duplicate_and_special_authority_entries_fail_closed() -> None:
    digest = hashlib.sha256(b"x").hexdigest()
    base = {
        "path": "../escape",
        "type": "file",
        "size": 1,
        "mode": 0o644,
        "sha256": digest,
    }
    with pytest.raises(TREE.RuntimeTreeError, match="unsafe"):
        TREE.content_identity([base])
    duplicate = dict(base, path="file")
    with pytest.raises(TREE.RuntimeTreeError, match="duplicate"):
        TREE.content_identity([duplicate, duplicate])
    with pytest.raises(TREE.RuntimeTreeError, match="unknown"):
        TREE.content_identity([{"path": "fifo", "type": "fifo"}])


def test_existing_canonical_manifests_have_path_bound_legacy_ids() -> None:
    python = json.loads(PYTHON_AUTHORITY.read_text(encoding="utf-8"))
    jdk = json.loads(JDK_AUTHORITY.read_text(encoding="utf-8"))
    python_result = TREE.validate_publication(python)
    jdk_result = TREE.validate_publication(jdk)
    assert (
        python_result["legacy_location_bound_tree_sha256"]
        == "82530b69f9839cd08456e52b6631c68c2c425bd12103c8eb143e2684ced43923"
    )
    assert (
        jdk_result["legacy_location_bound_tree_sha256"]
        == "bb6559bd3cd16041c0b7f1fca9a584bf50baa42ec171441fd4073f35bbbadf19"
    )
    assert python_result["tree_content_sha256"] != (
        python_result["legacy_location_bound_tree_sha256"]
    )
    assert jdk_result["tree_content_sha256"] != (
        jdk_result["legacy_location_bound_tree_sha256"]
    )
    assert python_result["tree_content_sha256"] == (
        "2e05572da3d9b630a24621fcf15299930c0b35b178036a0892eae84bd61cfc8d"
    )
    assert jdk_result["tree_content_sha256"] == (
        "ae440b32700ee008ed83f53bad410170c43b9f6d309c21c28fc9c39437d7c141"
    )


def test_xp_legacy_tree_id_is_source_path_bound_and_mode_size_unbound() -> None:
    document = json.loads(XP_JDK_AUTHORITY.read_text(encoding="utf-8"))
    identity = dict(document)
    tree_id = identity.pop("tree_id")
    identity.pop("java_binary_sha256")
    identity.pop("release_sha256")
    assert TREE.sha_bytes(TREE.canonical(identity)) == tree_id
    changed_source = dict(identity, source_path="/copied/runtime")
    assert TREE.sha_bytes(TREE.canonical(changed_source)) != tree_id
    assert all(isinstance(value, str) for value in document["files"].values())
    assert "mode" not in next(iter(document["files"].values()))


def test_cli_dispatches_portable_manifest_and_fails_closed(
    tmp_path: Path,
) -> None:
    installed = tmp_path / "installed"
    entries = write_fixture(installed)
    manifest = TREE.portable_manifest(
        entries, {"source": "unit-test", "path_excluded": True}
    )
    authority = tmp_path / "portable.json"
    authority.write_bytes(TREE.canonical(manifest))
    completed = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            "--authority-manifest",
            str(authority),
            "--runtime-root",
            str(installed),
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["tree_content_sha256"] == manifest["tree_content_sha256"]
    assert result["status"] == "PASS_EXACT_PATH_INDEPENDENT_TREE_READBACK"

    bad = dict(manifest)
    bad["schema_version"] = "unknown_runtime_tree_authority"
    authority.write_bytes(TREE.canonical(bad))
    rejected = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            "--authority-manifest",
            str(authority),
            "--runtime-root",
            str(installed),
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert rejected.returncode != 0
    assert "schema is unsupported" in rejected.stderr
