"""Append-only accounting for capped real workload replays."""

from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


TERMINAL_CLASSIFICATIONS = {
    "clean_success",
    "algorithmic_rejection",
    "technical_failure",
}
SUCCESS_BUDGET_CLASSIFICATIONS = {
    "clean_success",
    "algorithmic_rejection",
}


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def create_ledger(
    path: Path,
    *,
    contract: str,
    source_history_sha256: str,
    replay_root: Path,
    maximum_successful_replays: int = 5,
) -> Dict[str, Any]:
    """Create a new immutable-attempt ledger."""

    if path.exists():
        raise FileExistsError(f"replay ledger already exists: {path}")
    if maximum_successful_replays <= 0:
        raise ValueError("maximum_successful_replays must be positive")
    ledger: Dict[str, Any] = {
        "schema_version": 3,
        "contract": contract,
        "source_history_sha256": source_history_sha256,
        "replay_root": str(replay_root.resolve()),
        "maximum_successful_replays": int(maximum_successful_replays),
        "attempts": [],
        "classification_history": [],
    }
    _atomic_json(path, ledger)
    return ledger


def load_ledger(
    path: Path,
    *,
    contract: str,
    source_history_sha256: str,
    replay_root: Path,
) -> Dict[str, Any]:
    """Load and validate a v3 ledger without repairing or deleting history."""

    parsed = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(parsed, dict)
        or parsed.get("schema_version") != 3
        or parsed.get("contract") != contract
        or parsed.get("source_history_sha256") != source_history_sha256
        or Path(str(parsed.get("replay_root") or "")).resolve()
        != replay_root.resolve()
        or not isinstance(parsed.get("attempts"), list)
        or not isinstance(parsed.get("classification_history"), list)
    ):
        raise RuntimeError("persistent replay ledger contract mismatch")
    seen_attempt_ids = set()
    for record in parsed["attempts"]:
        if not isinstance(record, dict):
            raise RuntimeError("replay ledger contains a non-object attempt")
        attempt_id = str(record.get("attempt_id") or "")
        if not attempt_id or attempt_id in seen_attempt_ids:
            raise RuntimeError("replay ledger contains duplicate attempt_id")
        seen_attempt_ids.add(attempt_id)
        terminal = record.get("terminal")
        if terminal is not None:
            classification = terminal.get("classification")
            if classification not in TERMINAL_CLASSIFICATIONS:
                raise RuntimeError("replay ledger has invalid terminal classification")
            expected_consumption = classification in SUCCESS_BUDGET_CLASSIFICATIONS
            if terminal.get("consumes_successful_replay_budget") is not expected_consumption:
                raise RuntimeError("replay ledger has inconsistent budget classification")
    if replay_budget_used(parsed) > int(parsed["maximum_successful_replays"]):
        raise RuntimeError("persistent replay ledger exceeds successful replay cap")
    return parsed


def replay_budget_used(ledger: Mapping[str, Any]) -> int:
    return sum(
        1
        for record in ledger.get("attempts") or []
        if isinstance(record, Mapping)
        and isinstance(record.get("terminal"), Mapping)
        and record["terminal"].get("classification")
        in SUCCESS_BUDGET_CLASSIFICATIONS
    )


def begin_attempt(
    path: Path,
    ledger: Mapping[str, Any],
    *,
    rank: int,
    attempt: int,
    point_root: Path,
) -> Dict[str, Any]:
    """Persist an immutable physical-attempt claim before launching workload."""

    mutable = copy.deepcopy(dict(ledger))
    attempt_id = f"rank-{int(rank):02d}-attempt-{int(attempt):02d}"
    if any(
        str(record.get("attempt_id") or "") == attempt_id
        for record in mutable["attempts"]
    ):
        raise RuntimeError(f"duplicate physical replay attempt: {attempt_id}")
    if replay_budget_used(mutable) >= int(mutable["maximum_successful_replays"]):
        raise RuntimeError("successful replay hard cap reached before workload")
    if any(
        int(record.get("rank") or 0) == int(rank)
        and isinstance(record.get("terminal"), Mapping)
        and record["terminal"].get("classification")
        in SUCCESS_BUDGET_CLASSIFICATIONS
        for record in mutable["attempts"]
    ):
        raise RuntimeError(f"rank {rank} already consumed successful replay budget")
    mutable["attempts"].append(
        {
            "attempt_id": attempt_id,
            "rank": int(rank),
            "attempt": int(attempt),
            "point_root": str(point_root.resolve()),
            "started_epoch": time.time(),
            "terminal": None,
        }
    )
    _atomic_json(path, mutable)
    return mutable


