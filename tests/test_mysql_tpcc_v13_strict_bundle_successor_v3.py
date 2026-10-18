from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "shell/build_mysql_tpcc_v13_dedicated_daemon_successor_v3.py"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_builder_is_idempotent_and_strictly_closed():
    builder = load(BUILDER, "tpcc_v13_raw_all_subset_builder")
    first = builder.build()
    second = builder.build()
    assert first["bundle_id"] == second["bundle_id"]
    assert first["manifest_sha256"] == second["manifest_sha256"]
    verification = first["strict_verification"]
    assert verification["extra_files"] == 0
    assert verification["generated_bytecode_files"] == 0
    assert verification["formal_launch_authorized"] is False
    assert verification["accepted_round_authorized"] is False
    manifest = __import__("json").loads(
        (Path(first["bundle"]) / "bundle_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["algorithm_c_contract"] == builder.ALGORITHM_C_CONTRACT
    assert manifest["invalidates_algorithm_bundle_id"] == (
        builder.INVALIDATED_ALGORITHM_BUNDLE_ID
    )
    assert all("/.codex_backup_" not in path for path in manifest["files"])


def test_strict_verifier_rejects_manifest_external_bytecode(tmp_path):
    builder = load(BUILDER, "tpcc_v13_raw_builder_for_pollution")
    result = builder.build()
    source = Path(result["bundle"])
    polluted = tmp_path / source.name
    import shutil

    shutil.copytree(source, polluted)
    cache = polluted / "qualification/__pycache__"
    cache.mkdir()
    (cache / "polluted.pyc").write_bytes(b"not sealed")
    verifier = builder.verifier_module()
    with pytest.raises(verifier.VerificationError, match="strict file closure"):
        verifier.verify(polluted)
