from copy import deepcopy

import pytest

from formal_recost_scheduler import (
    SchedulerNotOurTurn,
    claim_transition,
    completion_transition,
)


def schedule():
    return {
        1: {
            "ordinal": 1,
            "session_id": "mysql_recost",
            "engine": "mysql",
            "method": "recost_gradient_descent",
            "phase": "A",
            "run_id": "A21",
            "repeat_id": 1,
        },
        2: {
            "ordinal": 2,
            "session_id": "pg_brute_force",
            "engine": "postgresql",
            "method": "brute_force",
            "phase": "A",
            "run_id": "A1",
            "repeat_id": 1,
        },
    }


def state():
    return {
        "schema_version": 1,
        "freeze_id": "frozen_formal_config_20260721_v3",
        "point_schedule_sha256": "a" * 64,
        "next_ordinal": 1,
        "active": None,
        "completed_entry_count": 0,
        "completed_entries": [],
    }


def test_claim_and_complete_advance_exactly_once():
    original = state()
    claimed, entry, resumed = claim_transition(
        original, schedule(), attempt_id="A21_r1_attempt_01", claimed_at_utc="2026-07-21T05:00:00Z"
    )
    assert not resumed
    assert original["active"] is None
    assert claimed["active"]["ordinal"] == 1
    completed = completion_transition(
        claimed,
        schedule(),
        result_manifest_relative_path="mysql/recost_gradient_descent/A21_r1/result_manifest.json",
        result_manifest_sha256="b" * 64,
        completed_at_utc="2026-07-21T06:00:00Z",
    )
    assert completed["next_ordinal"] == 2
    assert completed["completed_entry_count"] == 1
    assert completed["active"] is None


def test_existing_own_active_entry_resumes_without_replacing_attempt():
    claimed, _, _ = claim_transition(
        state(), schedule(), attempt_id="original", claimed_at_utc="2026-07-21T05:00:00Z"
    )
    resumed, _, was_resumed = claim_transition(
        claimed, schedule(), attempt_id="new-process", claimed_at_utc="2026-07-21T05:30:00Z"
    )
    assert was_resumed
    assert resumed["active"]["attempt_id"] == "original"


def test_other_session_next_ordinal_is_not_claimable():
    other = state()
    other["next_ordinal"] = 2
    other["completed_entries"] = [{"placeholder": True}]
    other["completed_entry_count"] = 1
    with pytest.raises(SchedulerNotOurTurn):
        claim_transition(
            other, schedule(), attempt_id="wrong", claimed_at_utc="2026-07-21T05:00:00Z"
        )


def test_completion_rejects_noncontiguous_state():
    claimed, _, _ = claim_transition(
        state(), schedule(), attempt_id="attempt", claimed_at_utc="2026-07-21T05:00:00Z"
    )
    claimed["completed_entries"] = [{"unexpected": True}]
    with pytest.raises(Exception, match="not contiguous"):
        completion_transition(
            claimed,
            schedule(),
            result_manifest_relative_path="mysql/x.json",
            result_manifest_sha256="b" * 64,
            completed_at_utc="2026-07-21T06:00:00Z",
        )
