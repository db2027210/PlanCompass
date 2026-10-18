from pathlib import Path
import hashlib
import json

from scripts import (
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v19
    as subject,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_v19_source_reuses_only_complete_outer6_rank1_and_rank2():
    state = subject.validate_source()
    assert state["stage"] == "ALGORITHM_B_OUTER_006_RANK_3_MEASURING"
    assert (6, 1) in subject.REUSED_BATCHES
    assert (6, 2) in subject.REUSED_BATCHES
    assert (6, 3) not in subject.REUSED_BATCHES
    assert not (subject.SOURCE / "outer_006/top5_batch_3/batch_result.json").exists()


def test_v19_contamination_evidence_proves_old_sessions_and_clean_readback():
    evidence = json.loads(subject.INTERRUPTED_DRAIN.read_text(encoding="utf-8"))
    assert evidence["all_workers_zero_sessions_after"] is True
    assert evidence["all_tpch_revenue0_views_absent_after"] is True
    assert {row["worker_id"] for row in evidence["workers"]} == {
        "tpch_w1", "tpch_w2",
    }
    assert all(
        max(item["time_seconds"] for item in row["sessions_before"]) > 900
        for row in evidence["workers"]
    )


def test_v19_contract_binds_drain_and_unchanged_convergence():
    contract = subject.validate_contract()
    assert contract["pre_measure_session_drain"][
        "zero_benchmark_session_readback_required"
    ] is True
    assert contract["continuation"]["outer6_completed_ranks_reused"] == [1, 2]
    assert contract["continuation"]["outer6_fresh_ranks_required"] == [3, 4, 5]
    assert contract["convergence"]["accepted_shrinks_required_per_scene"] == 10
    assert contract["convergence"]["maximum_interval_ratio"] == 1.01
    for binding in contract["source_bindings"].values():
        path = ROOT / binding["path"]
        assert path.is_file() and not path.is_symlink()
        assert _sha(path) == binding["sha256"]

