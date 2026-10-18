from scripts import run_mysql_algorithm_c_async_scene_v51 as subject


def test_cached_only_evidence_cannot_preempt_physical_top5(monkeypatch):
    accepted = {"row": {"rank": 5}}

    def original(rows, reference, *, canonical_baseline=None, target):
        return accepted, [{"row": dict(rows[0])}]

    monkeypatch.setattr(subject, "_ORIGINAL_EVALUATE_BATCH", original)
    selected, valid = subject.evaluate_batch_without_cached_incumbent_preemption(
        [{"cached_valid_replay_redecision": True}], {}, target="memory",
    )
    assert selected is None
    assert len(valid) == 1


def test_physical_or_mixed_batch_keeps_postgresql_evaluation(monkeypatch):
    accepted = {"row": {"rank": 1}}

    def original(rows, reference, *, canonical_baseline=None, target):
        return accepted, [{"row": dict(row)} for row in rows]

    monkeypatch.setattr(subject, "_ORIGINAL_EVALUATE_BATCH", original)
    physical = [{"rank": 1}]
    selected, _valid = subject.evaluate_batch_without_cached_incumbent_preemption(
        physical, {}, target="memory",
    )
    assert selected is accepted
    mixed = [{"cached_valid_replay_redecision": True}, {"rank": 1}]
    selected, _valid = subject.evaluate_batch_without_cached_incumbent_preemption(
        mixed, {}, target="memory",
    )
    assert selected is accepted
