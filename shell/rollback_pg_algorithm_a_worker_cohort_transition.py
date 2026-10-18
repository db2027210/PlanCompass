#!/usr/bin/env python3
"""Atomically restore an Algorithm A lane to its archived worker domain."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping


TRANSITION_CONTRACT = "algorithm_a_worker_cohort_transition_v1"
ROLLBACK_CONTRACT = "algorithm_a_worker_cohort_transition_rollback_v1"
PROTECTED_FIELDS = (
    "accepted",
    "round_index",
    "ranges",
    "incumbent_params",
    "best_feasible_champion",
    "accepted_candidate_archive",
)
COUNTER_FIELDS = (
    "physical_workload_invocations",
    "valid_replays",
    "technical_failures",
    "restore_technical_failures",
    "out_of_band_invalid_replays",
    "formal_candidate_wave_sequence",
)
FAILED_BASELINE_PHASE = "baseline_technical_failure"


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.rollback.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def worker_keys(config: Mapping[str, Any]) -> List[str]:
    try:
        workers = config["binary_plan_tuning"]["validation"]["docker_workers"][
            "workers"
        ]
    except (KeyError, TypeError) as exc:
        raise RuntimeError("configuration has no Docker worker list") from exc
    if not isinstance(workers, list):
        raise RuntimeError("configuration Docker workers must be a list")
    keys = [str(row.get("name") or "") for row in workers]
    if any(not value for value in keys) or len(keys) != len(set(keys)):
        raise RuntimeError("configuration worker names must be non-empty and unique")
    return keys


def find_evidence_archive(
    state: Mapping[str, Any], transition_id: str
) -> Mapping[str, Any]:
    matches = [
        row
        for row in (state.get("execution_domain_evidence_epoch_archives") or [])
        if isinstance(row, Mapping)
        and row.get("contract") == TRANSITION_CONTRACT
        and str(row.get("transition_id") or "") == transition_id
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one evidence epoch archive for {transition_id}, got {len(matches)}"
        )
    return matches[0]


def validate_failed_parallel_baseline(
    state: Mapping[str, Any], transition: Mapping[str, Any]
) -> None:
    """Require a fully checkpointed failed baseline before cohort rollback."""

    if str(transition.get("status") or "") != "fresh_baseline_pending":
        raise RuntimeError("failed baseline rollback requires fresh_baseline_pending")
    target_workers = {
        str(value) for value in (transition.get("to_worker_keys") or [])
    }
    context = state.get("baseline_pending_context")
    rows = state.get("baseline_pending_rows")
    if not isinstance(context, Mapping) or not isinstance(rows, list):
        raise RuntimeError("failed baseline checkpoint is missing")
    repetitions = int(context.get("repetitions_per_worker") or 0)
    context_workers = {
        str(value) for value in (context.get("worker_keys") or [])
    }
    if repetitions != 2 or context_workers != target_workers:
        raise RuntimeError("failed baseline checkpoint domain mismatch")
    expected_slots = {
        (repetition, worker)
        for repetition in range(1, repetitions + 1)
        for worker in target_workers
    }
    observed_slots = {
        (
            int(row.get("baseline_repetition") or 0),
            str(row.get("worker_key") or ""),
        )
        for row in rows
        if isinstance(row, Mapping)
    }
    if len(rows) != len(expected_slots) or observed_slots != expected_slots:
        raise RuntimeError("failed baseline is not fully checkpointed")
    if not all(
        int(row.get("physical_workload_invocations") or 0) == 1
        for row in rows
        if isinstance(row, Mapping)
    ):
        raise RuntimeError("failed baseline contains an unaccountable physical row")
    if not any(
        row.get("technical_failure") is True
        for row in rows
        if isinstance(row, Mapping)
    ):
        raise RuntimeError("baseline_technical_failure has no technical row")


def last_clean_restore_event(log_path: Path) -> Mapping[str, Any]:
    last_event: Mapping[str, Any] | None = None
    with log_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            try:
                row = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if row.get("event") == "algorithm_c_clean_baseline_restored":
                last_event = row
    if last_event is None:
        raise RuntimeError("clean baseline restore evidence is missing")
    return last_event


def assert_no_live_lane_controller(lane_dir: Path) -> None:
    needles = (
        f"--output-dir\x00{lane_dir}",
        f"--config\x00{lane_dir / 'optimizer_config.json'}",
    )
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            command = (entry / "cmdline").read_bytes().decode(
                "utf-8", errors="replace"
            )
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if any(needle in command for needle in needles):
            raise RuntimeError(
                f"lane controller is still live: pid={entry.name}"
            )


def build_rollback(
    *,
    state: Mapping[str, Any],
    current_config: Mapping[str, Any],
    prior_config: Mapping[str, Any],
    transition_archive: Mapping[str, Any],
    transition_id: str,
    now: float,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    transition = state.get("execution_domain_transition")
    if not isinstance(transition, Mapping):
        raise RuntimeError("state has no active worker cohort transition")
    if (
        transition.get("contract") != TRANSITION_CONTRACT
        or str(transition.get("transition_id") or "") != transition_id
    ):
        raise RuntimeError("active worker cohort transition identity mismatch")
    if state.get("algorithm_a_round_checkpoint") is not None:
        raise RuntimeError("rollback requires a completed Algorithm A round boundary")
    if state.get("inflight_physical_wave") is not None:
        raise RuntimeError("rollback is forbidden while a physical wave is in flight")
    phase = str(state.get("phase") or "")
    if phase not in {
        "stopped_by_signal_clean",
        "execution_domain_transition_technical_retry",
        FAILED_BASELINE_PHASE,
    }:
        raise RuntimeError(f"rollback phase is unsafe: {state.get('phase')}")
    if phase == FAILED_BASELINE_PHASE:
        validate_failed_parallel_baseline(state, transition)

    if transition_archive.get("contract") != TRANSITION_CONTRACT:
        raise RuntimeError("transition archive contract mismatch")
    if str(transition_archive.get("transition_id") or "") != transition_id:
        raise RuntimeError("transition archive identity mismatch")
    prior_domain = transition_archive.get("prior_execution_domain")
    protected_archive = transition_archive.get("protected_semantic_state")
    if not isinstance(prior_domain, Mapping) or not isinstance(
        protected_archive, Mapping
    ):
        raise RuntimeError("transition archive is incomplete")
    if canonical_sha256(prior_domain) != str(
        transition_archive.get("prior_execution_domain_sha256") or ""
    ):
        raise RuntimeError("prior execution domain SHA mismatch")

    protected_current = {
        key: copy.deepcopy(state.get(key)) for key in PROTECTED_FIELDS
    }
    protected_sha = canonical_sha256(protected_current)
    expected_protected_sha = str(
        transition.get("protected_semantic_state_sha256") or ""
    )
    if protected_sha != expected_protected_sha:
        raise RuntimeError("accepted/ranges/champion state changed during transition")
    if canonical_sha256(protected_archive) != expected_protected_sha:
        raise RuntimeError("archived protected semantic state SHA mismatch")

    from_keys = sorted(str(value) for value in transition.get("from_worker_keys") or [])
    to_keys = sorted(str(value) for value in transition.get("to_worker_keys") or [])
    if sorted(worker_keys(current_config)) != to_keys:
        raise RuntimeError("current configuration is not the failed target cohort")
    if sorted(worker_keys(prior_config)) != from_keys:
        raise RuntimeError("prior configuration is not the archived source cohort")

    evidence_archive = find_evidence_archive(state, transition_id)
    prior_identity = evidence_archive.get("prior_valid_replay_identity_memory")
    prior_pruning = evidence_archive.get("prior_outer_accept_reject_pruning")
    if not isinstance(prior_identity, Mapping) or not isinstance(
        prior_pruning, Mapping
    ):
        raise RuntimeError("prior evidence memories are missing")
    if canonical_sha256(prior_identity) != str(
        evidence_archive.get("prior_valid_replay_identity_memory_sha256") or ""
    ):
        raise RuntimeError("prior valid replay identity memory SHA mismatch")
    if canonical_sha256(prior_pruning) != str(
        evidence_archive.get("prior_outer_accept_reject_pruning_sha256") or ""
    ):
        raise RuntimeError("prior outer pruning memory SHA mismatch")

    counters_before = {
        key: copy.deepcopy(state.get(key)) for key in COUNTER_FIELDS
    }
    next_state = copy.deepcopy(dict(state))
    for key, value in prior_domain.items():
        next_state[key] = copy.deepcopy(value)
    next_state["valid_replay_identity_memory"] = copy.deepcopy(dict(prior_identity))
    next_state["outer_accept_reject_pruning"] = copy.deepcopy(dict(prior_pruning))
    next_state.pop("pending_execution_domain_evidence_epoch_transition", None)
    next_state.pop("valid_replay_identity_epoch_start_s", None)
    next_state.pop("outer_pruning_epoch_start_s", None)
    failed_transition = copy.deepcopy(dict(transition))
    failed_transition["status"] = "rolled_back_after_target_cohort_failure"
    failed_transition["rolled_back_at_epoch_s"] = now
    failed_transition["failed_target_domain_state_sha256"] = canonical_sha256(state)
    rollback_history = next_state.get("execution_domain_transition_rollbacks")
    rollback_history = rollback_history if isinstance(rollback_history, list) else []
    rollback_history.append(failed_transition)
    next_state["execution_domain_transition_rollbacks"] = rollback_history
    next_state.pop("execution_domain_transition", None)
    next_state.pop("baseline_pending_rows", None)
    next_state.pop("baseline_pending_context", None)
    next_state["phase"] = "stopped_by_signal_clean"
    next_state["updated_at_epoch_s"] = now

    protected_after = {
        key: copy.deepcopy(next_state.get(key)) for key in PROTECTED_FIELDS
    }
    counters_after = {
        key: copy.deepcopy(next_state.get(key)) for key in COUNTER_FIELDS
    }
    if canonical_sha256(protected_after) != protected_sha:
        raise RuntimeError("rollback mutated accepted/ranges/champion state")
    if counters_after != counters_before:
        raise RuntimeError("rollback mutated physical/valid/technical counters")

    manifest = {
        "contract": ROLLBACK_CONTRACT,
        "transition_id": transition_id,
        "rolled_back_at_epoch_s": now,
        "from_failed_worker_keys": to_keys,
        "to_restored_worker_keys": from_keys,
        "failed_target_domain_state_sha256": canonical_sha256(state),
        "restored_state_sha256": canonical_sha256(next_state),
        "prior_config_sha256": canonical_sha256(prior_config),
        "protected_semantic_state_sha256": protected_sha,
        "counters_preserved": counters_after,
        "accepted_unchanged": True,
        "ranges_unchanged": True,
        "champion_history_unchanged": True,
        "technical_failures_preserved": True,
        "mysql_untouched": True,
    }
    return next_state, manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lane-dir", required=True, type=Path)
    parser.add_argument("--prior-config", required=True, type=Path)
    parser.add_argument("--transition-id", required=True)
    args = parser.parse_args()

    lane_dir = args.lane_dir.resolve()
    state_path = lane_dir / "algorithm_a_state.json"
    config_path = lane_dir / "optimizer_config.json"
    request_path = lane_dir / "algorithm_a_worker_cohort_transition.json"
    state = read_json(state_path)
    current_config = read_json(config_path)
    prior_config = read_json(args.prior_config.resolve())
    transition = state.get("execution_domain_transition")
    if not isinstance(transition, Mapping):
        raise SystemExit("active transition is missing")
    archive_path = Path(str(transition.get("archive_path") or "")).resolve()
    transition_archive = read_json(archive_path)
    expected_archive_sha = str(transition.get("archive_sha256") or "")
    if canonical_sha256(transition_archive) != expected_archive_sha:
        raise SystemExit("transition archive SHA mismatch")

    if not (lane_dir / ".algorithm_a_watchdog_pause").is_file():
        raise SystemExit("watchdog pause sentinel is required for rollback")
    assert_no_live_lane_controller(lane_dir)
    if str(state.get("phase") or "") == FAILED_BASELINE_PHASE:
        restore_event = last_clean_restore_event(lane_dir / "algorithm_a.jsonl")
        if (
            restore_event.get("reason") != FAILED_BASELINE_PHASE
            or restore_event.get("passed") is not True
        ):
            raise SystemExit("latest clean restore does not prove baseline recovery")

    now = time.time()
    next_state, manifest = build_rollback(
        state=state,
        current_config=current_config,
        prior_config=prior_config,
        transition_archive=transition_archive,
        transition_id=args.transition_id,
        now=now,
    )
    evidence_dir = lane_dir / "execution_domain_transitions"
    failed_state_path = evidence_dir / (
        f"{args.transition_id}_failed_target_state.json"
    )
    failed_config_path = evidence_dir / (
        f"{args.transition_id}_failed_target_config.json"
    )
    if failed_state_path.exists() or failed_config_path.exists():
        raise SystemExit("failed target state/config archive already exists")
    atomic_write_json(failed_state_path, state)
    atomic_write_json(failed_config_path, current_config)
    manifest["failed_target_state_path"] = str(failed_state_path)
    manifest["failed_target_config_path"] = str(failed_config_path)
    manifest["failed_target_config_sha256"] = canonical_sha256(current_config)
    failed_request_path = lane_dir / "execution_domain_transitions" / (
        f"{args.transition_id}_failed_request.json"
    )
    if request_path.exists():
        if failed_request_path.exists():
            raise SystemExit(f"failed request archive already exists: {failed_request_path}")
        request_path.replace(failed_request_path)

    atomic_write_json(config_path, prior_config)
    atomic_write_json(state_path, next_state)
    manifest["state_path"] = str(state_path)
    manifest["config_path"] = str(config_path)
    manifest["failed_request_archive"] = str(failed_request_path)
    manifest_path = evidence_dir / (
        f"{args.transition_id}_rollback_manifest.json"
    )
    atomic_write_json(manifest_path, manifest)
    log_path = lane_dir / "algorithm_a.jsonl"
    event = {
        "event": "algorithm_a_worker_cohort_transition_rolled_back",
        "time": now,
        "algorithm_variant": "A",
        "formal_state_mutation": False,
        **manifest,
    }
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
