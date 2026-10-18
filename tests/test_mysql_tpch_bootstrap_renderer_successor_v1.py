from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import stat


ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "shell/build_mysql_tpch_bootstrap_renderer_successor_v1.py"
SPEC = importlib.util.spec_from_file_location("tpch_bootstrap_renderer_builder", BUILDER_PATH)
BUILDER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(BUILDER)


def test_builder_publishes_exact_read_only_controller() -> None:
    bundle = BUILDER.build()
    manifest = json.loads((bundle / "bundle_manifest.json").read_text())
    unsigned = dict(manifest)
    unsigned.pop("bundle_id")
    assert hashlib.sha256(BUILDER.canonical(unsigned)).hexdigest() == bundle.name
    assert manifest["target_bundle_id"] == BUILDER.TARGET_ID
    assert manifest["target_manifest_sha256"] == BUILDER.TARGET_MANIFEST_SHA256
    assert manifest["formal_scope"] == "mysql_only"
    assert manifest["cross_engine_comparison_prohibited"] is True
    assert manifest["formal_launch_authorized"] is False
    assert manifest["accepted_round_authorized"] is False
    assert BUILDER.relative_files(bundle) == manifest["files"]
    for path in (bundle, *bundle.rglob("*")):
        assert not path.is_symlink()
        assert stat.S_IMODE(path.stat().st_mode) & 0o222 == 0


def test_controller_embeds_exact_ff313_target_closure() -> None:
    bundle = BUILDER.build()
    embedded = bundle / "target" / BUILDER.TARGET_ID
    target_manifest = json.loads((embedded / "bundle_manifest.json").read_text())
    assert target_manifest["bundle_id"] == BUILDER.TARGET_ID
    assert BUILDER.sha_file(embedded / "bundle_manifest.json") == BUILDER.TARGET_MANIFEST_SHA256
    assert BUILDER.relative_files(embedded) == target_manifest["files"]
