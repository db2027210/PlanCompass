#!/usr/bin/env python3
"""Offline JOB v58 cycle auditor with countable accepted/gradient accounting."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

import audit_job_v57_first_batch as base


GRADIENT_CONTRACT = "job_v58_complete_gradient_state_transition_v1"
TRANSACTION_CONTRACT = "job_v58_gradient_state_commit_transaction_v1"
REPAIR_CONTRACT = "job_v57_to_v58_zero_call_gradient_repair_v1"


class AuditFailure(RuntimeError):
    """A v58 accounting or complete-state contract failed."""


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AuditFailure(f"cannot load v58 JSON: {path}") from error
    if not isinstance(value, dict):
        raise AuditFailure(f"v58 JSON is not one object: {path}")
    return value


def _state5(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    fields = (
        "accepted_count",
        "incumbent_score",
        "incumbent_params",
        "range_by_parameter",
        "gradient_observation_count",
    )
    if any(field not in value for field in fields):
        raise AuditFailure(f"{label} lacks complete v58 state")
    return {field: value[field] for field in fields}


def _audit_v58_formal(
    cycle: Path,
    transaction: Mapping[str, Any],
) -> dict[str, Any]:
    formal = cycle / "formal"
    summary_path = formal / "replay/summary.json"
    summary = load(summary_path)
    successful = summary.get("successful") or []
    failures = summary.get("technical_failures") or []
    if (
        summary.get("gradient_state_contract") != GRADIENT_CONTRACT
        or summary.get("complete_gradient_state_transition_guard") != "passed"
        or not isinstance(successful, list)
        or not isinstance(failures, list)
    ):
        raise AuditFailure(f"v58 formal summary lacks gradient guard: {summary_path}")
    before_count = int(transaction.get("gradient_observation_count_before", -1))
    expected = None
    accepted_rows = 0
    for row in successful:
        if not isinstance(row, Mapping):
            raise AuditFailure(f"malformed v58 formal row: {summary_path}")
        rank = int(row.get("rank", -1))
        before = _state5(row.get("incumbent_state_before") or {}, f"rank {rank} before")
        after = _state5(row.get("incumbent_state_after") or {}, f"rank {rank} after")
        if expected is None and int(before["gradient_observation_count"]) != before_count:
            raise AuditFailure("v58 first formal row does not start at transaction gradient")
        if expected is not None and before != expected:
            raise AuditFailure(f"v58 formal state chain broke at rank {rank}")
        accepted = row.get("accepted") is True
        if accepted:
            if (
                row.get("selection_role") != "trusted"
                or row.get("gradient_update_applied") is not True
                or int(after["accepted_count"]) != int(before["accepted_count"]) + 1
                or int(after["gradient_observation_count"])
                != int(before["gradient_observation_count"]) + 1
            ):
                raise AuditFailure(f"v58 accepted rank {rank} has invalid full-state update")
            accepted_rows += 1
        elif (
            row.get("gradient_update_applied") is not False
            or row.get("incumbent_update_applied") is not False
            or row.get("range_update_applied") is not False
            or after != before
        ):
            raise AuditFailure(f"v58 rejected/probe rank {rank} mutated state")
        expected = after
    for row in failures:
        if not isinstance(row, Mapping) or any(
            row.get(field) is not False
            for field in (
                "accepted",
                "gradient_update_applied",
                "incumbent_update_applied",
                "range_update_applied",
            )
        ):
            raise AuditFailure(f"v58 technical row has an update flag: {summary_path}")
    final_state = _state5(summary.get("final_state") or {}, "v58 final state")
    if expected is None:
        if int(final_state["gradient_observation_count"]) != before_count:
            raise AuditFailure("empty v58 formal changed gradient state")
    elif final_state != expected:
        raise AuditFailure("v58 formal final state differs from row chain")
    if (
        int(final_state["accepted_count"]) != int(transaction.get("accepted_after", -1))
        or int(final_state["gradient_observation_count"])
        != int(transaction.get("gradient_observation_count_after", -1))
        or
        accepted_rows != int(transaction.get("accepted_after", -1))
        - int(transaction.get("accepted_before", -1))
        or accepted_rows != int(transaction.get("gradient_observation_count_delta", -1))
    ):
        raise AuditFailure("v58 formal row/accepted/gradient deltas differ")
    return {
        "cycle": cycle.name,
        "summary_sha256": base.sha256_file(summary_path),
        "accepted_delta": accepted_rows,
        "gradient_delta": accepted_rows,
        "gradient_rows_complete": True,
    }


def _run_base_audit(root: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(base.__file__).resolve()),
            "--root",
            str(root),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise AuditFailure(f"base JOB audit failed: {completed.stdout.strip()}")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise AuditFailure("base JOB audit output is malformed") from error
    if not isinstance(value, dict):
        raise AuditFailure("base JOB audit did not return one object")
    return value


def audit(root: Path) -> dict[str, Any]:
    root = root.resolve()
    base_result = _run_base_audit(root)
    state_path = root / "package/formal_state.json"
    state = load(state_path)
    current_state = _state5(state, "authoritative state")
    accepted_counter = int(
        (root / "formal_search_accepted_counter.txt").read_text(encoding="utf-8").strip()
    )
    if int(current_state["accepted_count"]) != accepted_counter:
        raise AuditFailure("v58 state/accepted counter mismatch")

    repair_transaction_path = root / "package/v57_to_v58_gradient_repair_transaction.json"
    repair_audit_path = root / "package/v57_to_v58_gradient_repair_audit.json"
    repair_transaction = load(repair_transaction_path)
    repair_audit = load(repair_audit_path)
    if (
        repair_transaction.get("status") != "committed"
        or repair_transaction.get("contract") != REPAIR_CONTRACT
        or repair_audit.get("status") != "complete"
        or repair_audit.get("contract") != REPAIR_CONTRACT
        or repair_transaction.get("audit_sha256") != base.sha256_file(repair_audit_path)
        or int(repair_transaction.get("new_workload_calls", -1)) != 0
        or int(repair_transaction.get("counter_updates", -1)) != 0
    ):
        raise AuditFailure("v58 zero-call repair audit is invalid")

    accepted_sum = 0
    gradient_sum = 0
    valid_sum = 0
    committed = []
    expected_diagnostic_state_sha = None
    partial_formals = []
    for cycle in sorted(root.glob("cycle_[0-9][0-9][0-9]")):
        transaction_path = cycle / "formal/replay/formal_state_commit_transaction.json"
        summary_path = cycle / "formal/replay/summary.json"
        if expected_diagnostic_state_sha is None:
            gate_path = cycle / "formal/package/entry_gate.json"
            if gate_path.is_file():
                expected_diagnostic_state_sha = str(
                    load(gate_path).get("formal_state_sha256_before") or ""
                )
        for diagnostic_audit_path in sorted(cycle.glob("**/diagnostic_audit.json")):
            if "/formal/" in str(diagnostic_audit_path):
                continue
            diagnostic = load(diagnostic_audit_path)
            if (
                expected_diagnostic_state_sha is not None
                and diagnostic.get("formal_state_sha256")
                != expected_diagnostic_state_sha
            ):
                raise AuditFailure(
                    f"diagnostic did not preserve the prior committed state: {diagnostic_audit_path}"
                )
        if summary_path.exists() and not transaction_path.exists():
            partial_formals.append(
                {
                    "cycle": cycle.name,
                    "status": "formal_summary_without_commit_fail_closed_no_retry",
                    "known_technical_failures": None,
                }
            )
        if transaction_path.is_file():
            transaction = load(transaction_path)
            accepted_delta = int(transaction.get("accepted_after", -1)) - int(
                transaction.get("accepted_before", -1)
            )
            gradient_delta = int(
                transaction.get("gradient_observation_count_after", -1)
            ) - int(transaction.get("gradient_observation_count_before", -1))
            if (
                transaction.get("status") != "committed"
                or transaction.get("gradient_transaction_contract")
                != TRANSACTION_CONTRACT
                or transaction.get("gradient_state_contract") != GRADIENT_CONTRACT
                or accepted_delta < 0
                or gradient_delta != accepted_delta
                or transaction.get("state_sha256_after")
                != transaction.get("gradient_state_sha256_after")
            ):
                raise AuditFailure(f"invalid v58 transaction: {transaction_path}")
            accepted_sum += accepted_delta
            gradient_sum += gradient_delta
            summary = load(summary_path)
            valid_sum += int(summary.get("successful_real_replay_count", -1))
            if transaction.get("v57_to_v58_repair_contract") == REPAIR_CONTRACT:
                if cycle.name != "cycle_001":
                    raise AuditFailure("v57 repair contract appears outside cycle_001")
                events_path = cycle / "formal/replay/events.jsonl"
                legacy_accepted_rows = sum(
                    1
                    for row in summary.get("successful") or []
                    if isinstance(row, Mapping)
                    and row.get("accepted") is True
                    and row.get("selection_role") == "trusted"
                    and row.get("gradient_update_applied") is True
                )
                if (
                    legacy_accepted_rows != accepted_delta
                    or transaction.get("gradient_summary_sha256")
                    != base.sha256_file(summary_path)
                    or not events_path.is_file()
                    or transaction.get("gradient_events_sha256")
                    != base.sha256_file(events_path)
                ):
                    raise AuditFailure("cycle_001 zero-call gradient repair binding changed")
                committed.append(
                    {
                        "cycle": cycle.name,
                        "accepted_delta": accepted_delta,
                        "gradient_delta": gradient_delta,
                        "legacy_summary_repaired_zero_call": True,
                    }
                )
            else:
                committed.append(_audit_v58_formal(cycle, transaction))
            expected_diagnostic_state_sha = str(transaction["state_sha256_after"])

    if accepted_sum != int(current_state["accepted_count"]):
        raise AuditFailure("countable formal accepted sum differs from authoritative state")
    if gradient_sum != int(current_state["gradient_observation_count"]):
        raise AuditFailure("formal gradient sum differs from authoritative state")
    if valid_sum != int(state.get("formal_valid_replay_count", -1)):
        raise AuditFailure("formal valid replay sum differs from authoritative state")

    return {
        "schema_version": "job_v58_offline_cycles_audit_v1",
        "status": base_result["status"],
        "root": str(root),
        "formal_accepted_inherited": 0,
        "formal_accepted_new": accepted_sum,
        "formal_accepted_countable_total": int(current_state["accepted_count"]),
        "formal_accepted_target": 10,
        "formal_accepted_remaining": 10 - int(current_state["accepted_count"]),
        "gradient_observation_count": int(current_state["gradient_observation_count"]),
        "formal_valid_replays": valid_sum,
        "committed_formal_audits": committed,
        "partial_formal_audits": partial_formals,
        "diagnostic_audits": base_result["diagnostic_audits"],
        "selection_audits": base_result["selection_audits"],
        "rank_gate_audits": base_result["rank_gate_audits"],
        "partial_phase_audits": base_result["partial_phase_audits"],
        "directory_cycle_numbers_not_used_as_progress": True,
        "new_workload_calls": 0,
        "ssh_calls": 0,
        "mysql_touched": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.root)
    if args.output:
        base.atomic_json(args.output.resolve(), result)
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AuditFailure, OSError, ValueError, TypeError) as error:
        print(
            json.dumps(
                {
                    "status": "fail_closed",
                    "error": str(error),
                    "new_workload_calls": 0,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        raise SystemExit(2)
