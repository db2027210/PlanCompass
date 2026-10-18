#!/usr/bin/env python3
"""Focused test: write_markdown must receive explicit capability/scenario.

Regression for NameError: name 'capability' is not defined inside write_markdown
when building diversity columns via candidate_diversity_record.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_rescore_module():
    path = ROOT / "shell" / "bonect_offline_rescore_scan.py"
    spec = importlib.util.spec_from_file_location("bonect_offline_rescore_scan", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CAPABILITY: Dict[str, float] = {"cpu": 11.0, "memory": 2.0, "io": 1600.0}
SCENARIO = "memory"

REPRESENTATIVE_TOP_ROW: Dict[str, Any] = {
    "estimated_resource": {"cpu": 8.22, "memory": 1.29, "io": 337.6},
    "resource": {"cpu": 8.22, "memory": 1.29, "io": 337.6},
    "utilization_pct": {"cpu": 74.75, "memory": 64.67, "io": 21.10},
    "estimated_db_qps_delta_pct": 112.87,
    "estimated_db_qps_error_bound_pct": 49.29,
    "estimated_db_qps_conservative_delta_pct": 63.58,
    "plan_delta_features": {
        "weighted_gather_count": 2.0,
        "weighted_gather_workers_planned": 4.0,
    },
    "params": {
        "seq_page_cost": 0.0001,
        "random_page_cost": 4.0,
        "cpu_tuple_cost": 0.000001,
        "cpu_index_tuple_cost": 0.000001,
        "cpu_operator_cost": 0.0025,
        "parallel_setup_cost": 10000.0,
        "parallel_tuple_cost": 0.000001,
    },
    "plan_hash": "test_hash_md_write",
    "replay_delta_tree": {"supported": True, "leaf_id": 3},
}

SUMMARY: Dict[str, Any] = {
    "candidate_count": 3,
    "elapsed_seconds": 0.012,
    "supported_candidate_count": 2,
    "predicted_conservative_goal_hit_count": 1,
    "model_leave_one_out_pairwise_inversion_rate": 0.14,
    "model_leave_one_out_pairwise_error_aware_10pp_rate": 0.097,
    "diversity_policy": {
        "max_per_pf_bin": 8,
        "max_per_resp_bin": 12,
        "max_per_qps_bin": 15,
        "max_per_lane_cluster": 20,
    },
}


def test_write_markdown_no_nameerror_emits_diversity_fields() -> None:
    mod = _load_rescore_module()
    with tempfile.TemporaryDirectory(prefix="bonect_rescore_md_") as tmp:
        out = Path(tmp) / "rescore_summary.md"
        # Must not raise NameError for capability / args.scenario
        mod.write_markdown(
            out,
            SUMMARY,
            [REPRESENTATIVE_TOP_ROW],
            capability=CAPABILITY,
            scenario=SCENARIO,
        )
        text = out.read_text(encoding="utf-8")
        assert "NameError" not in text
        assert out.is_file() and out.stat().st_size > 0
        assert "# Bonect Replay-Delta Offline Rescore" in text
        assert f"- scenario: `{SCENARIO}`" in text
        # Table header includes diversity columns
        assert "pf_bin" in text
        assert "resp_bin" in text
        assert "qps_bin" in text
        assert "lane" in text
        # Row data: utilization and QPS present
        assert "74.75" in text
        assert "64.67" in text
        assert "112.87" in text
        assert "63.58" in text
        # Diversity bins: fields themselves may contain '|' so do not cell-split.
        # Assert resource-response diversity (resp_bin) and other bins via known tokens.
        from bonect_robust_ranking import candidate_diversity_record

        expected = candidate_diversity_record(
            REPRESENTATIVE_TOP_ROW,
            capability=CAPABILITY,
            scenario=SCENARIO,
        )
        for key in ("pf_bin", "resp_bin", "qps_bin", "param_lane"):
            token = str(expected[key])
            assert token, f"empty expected {key}"
            # Markdown truncates long bins; match a stable prefix that fits the table.
            assert token[:20] in text, f"missing diversity field {key}={token!r} in markdown"
        # Resource diversity specifically: resp_bin encodes cpu/memory/io util buckets
        assert "cpu=" in expected["resp_bin"] and "memory=" in expected["resp_bin"]
        assert "cpu=" in text and "memory=" in text
        data_lines = [ln for ln in text.splitlines() if ln.startswith("| 1 |")]
        assert len(data_lines) == 1, data_lines
        print(
            f"write_markdown ok: pf_bin={expected['pf_bin']!r} "
            f"resp_bin={expected['resp_bin']!r} qps_bin={expected['qps_bin']!r} "
            f"lane={expected['param_lane']!r}",
            flush=True,
        )


def test_write_markdown_signature_requires_capability_scenario() -> None:
    """Guard: call site must pass explicit kwargs (no hidden globals)."""
    import inspect

    mod = _load_rescore_module()
    sig = inspect.signature(mod.write_markdown)
    params = sig.parameters
    assert "capability" in params, params
    assert "scenario" in params, params
    assert params["capability"].kind == inspect.Parameter.KEYWORD_ONLY
    assert params["scenario"].kind == inspect.Parameter.KEYWORD_ONLY
    print("write_markdown signature requires keyword-only capability/scenario", flush=True)


def main() -> int:
    test_write_markdown_signature_requires_capability_scenario()
    test_write_markdown_no_nameerror_emits_diversity_fields()
    print("bonect offline rescore write_markdown tests passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
