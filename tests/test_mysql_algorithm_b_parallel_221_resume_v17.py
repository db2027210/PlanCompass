from __future__ import annotations

import pytest

from scripts import (
    run_mysql_four_scene_algorithm_b_independent_lane_failure_isolation_core_v17
    as decision_core,
)
from scripts import (
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v17
    as subject,
)
from scripts import (
    run_mysql_four_scene_algorithm_b_scene_independent_measurement_v1
    as measurement,
)


def test_v17_source_is_exact_outer5_predecision_failure():
    state = subject.validate_source()
    assert state["status"] == "FAILED_CLOSED"
    assert state["outer_attempt"] == 4
    assert state["error"] == subject.CARDINALITY_ERROR


def test_v17_does_not_reuse_any_outer5_batch():
    assert all(outer <= 4 for outer, _rank in subject.REUSED_BATCHES)
    assert sorted(key for key in subject.REUSED_BATCHES if key[0] == 4) == [
        (4, rank) for rank in range(1, 6)
    ]


def test_scene_candidate_accounting_is_not_global():
    invalid = [
        {"rank": rank, "affected_scenes": ["tpch_sf4_memory"]}
        for rank in range(1, 6)
    ]
    tpch_invalid, tpch_valid = decision_core.scene_candidate_accounting(
        "tpch_sf4_memory", invalid, [],
    )
    job_invalid, job_valid = decision_core.scene_candidate_accounting(
        "job_memory", invalid, [{"rank": rank} for rank in range(1, 6)],
    )
    assert len(tpch_invalid) == 5 and tpch_valid == 0
    assert job_invalid == [] and job_valid == 5


def test_scene_candidate_accounting_fails_only_its_own_mismatch():
    invalid = [{"rank": 1, "affected_scenes": ["tpch_sf4_memory"]}]
    with pytest.raises(RuntimeError, match="tpch_sf4_memory"):
        decision_core.scene_candidate_accounting(
            "tpch_sf4_memory", invalid, [],
        )


def test_outer4_reconstructs_global_legacy_candidate_cardinality():
    invalid = [{"rank": 2, "affected_scenes": ["tpch_sf4_memory"]}]
    rows = [{"rank": rank} for rank in [1, 3, 4, 5]]
    job_invalid, job_valid = decision_core.candidate_accounting_for_outer(
        4, "job_memory", invalid, rows,
    )
    assert job_invalid == invalid
    assert job_valid == 4


def test_outer5_uses_scene_local_candidate_cardinality():
    invalid = [{"rank": 2, "affected_scenes": ["tpch_sf4_memory"]}]
    rows = [{"rank": rank} for rank in range(1, 6)]
    job_invalid, job_valid = decision_core.candidate_accounting_for_outer(
        5, "job_memory", invalid, rows,
    )
    assert job_invalid == []
    assert job_valid == 5


def test_independent_stubs_are_complete_but_explicitly_discarded(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        '{"queries":[{"path":"/tmp/1a.sql"},{"path":"/tmp/2a.sql"}]}',
        encoding="utf-8",
    )
    job = measurement._fake_job_runner(manifest, None, tmp_path)
    tpch = measurement._fake_tpch_runner(
        "tpch", None, tmp_path, query_order=[9, 6],
    )
    assert job["query_coverage"]["completed_queries"] == ["1a.sql", "2a.sql"]
    assert tpch["query_coverage"]["completed_queries"] == ["Q9", "Q6"]
    assert job["discarded_validation_stub"] is True
    assert tpch["discarded_validation_stub"] is True
