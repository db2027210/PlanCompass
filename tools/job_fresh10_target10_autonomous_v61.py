#!/usr/bin/env python3
"""JOB v61: restart-safe recovery for interrupted EXPLAIN successor generation."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping

import job_fresh10_target10_autonomous_v60 as v60


implementation = v60.implementation
EXPECTED_V60_SHA256 = "394f207eea62d93ceae570d3bca25abe36c4ae787a37f8913cbbc4e492ed222b"
if hashlib.sha256(Path(v60.__file__).read_bytes()).hexdigest() != EXPECTED_V60_SHA256:
    raise implementation.v49.FailClosed("v61 sealed v60 controller SHA mismatch")

v55 = v60.v59.v58.v57.v55
RECOVERY_CONTRACT = "job_v61_restart_safe_successor_partial_recovery_v1"
RECOVERY_EPOCH = "job_v61_cycle002_attempt05_generator_recovery_v1"
RECOVERY_ATTEMPT = 5
RAW_SLOTS = tuple(v55.SUCCESSOR_GENERATOR_SLOTS)

_GENERATE_V60 = implementation.generate_fresh_discovery
_GENERATE_V55 = v60.v59._GENERATE_V58
_RESUME_V60 = implementation.resume_position_v50
_REQUIRE_V60_HASHES = implementation.require_v50_hashes

implementation.CONTROLLER_VERSION = "v61"


def _canonical_sha(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _counter_pin() -> dict[str, int]:
    return {
        "global": implementation.v49.counter(implementation.v49.GLOBAL_COUNTER),
        "formal_replay": implementation.v49.counter(
            implementation.v49.FORMAL_REPLAY_COUNTER
        ),
        "accepted": implementation.v49.counter(implementation.v49.ACCEPTED_COUNTER),
    }


def _authority_pin() -> dict[str, Any]:
    state_sha, state = implementation.v49.state_pin()
    counters = _counter_pin()
    if int(state.get("accepted_count", -1)) != counters["accepted"]:
        raise implementation.v49.FailClosed("v61 state/counter pin mismatch")
    return {
        "formal_state_sha256": state_sha,
        "accepted_count": int(state["accepted_count"]),
        "counters": counters,
    }


def _assert_authority_pin(expected: Mapping[str, Any]) -> None:
    if _authority_pin() != dict(expected):
        raise implementation.v49.FailClosed(
            "v61 partial recovery would cross a formal authority change"
        )


def _recovery_paths(fresh_root: Path) -> tuple[Path, Path, Path]:
    return (
        fresh_root / "v61_partial_recovery_transaction.json",
        fresh_root / "v61_partial_recovery_audit.json",
        fresh_root / "partial_quarantine_v61",
    )


def _replacement_seed_path(fresh_root: Path) -> Path:
    return fresh_root / "v61_replacement_budget_seed.json"


def _validate_exclusion(fresh_root: Path) -> tuple[str, int]:
    path = fresh_root / "exclusion.json"
    if not path.is_file():
        raise implementation.v49.FailClosed("v61 successor exclusion is absent")
    value = implementation.v49.read_json(path)
    identifiers = {
        str((item.get("three_level_fingerprint") or {}).get("id") or "")
        for item in value.get("fingerprints") or []
        if isinstance(item, Mapping)
    }
    identifiers.discard("")
    cycle_root = fresh_root.parent
    quarantine = v60.ensure_network_quarantine_v60(cycle_root)
    historical = set(quarantine["quarantined_fingerprints"])
    if (
        value.get("schema_version") != "job_v50_fresh_diagnostic_exclusion_v1"
        or int(value.get("fingerprint_count", -1)) != len(identifiers)
        or len(historical) != 20
        or not historical.issubset(identifiers)
    ):
        raise implementation.v49.FailClosed(
            "v61 partial successor does not retain all 20 quarantined fingerprints"
        )
    return implementation.v49.sha256_file(path), len(identifiers)


def _partial_candidates(fresh_root: Path) -> list[Path]:
    candidates = [
        fresh_root / "v55_successor_budget.json",
        fresh_root / "historical_training_a.json.progress.json",
        fresh_root / "historical_training_b.json.progress.json",
        fresh_root / "top5.json",
        fresh_root / "top5.second.json",
        fresh_root / "top5.json.failure_audit.json",
        fresh_root / "top5.second.json.failure_audit.json",
        fresh_root / "generator_a.log",
        fresh_root / "generator_b.log",
    ]
    candidates.extend(fresh_root.glob(".*.tmp.*"))
    return sorted({path.resolve() for path in candidates if path.exists()})


def _replacement_budget(
    original: Mapping[str, Any],
    fresh_root: Path,
) -> tuple[dict[str, Any], list[Path], list[dict[str, Any]]]:
    raw_by_slot = {name: fresh_root / name for name in RAW_SLOTS}
    invocations = original.get("invocations") or []
    by_slot = {
        str(item.get("slot") or ""): item
        for item in invocations
        if isinstance(item, Mapping)
    }
    preserved_paths: list[Path] = []
    preserved_invocations: list[dict[str, Any]] = []
    for slot in RAW_SLOTS:
        raw = raw_by_slot[slot]
        invocation = by_slot.get(slot)
        if invocation is None or invocation.get("status") != "complete":
            continue
        expected_sha = str(invocation.get("artifact_sha256") or "")
        if (
            not raw.is_file()
            or not expected_sha
            or implementation.v49.sha256_file(raw) != expected_sha
        ):
            raise implementation.v49.FailClosed(
                f"v61 completed generator slot is not SHA-bound: {slot}"
            )
        preserved_paths.append(raw.resolve())
        preserved_invocations.append(copy.deepcopy(dict(invocation)))
    now = time.time()
    replacement = {
        "schema_version": v55.SUCCESSOR_BUDGET_SCHEMA,
        "status": "generating",
        "replay_attempt": RECOVERY_ATTEMPT,
        "fresh_root": str(fresh_root.resolve()),
        "generator_slots": list(RAW_SLOTS),
        "generator_invocation_limit": len(RAW_SLOTS),
        "per_generator_timeout_seconds": v55.SUCCESSOR_GENERATOR_TIMEOUT_SECONDS,
        "wall_timeout_seconds": v55.SUCCESSOR_DISCOVERY_WALL_SECONDS,
        "started_unix_seconds": now,
        "deadline_unix_seconds": now + v55.SUCCESSOR_DISCOVERY_WALL_SECONDS,
        "invocations": preserved_invocations,
        "workload_calls": 0,
        "formal_state_updates": 0,
        "recovery_contract": RECOVERY_CONTRACT,
        "recovery_epoch": RECOVERY_EPOCH,
        "prior_budget_sha256": implementation.v49.sha256_file(
            fresh_root / "v55_successor_budget.json"
        ),
        "preserved_complete_slots": [
            str(item["slot"]) for item in preserved_invocations
        ],
        "replacement_generator_invocation_limit": (
            len(RAW_SLOTS) - len(preserved_invocations)
        ),
    }
    return replacement, preserved_paths, preserved_invocations


def _validate_committed_recovery(
    transaction: Mapping[str, Any],
    fresh_root: Path,
) -> dict[str, Any]:
    transaction_path, audit_path, _quarantine_root = _recovery_paths(fresh_root)
    if (
        transaction.get("status") != "committed"
        or transaction.get("contract") != RECOVERY_CONTRACT
        or transaction.get("recovery_epoch") != RECOVERY_EPOCH
        or transaction.get("audit_sha256")
        != implementation.v49.sha256_file(audit_path)
    ):
        raise implementation.v49.FailClosed("v61 committed recovery transaction is invalid")
    audit = implementation.v49.read_json(audit_path)
    budget_path = fresh_root / "v55_successor_budget.json"
    seed_path = _replacement_seed_path(fresh_root)
    if (
        audit.get("status") != "committed"
        or audit.get("contract") != RECOVERY_CONTRACT
        or audit.get("recovery_epoch") != RECOVERY_EPOCH
        or audit.get("replacement_budget_initial_sha256")
        != implementation.v49.sha256_file(seed_path)
        or int(audit.get("new_workload_calls", -1)) != 0
        or int(audit.get("formal_state_updates", -1)) != 0
        or int(audit.get("counter_updates", -1)) != 0
    ):
        raise implementation.v49.FailClosed("v61 partial recovery audit is invalid")
    _assert_authority_pin(audit["authority_pin"])
    replacement = implementation.v49.read_json(budget_path)
    if (
        replacement.get("recovery_contract") != RECOVERY_CONTRACT
        or replacement.get("recovery_epoch") != RECOVERY_EPOCH
        or replacement.get("prior_budget_sha256")
        != audit.get("original_budget_sha256")
        or replacement.get("status")
        not in {"generating", "ready_for_first_replay", "replay_handoff_started"}
    ):
        raise implementation.v49.FailClosed("v61 replacement budget changed")
    _ = transaction_path
    return audit


def recover_partial_successor_v61(
    cycle_root: Path,
    replay_attempt: int = RECOVERY_ATTEMPT,
) -> dict[str, Any] | None:
    if replay_attempt != RECOVERY_ATTEMPT:
        return None
    fresh_root = cycle_root / f"fresh_discovery_{replay_attempt:02d}"
    transaction_path, audit_path, quarantine_root = _recovery_paths(fresh_root)
    if transaction_path.is_file():
        transaction = implementation.v49.read_json(transaction_path)
        if transaction.get("status") == "committed":
            return _validate_committed_recovery(transaction, fresh_root)
    output = fresh_root / "top5.json"
    second = fresh_root / "top5.second.json"
    budget_path = fresh_root / "v55_successor_budget.json"
    if transaction_path.is_file():
        transaction = implementation.v49.read_json(transaction_path)
        if transaction.get("status") != "prepared":
            raise implementation.v49.FailClosed("v61 recovery transaction status changed")
        prepared = transaction
        replacement = dict(prepared["replacement_budget"])
        moves = list(prepared["moves"])
        authority_pin = dict(prepared["authority_pin"])
        exclusion_sha, exclusion_count = _validate_exclusion(fresh_root)
        if (
            prepared.get("contract") != RECOVERY_CONTRACT
            or prepared.get("recovery_epoch") != RECOVERY_EPOCH
            or int(prepared.get("replay_attempt", -1)) != replay_attempt
            or prepared.get("exclusion_sha256") != exclusion_sha
            or int(prepared.get("exclusion_fingerprint_count", -1))
            != exclusion_count
            or _canonical_sha(replacement)
            != prepared.get("replacement_budget_canonical_sha256")
        ):
            raise implementation.v49.FailClosed("v61 prepared recovery changed")
    else:
        if output.is_file() and second.is_file() and budget_path.is_file():
            return None
        candidates = _partial_candidates(fresh_root)
        if not candidates:
            return None
        if not budget_path.is_file():
            raise implementation.v49.FailClosed(
                "v61 refuses unbudgeted partial successor artifacts"
            )
        authority_pin = _authority_pin()
        exclusion_sha, exclusion_count = _validate_exclusion(fresh_root)
        original_budget = implementation.v49.read_json(budget_path)
        v55._validate_successor_budget(original_budget, fresh_root, replay_attempt)
        replacement, preserved_paths, preserved_invocations = _replacement_budget(
            original_budget, fresh_root
        )
        preserved = set(preserved_paths)
        for slot in RAW_SLOTS:
            raw = (fresh_root / slot).resolve()
            if raw.exists() and raw not in preserved:
                candidates.append(raw)
        candidates = sorted(set(candidates))
        moves = []
        for source in candidates:
            if source in preserved:
                continue
            destination = quarantine_root / source.name
            moves.append(
                {
                    "source": str(source),
                    "destination": str(destination),
                    "sha256": implementation.v49.sha256_file(source),
                }
            )
        replacement_sha = _canonical_sha(replacement)
        prepared = {
            "status": "prepared",
            "contract": RECOVERY_CONTRACT,
            "recovery_epoch": RECOVERY_EPOCH,
            "replay_attempt": replay_attempt,
            "authority_pin": authority_pin,
            "exclusion_sha256": exclusion_sha,
            "exclusion_fingerprint_count": exclusion_count,
            "original_budget_sha256": implementation.v49.sha256_file(budget_path),
            "original_budget_status": original_budget.get("status"),
            "original_invocations": copy.deepcopy(original_budget.get("invocations") or []),
            "cursor_reusable": False,
            "cursor_reuse_reason": (
                "progress records counts only; it lacks generated candidates and "
                "the per-anchor attempt cursor"
            ),
            "preserved_complete_slots": [
                str(item["slot"]) for item in preserved_invocations
            ],
            "moves": moves,
            "replacement_budget": replacement,
            "replacement_budget_canonical_sha256": replacement_sha,
            "new_workload_calls": 0,
            "formal_state_updates": 0,
            "counter_updates": 0,
        }
        implementation.v49.atomic_json(transaction_path, prepared)
    prepared_sha = implementation.v49.sha256_file(transaction_path)
    _assert_authority_pin(authority_pin)
    quarantine_root.mkdir(parents=True, exist_ok=True)
    replacement_sha = _canonical_sha(replacement)
    for move in moves:
        source = Path(str(move["source"]))
        destination = Path(str(move["destination"]))
        expected_sha = str(move["sha256"])
        source_exists = source.is_file()
        destination_exists = destination.is_file()
        if source == budget_path and source_exists and destination_exists:
            current = implementation.v49.read_json(source)
            if _canonical_sha(current) != replacement_sha:
                raise implementation.v49.FailClosed(
                    "v61 budget source/destination overlap is not the replacement"
                )
            continue
        if source_exists and destination_exists:
            raise implementation.v49.FailClosed(
                "v61 partial recovery refuses source/destination overwrite"
            )
        if destination_exists:
            if implementation.v49.sha256_file(destination) != expected_sha:
                raise implementation.v49.FailClosed("v61 quarantined partial SHA changed")
            continue
        if not source_exists or implementation.v49.sha256_file(source) != expected_sha:
            raise implementation.v49.FailClosed("v61 partial source changed before quarantine")
        os.replace(source, destination)
    if not budget_path.is_file():
        implementation.v49.atomic_json(budget_path, replacement)
    if _canonical_sha(implementation.v49.read_json(budget_path)) != replacement_sha:
        raise implementation.v49.FailClosed("v61 replacement budget mismatch")
    seed_path = _replacement_seed_path(fresh_root)
    if seed_path.is_file():
        if _canonical_sha(implementation.v49.read_json(seed_path)) != replacement_sha:
            raise implementation.v49.FailClosed("v61 replacement budget seed changed")
    else:
        implementation.v49.atomic_json(seed_path, replacement)
    _assert_authority_pin(authority_pin)
    audit = {
        "status": "committed",
        "contract": RECOVERY_CONTRACT,
        "recovery_epoch": RECOVERY_EPOCH,
        "replay_attempt": replay_attempt,
        "prepared_transaction_sha256": prepared_sha,
        "authority_pin": authority_pin,
        "exclusion_sha256": prepared["exclusion_sha256"],
        "exclusion_fingerprint_count": prepared["exclusion_fingerprint_count"],
        "original_budget_sha256": prepared["original_budget_sha256"],
        "quarantined_files": copy.deepcopy(moves),
        "preserved_complete_slots": prepared["preserved_complete_slots"],
        "cursor_reusable": prepared["cursor_reusable"],
        "cursor_reuse_reason": prepared["cursor_reuse_reason"],
        "replacement_budget_initial_sha256": implementation.v49.sha256_file(seed_path),
        "replacement_generator_invocation_limit": int(
            replacement["replacement_generator_invocation_limit"]
        ),
        "new_workload_calls": 0,
        "formal_state_updates": 0,
        "counter_updates": 0,
        "old_partial_artifacts_overwritten": False,
    }
    if audit_path.is_file():
        if implementation.v49.read_json(audit_path) != audit:
            raise implementation.v49.FailClosed("v61 recovery audit changed")
    else:
        implementation.v49.atomic_json(audit_path, audit)
    committed = copy.deepcopy(prepared)
    committed["status"] = "committed"
    committed["prepared_transaction_sha256"] = prepared_sha
    committed["audit_sha256"] = implementation.v49.sha256_file(audit_path)
    implementation.v49.atomic_json(transaction_path, committed)
    return _validate_committed_recovery(committed, fresh_root)


def generate_fresh_discovery_v61(
    cycle_root: Path,
    replay_attempt: int,
    **kwargs: Any,
) -> Path:
    recovery = recover_partial_successor_v61(cycle_root, replay_attempt)
    if recovery is None:
        return _GENERATE_V60(cycle_root, replay_attempt, **kwargs)
    result = _GENERATE_V55(cycle_root, replay_attempt, **kwargs)
    quarantine = v60.ensure_network_quarantine_v60(cycle_root)
    identifiers = implementation.v49.fingerprint_set(result)
    if identifiers & set(quarantine["quarantined_fingerprints"]):
        raise implementation.v49.FailClosed(
            "v61 recovered discovery repeated a quarantined fingerprint"
        )
    return result


def resume_position_v61() -> tuple[Path, int]:
    latest, cycle = _RESUME_V60()
    cycle_root = implementation.v49.ROOT / f"cycle_{cycle:03d}"
    if cycle_root.name == "cycle_002" and cycle_root.is_dir():
        recover_partial_successor_v61(cycle_root, RECOVERY_ATTEMPT)
    return latest, cycle


def require_v61_hashes() -> None:
    _REQUIRE_V60_HASHES()


implementation.generate_fresh_discovery = generate_fresh_discovery_v61
implementation.resume_position_v50 = resume_position_v61
implementation.require_v50_hashes = require_v61_hashes


if __name__ == "__main__":
    try:
        raise SystemExit(implementation.main())
    except implementation.v49.FailClosed as error:
        try:
            implementation.write_controller_state(status="fail_closed", error=str(error))
        except Exception:
            pass
        print(
            f"JOB target10 v61 fail closed: {error}",
            file=implementation.sys.stderr,
            flush=True,
        )
        raise SystemExit(2)
