#!/usr/bin/env python3
"""Regression tests for the formal PostgreSQL DB-QPS source contract."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parameter_optimizer import _resolve_formal_db_qps  # noqa: E402


def test_fresh_point_uses_benchmark_db_qps() -> None:
    value, source = _resolve_formal_db_qps(
        source="formal_benchmark_db_qps",
        benchmark_metrics={"db_qps": 123.5, "xp_qps": 3.0},
        db_view_monitor={"db_qps": 999.0},
    )
    assert value == 123.5
    assert source == "formal_benchmark_db_qps"


def test_same_endpoint_uses_db_view_monitor() -> None:
    value, source = _resolve_formal_db_qps(
        source="db_view_monitor",
        benchmark_metrics={"xp_qps": 3.0},
        db_view_monitor={"db_qps": 88.25},
    )
    assert value == 88.25
    assert source == "formal_db_view_monitor"


def test_workload_qps_fallback_is_forbidden() -> None:
    try:
        _resolve_formal_db_qps(
            source="formal_fixed_horizon_workload_qps",
            benchmark_metrics={"xp_qps": 3.0},
            db_view_monitor={"db_qps": 88.25},
        )
    except RuntimeError as exc:
        assert "workload QPS/TPS fallback is forbidden" in str(exc)
    else:
        raise AssertionError("workload-QPS source must fail closed")


def test_missing_selected_db_qps_fails_closed() -> None:
    try:
        _resolve_formal_db_qps(
            source="formal_benchmark_db_qps",
            benchmark_metrics={"xp_qps": 3.0},
            db_view_monitor={"db_qps": 88.25},
        )
    except RuntimeError as exc:
        assert "missing positive finite DB QPS" in str(exc)
    else:
        raise AssertionError("missing benchmark.db_qps must fail closed")


def test_latency_metrics_are_optional_unless_explicitly_required() -> None:
    source = (ROOT / "parameter_optimizer.py").read_text(encoding="utf-8")
    expected = (
        "required=formal_pg_resource_enabled and "
        "final_feedback_require_latency_metrics"
    )
    assert source.count(expected) == 2


def test_startup_baseline_uses_explicit_db_qps_resolver() -> None:
    source = (ROOT / "parameter_optimizer.py").read_text(encoding="utf-8")
    # Character offset 10000 can still land on the unrelated online-update
    # helper near line 1100 as the module grows. The startup baseline block is
    # the final assignment with this spelling.
    start = source.rindex("baseline_qps = _to_float_or_none(")
    end = source.index("baseline_tps = _to_float_or_none(", start)
    block = source[start:end]
    compact = "".join(block.split())
    assert "_resolve_formal_db_qps(" in block
    assert 'or baseline_qps' not in block
    assert '"formal_fixed_horizon_workload_qps"' not in block
    assert (
        'baseline_db_view_summary["db_qps_source"]=formal_baseline_qps_source'
        in compact
    )


if __name__ == "__main__":
    test_fresh_point_uses_benchmark_db_qps()
    test_same_endpoint_uses_db_view_monitor()
    test_workload_qps_fallback_is_forbidden()
    test_missing_selected_db_qps_fails_closed()
    test_latency_metrics_are_optional_unless_explicitly_required()
    test_startup_baseline_uses_explicit_db_qps_resolver()
    print("formal DB-QPS source contract tests passed", flush=True)
