from __future__ import annotations

from pathlib import Path

import pytest

from replay_attempt_ledger import (
    begin_attempt,
    create_ledger,
    finish_attempt,
    ledger_counts,
    load_ledger,
    validate_attempt_artifact_ownership,
)


def _new_ledger(tmp_path: Path):
    path = tmp_path / "ledger.json"
    ledger = create_ledger(
        path,
        contract="test-v3",
        source_history_sha256="a" * 64,
        replay_root=tmp_path / "replay",
    )
    return path, ledger


def test_algorithmic_rejection_consumes_budget_and_blocks_rank_retry(
    tmp_path: Path,
) -> None:
    path, ledger = _new_ledger(tmp_path)
    ledger = begin_attempt(
        path,
        ledger,
        rank=1,
        attempt=1,
        point_root=tmp_path / "point1",
    )
    ledger = finish_attempt(
        path,
        ledger,
        rank=1,
        attempt=1,
        classification="algorithmic_rejection",
        evidence={"returncode": -15, "stable_payload": True},
    )
    assert ledger_counts(ledger)["successful_replay_budget_used"] == 1
    with pytest.raises(RuntimeError):
        begin_attempt(
            path,
            ledger,
            rank=1,
            attempt=2,
            point_root=tmp_path / "point2",
        )


def test_technical_failure_does_not_consume_success_budget(tmp_path: Path) -> None:
    path, ledger = _new_ledger(tmp_path)
    ledger = begin_attempt(
        path,
        ledger,
        rank=1,
        attempt=1,
        point_root=tmp_path / "point1",
    )
    ledger = finish_attempt(
        path,
        ledger,
        rank=1,
        attempt=1,
        classification="technical_failure",
        evidence={"missing_payload": True},
    )
    ledger = begin_attempt(
        path,
        ledger,
        rank=1,
        attempt=2,
        point_root=tmp_path / "point2",
    )
    assert ledger_counts(ledger) == {
        "physical_attempt_count": 2,
        "incomplete_attempt_count": 1,
        "clean_success_count": 0,
        "algorithmic_rejection_count": 0,
        "technical_failure_count": 1,
        "successful_replay_budget_used": 0,
    }


def test_cap_is_derived_from_append_only_attempts(tmp_path: Path) -> None:
    path, ledger = _new_ledger(tmp_path)
    for rank in range(1, 6):
        ledger = begin_attempt(
            path,
            ledger,
            rank=rank,
            attempt=1,
            point_root=tmp_path / f"point{rank}",
        )
        ledger = finish_attempt(
            path,
            ledger,
            rank=rank,
            attempt=1,
            classification="clean_success",
            evidence={"rank": rank},
        )
    with pytest.raises(RuntimeError):
        begin_attempt(
            path,
            ledger,
            rank=6,
            attempt=1,
            point_root=tmp_path / "point6",
        )


def test_external_deletion_or_reclassification_is_rejected(tmp_path: Path) -> None:
    path, ledger = _new_ledger(tmp_path)
    ledger = begin_attempt(
        path,
        ledger,
        rank=1,
        attempt=1,
        point_root=tmp_path / "point1",
    )
    ledger = finish_attempt(
        path,
        ledger,
        rank=1,
        attempt=1,
        classification="clean_success",
        evidence={},
    )
    ledger["attempts"][0]["terminal"][
        "consumes_successful_replay_budget"
    ] = False
    path.write_text(__import__("json").dumps(ledger), encoding="utf-8")
    with pytest.raises(RuntimeError):
        load_ledger(
            path,
            contract="test-v3",
            source_history_sha256="a" * 64,
            replay_root=tmp_path / "replay",
        )


def test_attempt_artifacts_must_match_ledger_claims(tmp_path: Path) -> None:
    root = tmp_path / "replay"
    root.mkdir()
    path = root / "ledger.json"
    ledger = create_ledger(
        path,
        contract="test-v3",
        source_history_sha256="a" * 64,
        replay_root=root,
    )
    point_root = root / "rank_01" / "attempt_01"
    ledger = begin_attempt(
        path,
        ledger,
        rank=1,
        attempt=1,
        point_root=point_root,
    )
    point_root.mkdir(parents=True)
    result = validate_attempt_artifact_ownership(ledger, root)
    assert result["passed"] is True
    (root / "rank_02" / "attempt_01").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="do not match"):
        validate_attempt_artifact_ownership(ledger, root)
