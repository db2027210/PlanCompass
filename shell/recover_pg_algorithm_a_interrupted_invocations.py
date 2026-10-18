#!/usr/bin/env python3
"""Recover Algorithm A invocations interrupted before controller accounting.

This utility is intentionally narrow and fail-closed.  It never reconstructs a
successful candidate result from benchmark-side files.  A physically started
candidate that lacks an authoritative controller terminal row is recorded as a
technical failure, while completed baseline rows already present in the
append-only controller log may be checkpointed for crash-resumable reuse.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from binary_plan_resource_tuner import validation_workers
from shell import run_pg_algorithm_c_scene as framework


RECOVERY_CONTRACT = "algorithm_a_interrupted_invocation_recovery_v1"


def read_events(path: Path) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            try:
                value = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"invalid JSONL at {path}:{line_number}: {exc}"
                ) from exc
            if not isinstance(value, dict):
                raise RuntimeError(f"non-object JSONL row at {path}:{line_number}")
            events.append(value)
    return events


def recovery_already_applied(state: Mapping[str, Any], recovery_id: str) -> bool:
    recoveries = state.get("interrupted_invocation_recoveries") or []
    return any(
        isinstance(row, Mapping) and row.get("recovery_id") == recovery_id
        for row in recoveries
    )


def commit_recovery(
    *,
    state_path: Path,
    log_path: Path,
    state: Dict[str, Any],
    recovery: Dict[str, Any],
    terminal_events: Sequence[Mapping[str, Any]],
    apply: bool,
) -> Dict[str, Any]:
    recovery_id = str(recovery["recovery_id"])
    if recovery_already_applied(state, recovery_id):
        return {"status": "already_applied", "recovery": recovery}
    if not apply:
        return {"status": "dry_run", "recovery": recovery}

    # Journal first.  If the process stops between log and final-state commit,
    # a rerun can see the durable journal and fail closed instead of replaying.
    state["interrupted_invocation_recovery_journal"] = copy.deepcopy(recovery)
    framework.write_json(state_path, state)
    for event in terminal_events:
        framework.append_jsonl(log_path, event)

    counts = recovery["accounting"]
    for key in (
        "physical_workload_invocations",
        "valid_replays",
        "technical_failures",
    ):
        state[key] = int(state.get(key) or 0) + int(counts.get(key) or 0)
    state.setdefault("interrupted_invocation_recoveries", []).append(
        copy.deepcopy(recovery)
    )
    state.pop("interrupted_invocation_recovery_journal", None)
    framework.write_json(state_path, state)
    framework.append_jsonl(
        log_path,
        {
            "event": "algorithm_a_interrupted_invocation_recovery_committed",
            "time": time.time(),
            "contract": RECOVERY_CONTRACT,
            "recovery": copy.deepcopy(recovery),
            "accepted_unchanged": True,
            "incumbent_unchanged": True,
            "ranges_unchanged": True,
            "technical_failures_excluded_from_valid_and_acceptance": True,
        },
    )
    return {"status": "applied", "recovery": recovery}


def events_after_latest(
    events: Sequence[Mapping[str, Any]], event_name: str
) -> List[Mapping[str, Any]]:
    indices = [
        index for index, event in enumerate(events)
        if event.get("event") == event_name
    ]
    if not indices:
        raise RuntimeError(f"required event is absent: {event_name}")
    return list(events[indices[-1]:])


def recover_job_baseline(output_dir: Path, *, apply: bool) -> Dict[str, Any]:
    state_path = output_dir / "algorithm_a_state.json"
    log_path = output_dir / "algorithm_a.jsonl"
    cfg_path = output_dir / "optimizer_config.json"
    state = framework.read_json(state_path)
    cfg = framework.read_json(cfg_path)
    recovery_id = "job_workers4_baseline_interrupted_20260819_1433"
    if recovery_already_applied(state, recovery_id):
        return {"status": "already_applied", "recovery_id": recovery_id}
    if state.get("phase") != "fresh_parallel_baseline":
        raise RuntimeError(
            f"JOB recovery requires fresh_parallel_baseline, got {state.get('phase')!r}"
        )
    if state.get("baseline_pending_rows"):
        raise RuntimeError("JOB baseline already has pending rows")

    events = read_events(log_path)
    latest = events_after_latest(events, "algorithm_c_parallel_baseline_start")
    completed_rows: List[Dict[str, Any]] = []
    for event in latest:
        if event.get("event") != "algorithm_c_baseline_worker_validation":
            continue
        validation = event.get("validation")
        if not isinstance(validation, Mapping):
            continue
        if int(validation.get("baseline_repetition") or 0) != 1:
            continue
        completed_rows.append(copy.deepcopy(dict(validation)))
    workers = validation_workers(cfg, "memory")
    worker_keys = sorted(framework.worker_key(worker) for worker in workers)
    row_keys = sorted(str(row.get("worker_key") or "") for row in completed_rows)
    if len(completed_rows) != 4 or row_keys != worker_keys:
        raise RuntimeError(
            f"JOB recovery expected four repetition-1 rows: rows={row_keys} workers={worker_keys}"
        )
    if not all(framework.physical_valid(row) for row in completed_rows):
        raise RuntimeError("JOB recovered repetition-1 baseline contains an invalid row")

    interrupted_labels: List[str] = []
    for worker in workers:
        key = framework.worker_key(worker)
        pattern = (
            output_dir / "baseline" / key / "hybench_logs"
        ).glob(f"algorithm_c_baseline_*_rep02_{key}/progress.jsonl")
        matches = list(pattern)
        if len(matches) != 1:
            raise RuntimeError(
                f"JOB recovery expected one repetition-2 progress file for {key}: {matches}"
            )
        progress_events = read_events(matches[0])
        start_indices = [
            index for index, row in enumerate(progress_events)
            if row.get("event") == "hybench_start"
        ]
        if not start_indices:
            raise RuntimeError(f"JOB interruption proof lacks a start for {key}")
        latest_attempt = progress_events[start_indices[-1]:]
        starts = [row for row in latest_attempt if row.get("event") == "hybench_start"]
        terminals = [
            row for row in latest_attempt
            if row.get("event") in {"validation_done", "hybench_done"}
        ]
        if len(starts) != 1 or terminals:
            raise RuntimeError(
                f"JOB interruption proof failed for {key}: starts={len(starts)} terminals={len(terminals)}"
            )
        interrupted_labels.append(str(starts[0].get("label") or ""))

    workload_contract = (cfg.get("algorithm_c") or {}).get("workload_contract") or {}
    state["baseline_pending_rows"] = copy.deepcopy(completed_rows)
    state["baseline_pending_context"] = {
        "contract": "algorithm_c_crash_resumable_parallel_baseline_v1",
        "worker_keys": worker_keys,
        "repetitions_per_worker": 2,
        "workload_contract_sha256": framework.canonical_sha256(workload_contract),
        "global_restore_parallelism": 4,
    }
    recovery = {
        "contract": RECOVERY_CONTRACT,
        "recovery_id": recovery_id,
        "lane": "job_memory",
        "kind": "parallel_baseline",
        "completed_valid_labels": sorted(str(row.get("label") or "") for row in completed_rows),
        "interrupted_technical_labels": sorted(interrupted_labels),
        "accounting": {
            "physical_workload_invocations": 8,
            "valid_replays": 4,
            "technical_failures": 4,
        },
        "formal_state_mutation": False,
        "baseline_pending_rows_checkpointed": 4,
    }
    terminal_events = [
        {
            "event": "algorithm_c_baseline_interrupted_invocations_recovered",
            "time": time.time(),
            "scene": "job_memory",
            "target": "memory",
            "contract": RECOVERY_CONTRACT,
            "recovery_id": recovery_id,
            "labels": sorted(interrupted_labels),
            "physical_workload_invocations": 4,
            "valid_replays": 0,
            "technical_failures": 4,
            "accepted_unchanged": True,
            "incumbent_unchanged": True,
            "ranges_unchanged": True,
        }
    ]
    return commit_recovery(
        state_path=state_path,
        log_path=log_path,
        state=state,
        recovery=recovery,
        terminal_events=terminal_events,
        apply=apply,
    )


def candidate_terminal_signatures(
    events: Iterable[Mapping[str, Any]],
) -> set[tuple[str, ...]]:
    signatures: set[tuple[str, ...]] = set()
    for event in events:
        if event.get("event") != "algorithm_c_candidate_replay":
            continue
        validation = event.get("validation")
        candidate = validation.get("candidate_ranking") if isinstance(validation, Mapping) else None
        if isinstance(candidate, Mapping):
            signatures.add(
                framework.exact_param_signature(candidate.get("optimized_params") or {})
            )
    return signatures


def recover_tpch_candidates(output_dir: Path, *, apply: bool) -> Dict[str, Any]:
    state_path = output_dir / "algorithm_a_state.json"
    log_path = output_dir / "algorithm_a.jsonl"
    state = framework.read_json(state_path)
    recovery_id = "tpch_round4_candidates23_24_interrupted_20260819_1435"
    if recovery_already_applied(state, recovery_id):
        return {"status": "already_applied", "recovery_id": recovery_id}
    checkpoint = state.get("algorithm_a_round_checkpoint")
    if not isinstance(checkpoint, Mapping) or int(checkpoint.get("round_index") or 0) != 4:
        raise RuntimeError("TPC-H recovery requires the resumable round-4 checkpoint")
    context_sha = str(checkpoint.get("context_sha256") or "")
    artifacts = list(
        (output_dir / "offline_factorial_candidates").glob(
            f"accepted_{int(state.get('accepted') or 0):02d}_{context_sha[:16]}.json"
        )
    )
    if len(artifacts) != 1:
        raise RuntimeError(f"TPC-H recovery ranking artifact mismatch: {artifacts}")
    artifact = framework.read_json(artifacts[0])
    rankings = artifact.get("candidates")
    if not isinstance(rankings, list) or len(rankings) != 128:
        raise RuntimeError("TPC-H recovery requires the complete 128-candidate artifact")
    selected = [copy.deepcopy(rankings[index - 1]) for index in (23, 24)]
    events = read_events(log_path)
    terminal = candidate_terminal_signatures(events)
    if any(
        framework.exact_param_signature(candidate.get("optimized_params") or {}) in terminal
        for candidate in selected
    ):
        raise RuntimeError("TPC-H interrupted candidate already has a terminal controller row")

    terminal_events: List[Dict[str, Any]] = []
    labels: List[str] = []
    for rank, candidate in zip((23, 24), selected):
        matches = list(
            (
                output_dir
                / "replays"
                / "round_004"
                / "algorithm_a_full_factorial"
                / "hybench_logs"
            ).glob(f"algorithm_c_r004_algorithm_a_full_factorial_c{rank:03d}_*/progress.jsonl")
        )
        if len(matches) != 1:
            raise RuntimeError(f"TPC-H interruption progress mismatch for rank {rank}: {matches}")
        progress_events = read_events(matches[0])
        starts = [row for row in progress_events if row.get("event") == "hybench_start"]
        if len(starts) != 1:
            raise RuntimeError(f"TPC-H rank {rank} lacks one physical-start marker")
        label = str(starts[0].get("label") or "")
        worker = str(starts[0].get("worker") or "")
        labels.append(label)
        validation = {
            "status": "failed",
            "technical_failure": True,
            "technical_failure_reason": (
                "controller_process_terminated_before_authoritative_wave_completion"
            ),
            "used_real_replay": True,
            "physical_workload_invocations": 1,
            "valid_replay": False,
            "label": label,
            "worker_key": worker,
            "candidate_ranking": candidate,
            "workload_completeness": {
                "passed": False,
                "errors": ["controller_terminated_before_authoritative_wave_completion"],
            },
            "interrupted_invocation_recovery": {
                "contract": RECOVERY_CONTRACT,
                "recovery_id": recovery_id,
                "benchmark_side_terminal_observed": any(
                    row.get("event") == "validation_done" for row in progress_events
                ),
                "excluded_from_valid_and_acceptance": True,
            },
        }
        terminal_events.append(
            {
                "event": "algorithm_c_candidate_replay",
                "time": time.time(),
                "scene": "tpch_memory",
                "target": "memory",
                "algorithm_variant": "A",
                "round_index": 4,
                "batch_kind": "algorithm_a_full_factorial",
                "physical_slot": rank,
                "validation": validation,
                "recovery_contract": RECOVERY_CONTRACT,
                "recovery_id": recovery_id,
            }
        )
    recovery = {
        "contract": RECOVERY_CONTRACT,
        "recovery_id": recovery_id,
        "lane": "tpch_memory",
        "kind": "candidate_wave",
        "round_index": 4,
        "candidate_ranks": [23, 24],
        "labels": labels,
        "accounting": {
            "physical_workload_invocations": 2,
            "valid_replays": 0,
            "technical_failures": 2,
        },
        "formal_state_mutation": False,
    }
    return commit_recovery(
        state_path=state_path,
        log_path=log_path,
        state=state,
        recovery=recovery,
        terminal_events=terminal_events,
        apply=apply,
    )


def recover_logged_unaccounted_wave(
    output_dir: Path,
    *,
    lane: str,
    target: str,
    round_index: int,
    ranks: Sequence[int],
    recovery_id: str,
    apply: bool,
) -> Dict[str, Any]:
    """Account terminal controller rows skipped by an old early-return path."""

    state_path = output_dir / "algorithm_a_state.json"
    log_path = output_dir / "algorithm_a.jsonl"
    state = framework.read_json(state_path)
    if recovery_already_applied(state, recovery_id):
        return {"status": "already_applied", "recovery_id": recovery_id}
    events = read_events(log_path)
    rows_by_rank: Dict[int, Dict[str, Any]] = {}
    for event in events:
        if event.get("event") != "algorithm_c_candidate_replay":
            continue
        if int(event.get("round_index") or 0) != round_index:
            continue
        validation = event.get("validation")
        candidate = validation.get("candidate_ranking") if isinstance(validation, Mapping) else None
        if not isinstance(candidate, Mapping):
            continue
        rank = int(candidate.get("rank") or 0)
        label = str(validation.get("label") or "")
        if rank in ranks and f"_c{rank:03d}_" in label:
            rows_by_rank.setdefault(rank, copy.deepcopy(dict(validation)))
    if sorted(rows_by_rank) != sorted(ranks):
        raise RuntimeError(
            f"logged-wave recovery rows mismatch: expected={sorted(ranks)} actual={sorted(rows_by_rank)}"
        )
    rows = [rows_by_rank[rank] for rank in ranks]
    if not all(framework.physical_valid(row) for row in rows):
        raise RuntimeError("logged-wave recovery requires terminal valid rows")
    recovery = {
        "contract": RECOVERY_CONTRACT,
        "recovery_id": recovery_id,
        "lane": lane,
        "kind": "logged_terminal_wave_accounting",
        "candidate_ranks": list(ranks),
        "round_index": round_index,
        "labels": [str(row.get("label") or "") for row in rows],
        "accounting": {
            "physical_workload_invocations": len(rows),
            "valid_replays": len(rows),
            "technical_failures": 0,
        },
        "formal_state_mutation": False,
        "reason": "legacy_replay_batch_early_return_before_batch_accounting",
    }
    return commit_recovery(
        state_path=state_path,
        log_path=log_path,
        state=state,
        recovery=recovery,
        terminal_events=[],
        apply=apply,
    )


def recover_xp_cpu_interrupted_wave(
    output_dir: Path,
    *,
    apply: bool,
) -> Dict[str, Any]:
    """Record the XP-CPU 63/64 wave killed by the systemd stop timeout."""

    state_path = output_dir / "algorithm_a_state.json"
    log_path = output_dir / "algorithm_a.jsonl"
    state = framework.read_json(state_path)
    recovery_id = "xp_cpu_round7_candidates63_64_sigkill_20260819"
    if recovery_already_applied(state, recovery_id):
        return {"status": "already_applied", "recovery_id": recovery_id}
    checkpoint = state.get("algorithm_a_round_checkpoint")
    if not isinstance(checkpoint, Mapping) or int(checkpoint.get("round_index") or 0) != 7:
        raise RuntimeError("XP-CPU recovery requires the resumable round-7 checkpoint")
    context_sha = str(checkpoint.get("context_sha256") or "")
    artifacts = list(
        (output_dir / "offline_factorial_candidates").glob(
            f"accepted_{int(state.get('accepted') or 0):02d}_{context_sha[:16]}.json"
        )
    )
    if len(artifacts) != 1:
        raise RuntimeError(f"XP-CPU ranking artifact mismatch: {artifacts}")
    rankings = framework.read_json(artifacts[0]).get("candidates")
    if not isinstance(rankings, list) or len(rankings) != 128:
        raise RuntimeError("XP-CPU recovery requires the complete 128-candidate artifact")
    events = read_events(log_path)
    terminal = candidate_terminal_signatures(events)
    terminal_events: List[Dict[str, Any]] = []
    labels: List[str] = []
    for rank in (63, 64):
        candidate = copy.deepcopy(rankings[rank - 1])
        signature = framework.exact_param_signature(
            candidate.get("optimized_params") or {}
        )
        if signature in terminal:
            raise RuntimeError(f"XP-CPU rank {rank} already has a terminal row")
        matches = list(
            (
                output_dir
                / "replays"
                / "round_007"
                / "algorithm_a_full_factorial"
                / "hybench_logs"
            ).glob(f"algorithm_c_r007_algorithm_a_full_factorial_c{rank:03d}_*/progress.jsonl")
        )
        if len(matches) != 1:
            raise RuntimeError(f"XP-CPU progress mismatch for rank {rank}: {matches}")
        progress = read_events(matches[0])
        starts = [row for row in progress if row.get("event") == "hybench_start"]
        terminals = [row for row in progress if row.get("event") == "validation_done"]
        if len(starts) != 1 or terminals:
            raise RuntimeError(
                f"XP-CPU interruption proof failed for rank {rank}: "
                f"starts={len(starts)} terminals={len(terminals)}"
            )
        label = str(starts[0].get("label") or "")
        labels.append(label)
        validation = {
            "status": "failed",
            "technical_failure": True,
            "technical_failure_reason": "systemd_stop_timeout_sigkill_during_workload",
            "used_real_replay": True,
            "physical_workload_invocations": 1,
            "valid_replay": False,
            "label": label,
            "worker_key": str(starts[0].get("worker") or ""),
            "candidate_ranking": candidate,
            "workload_completeness": {
                "passed": False,
                "errors": ["systemd_stop_timeout_sigkill_during_workload"],
            },
            "interrupted_invocation_recovery": {
                "contract": RECOVERY_CONTRACT,
                "recovery_id": recovery_id,
                "excluded_from_valid_and_acceptance": True,
            },
        }
        terminal_events.append(
            {
                "event": "algorithm_c_candidate_replay",
                "time": time.time(),
                "scene": "xp_cpu",
                "target": "cpu",
                "algorithm_variant": "A",
                "round_index": 7,
                "batch_kind": "algorithm_a_full_factorial",
                "physical_slot": rank,
                "validation": validation,
                "recovery_contract": RECOVERY_CONTRACT,
                "recovery_id": recovery_id,
            }
        )
    recovery = {
        "contract": RECOVERY_CONTRACT,
        "recovery_id": recovery_id,
        "lane": "xp_cpu",
        "kind": "candidate_wave",
        "round_index": 7,
        "candidate_ranks": [63, 64],
        "labels": labels,
        "accounting": {
            "physical_workload_invocations": 2,
            "valid_replays": 0,
            "technical_failures": 2,
        },
        "formal_state_mutation": False,
    }
    return commit_recovery(
        state_path=state_path,
        log_path=log_path,
        state=state,
        recovery=recovery,
        terminal_events=terminal_events,
        apply=apply,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-root", required=True, type=Path)
    parser.add_argument(
        "--lane",
        required=True,
        choices=("job_memory", "tpch_memory", "xp_cpu"),
    )
    parser.add_argument(
        "--mode",
        choices=("interrupted", "logged-wave"),
        default="interrupted",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    output_dir = args.campaign_root / args.lane
    if args.mode == "logged-wave" and args.lane == "tpch_memory":
        result = recover_logged_unaccounted_wave(
            output_dir,
            lane="tpch_memory",
            target="memory",
            round_index=4,
            ranks=(25, 26),
            recovery_id="tpch_round4_candidates25_26_logged_unaccounted_20260819",
            apply=args.apply,
        )
    elif args.mode == "logged-wave" and args.lane == "xp_cpu":
        result = recover_logged_unaccounted_wave(
            output_dir,
            lane="xp_cpu",
            target="cpu",
            round_index=7,
            ranks=(61, 62),
            recovery_id="xp_cpu_round7_candidates61_62_logged_unaccounted_20260819",
            apply=args.apply,
        )
    elif args.mode == "logged-wave":
        raise RuntimeError(f"logged-wave mode is unsupported for {args.lane}")
    elif args.lane == "job_memory":
        result = recover_job_baseline(output_dir, apply=args.apply)
    elif args.lane == "tpch_memory":
        result = recover_tpch_candidates(output_dir, apply=args.apply)
    elif args.lane == "xp_cpu":
        result = recover_xp_cpu_interrupted_wave(output_dir, apply=args.apply)
    else:
        raise RuntimeError(f"interrupted mode is unsupported for {args.lane}")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
