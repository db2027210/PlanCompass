#!/usr/bin/env python3
"""Read-only audit of the JOB formal004 -> autonomous v49 handoff boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
from typing import Any, Mapping


DEFAULT_B = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "job_node02/history/job/outer/attempt_7_cap5_contract_20260726"
)
TARGET_ACCEPTED = 10
HISTORICAL_EXCLUDED_ACCEPTED = 2

DEPENDENCY_HASHES = {
    "/tmp/job_fresh10_target10_autonomous_v49.py": (
        "3688f00b0a32c490f5dd3781b26642f48361c3ba541256f9855730284016035c"
    ),
    "/tmp/launch_job_target10_autonomous_v49_after_formal004.sh": (
        "380b078d53b4ed3ba737a979e5ddde18b2fee05c328027ae073e811523ff1329"
    ),
    "/tmp/job_exact_regime_successor_expand.py": (
        "a03c2264cdfe2b26a8bb4a5bcbc5648498ce68020806d6d4a84e3cb592b51da5"
    ),
    "/tmp/build_job_exact_plan_repeat_validation.py": (
        "89c3a2238830087c129cadee76cd5a28248d87ce10b0fd98c6d5f9cb62bdd6cf"
    ),
    "/tmp/evaluate_job_clean5_gate.py": (
        "72700f5169ca7ba3bd1b527397ac179036ab8bebebb5fb7e977f5be4de2b558e"
    ),
    "/tmp/build_job_three_observation_formal_selection_v1.py": (
        "40c528bb43ecd6b31987daa6d0dec2704fde93da11d510261d854d9eceb532a7"
    ),
    "/tmp/run_job_round8_strict_serial_robust_v7.py": (
        "9251d7fa68276113656826bd6df1777c7b7dfe71b42d012674fbd19ca4b2f09c"
    ),
    "/tmp/run_job_round8_strict_serial_robust_v8_apply_failure.py": (
        "5d965cd9410fba78486856dc2d78bd594b9ebf1d17eda4a8f593a5f43d6fa857"
    ),
    "/tmp/job_formal_target10_batch_controller.py": (
        "3c9246b67a7fbaf5e28115efc59e4ac68066fc96e6515b6524099ca804c69668"
    ),
}
BASE_RUNNER_SHA = "9d033a7592307cca042b9b4c70057f8ed54394a576162000592d3605b46594c1"
CONTRACT_SHA = "a5ae7fddcfcd16e1715e35cc5a67c3d3d135b2542ff0e132a4cca5817afddf05"

ACTIVE_JOB_MARKERS = (
    "launch_job_fresh10_v45_diagnostic_probe004",
    "watch_job_fresh10_v46_repeat_gate_formal004",
    "run_job_fresh10_successor004_autonomous_v47",
    "run_job_round8_strict_serial",
    "job_formal_target10_batch_controller",
    "formal_pg_point_runner",
    "job_fresh10_target10_autonomous_v49",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def read_counter(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(raw) if raw.isdigit() else None


def active_job_processes(proc_root: Path = Path("/proc")) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    own_pid = str(__import__("os").getpid())
    try:
        children = list(proc_root.iterdir())
    except OSError:
        return [{"pid": None, "command": None, "error": "proc_unreadable"}]
    for child in children:
        if not child.name.isdigit() or child.name == own_pid:
            continue
        try:
            command = (child / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", errors="replace"
            ).strip()
        except OSError:
            continue
        if command and any(marker in command for marker in ACTIVE_JOB_MARKERS):
            result.append({"pid": int(child.name), "command": command})
    return sorted(result, key=lambda item: int(item.get("pid") or -1))


def dependency_audit(
    expected: Mapping[str, str],
) -> tuple[bool, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    passed = True
    for raw_path, expected_sha in expected.items():
        path = Path(raw_path)
        observed_sha = sha256_file(path) if path.is_file() else None
        matched = observed_sha == expected_sha
        passed = passed and matched
        rows.append(
            {
                "path": str(path),
                "expected_sha256": expected_sha,
                "observed_sha256": observed_sha,
                "matched": matched,
            }
        )
    return passed, rows


def live_replay_progress(
    events_path: Path,
    selection: Mapping[str, Any] | None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    parse_errors: list[int] = []
    try:
        lines = events_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            parse_errors.append(line_number)
            continue
        if isinstance(value, dict):
            rows.append(value)

    def ranks(event_name: str) -> list[int]:
        return sorted(
            {
                int(row["rank"])
                for row in rows
                if row.get("event") == event_name and row.get("rank") is not None
            }
        )

    started = ranks("job_round8_candidate_invocation_started")
    successful = ranks("job_round8_candidate_replay_complete")
    technical = ranks("job_round8_candidate_technical_failure")
    finished = sorted(set(successful) | set(technical))
    in_flight = sorted(set(started) - set(finished))
    provisional_accepted = sorted(
        {
            int(row["rank"])
            for row in rows
            if row.get("event") == "job_round8_candidate_replay_complete"
            and row.get("accepted") is True
            and row.get("rank") is not None
        }
    )
    top5 = selection.get("top5") if selection else None
    trusted_prior: list[dict[str, Any]] = []
    trusted_ranks: set[int] = set()
    if isinstance(top5, list):
        for candidate in top5:
            if (
                not isinstance(candidate, dict)
                or candidate.get("selection_role") != "trusted"
            ):
                continue
            rank = int(candidate.get("rank", -1))
            observations = (
                (candidate.get("robust_acceptance_policy") or {}).get(
                    "prior_observations"
                )
                or []
            )
            trusted_ranks.add(rank)
            trusted_prior.append(
                {"rank": rank, "sealed_prior_observation_count": len(observations)}
            )
    return {
        "events_path": str(events_path),
        "event_line_count": len(rows),
        "event_parse_error_lines": parse_errors,
        "physical_started_count": len(started),
        "physical_started_ranks": started,
        "successful_third_observation_count": len(successful),
        "successful_third_observation_ranks": successful,
        "technical_failure_count": len(technical),
        "technical_failure_ranks": technical,
        "batch_budget_attempted_rank_count": len(
            set(started) | set(technical)
        ),
        "in_flight_count": len(in_flight),
        "in_flight_ranks": in_flight,
        "baseline_restored_event_seen": any(
            row.get("event") == "job_round8_baseline_restored" for row in rows
        ),
        "trusted_prior_observation_count_by_rank": trusted_prior,
        "trusted_third_observation_completed_ranks": sorted(
            set(successful) & trusted_ranks
        ),
        "provisional_event_accepted_ranks_not_countable_until_commit": (
            provisional_accepted
        ),
    }


def directional_band_score(utilization: float, weight: float, target: bool) -> float:
    target_min, target_max, hard = (
        (0.85, 0.90, 0.95) if target else (0.30, 0.50, 0.90)
    )
    if target:
        if utilization < target_min:
            gap = (target_min - utilization) / target_min
            return weight * (1.0 - gap * gap)
        if utilization <= target_max:
            position = (utilization - target_min) / (target_max - target_min)
            return weight * (1.0 - 0.2 * position)
        span = max(hard - target_max, 0.05)
        gap = (utilization - target_max) / span
        hard_excess = max(0.0, utilization - hard) / span
        return weight * (0.8 - gap * gap - 4.0 * hard_excess)
    if utilization < target_min:
        gap = (target_min - utilization) / target_min
        return weight * 0.8 * (1.0 - gap * gap)
    if utilization <= target_max:
        position = (utilization - target_min) / (target_max - target_min)
        return weight * (0.8 + 0.2 * position)
    span = max(hard - target_max, 0.05)
    gap = (utilization - target_max) / span
    hard_excess = max(0.0, utilization - hard) / span
    return weight * (1.0 - gap * gap - 4.0 * hard_excess)


def raw_actual_score(utilization: Mapping[str, Any]) -> float:
    return sum(
        directional_band_score(float(utilization[name]), weight, name == "memory")
        for name, weight in (("cpu", 1.0), ("io", 3.0), ("memory", 1.0))
    )


def live_acceptance_audit(
    events_path: Path,
    selection: Mapping[str, Any] | None,
    accepted_target: int = TARGET_ACCEPTED,
) -> dict[str, Any]:
    try:
        rows = [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError):
        rows = []
    candidates = {
        int(item["rank"]): item
        for item in (selection.get("top5") if selection else []) or []
        if isinstance(item, dict) and item.get("rank") is not None
    }
    completed = {
        int(row["rank"]): row
        for row in rows
        if isinstance(row, dict)
        and row.get("event") == "job_round8_candidate_replay_complete"
        and row.get("rank") is not None
    }
    technical_ranks = {
        int(row["rank"])
        for row in rows
        if isinstance(row, dict)
        and row.get("event") == "job_round8_candidate_technical_failure"
        and row.get("rank") is not None
    }
    details: list[dict[str, Any]] = []
    mismatch_ranks: list[int] = []
    observed_accepted_ranks: list[int] = []
    trusted_ranks = {
        rank
        for rank, candidate in candidates.items()
        if candidate.get("selection_role") == "trusted"
    }
    probe_ranks = set(candidates) - trusted_ranks
    for rank in sorted(completed):
        event = completed[rank]
        candidate = candidates.get(rank) or {}
        role = candidate.get("selection_role")
        utilization = event.get("actual_utilization")
        before = event.get("incumbent_state_before")
        after = event.get("incumbent_state_after")
        errors: list[str] = []
        raw_score = None
        robust_score = None
        expected_accepted = False
        if not isinstance(utilization, Mapping):
            errors.append("missing_actual_utilization")
        else:
            raw_score = raw_actual_score(utilization)
        if role == "trusted":
            policy = candidate.get("robust_acceptance_policy") or {}
            prior = policy.get("prior_observations") or []
            prior_scores = [
                float(item["actual_score"])
                for item in prior
                if isinstance(item, Mapping) and item.get("actual_score") is not None
            ]
            if len(prior_scores) != 2 or raw_score is None:
                errors.append("trusted_does_not_have_three_scores")
            else:
                robust_score = float(statistics.median([*prior_scores, raw_score]))
                event_score = event.get("actual_score")
                if event_score is None or abs(float(event_score) - robust_score) > 1e-12:
                    errors.append("event_robust_score_mismatch")
                eligible = bool(
                    candidate.get("incumbent_update_eligible") is True
                    and candidate.get("used_for_inner_incumbent_update") is True
                    and not (
                        isinstance(candidate.get("algorithmic_gate"), Mapping)
                        and candidate["algorithmic_gate"].get("accepted") is False
                    )
                )
                if not isinstance(before, Mapping):
                    errors.append("missing_incumbent_state_before")
                else:
                    expected_accepted = bool(
                        eligible
                        and int(before.get("accepted_count", -1)) < accepted_target
                        and robust_score > float(before.get("incumbent_score")) + 1e-12
                    )
        elif role == "probe":
            robust_score = raw_score
            if event.get("accepted") is not False:
                errors.append("probe_was_accepted")
            if any(
                event.get(name) is not False
                for name in (
                    "gradient_update_applied",
                    "incumbent_update_applied",
                    "range_update_applied",
                )
            ):
                errors.append("probe_applied_formal_update")
            if before != after:
                errors.append("probe_mutated_incumbent_state")
        else:
            errors.append("unknown_selection_role")
        if bool(event.get("accepted")) != expected_accepted:
            errors.append("accepted_flag_mismatch")
        expected_gradient = bool(
            expected_accepted and candidate.get("used_for_gradient") is True
        )
        if event.get("gradient_update_applied") is not expected_gradient:
            errors.append("gradient_update_flag_mismatch")
        if event.get("incumbent_update_applied") is not expected_accepted:
            errors.append("incumbent_update_flag_mismatch")
        if event.get("range_update_applied") is not expected_accepted:
            errors.append("range_update_flag_mismatch")
        if not expected_accepted and before != after:
            errors.append("rejected_candidate_mutated_state")
        if expected_accepted:
            observed_accepted_ranks.append(rank)
            if not isinstance(before, Mapping) or not isinstance(after, Mapping):
                errors.append("accepted_state_transition_missing")
            elif (
                int(after.get("accepted_count", -1))
                != int(before.get("accepted_count", -1)) + 1
                or robust_score is None
                or abs(float(after.get("incumbent_score")) - robust_score) > 1e-12
            ):
                errors.append("accepted_state_transition_mismatch")
        if errors:
            mismatch_ranks.append(rank)
        details.append(
            {
                "rank": rank,
                "role": role,
                "raw_third_observation_score": raw_score,
                "robust_median_acceptance_score": robust_score,
                "expected_accepted": expected_accepted,
                "observed_accepted": bool(event.get("accepted")),
                "errors": errors,
            }
        )
    unfinished_trusted = sorted(trusted_ranks - set(completed) - technical_ranks)
    observed_delta = len(observed_accepted_ranks)
    accepted_before_batch = None
    if details:
        first_before = completed[details[0]["rank"]].get("incumbent_state_before")
        if isinstance(first_before, Mapping):
            accepted_before_batch = int(first_before.get("accepted_count", -1))
    remaining_target_room = (
        max(0, accepted_target - accepted_before_batch - observed_delta)
        if accepted_before_batch is not None and accepted_before_batch >= 0
        else len(unfinished_trusted)
    )
    maximum_delta = observed_delta + min(
        len(unfinished_trusted), remaining_target_room
    )
    all_ranks_finished = set(candidates) <= set(completed) | technical_ranks
    return {
        "trusted_acceptance_formula": (
            "median(prior_score_1,prior_score_2,raw_third_score) > "
            "sequential_incumbent_score + 1e-12"
        ),
        "probe_policy": "always_state_neutral_never_accepted",
        "completed_rank_calculations": details,
        "calculation_mismatch_ranks": sorted(set(mismatch_ranks)),
        "observed_accepted_trusted_ranks": sorted(observed_accepted_ranks),
        "unfinished_trusted_ranks": unfinished_trusted,
        "possible_accepted_delta_range": [observed_delta, maximum_delta],
        "all_five_ranks_finished": all_ranks_finished,
        "probe_state_neutral_so_far": not any(
            rank in probe_ranks and rank in mismatch_ranks for rank in completed
        ),
        "acceptance_calculation_consistent_so_far": not mismatch_ranks,
    }


def audit_boundary(
    b: Path,
    *,
    proc_root: Path = Path("/proc"),
    dependencies: Mapping[str, str] = DEPENDENCY_HASHES,
) -> dict[str, Any]:
    t = b / "job_final_algorithm_fresh10_v36_20260731T2020"
    p = b / "job_round8_corrected_top5_v6_20260731"
    formal = t / "autonomous_v45/formal_batch004"
    state_path = t / "package/formal_state.json"
    selection_path = formal / "package/top5_promoted_formal.json"
    events_path = formal / "replay/events.jsonl"
    summary_path = formal / "replay/summary.json"
    transaction_path = formal / "replay/formal_state_commit_transaction.json"
    state = load_json(state_path)
    selection = load_json(selection_path)
    summary = load_json(summary_path)
    transaction = load_json(transaction_path)

    counters = {
        "global_ordinal": read_counter(b / "replay_counter.txt"),
        "formal_search_replay_count": read_counter(
            b / "formal_search_replay_counter.txt"
        ),
        "formal_search_accepted_count": read_counter(
            b / "formal_search_accepted_counter.txt"
        ),
    }
    state_sha = sha256_file(state_path) if state_path.is_file() else None
    selection_sha = (
        sha256_file(selection_path) if selection_path.is_file() else None
    )
    active = active_job_processes(proc_root)
    effective_dependencies = dict(dependencies)
    if dependencies is DEPENDENCY_HASHES:
        effective_dependencies.update(
            {
                str(p / "run_job_round8_strict_serial.py"): BASE_RUNNER_SHA,
                str(t / "package/job_round8_strict_replay_contract.py"): (
                    CONTRACT_SHA
                ),
            }
        )
    dependencies_ok, dependency_rows = dependency_audit(effective_dependencies)
    live_progress = live_replay_progress(events_path, selection)
    acceptance_audit = live_acceptance_audit(events_path, selection)

    physical = int(summary.get("physical_invocation_count", -1)) if summary else -1
    valid = int(summary.get("successful_real_replay_count", -1)) if summary else -1
    technical = int(summary.get("technical_failure_count", -1)) if summary else -1
    accepted_before = (
        int(transaction.get("accepted_before", -1)) if transaction else -1
    )
    accepted_after = (
        int(transaction.get("accepted_after", -1)) if transaction else -1
    )
    state_accepted = int(state.get("accepted_count", -1)) if state else -1
    state_global = int(state.get("global_ordinal", -1)) if state else -1
    state_formal_replay = (
        int(state.get("formal_replay_count", -1)) if state else -1
    )
    top5 = selection.get("top5") if selection else None
    roles = (
        [str(item.get("selection_role") or "") for item in top5]
        if isinstance(top5, list) and all(isinstance(item, dict) for item in top5)
        else []
    )

    checks = {
        "formal004_transaction_committed": bool(
            transaction and transaction.get("status") == "committed"
        ),
        "formal004_baseline_restored": bool(
            summary and summary.get("baseline_params_restored") is True
        ),
        "formal004_batch_budget_valid": bool(
            0 <= physical <= 5
            and valid >= 0
            and technical >= 0
            and valid + technical <= 5
            and valid <= physical <= valid + technical
        ),
        "formal004_three_observation_contract": bool(
            summary
            and summary.get("three_observation_acceptance_contract")
            == "job_three_independent_exact_point_median_acceptance_v2"
        ),
        "formal004_qps_outer_only": bool(
            summary and summary.get("qps_used_for_inner_scoring") is False
        ),
        "formal004_selection_sha_bound": bool(
            summary and summary.get("selection_sha256") == selection_sha
        ),
        "formal004_roles_trusted3_probe2": roles
        == ["trusted", "trusted", "trusted", "probe", "probe"],
        "state_sha_matches_transaction": bool(
            transaction and transaction.get("state_sha256_after") == state_sha
        ),
        "accepted_state_transaction_counter_match": bool(
            transaction
            and state_accepted == accepted_after
            and accepted_after == counters["formal_search_accepted_count"]
        ),
        "global_state_transaction_counter_match": bool(
            transaction
            and state_global == int(transaction.get("global_ordinal_after", -2))
            and state_global == counters["global_ordinal"]
        ),
        "formal_replay_state_transaction_counter_match": bool(
            transaction
            and state_formal_replay
            == int(transaction.get("formal_replay_count_after", -2))
            and state_formal_replay == counters["formal_search_replay_count"]
        ),
        "accepted_transition_valid": bool(
            0 <= accepted_before <= accepted_after <= TARGET_ACCEPTED
            and accepted_after - accepted_before <= 3
        ),
        "accepted_target_is_10": bool(
            state and int(state.get("accepted_target", -1)) == TARGET_ACCEPTED
        ),
        "formal004_acceptance_calculation_consistent": bool(
            acceptance_audit["acceptance_calculation_consistent_so_far"]
        ),
        "formal004_probe_state_neutral": bool(
            acceptance_audit["probe_state_neutral_so_far"]
        ),
        "no_active_job_replay_or_controller": not active,
        "all_v49_dependencies_sha_matched": dependencies_ok,
    }
    boundary_complete = all(
        checks[name]
        for name in checks
        if name != "all_v49_dependencies_sha_matched"
    )
    already_complete = boundary_complete and state_accepted == TARGET_ACCEPTED
    deployable = boundary_complete and dependencies_ok and not already_complete
    if already_complete:
        status = "already_complete_10_of_10"
        next_action = "do_not_launch_v49"
    elif deployable:
        status = "ready_to_launch_v49"
        next_action = "run_zero_call_preflight_then_launch"
    elif not checks["formal004_transaction_committed"]:
        status = "waiting_formal004_commit"
        next_action = "wait_without_starting_another_job_replay"
    elif active:
        status = "waiting_old_job_chain_exit"
        next_action = "wait_without_overlapping_job_replay"
    elif boundary_complete and not dependencies_ok:
        status = "dependencies_not_deployed_or_sha_mismatched"
        next_action = "deploy_only_sealed_job_v49_files_then_reaudit"
    else:
        status = "fail_closed_boundary_mismatch"
        next_action = "inspect_failed_checks_without_starting_workload"

    return {
        "schema_version": "job_formal004_v49_read_only_boundary_audit_v1",
        "status": status,
        "deployable": deployable,
        "boundary_ready_for_dependency_deployment": boundary_complete,
        "already_complete": already_complete,
        "next_action": next_action,
        "paths": {
            "job_attempt_root": str(b),
            "formal_campaign_root": str(t),
            "runner_package_root": str(p),
            "formal004_root": str(formal),
            "formal_state": str(state_path),
        },
        "formal_progress": {
            "historical_excluded_accepted": HISTORICAL_EXCLUDED_ACCEPTED,
            "inherited_formal_accepted_before_formal004": accepted_before,
            "formal004_added_accepted": (
                accepted_after - accepted_before
                if accepted_before >= 0 and accepted_after >= accepted_before
                else None
            ),
            "countable_formal_accepted_total": (
                state_accepted if state_accepted >= 0 else None
            ),
            "accepted_target": TARGET_ACCEPTED,
        },
        "formal004_replay": {
            "physical_workload_calls": physical if physical >= 0 else None,
            "batch_budget_attempts": (
                valid + technical if valid >= 0 and technical >= 0 else None
            ),
            "valid_replays": valid if valid >= 0 else None,
            "technical_failures": technical if technical >= 0 else None,
            "selection_roles": roles,
        },
        "live_formal004_progress": live_progress,
        "live_formal004_acceptance_audit": acceptance_audit,
        "authoritative_state": {
            "sha256": state_sha,
            "accepted_count": state_accepted if state_accepted >= 0 else None,
            "global_ordinal": state_global if state_global >= 0 else None,
            "formal_replay_count": (
                state_formal_replay if state_formal_replay >= 0 else None
            ),
        },
        "external_counters": counters,
        "transaction": {
            "status": transaction.get("status") if transaction else None,
            "accepted_before": accepted_before if accepted_before >= 0 else None,
            "accepted_after": accepted_after if accepted_after >= 0 else None,
            "global_ordinal_after": (
                transaction.get("global_ordinal_after") if transaction else None
            ),
            "formal_replay_count_after": (
                transaction.get("formal_replay_count_after")
                if transaction
                else None
            ),
            "state_sha256_after": (
                transaction.get("state_sha256_after") if transaction else None
            ),
        },
        "checks": checks,
        "failed_checks": sorted(name for name, passed in checks.items() if not passed),
        "active_job_processes": active,
        "dependencies": dependency_rows,
        "read_only": True,
        "new_workload_calls": 0,
        "explain_calls": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-attempt-root", type=Path, default=DEFAULT_B)
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"))
    args = parser.parse_args()
    result = audit_boundary(args.job_attempt_root, proc_root=args.proc_root)
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0 if result["deployable"] or result["already_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
