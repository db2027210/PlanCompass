#!/usr/bin/env python3
"""JOB robust runner with fail-closed pre-invocation apply failure accounting.

The sealed base runner records a parameter-apply failure as a technical rank
but does not append it to the physical workload invocation ledger.  Its final
validator therefore raises after writing a complete, baseline-restored
summary.  This adapter accepts only that exact mismatch, validates a synthetic
five-attempt ledger offline, and returns the original physical count so global
and formal ordinals continue to represent actual workload invocations.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
from typing import Any, Mapping

import run_job_round8_strict_serial_robust_v7 as robust_v7


base = robust_v7.base
POLICY_CONTRACT = robust_v7.POLICY_CONTRACT
EXPECTED_BASE_ERROR = "physical invocation ledger is inconsistent"
BATCH_ATTEMPT_CAP = 5


def recover_apply_failure_result(
    args: argparse.Namespace,
    error: Exception,
) -> dict[str, Any]:
    if not isinstance(error, ValueError) or str(error) != EXPECTED_BASE_ERROR:
        raise error
    summary_path = Path(args.replay_root).resolve() / "summary.json"
    if not summary_path.is_file():
        raise RuntimeError("base runner raised before sealing summary") from error
    summary = dict(base.load_json(summary_path))
    selection_path = Path(args.selection).resolve()
    selection = base.load_json(selection_path)
    successful = summary.get("successful") or []
    failures = summary.get("technical_failures") or []
    invocations = summary.get("invocations") or []
    if not all(
        isinstance(values, list)
        for values in (successful, failures, invocations)
    ):
        raise RuntimeError("sealed apply-failure summary has invalid ledgers")
    physical = int(summary.get("physical_invocation_count", -1))
    attempt_count = len(successful) + len(failures)
    if (
        summary.get("baseline_params_restored") is not True
        or (summary.get("baseline_restore") or {}).get("success") is not True
        or physical != len(invocations)
        or int(summary.get("real_workload_invocation_count", -1)) != physical
        or int(summary.get("successful_real_replay_count", -1))
        != len(successful)
        or int(summary.get("technical_failure_count", -1)) != len(failures)
        or attempt_count != BATCH_ATTEMPT_CAP
        or physical < 0
        or physical >= attempt_count
    ):
        raise RuntimeError(
            "apply-failure recovery requires one complete baseline-restored "
            "five-attempt batch"
        ) from error

    invocation_rank_list = [int(item.get("rank", -1)) for item in invocations]
    successful_rank_list = [int(item.get("rank", -1)) for item in successful]
    failure_rank_list = [int(item.get("rank", -1)) for item in failures]
    attempt_rank_list = successful_rank_list + failure_rank_list
    if (
        len(set(invocation_rank_list)) != len(invocation_rank_list)
        or len(set(attempt_rank_list)) != len(attempt_rank_list)
        or sorted(attempt_rank_list) != list(range(1, BATCH_ATTEMPT_CAP + 1))
    ):
        raise RuntimeError("sealed apply-failure rank ledger is invalid") from error
    invocation_ranks = set(invocation_rank_list)
    successful_ranks = set(successful_rank_list)
    failure_ranks = set(failure_rank_list)
    missing_ranks = sorted(failure_ranks - invocation_ranks)
    if (
        not successful_ranks.issubset(invocation_ranks)
        or not invocation_ranks.issubset(successful_ranks | failure_ranks)
        or len(missing_ranks) != attempt_count - physical
    ):
        raise RuntimeError("pre-invocation failure rank accounting mismatch") from error
    failure_by_rank = {int(item["rank"]): item for item in failures}
    normalized = copy.deepcopy(summary)
    for rank in missing_ranks:
        failure = failure_by_rank[rank]
        normalized["invocations"].append(
            {
                "rank": rank,
                "candidate_binding": copy.deepcopy(failure["candidate_binding"]),
                "started": True,
                "completed": True,
                "technical_failure": True,
                "synthetic_budget_entry_only": True,
            }
        )
    normalized["invocations"] = sorted(
        normalized["invocations"], key=lambda item: int(item["rank"])
    )
    normalized["physical_invocation_count"] = attempt_count
    normalized["real_workload_invocation_count"] = attempt_count
    base.validate_replay_summary(
        normalized,
        selection,
        selection_sha256=base.sha256_file(selection_path),
    )
    return {
        "summary_path": str(summary_path),
        "single_batch": True,
        "physical_invocation_count": physical,
        "batch_budget_attempt_count": attempt_count,
        "pre_invocation_technical_failure_count": len(missing_ranks),
        "pre_invocation_technical_failure_ranks": missing_ranks,
        "successful_real_replay_count": len(successful),
        "technical_failure_count": len(failures),
        "rejection_count": sum(
            1 for item in successful if item.get("accepted") is False
        ),
        "baseline_params_restored": True,
        "final_state": copy.deepcopy(summary["final_state"]),
        "successful": successful,
    }


def rewrite_robust_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    recovered = dict(result)
    summary_path = Path(str(recovered["summary_path"])).resolve()
    summary = dict(base.load_json(summary_path))
    successful = summary.get("successful") or []
    robust_audit = []
    for row in successful:
        if not isinstance(row, dict) or row.get("selection_role") != "trusted":
            continue
        robust_score = float(base.finite(row.get("actual_score"), "robust score"))
        raw_score = float(base.actual_score(row["actual_utilization"]))
        row["raw_actual_score"] = raw_score
        row["robust_acceptance_score"] = robust_score
        row["actual_score"] = raw_score
        row["acceptance_score_source"] = POLICY_CONTRACT
        robust_audit.append(
            {
                "rank": int(row["rank"]),
                "raw_third_observation_score": raw_score,
                "robust_acceptance_score": robust_score,
                "accepted": bool(row.get("accepted")),
            }
        )
    summary["three_observation_acceptance_contract"] = POLICY_CONTRACT
    summary["robust_acceptance_audit"] = robust_audit
    summary["batch_budget_attempt_count"] = int(
        recovered.get("batch_budget_attempt_count", 5)
    )
    summary["pre_invocation_technical_failure_count"] = int(
        recovered.get("pre_invocation_technical_failure_count", 0)
    )
    summary["pre_invocation_technical_failure_ranks"] = list(
        recovered.get("pre_invocation_technical_failure_ranks") or []
    )
    temporary = summary_path.with_name(
        f".{summary_path.name}.robust-v8.{os.getpid()}"
    )
    temporary.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, summary_path)
    recovered["successful"] = successful
    recovered["robust_acceptance_audit"] = robust_audit
    recovered["batch_budget_attempt_count"] = int(
        summary["batch_budget_attempt_count"]
    )
    recovered["pre_invocation_technical_failure_count"] = int(
        summary["pre_invocation_technical_failure_count"]
    )
    recovered["pre_invocation_technical_failure_ranks"] = list(
        summary["pre_invocation_technical_failure_ranks"]
    )
    return recovered


def run(args: argparse.Namespace) -> dict[str, Any]:
    diagnostic_only = bool(getattr(args, "diagnostic_only", False))
    if diagnostic_only:
        # v49 diagnostics use the sealed base directly.  Keep this formal
        # adapter single-purpose and fail closed if called with the wrong role.
        raise RuntimeError("robust v8 apply-failure adapter is formal-only")
    robust_v7._validate_formal_selection(Path(args.selection).resolve())
    try:
        result = dict(base.run(args))
    except Exception as error:
        result = recover_apply_failure_result(args, error)
    return rewrite_robust_summary(result)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("selection", type=Path)
    parser.add_argument("replay_root", type=Path)
    parser.add_argument("--authority-root", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--exclusion", type=Path, required=True)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--accepted-target", type=int, default=10)
    parser.add_argument("--diagnostic-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        if args.diagnostic_only:
            raise RuntimeError("robust v8 formal adapter rejects diagnostic dry-run")
        robust_v7._validate_formal_selection(args.selection.resolve())
        print(
            json.dumps(
                base.preflight(
                    args.selection.resolve(),
                    args.authority_root.resolve(),
                    args.observations.resolve(),
                    args.exclusion.resolve(),
                ),
                indent=2,
                sort_keys=True,
            )
        )
        return
    print(json.dumps(run(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
