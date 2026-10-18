from pathlib import Path
import hashlib
import json

from scripts import wait_and_launch_mysql_algorithm_c_v42_rank4_v1 as waiter


def state() -> dict[str, object]:
    return {
        "status": "FAILED_CLOSED",
        "stage": "FAILED",
        "error": (
            "RuntimeError: external physical workload bypassed the exclusive wave lease"
        ),
        "outer_attempt": 9,
        "accepted_rounds": dict(waiter.INHERITED_ACCEPTED_ROUNDS),
    }


def test_zero_replay_lease_race_is_retryable(tmp_path: Path) -> None:
    assert waiter.retryable_preworkload_lease_race(state(), run_root=tmp_path)


def test_any_rank4_measurement_makes_lease_race_non_retryable(tmp_path: Path) -> None:
    batch = tmp_path / "outer_010/top5_batch_4/batch_result.json"
    batch.parent.mkdir(parents=True)
    batch.write_text("{}\n", encoding="utf-8")
    assert not waiter.retryable_preworkload_lease_race(state(), run_root=tmp_path)


def test_state_credit_change_makes_lease_race_non_retryable(tmp_path: Path) -> None:
    changed = state()
    changed["accepted_rounds"] = {
        **waiter.INHERITED_ACCEPTED_ROUNDS,
        "job_memory": 6,
    }
    assert not waiter.retryable_preworkload_lease_race(changed, run_root=tmp_path)


def test_atomic_publishes_result_and_matching_digest(
    tmp_path: Path, monkeypatch,
) -> None:
    result = tmp_path / "result.json"
    monkeypatch.setattr(waiter, "RESULT", result)

    waiter.atomic({"status": "STARTED", "attempt": 1})

    assert json.loads(result.read_text(encoding="utf-8")) == {
        "attempt": 1,
        "status": "STARTED",
    }
    digest = hashlib.sha256(result.read_bytes()).hexdigest()
    assert result.with_name("result.json.sha256").read_text(encoding="ascii") == (
        f"{digest}  result.json\n"
    )
