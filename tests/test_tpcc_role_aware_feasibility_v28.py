from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.tpcc_autonomous_target10_controller_v28_role_aware import (
    role_aware_feasibility_rows,
)


def _candidate(
    rank: int,
    role: str,
    scores: list[float],
) -> dict[str, object]:
    return {
        "rank": rank,
        "selection_role": role,
        "independent_actual_scores": scores,
        "three_level_fingerprint": {"id": f"fp-{rank}"},
    }


def test_probe_cannot_pass_feasibility_gate() -> None:
    rows = role_aware_feasibility_rows(
        [
            _candidate(1, "trusted", [4.6, 4.7]),
            _candidate(2, "uncertain_probe", [4.6, 4.9]),
        ],
        incumbent_score=4.8,
    )
    assert rows[0]["trusted_eligible"] is True
    assert rows[0]["feasible"] is False
    assert rows[1]["trusted_eligible"] is False
    assert rows[1]["feasible"] is False


def test_trusted_candidate_can_pass_feasibility_gate() -> None:
    rows = role_aware_feasibility_rows(
        [_candidate(1, "trusted", [4.6, 4.9])],
        incumbent_score=4.8,
    )
    assert rows[0]["maximum_median_after_one_observation"] == 4.9
    assert rows[0]["feasible"] is True


if __name__ == "__main__":
    test_probe_cannot_pass_feasibility_gate()
    test_trusted_candidate_can_pass_feasibility_gate()
    print("TPC-C role-aware feasibility v28 regression passed", flush=True)
