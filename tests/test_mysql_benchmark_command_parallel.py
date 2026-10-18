from pathlib import Path
import hashlib
import json

import pytest

from mysql_benchmark_command import discover_job_query_files, run_job_tpch_parallel
from mysql_workload_adapter import MySQLEndpoint, build_job_manifest


def test_job_tpch_start_in_parallel_and_require_complete_coverage(tmp_path: Path) -> None:
    calls = []

    query_dir = tmp_path / "queries"
    query_dir.mkdir()
    names = [f"{group}{suffix}.sql" for group in range(1, 34)
             for suffix in "abcd"][:113]
    paths = []
    frozen_rows = []
    import hashlib, json
    for index, name in enumerate(names):
        path = query_dir / name
        path.write_text(f"SELECT {index};\n", encoding="utf-8")
        paths.append(path)
        frozen_rows.append({"name": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    job_manifest = build_job_manifest(paths, tmp_path / "manifest")
    lanes = [[9], [22, 16, 12, 8, 15], [17, 6, 20, 4, 3],
             [19, 18, 13, 10, 21], [2, 11, 14, 5, 7, 1]]
    freeze = tmp_path / "freeze.json"
    freeze.write_text(json.dumps({
        "formal_scope": "mysql_only",
        "active_benchmarks": ["job", "tpch", "hybench_xp"],
        "job": {"required_count": 113, "queries": frozen_rows},
        "tpch": {"lanes": lanes},
    }), encoding="utf-8")

    def job_runner(*args, **kwargs):
        calls.append(("job", kwargs))
        return {
            "metrics": {"qps": 1.0, "latency_p95_ms": 2.0,
                        "latency_p99_ms": 3.0, "explain_samples": []},
            "query_coverage": {"coverage_complete": True, "missing_queries": [],
                               "completed_queries": names},
        }

    def tpch_runner(*args, **kwargs):
        calls.append(("tpch", kwargs))
        completed = [f"Q{query_id}" for query_id in kwargs["query_order"]]
        lane = kwargs["query_order"][0]
        return {
            "metrics": {"qps": float(lane), "latency_p95_ms": float(lane * 10),
                        "latency_p99_ms": float(lane * 20), "explain_samples": []},
            "query_coverage": {
                "coverage_complete": True, "missing_queries": [],
                "completed_queries": completed, "unexpected_transaction_names": [],
            },
        }

    endpoint = MySQLEndpoint("127.0.0.1", 3306, "unused", "root")
    result = run_job_tpch_parallel(
        job_manifest=job_manifest, job_endpoint=endpoint,
        tpch_endpoint=endpoint, output_dir=tmp_path / "output",
        benchbase_jar=tmp_path / "benchbase.jar", clients=16,
        tpch_clients=5, scale_factor=10, timeout_s=900, formal_scope="mysql_only",
        workload_freeze=freeze,
        job_runner=job_runner, tpch_runner=tpch_runner,
    )
    assert result["execution"] == "parallel"
    assert result["all_ap_sql_completed"] is True
    assert result["cross_engine_comparison_prohibited"] is True
    assert result["formal_launch_authorized"] is False
    assert result["formal_measurement_valid"] is True
    assert {name for name, _ in calls} == {"job", "tpch"}
    tpch_calls = [kwargs for name, kwargs in calls if name == "tpch"]
    assert len(tpch_calls) == 5
    assert all(kwargs["terminals"] == 1 for kwargs in tpch_calls)
    assert all(kwargs["complete_query_set"] is True for kwargs in tpch_calls)
    assert set().union(*(set(kwargs["query_order"]) for kwargs in tpch_calls)) == set(range(1, 23))
    job_kwargs = next(kwargs for name, kwargs in calls if name == "job")
    assert len(job_kwargs["estimated_duration_s"]) == 113
    assert job_kwargs["heavy_query_threshold_s"] == 30.0
    assert job_kwargs["max_concurrent_heavy_queries"] == 4
    assert job_kwargs["capture_explain"] is False
    assert result["scheduling"]["tpch_effective_concurrency"] == 5
    assert result["scheduling"]["tpch_historical_median_theoretical_makespan_s"] == 572.9
    assert result["workloads"]["tpch"]["metrics"]["qps"] > 0
    assert result["workloads"]["tpch"]["metrics"]["latency_p95_ms"] == 220.0
    assert result["workloads"]["tpch"]["metrics"]["latency_p99_ms"] == 440.0


def test_job_schedule_successor_uses_dedicated_staggered_heavy_pool(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    paths = discover_job_query_files(
        Path("/home/anonymous/dataset/pg_data/join-order-benchmark")
    )
    names = [path.name for path in paths]
    manifest = build_job_manifest(paths, tmp_path / "manifest")
    calls = []

    def job_runner(*args, **kwargs):
        calls.append(("job", kwargs))
        return {
            "metrics": {"qps": 1.0, "latency_p95_ms": 2.0,
                        "latency_p99_ms": 3.0, "explain_samples": []},
            "query_coverage": {"coverage_complete": True, "missing_queries": [],
                               "completed_queries": names},
        }

    def tpch_runner(*args, **kwargs):
        completed = [f"Q{query_id}" for query_id in kwargs["query_order"]]
        return {
            "metrics": {"qps": 1.0, "latency_p95_ms": 2.0,
                        "latency_p99_ms": 3.0, "explain_samples": []},
            "query_coverage": {
                "coverage_complete": True, "missing_queries": [],
                "completed_queries": completed, "unexpected_transaction_names": [],
            },
        }

    endpoint = MySQLEndpoint("127.0.0.1", 3306, "unused", "root")
    result = run_job_tpch_parallel(
        job_manifest=manifest, job_endpoint=endpoint, tpch_endpoint=endpoint,
        output_dir=tmp_path / "output", benchbase_jar=tmp_path / "benchbase.jar",
        clients=16, tpch_clients=5, scale_factor=4, timeout_s=900,
        formal_scope="mysql_only",
        workload_freeze=(
            root / "config/formal/frozen_mysql_four_scene_job_schedule_20260810_v3"
            / "workload_successor.json"
        ),
        job_runner=job_runner, tpch_runner=tpch_runner,
    )
    job_kwargs = next(kwargs for name, kwargs in calls if name == "job")
    assert job_kwargs["dedicated_heavy_pool"] is True
    assert job_kwargs["heavy_start_stagger_s"] == 1.0
    assert job_kwargs["heavy_query_order"] == "longest_first"
    assert result["scheduling"]["policy"] == (
        "dedicated_longest_first_heavy_pool_bounded_start_stagger"
    )
    binding = result["scheduling"]["workload_freeze_binding"]
    assert "job_schedule_predecessor" in binding
    assert "predecessor" in binding


def test_job_discovery_selects_workload_sql_and_excludes_helpers(tmp_path: Path) -> None:
    for name in ("1a.sql", "1b.sql", "33c.sql", "schema.sql", "fkindexes.sql", "run_all.sql"):
        (tmp_path / name).write_text("SELECT 1;", encoding="utf-8")
    assert [path.name for path in discover_job_query_files(tmp_path)] == [
        "1a.sql", "1b.sql", "33c.sql"
    ]


def _frozen_inputs(tmp_path: Path):
    query_dir = tmp_path / "queries"
    query_dir.mkdir()
    names = [f"{group}{suffix}.sql" for group in range(1, 34)
             for suffix in "abcd"][:113]
    paths = []
    rows = []
    for index, name in enumerate(names):
        path = query_dir / name
        path.write_text(f"SELECT {index};\n", encoding="utf-8")
        paths.append(path)
        rows.append({"name": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest = build_job_manifest(paths, tmp_path / "manifest")
    lanes = [[9], [22, 16, 12, 8, 15], [17, 6, 20, 4, 3],
             [19, 18, 13, 10, 21], [2, 11, 14, 5, 7, 1]]
    freeze = tmp_path / "freeze.json"
    freeze.write_text(json.dumps({
        "formal_scope": "mysql_only",
        "active_benchmarks": ["job", "tpch", "hybench_xp"],
        "job": {"required_count": 113, "queries": rows},
        "tpch": {"lanes": lanes},
    }), encoding="utf-8")
    return names, paths, manifest, freeze


def test_job_tpch_rejects_non_frozen_scheduler_or_wall_limit(tmp_path: Path) -> None:
    _, _, manifest, freeze = _frozen_inputs(tmp_path)
    endpoint = MySQLEndpoint("127.0.0.1", 3306, "unused", "root")
    common = dict(
        job_manifest=manifest, job_endpoint=endpoint, tpch_endpoint=endpoint,
        output_dir=tmp_path / "output", benchbase_jar=tmp_path / "benchbase.jar",
        clients=16, tpch_clients=5, scale_factor=10, timeout_s=900,
        formal_scope="mysql_only", workload_freeze=freeze,
    )
    with pytest.raises(ValueError, match="16/5/4"):
        run_job_tpch_parallel(**(common | {"clients": 15}))
    with pytest.raises(ValueError, match="exactly 900"):
        run_job_tpch_parallel(**(common | {"target_wall_time_s": 899}))


def test_job_tpch_rejects_query_byte_drift_before_execution(tmp_path: Path) -> None:
    _, paths, manifest, freeze = _frozen_inputs(tmp_path)
    paths[0].write_text("SELECT 999;\n", encoding="utf-8")
    endpoint = MySQLEndpoint("127.0.0.1", 3306, "unused", "root")
    with pytest.raises(ValueError, match="exact frozen 113-query byte set"):
        run_job_tpch_parallel(
            job_manifest=manifest, job_endpoint=endpoint, tpch_endpoint=endpoint,
            output_dir=tmp_path / "output", benchbase_jar=tmp_path / "benchbase.jar",
            clients=16, tpch_clients=5, scale_factor=10, timeout_s=900,
            formal_scope="mysql_only", workload_freeze=freeze,
        )


def test_job_tpch_rejects_per_lane_coverage_drift(tmp_path: Path) -> None:
    names, _, manifest, freeze = _frozen_inputs(tmp_path)
    endpoint = MySQLEndpoint("127.0.0.1", 3306, "unused", "root")

    def job_runner(*args, **kwargs):
        return {"query_coverage": {"coverage_complete": True, "missing_queries": [],
                                    "completed_queries": names}}

    def tpch_runner(*args, **kwargs):
        completed = [f"Q{query_id}" for query_id in kwargs["query_order"]]
        if kwargs["query_order"] == [9]:
            completed = ["Q8"]
        return {"query_coverage": {"coverage_complete": True, "missing_queries": [],
                                    "completed_queries": completed,
                                    "unexpected_transaction_names": []}}

    with pytest.raises(RuntimeError, match="lane coverage differs"):
        run_job_tpch_parallel(
            job_manifest=manifest, job_endpoint=endpoint, tpch_endpoint=endpoint,
            output_dir=tmp_path / "output", benchbase_jar=tmp_path / "benchbase.jar",
            clients=16, tpch_clients=5, scale_factor=10, timeout_s=900,
            formal_scope="mysql_only", workload_freeze=freeze,
            job_runner=job_runner, tpch_runner=tpch_runner,
        )
