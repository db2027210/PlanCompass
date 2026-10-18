from __future__ import annotations

import json
from pathlib import Path

import pytest

from formal_mysql_sgd_checkpoint import (
    FormalOuterReplayLedger, FormalProductionR0Ledger,
    FormalReplayCheckpointError,
)


def _candidates() -> list[dict]:
    return [
        {
            "rank": rank, "subset_id": f"s{rank}",
            "strict_plan_cluster_key": f"strict:c{rank % 2}",
            "canonical_plan_bundle": {
                "canonical_plan_bundle_sha256": f"{rank:064x}",
            },
            "optimized_params": {"p": float(rank), "q": 0.25},
        }
        for rank in range(1, 13)
    ]


def _ledger(path: Path) -> FormalOuterReplayLedger:
    return FormalOuterReplayLedger.create(
        path, outer_iter=2, ranked_candidates=_candidates(),
        representative_ranks=[1, 3, 4, 7, 8, 9, 10, 11, 12],
        completed_subset_ids=["s1", "s4"], r0_completed=True,
        completed_subset_results={"s1": {"value": 1}, "s4": {"value": 4}},
        r0_result={"resource": {"cpu": 1}},
        pruning_log=[{"rank": 2, "decision": "pruned", "bounds": {"cpu": [0.1, 0.2]}}],
        plan_cluster_log=[{"cluster_id": "c1", "members": [1, 3, 7]}],
        representative_log=[{"cluster_id": "c1", "representative_rank": 1}],
        suppression_log=[{"rank": 2, "reason": "optimistic_pruning"}],
    )


def test_non_contiguous_replayed_ranks_survive_verified_resume(tmp_path: Path) -> None:
    path = tmp_path / "outer.json"
    ledger = _ledger(path)
    ledger.mark_replayed(1, stage="top_5", evidence={"sha256": "a" * 64})
    ledger.mark_replayed(7, stage="top_10", evidence={"sha256": "b" * 64})
    resumed = FormalOuterReplayLedger.load_verified(path, _candidates())
    assert resumed.document["replayed_candidate_ranks"] == [1, 7]
    assert resumed.pending_replay_ranks(9) == [3, 4, 8, 9, 10, 11, 12]
    assert resumed.pending_subsets(["s1", "s2", "s3", "s4"]) == ["s2", "s3"]
    with pytest.raises(FormalReplayCheckpointError, match="R0 is already complete"):
        resumed.assert_r0_may_run()
    with pytest.raises(FormalReplayCheckpointError, match="already replayed"):
        resumed.mark_replayed(7, stage="top_k", evidence={})
    with pytest.raises(FormalReplayCheckpointError, match="already complete"):
        resumed.mark_subset_completed("s4")


def test_resume_rejects_changed_candidate_or_sealed_representative_order(tmp_path: Path) -> None:
    path = tmp_path / "outer.json"
    _ledger(path)
    changed = _candidates()
    changed[0]["optimized_params"]["p"] = 1.0000000000000002
    with pytest.raises(FormalReplayCheckpointError, match="sealed order"):
        FormalOuterReplayLedger.load_verified(path, changed)

    changed_cluster = _candidates()
    changed_cluster[0]["strict_plan_cluster_key"] = "strict:changed"
    with pytest.raises(FormalReplayCheckpointError, match="sealed order"):
        FormalOuterReplayLedger.load_verified(path, changed_cluster)

    payload = json.loads(path.read_text())
    payload["sealed_representative_ranks"] = list(reversed(payload["sealed_representative_ranks"]))
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    import hashlib
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n")
    with pytest.raises(FormalReplayCheckpointError, match="representative order checksum"):
        FormalOuterReplayLedger.load_verified(path, _candidates())


def test_checkpoint_contains_full_pruning_cluster_representative_suppression_and_stage_log(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "outer.json")
    events = [row["event"] for row in ledger.document["audit_log"]]
    assert events[:5] == [
        "pruning_decisions_sealed", "plan_clusters_sealed", "representatives_sealed",
        "suppressed_candidates_sealed", "replay_stage_schedule_sealed",
    ]
    stages = ledger.document["audit_log"][4]["stages"]
    assert [row["stage"] for row in stages] == ["top_5", "top_10", "top_k"]


def test_root_cause_review_is_atomic_and_restorable(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "outer.json")
    ledger.mark_root_cause_review(
        diagnostic={"code": "none_eligible"},
        review={"approved": True, "ticket": "review-1"},
    )
    resumed = FormalOuterReplayLedger.load_verified(
        tmp_path / "outer.json", _candidates()
    )
    recorded = resumed.approved_root_cause_review()
    assert recorded["diagnostic"]["code"] == "none_eligible"
    assert recorded["review"]["ticket"] == "review-1"
    with pytest.raises(FormalReplayCheckpointError, match="already recorded"):
        resumed.mark_root_cause_review(
            diagnostic={"code": "again"}, review={"approved": True},
        )


def test_production_real_r0_is_checksum_and_identity_bound(tmp_path: Path) -> None:
    path = tmp_path / "real_r0.json"
    identity = {"benchmark": "job", "config_sha256": "a" * 64}
    measurement = {
        "qps": 100.0, "db_qps": 100.0,
        "db_qps_source": "database_status_counter",
    }
    FormalProductionR0Ledger.create(
        path, campaign_identity=identity, measurement=measurement,
    )
    assert FormalProductionR0Ledger.load_verified(
        path, campaign_identity=identity,
    ) == measurement
    with pytest.raises(FormalReplayCheckpointError, match="identity mismatch"):
        FormalProductionR0Ledger.load_verified(
            path,
            campaign_identity={"benchmark": "job", "config_sha256": "b" * 64},
        )

    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(FormalReplayCheckpointError, match="checksum mismatch"):
        FormalProductionR0Ledger.load_verified(path, campaign_identity=identity)
