from scripts import (
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v10 as subject,
)


def test_state_document_initializes_parallel_continuation_metadata(monkeypatch):
    monkeypatch.setattr(subject.alg, "sha", lambda _path: "0" * 64)
    document = subject.state_document_v10("parallel_221_test")
    assert document["technical_continuation"]["kind"].endswith("resume_v10")
    assert document["technical_continuation"]["v9_prestart_failure_started_workload"] is False
    assert document["formal_candidate_batches"] == [[1, 2], [3, 4], [5]]
    assert document["candidate_parallelism"] == 2


def test_v9_directed_parallel_invariants_still_hold():
    batches = subject.predecessor_221.validate_outer1_replay_source()
    assert sorted(batches) == [1, 2, 3, 4, 5]
    assert subject.predecessor_221._failure_ranks(
        {"task": "pair_2", "error": "timeout"},
        {"job_w1": 1, "tpch_w1": 1, "job_w2": 2, "tpch_w2": 2},
        (1, 2),
    ) == {2}
