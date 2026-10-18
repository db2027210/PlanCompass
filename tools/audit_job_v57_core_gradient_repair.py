#!/usr/bin/env python3
"""Read-only audit and expected patch for sealed JOB v57 core artifacts."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

import repair_job_v57_to_v58_gradient_state as repair


def canonical_json_sha(value: Mapping[str, Any]) -> str:
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def require_sha(path: Path, expected: str, label: str) -> str:
    if not path.is_file():
        raise repair.RepairFailure(f"missing core artifact: {label}")
    observed = repair.sha256_file(path)
    if observed != expected:
        raise repair.RepairFailure(
            f"core artifact SHA mismatch: {label}: expected={expected} observed={observed}"
        )
    return observed


def state4(value: Mapping[str, Any]) -> dict[str, Any]:
    return repair._state_without_gradient(value)


def audit(
    *,
    summary_path: Path,
    events_path: Path,
    state_path: Path,
    transaction_path: Path,
    expected_sha: Mapping[str, str],
    expected_accepted_count: int,
    expected_gradient_before: int,
    expected_gradient_after: int,
    expected_incumbent_score: float,
) -> dict[str, Any]:
    paths = {
        "summary": summary_path.resolve(),
        "events": events_path.resolve(),
        "state": state_path.resolve(),
        "transaction": transaction_path.resolve(),
    }
    observed = {
        name: require_sha(path, expected_sha[name], name)
        for name, path in paths.items()
    }
    summary = repair.load_json(paths["summary"])
    state = repair.load_json(paths["state"])
    transaction = repair.load_json(paths["transaction"])
    successful = summary.get("successful") or []
    failures = summary.get("technical_failures") or []
    physical = int(summary.get("physical_invocation_count", -1))
    valid = int(summary.get("successful_real_replay_count", -1))
    technical = int(summary.get("technical_failure_count", -1))
    attempts = int(summary.get("batch_budget_attempt_count", valid + technical))
    if (
        transaction.get("status") != "committed"
        or transaction.get("state_sha256_after") != observed["state"]
        or not isinstance(successful, list)
        or not isinstance(failures, list)
        or len(successful) != valid
        or len(failures) != technical
        or not (physical == valid == attempts == 5 and technical == 0)
        or summary.get("baseline_params_restored") is not True
        or int(transaction.get("accepted_after", -1)) != expected_accepted_count
        or int(state.get("accepted_count", -1)) != expected_accepted_count
        or int(state.get("gradient_observation_count", -1))
        != expected_gradient_before
        or float(state.get("incumbent_score")) != expected_incumbent_score
        or state4(summary.get("final_state") or {}) != state4(state)
    ):
        raise repair.RepairFailure("core v57 summary/state/transaction contract mismatch")

    chain = state4(summary.get("initial_state") or {})
    accepted_rows = []
    keys = {}
    for row in successful:
        if not isinstance(row, Mapping):
            raise repair.RepairFailure("core successful row is malformed")
        rank = int(row.get("rank", -1))
        fingerprint = repair._fingerprint(row)
        before = state4(row.get("incumbent_state_before") or {})
        after = state4(row.get("incumbent_state_after") or {})
        if rank < 1 or rank > 5 or not fingerprint or before != chain:
            raise repair.RepairFailure("core successful state/rank/fingerprint chain is invalid")
        accepted = row.get("accepted") is True
        flags = (
            row.get("gradient_update_applied") is True,
            row.get("incumbent_update_applied") is True,
            row.get("range_update_applied") is True,
        )
        if accepted:
            if row.get("selection_role") != "trusted" or flags != (True, True, True):
                raise repair.RepairFailure("core accepted row lacks complete update flags")
            if int(after["accepted_count"]) != int(before["accepted_count"]) + 1:
                raise repair.RepairFailure("core accepted row count transition is invalid")
            accepted_rows.append(rank)
        elif flags != (False, False, False) or after != before:
            raise repair.RepairFailure("core rejected/probe row is not state-neutral")
        chain = after
        keys[(rank, fingerprint)] = row
    if chain != state4(state):
        raise repair.RepairFailure("core successful chain does not end at formal state")
    if (
        len(accepted_rows) != expected_gradient_after - expected_gradient_before
        or int(transaction.get("accepted_after", -1))
        - int(transaction.get("accepted_before", -1))
        != len(accepted_rows)
    ):
        raise repair.RepairFailure("core accepted delta does not justify gradient patch")

    event_objects = repair._event_objects(paths["events"])
    for (rank, fingerprint), row in keys.items():
        matches = [
            item
            for item in event_objects
            if item.get("event") == "job_round8_candidate_replay_complete"
            and int(item.get("rank", -1)) == rank
            and repair._fingerprint(item) == fingerprint
            and item.get("accepted") is row.get("accepted")
            and item.get("gradient_update_applied")
            is row.get("gradient_update_applied")
        ]
        if len(matches) != 1:
            raise repair.RepairFailure(
                f"core events do not uniquely bind completed rank {rank}"
            )

    patched_state = copy.deepcopy(state)
    patched_state["gradient_observation_count"] = expected_gradient_after
    if any(
        patched_state[name] != state[name]
        for name in state
        if name != "gradient_observation_count"
    ):
        raise repair.RepairFailure("core dry-run changed non-gradient formal state")
    patched_state_sha = canonical_json_sha(patched_state)
    patched_transaction = copy.deepcopy(transaction)
    patched_transaction.update(
        {
            "gradient_observation_count_before": expected_gradient_before,
            "gradient_observation_count_after": expected_gradient_after,
            "gradient_observation_count_delta": len(accepted_rows),
            "gradient_summary_sha256": observed["summary"],
            "gradient_events_sha256": observed["events"],
            "gradient_state_contract": repair.GRADIENT_CONTRACT,
            "gradient_transaction_contract": repair.TRANSACTION_CONTRACT,
            "v57_to_v58_repair_contract": repair.REPAIR_CONTRACT,
            "state_sha256_after": patched_state_sha,
            "gradient_state_sha256_after": patched_state_sha,
        }
    )
    return {
        "status": "core_audit_passed_full_remote_bindings_still_required",
        "contract": repair.REPAIR_CONTRACT,
        "observed_sha256": observed,
        "physical_calls": physical,
        "valid_replays": valid,
        "technical_failures": technical,
        "accepted_ranks": accepted_rows,
        "accepted_delta": len(accepted_rows),
        "state_patch": [
            {
                "op": "replace",
                "path": "/gradient_observation_count",
                "from": expected_gradient_before,
                "value": expected_gradient_after,
            }
        ],
        "non_gradient_state_fields_changed": [],
        "expected_repaired_state_sha256": patched_state_sha,
        "transaction_patch": {
            name: patched_transaction[name]
            for name in (
                "gradient_observation_count_before",
                "gradient_observation_count_after",
                "gradient_observation_count_delta",
                "gradient_summary_sha256",
                "gradient_events_sha256",
                "gradient_state_contract",
                "gradient_transaction_contract",
                "v57_to_v58_repair_contract",
                "state_sha256_after",
                "gradient_state_sha256_after",
            )
        },
        "expected_repaired_transaction_sha256": canonical_json_sha(
            patched_transaction
        ),
        "new_workload_calls": 0,
        "counter_updates": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--summary-sha256", required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--events-sha256", required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--state-sha256", required=True)
    parser.add_argument("--transaction", type=Path, required=True)
    parser.add_argument("--transaction-sha256", required=True)
    parser.add_argument("--expected-accepted-count", type=int, required=True)
    parser.add_argument("--expected-gradient-before", type=int, required=True)
    parser.add_argument("--expected-gradient-after", type=int, required=True)
    parser.add_argument("--expected-incumbent-score", type=float, required=True)
    args = parser.parse_args()
    result = audit(
        summary_path=args.summary,
        events_path=args.events,
        state_path=args.state,
        transaction_path=args.transaction,
        expected_sha={
            "summary": args.summary_sha256,
            "events": args.events_sha256,
            "state": args.state_sha256,
            "transaction": args.transaction_sha256,
        },
        expected_accepted_count=args.expected_accepted_count,
        expected_gradient_before=args.expected_gradient_before,
        expected_gradient_after=args.expected_gradient_after,
        expected_incumbent_score=args.expected_incumbent_score,
    )
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (repair.RepairFailure, OSError, ValueError, TypeError) as error:
        print(
            json.dumps(
                {
                    "status": "fail_closed",
                    "error": str(error),
                    "new_workload_calls": 0,
                    "counter_updates": 0,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(2)
