from __future__ import annotations

from pathlib import Path

from scripts import (
    run_mysql_four_scene_algorithm_b_scene_independent_measurement_v4 as subject,
)


def test_measurement_drains_all_relational_workers_before_and_after(monkeypatch, tmp_path):
    workers = {
        "job_w1": {}, "job_w2": {}, "tpch_w1": {}, "tpch_w2": {},
        "xp_cpu_w1": {}, "xp_cpu_w2": {},
    }
    assignments = {"job_w1": {"params": {}}, "tpch_w1": {"params": {}}}
    drains: list[list[str]] = []
    artifacts: list[Path] = []
    stages: list[str] = []

    def fake_drain(_workers, worker_ids):
        drains.append(list(worker_ids))
        return {
            "status": "PASS",
            "all_workers_zero_sessions_after": True,
            "all_tpch_revenue0_views_absent_after": True,
            "workers": [],
        }

    def fake_atomic(path, _document):
        artifacts.append(Path(path))

    def fake_stage(_path, stage):
        stages.append(stage)

    def fake_v3_measure(
        _freeze, selected_workers, _assignments, _output, _reset_token,
        *, state_path=None, stage_prefix="FORMAL_POINT",
    ):
        # Exercise the same v3 hook points used by the wrapper.
        subject.v3.session_drain.drain_workers(
            selected_workers, ["job_w1", "tpch_w1"],
        )
        subject.v3.alg.set_runtime_stage(
            state_path, f"{stage_prefix}_PLAN_CAPTURE",
        )
        return {"job_w1": {}}

    monkeypatch.setattr(subject.v3.session_drain, "drain_workers", fake_drain)
    monkeypatch.setattr(subject.v3.alg, "atomic", fake_atomic)
    monkeypatch.setattr(subject.v3.alg, "set_runtime_stage", fake_stage)
    monkeypatch.setattr(subject.v3, "measure_batch", fake_v3_measure)

    result = subject.measure_batch(
        {}, workers, assignments, tmp_path, "token",
        state_path=tmp_path / "state.json", stage_prefix="OUTER_007_RANK_1",
    )
    expected = ["job_w1", "job_w2", "tpch_w1", "tpch_w2"]
    assert drains == [expected, expected]
    assert artifacts == [tmp_path / "post_measure_benchmark_session_drain.json"]
    assert stages == [
        "OUTER_007_RANK_1_POST_MEASURE_SESSION_DRAIN",
        "OUTER_007_RANK_1_PLAN_CAPTURE",
    ]
    assert result == {"job_w1": {}}


def test_xp_only_measurement_records_valid_empty_relational_drains(
    monkeypatch, tmp_path,
):
    workers = {"xp_cpu_w1": {}, "xp_cpu_w2": {}}
    assignments = {"xp_cpu_w1": {"params": {}}}
    unexpected_drains: list[list[str]] = []
    artifacts: dict[Path, dict] = {}
    stages: list[str] = []

    def fake_drain(_workers, worker_ids):
        unexpected_drains.append(list(worker_ids))
        raise AssertionError("XP-only execution must not call relational drain")

    def fake_atomic(path, document):
        artifacts[Path(path)] = dict(document)

    def fake_stage(_path, stage):
        stages.append(stage)

    def fake_v3_measure(
        _freeze, selected_workers, _assignments, _output, _reset_token,
        *, state_path=None, stage_prefix="FORMAL_POINT",
    ):
        pre = subject.v3.session_drain.drain_workers(selected_workers, [])
        assert pre["status"] == "PASS"
        assert pre["relational_workers_present"] is False
        assert pre["workers"] == []
        subject.v3.alg.set_runtime_stage(
            state_path, f"{stage_prefix}_PLAN_CAPTURE",
        )
        return {"xp_cpu_w1": {}}

    monkeypatch.setattr(subject.v3.session_drain, "drain_workers", fake_drain)
    monkeypatch.setattr(subject.v3.alg, "atomic", fake_atomic)
    monkeypatch.setattr(subject.v3.alg, "set_runtime_stage", fake_stage)
    monkeypatch.setattr(subject.v3, "measure_batch", fake_v3_measure)

    result = subject.measure_batch(
        {}, workers, assignments, tmp_path, "token",
        state_path=tmp_path / "state.json", stage_prefix="OUTER_001_RANK_1",
    )
    assert unexpected_drains == []
    post_path = tmp_path / "post_measure_benchmark_session_drain.json"
    assert artifacts[post_path]["status"] == "PASS"
    assert artifacts[post_path]["relational_workers_present"] is False
    assert stages == [
        "OUTER_001_RANK_1_POST_MEASURE_SESSION_DRAIN",
        "OUTER_001_RANK_1_PLAN_CAPTURE",
    ]
    assert result == {"xp_cpu_w1": {}}