def finish_attempt(
    path: Path,
    ledger: Mapping[str, Any],
    *,
    rank: int,
    attempt: int,
    classification: str,
    evidence: Mapping[str, Any],
) -> Dict[str, Any]:
    """Append a terminal classification without removing prior evidence."""

    if classification not in TERMINAL_CLASSIFICATIONS:
        raise ValueError(f"unsupported replay classification: {classification}")
    mutable = copy.deepcopy(dict(ledger))
    attempt_id = f"rank-{int(rank):02d}-attempt-{int(attempt):02d}"
    matching = [
        record
        for record in mutable["attempts"]
        if str(record.get("attempt_id") or "") == attempt_id
    ]
    if len(matching) != 1:
        raise RuntimeError(f"attempt claim not found: {attempt_id}")
    record = matching[0]
    if record.get("terminal") is not None:
        raise RuntimeError(f"attempt already terminal: {attempt_id}")
    consumes = classification in SUCCESS_BUDGET_CLASSIFICATIONS
    record["terminal"] = {
        "classification": classification,
        "consumes_successful_replay_budget": consumes,
        "finished_epoch": time.time(),
        "evidence": copy.deepcopy(dict(evidence)),
    }
    mutable["classification_history"].append(
        {
            "attempt_id": attempt_id,
            "classification": classification,
            "consumes_successful_replay_budget": consumes,
            "timestamp_epoch": record["terminal"]["finished_epoch"],
        }
    )
    if replay_budget_used(mutable) > int(mutable["maximum_successful_replays"]):
        raise RuntimeError("terminal classification would exceed successful replay cap")
    _atomic_json(path, mutable)
    return mutable


def ledger_counts(ledger: Mapping[str, Any]) -> Dict[str, int]:
    classifications = [
        record["terminal"]["classification"]
        for record in ledger.get("attempts") or []
        if isinstance(record, Mapping)
        and isinstance(record.get("terminal"), Mapping)
    ]
    return {
        "physical_attempt_count": len(ledger.get("attempts") or []),
        "incomplete_attempt_count": sum(
            1
            for record in ledger.get("attempts") or []
            if isinstance(record, Mapping) and record.get("terminal") is None
        ),
        "clean_success_count": classifications.count("clean_success"),
        "algorithmic_rejection_count": classifications.count(
            "algorithmic_rejection"
        ),
        "technical_failure_count": classifications.count("technical_failure"),
        "successful_replay_budget_used": replay_budget_used(ledger),
    }


def validate_attempt_artifact_ownership(
    ledger: Mapping[str, Any],
    replay_root: Path,
) -> Dict[str, Any]:
    """Require a one-to-one mapping between ledger claims and attempt roots."""

    root = replay_root.resolve()
    claimed = {
        Path(str(record.get("point_root") or "")).resolve()
        for record in ledger.get("attempts") or []
        if isinstance(record, Mapping)
    }
    if any(path.parent.parent != root for path in claimed):
        raise RuntimeError("replay ledger contains an out-of-root attempt path")
    actual = {
        path.resolve()
        for path in replay_root.glob("rank_*/attempt_*")
        if path.is_dir()
    }
    if actual != claimed:
        raise RuntimeError(
            "physical replay artifacts do not match immutable ledger claims: "
            f"unclaimed={sorted(str(path) for path in actual - claimed)}, "
            f"missing={sorted(str(path) for path in claimed - actual)}"
        )

    summary_owners: Dict[str, str] = {}
    for record in ledger.get("attempts") or []:
        terminal = record.get("terminal")
        if not isinstance(terminal, Mapping):
            continue
        evidence = terminal.get("evidence")
        result = (
            evidence.get("result")
            if isinstance(evidence, Mapping)
            else None
        )
        if not isinstance(result, Mapping):
            continue
        raw_summary = result.get("measurement_summary_path")
        if not raw_summary:
            continue
        summary = str(Path(str(raw_summary)).resolve())
        owner = str(record.get("attempt_id") or "")
        previous = summary_owners.setdefault(summary, owner)
        if previous != owner:
            raise RuntimeError(
                "one measurement summary is claimed by multiple attempts"
            )
        if not Path(summary).is_file():
            raise RuntimeError(
                f"attempt measurement summary is not readable: {summary}"
            )
    return {
        "contract": "physical_attempt_artifact_ownership_v1",
        "claimed_attempt_roots": len(claimed),
        "actual_attempt_roots": len(actual),
        "unique_measurement_summaries": len(summary_owners),
        "passed": True,
    }


__all__ = [
    "begin_attempt",
    "create_ledger",
    "finish_attempt",
    "ledger_counts",
    "load_ledger",
    "replay_budget_used",
    "validate_attempt_artifact_ownership",
]
