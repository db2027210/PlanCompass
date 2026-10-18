from __future__ import annotations

import json
from pathlib import Path

from scripts import run_mysql_algorithm_a_same_cohort_codefix_resume_v1 as resume


def test_build_resumed_state_preserves_reference_without_replay(tmp_path) -> None:
    predecessor_path = tmp_path / "predecessor.json"
    predecessor_path.write_text(json.dumps({"snapshot": True}))
    predecessor = {
        "run_id": "old_job",
        "accepted": 3,
        "rejected_rounds": 4,
        "consecutive_rejected_rounds": 1,
        "incumbent_reference_refresh_count": 1,
        "execution_evidence_epoch": 2,
    }
    preserved = {
        "ranges": {"p": [1, 2]},
        "incumbent_params": {"p": 1},
        "incumbent_validation": {"db_qps": 12.5},
        "incumbent_reference_source": "last_accepted_round",
        "baseline_by_worker": {"w1": {}, "w2": {}},
        "canonical_baseline": {"db_qps": 10.0},
    }

    state = resume.build_resumed_state(
        fresh={"algorithm": "A"}, predecessor=predecessor,
        preserved=preserved, completed_round=6, interrupted_round=7,
        predecessor_path=predecessor_path,
    )

    assert state["accepted"] == 3
    assert state["round_index"] == 6
    assert state["round_checkpoint"] is None
    assert state["incumbent_validation"] == {"db_qps": 12.5}
    assert state["incumbent_reference_source"] == "last_accepted_round"
    assert state["consecutive_rejected_rounds"] == 1
    assert state["partial_interrupted_round_credited"] is False


def test_resume_contract_explicitly_reuses_reference() -> None:
    source = Path(resume.__file__).read_text(encoding="utf-8")
    assert '"incumbent_reference_reused_without_replay": True' in source
    assert '"formal_client_log"' in source
