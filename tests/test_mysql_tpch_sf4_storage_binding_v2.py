from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FREEZE = ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v2"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_sf4_storage_successor_is_bound_and_fail_closed() -> None:
    manifest = json.loads((FREEZE / "manifest.json").read_text(encoding="utf-8"))
    binding_path = FREEZE / manifest["sf4_storage_binding"]["path"]
    binding = json.loads(binding_path.read_text(encoding="utf-8"))

    assert manifest["formal_scope"] == "mysql_only"
    assert manifest["active_scenes"] == ["job", "tpch", "hybench_xp"]
    assert manifest["retired_scenes"] == ["tpcc", "hybench_xp_io"]
    assert manifest["formal_launch_authorized"] is False
    assert manifest["launch_performed"] is False
    assert manifest["sf4_storage_binding"]["sha256"] == _sha(binding_path)
    assert binding["formal_scope"] == "mysql_only"
    assert binding["cross_engine_comparison_prohibited"] is True
    assert binding["predecessor_tpch_sf10_results_prohibited"] is True
    assert binding["storage_manifest"]["scale_factor"] == 4
    assert binding["storage_manifest"]["database"] == "tpch"
    assert binding["storage_manifest"]["template_bytes"] == 11035754516
    assert binding["storage_manifest"]["row_counts"]["lineitem"] == 23996604
    assert binding["storage_manifest"]["index_count"] == 31
    assert binding["fresh_sf4_r0_required"] is True
    assert binding["execution_authorized"] is False


def test_sf4_storage_manifest_bytes_match_the_sealed_template() -> None:
    binding = json.loads(
        (FREEZE / "sf4_storage_binding.json").read_text(encoding="utf-8")
    )
    storage = binding["storage_manifest"]
    storage_path = Path(storage["path"])
    payload = json.loads(storage_path.read_text(encoding="utf-8"))

    assert _sha(storage_path) == storage["sha256"]
    assert payload["status"] == "SEALED"
    assert payload["scale_factor"] == storage["scale_factor"]
    assert payload["database"] == storage["database"]
    assert payload["template_tree_sha256"] == storage["template_tree_sha256"]
    assert payload["template_bytes"] == storage["template_bytes"]
    assert payload["row_counts"] == storage["row_counts"]
    assert payload["index_count"] == storage["index_count"]
