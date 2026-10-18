#!/usr/bin/env python3
"""TPC-H v48 prethird feasibility and realized evidence binding.

The original v42 zero-workload checkpoint recovery remains available.  V44
also turns a small allowlist of deterministic, evidence-backed negative gates
into state-neutral re-exploration.  Unknown failures still stop immediately.
V45 additionally seals a probe clean5 produced before the v43 ``None`` return
compatibility crash and resumes at exact-repeat without replaying that probe.
V47 preserves strict ordinary clean5 row identity while binding postrobust
reseal rows by rank and exact parameters, then consumes their realized roles
and valid three-level fingerprints as state-neutral re-exploration evidence.
V48 additionally recognizes the sealed prethird feasibility artifact used by
the live cycle and validates its hashes and bound semantics without replay.
No threshold is relaxed and no candidate is injected by this controller.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple


V41_CONTROLLER = Path("/tmp/tpch_v41_preformal_handoff.py")
V32_CHECKPOINTED = Path(
    "/tmp/tpch_fresh10_target10_loop_v32_v8_gate_binding.py"
)
EXPANDER_CHECKPOINTED = Path(
    "/tmp/tpch_exact_regime_neighbor_expand_v6_checkpointed.py"
)
STABILITY_ACQUISITION_BUILDER = Path(
    "/tmp/build_tpch_autonomous_successor_top5_v10_stability_acquisition.py"
)
STABILITY_DIAGNOSTIC_RUNNER = Path(
    "/tmp/run_tpch_round29_diagnostic_exact_plan_regime_batch_v45_none_return.py"
)
EXPECTED = {
    V41_CONTROLLER: (
        "bb81fde56677fc2c19b1399e708005e3b8e950169643c5fe777da94e47b36a41"
    ),
    V32_CHECKPOINTED: (
        "a7174eb59bd467f51a1e55cff4b98f89ed27999cc4ca329ed291cdea1b1f9a69"
    ),
    EXPANDER_CHECKPOINTED: (
        "8e16189de0f7677792dcd6c8b5e1ab3fc8015e5242497efa385957b89ae70b9c"
    ),
    STABILITY_ACQUISITION_BUILDER: (
        "da43bfb66ee99f60702685d4b39018ab1ea9d365d3210009c85b6afb6612fc9b"
    ),
    STABILITY_DIAGNOSTIC_RUNNER: (
        "af90bc7f2e145de14d83449513016cd143a756f6cd7da1a5c015706ea60c55a2"
    ),
}
RECOVERY_CONTRACT = "tpch_v42_cycle004_zero_workload_recovery_v1"
LIVENESS_CONTRACT = "tpch_v44_deterministic_negative_gate_reexploration_v1"
CLEAN5_RECOVERY_CONTRACT = "tpch_v45_v43_none_return_clean5_recovery_v1"
POSTCLEAN5_BINDING_CONTRACT = "tpch_v48_postclean5_resume_state_binding_v1"
RANK_MISS_ABSORPTION_CONTRACT = (
    "tpch_v47_actual_best_realized_probe_rank4_automatic_absorption_v1"
)
RESEALED_EVIDENCE_CONTRACT = (
    "tpch_v47_postrobust_resealed_parameter_rank_binding_v1"
)
REALIZED_SELECTION_CONTRACT = (
    "tpch_v47_realized_postrobust_evidence_top5_v1"
)
PRETHIRD_FEASIBILITY_CONTRACT = (
    "tpch_v48_prethird_acceptance_feasibility_binding_v1"
)
REMOTE_PRETHIRD_FEASIBILITY_SHA256 = (
    "ec43a865b38cc9d329c631f0ad2244d1764813069ba8091a3034137c1ee57eb4"
)

DETERMINISTIC_FAILURES = {
    "exact_repeat": (
        "source score span is not distinguishable from replay noise",
        "repeat fingerprints are not distinct",
    ),
    "robust_repeat": (
        "fewer than three stable exact-plan regimes",
        "three stable regimes require excessive replay-noise span",
        "robust repeat is not unique trusted3+probe2",
    ),
    "prethird_feasibility": (
        "bounded four-observation median cannot beat incumbent",
    ),
    "postrobust_reseal": (
        "fewer than three resource-stable three-observation regimes",
        "fewer than three stable three-observation regimes",
        "three stable regimes require excessive replay-noise span",
        "post-robust reseal is not unique trusted3+probe2",
    ),
}

FAILURE_LOGS = {
    "exact_repeat": "exact_builder_a.log",
    "robust_repeat": "robust_builder_a.log",
    "postrobust_reseal": "postrobust_reseal_v32/reseal_a.log",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def clean5_evidence(
    selection: Path,
    run_root: Path,
    accepted: int,
    expected_resume_state_sha256: str,
) -> Dict[str, Any]:
    """Validate one completed state-neutral diagnostic and its SHA binding."""
    state_path = run_root / "diagnostic_state.json"
    summary_path = run_root / "diagnostic_summary.json"
    if not state_path.is_file() or not summary_path.is_file():
        raise RuntimeError(f"diagnostic artifacts are incomplete: {run_root}")
    state = load(state_path)
    summary = load(summary_path)
    selection_payload = load(selection)
    selected_candidates = selection_payload.get("top5")
    selection_sha = sha256(selection)
    update_fields = (
        "accepted_update_applied",
        "incumbent_update_applied",
        "range_update_applied",
        "gradient_update_applied",
    )
    successful = summary.get("successful")
    successful_ranks = (
        [int(item.get("rank", -1)) for item in successful]
        if isinstance(successful, list)
        and all(isinstance(item, dict) for item in successful)
        else []
    )
    if not (
        isinstance(selected_candidates, list)
        and len(selected_candidates) == 5
        and all(isinstance(item, dict) for item in selected_candidates)
        and [item.get("selection_role") for item in selected_candidates]
        == ["trusted", "trusted", "trusted", "probe", "probe"]
    ):
        raise RuntimeError(f"diagnostic selection lacks exact Top-5: {selection}")

    def fingerprint_id(item: Mapping[str, Any]) -> str:
        fingerprint = item.get("three_level_fingerprint")
        if not isinstance(fingerprint, dict):
            return ""
        return str(fingerprint.get("id") or "")

    row_bindings_valid = len(successful_ranks) == 5
    if row_bindings_valid:
        for rank, (candidate, result) in enumerate(
            zip(selected_candidates, successful),
            1,
        ):
            if not (
                int(result.get("rank", -1)) == rank
                and candidate.get("selection_role")
                == result.get("selection_role")
                and fingerprint_id(candidate) == fingerprint_id(result)
                and fingerprint_id(candidate).startswith("three-level-sha256:")
                and candidate.get("diagnostic_only") is True
                and candidate.get("accepted_update_allowed") is False
                and candidate.get("incumbent_update_allowed") is False
                and candidate.get("range_update_allowed") is False
                and candidate.get("gradient_update_allowed") is False
                and candidate.get("qps_used_for_inner_scoring") is False
                and result.get("diagnostic_only") is True
                and result.get("accepted_update_eligible") is False
                and result.get("accepted_update_allowed") is False
                and result.get("incumbent_update_allowed") is False
                and result.get("range_update_allowed") is False
                and result.get("gradient_update_allowed") is False
                and result.get("qps_used_for_inner_scoring") is False
            ):
                row_bindings_valid = False
                break
    if not (
        state.get("schema_version")
        == "tpch_exact_plan_regime_diagnostic_state_v1"
        and state.get("status") == "complete"
        and state.get("diagnostic_only") is True
        and int(state.get("accepted_count", -1)) == accepted
        and int(state.get("physical_call_count", -1)) == 5
        and int(state.get("valid_replay_count", -1)) == 5
        and int(state.get("technical_failure_count", -1)) == 0
        and state.get("baseline_restored") is True
        and state.get("accepted_update_allowed") is False
        and state.get("incumbent_update_allowed") is False
        and state.get("range_update_allowed") is False
        and state.get("gradient_update_allowed") is False
        and state.get("selection_sha256") == selection_sha
        and state.get("resume_state_sha256")
        == expected_resume_state_sha256
        and state.get("formal_state_mutation_allowed") is False
        and int(state.get("batch_added_accepted_count", -1)) == 0
        and state.get("full_selection_replay") is True
        and state.get("selection_replayed_ranks") == [1, 2, 3, 4, 5]
        and state.get("selection_excluded_ranks") == []
        and int(state.get("maximum_real_replay_invocations", -1)) == 5
        and summary.get("schema_version")
        == "tpch_exact_plan_regime_diagnostic_batch_summary_v1"
        and summary.get("diagnostic_only") is True
        and summary.get("formal_state_mutation_allowed") is False
        and int(summary.get("accepted_count", -1)) == accepted
        and int(summary.get("batch_added_accepted_count", -1)) == 0
        and int(summary.get("physical_call_count", -1)) == 5
        and int(summary.get("valid_replay_count", -1)) == 5
        and int(summary.get("technical_failure_count", -1)) == 0
        and summary.get("baseline_restored") is True
        and summary.get("selection_sha256") == selection_sha
        and summary.get("full_selection_replay") is True
        and summary.get("selection_replayed_ranks") == [1, 2, 3, 4, 5]
        and summary.get("selection_excluded_ranks") == []
        and int(summary.get("maximum_real_replay_invocations", -1)) == 5
        and summary.get("technical_failures") == []
        and successful_ranks == [1, 2, 3, 4, 5]
        and len(set(successful_ranks)) == 5
        and row_bindings_valid
        and all(
            item.get("valid_replay") is True
            and item.get("technical_failure") is False
            for item in successful
        )
        and all(summary.get(field) is False for field in update_fields)
    ):
        raise RuntimeError(f"diagnostic is not clean state-neutral 5/5/0: {run_root}")
    return {
        "selection": str(selection.resolve()),
        "selection_sha256": selection_sha,
        "run_root": str(run_root.resolve()),
        "state_sha256": sha256(state_path),
        "summary_sha256": sha256(summary_path),
        "resume_state_sha256": expected_resume_state_sha256,
        "physical_calls": 5,
        "valid_replays": 5,
        "technical_failures": 0,
    }


def candidate_params(candidate: Mapping[str, Any]) -> Dict[str, Any]:
    params = candidate.get("candidate_params") or candidate.get("optimized_params")
    if not isinstance(params, dict) or not params:
        raise RuntimeError("candidate parameter binding is missing")
    return dict(params)


def valid_realized_fingerprint(candidate: Mapping[str, Any]) -> bool:
    fingerprint = candidate.get("three_level_fingerprint") or {}
    return bool(
        fingerprint.get("contract") == "plan_feature_resource_three_level_v1"
        and fingerprint.get("valid") is True
        and fingerprint.get("validation_errors") == []
        and str(fingerprint.get("id") or "").startswith("three-level-sha256:")
        and str(fingerprint.get("plan_level") or "").startswith(
            "ratio-plan-sha256:"
        )
        and isinstance(fingerprint.get("feature_ratio_level"), dict)
        and isinstance(fingerprint.get("predicted_resource_level"), dict)
    )


def resealed_clean5_evidence(
    selection: Path,
    run_root: Path,
    accepted: int,
    expected_resume_state_sha256: str,
) -> Dict[str, Any]:
    """Validate postrobust reseal by rank+params and realized fingerprints.

    The resealer preserves a predicted trusted3/probe2 selection but records
    the realized plan fingerprint and realized role in each successful row.
    Therefore equality of predicted and realized fingerprint/role is invalid;
    rank and exact candidate parameters are the immutable binding.
    """

    state_path = run_root / "diagnostic_state.json"
    summary_path = run_root / "diagnostic_summary.json"
    if not state_path.is_file() or not summary_path.is_file():
        raise RuntimeError(f"resealed diagnostic artifacts are incomplete: {run_root}")
    state = load(state_path)
    summary = load(summary_path)
    selection_payload = load(selection)
    selected = selection_payload.get("top5") or []
    successful = summary.get("successful") or []
    selection_sha = sha256(selection)
    predicted_roles = [item.get("selection_role") for item in selected]
    realized_roles = [item.get("selection_role") for item in successful]
    realized_ids = [
        str((item.get("three_level_fingerprint") or {}).get("id") or "")
        for item in successful
    ]
    rank_parameter_binding = bool(
        len(selected) == 5
        and len(successful) == 5
        and all(isinstance(item, dict) for item in [*selected, *successful])
        and all(
            int(candidate.get("rank", -1)) == rank
            and int(result.get("rank", -1)) == rank
            and candidate_params(candidate) == candidate_params(result)
            for rank, (candidate, result) in enumerate(
                zip(selected, successful),
                1,
            )
        )
    )
    selection_policy_valid = bool(
        predicted_roles == ["trusted", "trusted", "trusted", "probe", "probe"]
        and all(
            item.get("diagnostic_only") is True
            and item.get("accepted_update_allowed") is False
            and item.get("incumbent_update_allowed") is False
            and item.get("range_update_allowed") is False
            and item.get("gradient_update_allowed") is False
            and item.get("qps_used_for_inner_scoring") is False
            for item in selected
        )
    )
    realized_policy_valid = bool(
        realized_roles.count("trusted") == 3
        and realized_roles.count("probe") == 2
        and len(set(realized_ids)) == 5
        and "" not in realized_ids
        and all(valid_realized_fingerprint(item) for item in successful)
        and all(
            item.get("valid_replay") is True
            and item.get("technical_failure") is False
            and item.get("diagnostic_only") is True
            and item.get("accepted_update_eligible") is False
            and item.get("accepted_update_allowed") is False
            and item.get("incumbent_update_allowed") is False
            and item.get("range_update_allowed") is False
            and item.get("gradient_update_allowed") is False
            and item.get("qps_used_for_inner_scoring") is False
            for item in successful
        )
    )
    update_fields = (
        "accepted_update_applied",
        "incumbent_update_applied",
        "range_update_applied",
        "gradient_update_applied",
    )
    if not (
        selection_payload.get("schema_version")
        == "tpch_three_observation_postrobust_reseal_top5_v1"
        and state.get("schema_version")
        == "tpch_postrobust_resealed_diagnostic_state_v1"
        and state.get("status") == "complete"
        and state.get("diagnostic_only") is True
        and state.get("formal_state_mutation_allowed") is False
        and int(state.get("accepted_count", -1)) == accepted
        and int(state.get("batch_added_accepted_count", -1)) == 0
        and int(state.get("physical_call_count", -1)) == 5
        and int(state.get("valid_replay_count", -1)) == 5
        and int(state.get("technical_failure_count", -1)) == 0
        and state.get("baseline_restored") is True
        and state.get("accepted_update_allowed") is False
        and state.get("incumbent_update_allowed") is False
        and state.get("range_update_allowed") is False
        and state.get("gradient_update_allowed") is False
        and state.get("selection_sha256") == selection_sha
        and state.get("resume_state_sha256") == expected_resume_state_sha256
        and state.get("full_selection_replay") is True
        and state.get("selection_replayed_ranks") == [1, 2, 3, 4, 5]
        and state.get("selection_excluded_ranks") == []
        and int(state.get("maximum_real_replay_invocations", -1)) == 5
        and summary.get("schema_version")
        == "tpch_postrobust_resealed_diagnostic_summary_v1"
        and summary.get("diagnostic_only") is True
        and summary.get("formal_state_mutation_allowed") is False
        and int(summary.get("accepted_count", -1)) == accepted
        and int(summary.get("batch_added_accepted_count", -1)) == 0
        and int(summary.get("physical_call_count", -1)) == 5
        and int(summary.get("valid_replay_count", -1)) == 5
        and int(summary.get("technical_failure_count", -1)) == 0
        and summary.get("baseline_restored") is True
        and summary.get("selection_sha256") == selection_sha
        and summary.get("full_selection_replay") is True
        and summary.get("selection_replayed_ranks") == [1, 2, 3, 4, 5]
        and summary.get("selection_excluded_ranks") == []
        and int(summary.get("maximum_real_replay_invocations", -1)) == 5
        and summary.get("technical_failures") == []
        and all(summary.get(field) is False for field in update_fields)
        and rank_parameter_binding
        and selection_policy_valid
        and realized_policy_valid
    ):
        raise RuntimeError(
            f"resealed diagnostic is not parameter-bound state-neutral 5/5/0: {run_root}"
        )
    return {
        "contract": RESEALED_EVIDENCE_CONTRACT,
        "selection": str(selection.resolve()),
        "selection_sha256": selection_sha,
        "run_root": str(run_root.resolve()),
        "state_sha256": sha256(state_path),
        "summary_sha256": sha256(summary_path),
        "resume_state_sha256": expected_resume_state_sha256,
        "physical_calls": 5,
        "valid_replays": 5,
        "technical_failures": 0,
        "rank_parameter_binding": True,
        "predicted_roles": predicted_roles,
        "realized_roles": realized_roles,
        "realized_fingerprint_ids": realized_ids,
        "realized_fingerprints_unique": True,
    }


def realized_reexploration_selection(
    selection: Path,
    run_root: Path,
    cycle_root: Path,
) -> Path:
    """Create an automatic evidence view using realized roles/fingerprints."""

    predicted = load(selection)
    summary_path = run_root / "diagnostic_summary.json"
    summary = load(summary_path)
    selected = list(predicted.get("top5") or [])
    successful = list(summary.get("successful") or [])
    if len(selected) != 5 or len(successful) != 5:
        raise RuntimeError("realized evidence requires exact Top-5")
    realized = dict(predicted)
    realized_top5 = []
    for rank, (candidate, result) in enumerate(zip(selected, successful), 1):
        if not (
            int(candidate.get("rank", -1)) == rank
            and int(result.get("rank", -1)) == rank
            and candidate_params(candidate) == candidate_params(result)
            and valid_realized_fingerprint(result)
        ):
            raise RuntimeError("realized evidence rank/parameter binding mismatch")
        item = dict(candidate)
        item["predicted_reseal_selection_role"] = candidate["selection_role"]
        item["predicted_reseal_three_level_fingerprint"] = dict(
            candidate["three_level_fingerprint"]
        )
        item["selection_role"] = result["selection_role"]
        item["three_level_fingerprint"] = dict(result["three_level_fingerprint"])
        item["actual_score"] = result["actual_score"]
        item["actual_resource"] = dict(result.get("actual_resource") or {})
        item["actual_utilization"] = dict(result.get("actual_utilization") or {})
        item["realized_postrobust_evidence"] = True
        item["diagnostic_only"] = True
        item["accepted_update_allowed"] = False
        item["incumbent_update_allowed"] = False
        item["range_update_allowed"] = False
        item["gradient_update_allowed"] = False
        item["qps_used_for_inner_scoring"] = False
        realized_top5.append(item)
    if (
        [item["selection_role"] for item in realized_top5].count("trusted") != 3
        or [item["selection_role"] for item in realized_top5].count("probe") != 2
    ):
        raise RuntimeError("realized evidence is not total trusted3/probe2")
    realized.update(
        {
            "schema_version": REALIZED_SELECTION_CONTRACT,
            "selection_contract": REALIZED_SELECTION_CONTRACT,
            "top5": realized_top5,
            "diagnostic_only": True,
            "formal_admission_allowed": False,
            "source_predicted_selection": str(selection.resolve()),
            "source_predicted_selection_sha256": sha256(selection),
            "source_realized_summary": str(summary_path.resolve()),
            "source_realized_summary_sha256": sha256(summary_path),
            "rank_parameter_binding": True,
            "manual_candidate_injection": False,
            "thresholds_relaxed": False,
            "formal_state_mutated": False,
            "new_workload_calls": 0,
        }
    )
    output = cycle_root / "v47_realized_reseal_evidence.json"
    if output.exists():
        if load(output) != realized:
            raise RuntimeError("realized reseal evidence changed")
    else:
        atomic_json(output, realized)
    return output


def absorb_actual_best_probe_rank4(
    v26: Any,
    *,
    cycle_root: Path,
    selection: Path,
    validation_run: Path,
    analysis_path: Path,
    accepted: int,
    resume_state: Path,
    expected_formal_sha256: str,
) -> Tuple[Dict[str, Any], Path, Path]:
    """Seal one rank-4-only gate miss as next-cycle re-exploration evidence."""

    if not resume_state.is_file() or v26.sha256(resume_state) != expected_formal_sha256:
        raise RuntimeError("rank-miss absorption observed formal-state mutation")
    if v26.accepted_counter() != accepted:
        raise RuntimeError("rank-miss absorption observed accepted-counter mutation")
    selection_payload = v26.load(selection)
    resealed = (
        selection_payload.get("schema_version")
        == "tpch_three_observation_postrobust_reseal_top5_v1"
    )
    evidence_validator = resealed_clean5_evidence if resealed else clean5_evidence
    clean = evidence_validator(
        selection,
        validation_run,
        accepted,
        expected_formal_sha256,
    )
    analysis = v26.load(analysis_path)
    checks = analysis.get("checks") or {}
    failed_checks = sorted(name for name, value in checks.items() if value is not True)
    trusted_spearman = float(analysis.get("trusted_only_spearman"))
    resource_factor = float(analysis.get("maximum_trusted_resource_factor_error"))
    if not (
        analysis.get("passed") is False
        and failed_checks == ["actual_best_in_predicted_top3"]
        and int(analysis.get("actual_best_predicted_rank", -1)) == 4
        and math.isfinite(trusted_spearman)
        and trusted_spearman > 0.0
        and math.isfinite(resource_factor)
        and 0.0 < resource_factor <= 1.5
        and checks.get("top5_no_technical_failure") is True
        and checks.get("trusted3_probe2") is True
        and checks.get("five_unique_three_level_fingerprints") is True
        and checks.get("candidate_audit_fields_complete") is True
        and checks.get("trusted_only_spearman_positive") is True
        and checks.get(
            "trusted_resource_predictions_no_order_of_magnitude_error"
        )
        is True
    ):
        raise RuntimeError("failed gate is not the sealed rank4-only quality miss")

    summary = v26.load(validation_run / "diagnostic_summary.json")
    top5 = selection_payload.get("top5") or []
    successful = summary.get("successful") or []
    if len(top5) != 5 or len(successful) != 5:
        raise RuntimeError("rank-miss absorption lacks clean Top-5 evidence")
    by_rank = {int(item.get("rank", -1)): item for item in successful}
    rank4 = top5[3]
    rank4_result = by_rank.get(4)
    predicted_rank4_fingerprint = str(
        (rank4.get("three_level_fingerprint") or {}).get("id") or ""
    )
    realized_rank4_fingerprint = str(
        ((rank4_result or {}).get("three_level_fingerprint") or {}).get("id")
        or ""
    )
    actual_scores = {
        rank: float(item.get("actual_score")) for rank, item in by_rank.items()
    }
    common_binding_valid = bool(
        rank4.get("selection_role") == "probe"
        and predicted_rank4_fingerprint.startswith("three-level-sha256:")
        and rank4_result is not None
        and rank4_result.get("valid_replay") is True
        and rank4_result.get("technical_failure") is False
        and len(actual_scores) == 5
        and all(math.isfinite(score) for score in actual_scores.values())
        and actual_scores[4] == max(actual_scores.values())
    )
    if resealed:
        rank4_binding_valid = bool(
            common_binding_valid
            and candidate_params(rank4) == candidate_params(rank4_result or {})
            and (rank4_result or {}).get("selection_role") == "probe"
            and valid_realized_fingerprint(rank4_result or {})
            and realized_rank4_fingerprint.startswith("three-level-sha256:")
        )
    else:
        rank4_binding_valid = bool(
            common_binding_valid
            and realized_rank4_fingerprint == predicted_rank4_fingerprint
        )
    if not rank4_binding_valid:
        raise RuntimeError("actual-best rank4 probe binding mismatch")

    if resealed:
        effective_selection = realized_reexploration_selection(
            selection,
            validation_run,
            cycle_root,
        )
        effective_run = validation_run
        absorbed_fingerprint = realized_rank4_fingerprint
    else:
        resolver = getattr(v26, "resolve_reexploration_evidence", None)
        if not callable(resolver):
            raise RuntimeError("re-exploration evidence resolver is not installed")
        effective_selection, effective_run = resolver(selection, validation_run)
        absorbed_fingerprint = predicted_rank4_fingerprint
    effective_payload = v26.load(effective_selection)
    effective_ids = {
        str((item.get("three_level_fingerprint") or {}).get("id") or "")
        for item in effective_payload.get("top5") or []
    }
    if absorbed_fingerprint not in effective_ids:
        raise RuntimeError("rank4 probe was not absorbed into effective evidence")

    payload = {
        "contract": RANK_MISS_ABSORPTION_CONTRACT,
        "status": "automatic_bounded_reexploration_ready",
        "cycle": int(cycle_root.name.rsplit("_", 1)[-1]),
        "failed_checks": failed_checks,
        "actual_best_predicted_rank": 4,
        "actual_best_probe_fingerprint": absorbed_fingerprint,
        "predicted_rank4_fingerprint": predicted_rank4_fingerprint,
        "realized_rank4_fingerprint": realized_rank4_fingerprint,
        "resealed_realized_evidence": resealed,
        "actual_best_probe_score": actual_scores[4],
        "trusted_only_spearman": trusted_spearman,
        "maximum_trusted_resource_factor_error": resource_factor,
        "source_selection": str(selection.resolve()),
        "source_selection_sha256": v26.sha256(selection),
        "source_run": str(validation_run.resolve()),
        "source_clean5": clean,
        "entry_gate": str(analysis_path.resolve()),
        "entry_gate_sha256": v26.sha256(analysis_path),
        "effective_reexploration_selection": str(effective_selection.resolve()),
        "effective_reexploration_selection_sha256": v26.sha256(effective_selection),
        "effective_reexploration_run": str(effective_run.resolve()),
        "probe_absorbed_as_trusted": False,
        "probe_used_as_observed_reexploration_evidence": True,
        "next_action": "automatic_bounded_explain_reexploration",
        "top3_threshold_relaxed": False,
        "manual_candidate_injection": False,
        "new_workload_calls": 0,
        "formal_started": False,
        "formal_state_mutated": False,
        "accepted_before": accepted,
        "accepted_after": accepted,
        "accepted_counter_mutated": False,
        "qps_role": "outer_audit_only_not_inner_score",
    }
    audit_path = cycle_root / "v47_rank4_probe_absorption.json"
    if audit_path.exists():
        existing = v26.load(audit_path)
        if existing != payload:
            raise RuntimeError("rank4 probe absorption audit changed")
    else:
        v26.atomic_json(audit_path, payload)
    payload["audit_path"] = str(audit_path.resolve())
    payload["audit_sha256"] = v26.sha256(audit_path)
    return payload, effective_selection, effective_run


def deterministic_failure(
    stage: str,
    cycle_root: Path,
    error_text: str = "",
) -> Optional[Dict[str, Any]]:
    """Return allowlisted failure evidence or ``None`` for an unknown error."""
    if stage not in DETERMINISTIC_FAILURES:
        return None
    if stage == "prethird_feasibility":
        gate_path = cycle_root / "prethird_acceptance_feasibility.json"
        if not gate_path.is_file():
            return None
        gate = load(gate_path)
        if not (
            gate.get("contract")
            == "tpch_bounded_four_observation_median_prethird_v1"
            and gate.get("passed") is False
            and int(gate.get("physical_workload_calls", -1)) == 0
            and gate.get("formal_state_mutated") is False
            and gate.get("manual_candidate_injection") is False
            and gate.get("thresholds_relaxed") is False
        ):
            return None
        phrase = DETERMINISTIC_FAILURES[stage][0]
        if error_text and phrase not in error_text:
            return None
        return {
            "stage": stage,
            "reason": phrase,
            "evidence_path": str(gate_path.resolve()),
            "evidence_sha256": sha256(gate_path),
        }

    log_path = cycle_root / FAILURE_LOGS[stage]
    if not log_path.is_file():
        return None
    text = log_path.read_text(encoding="utf-8", errors="replace")
    matched = [phrase for phrase in DETERMINISTIC_FAILURES[stage] if phrase in text]
    if len(matched) != 1:
        return None
    return {
        "stage": stage,
        "reason": matched[0],
        "evidence_path": str(log_path.resolve()),
        "evidence_sha256": sha256(log_path),
    }


def negative_gate_payload(
    *,
    cycle: int,
    stage: str,
    failure: Mapping[str, Any],
    accepted: int,
    resume_state: Path,
    expected_formal_sha256: str,
    completed_diagnostics: Sequence[Tuple[Path, Path]],
) -> Dict[str, Any]:
    """Build a fail-closed negative gate after validating every spent replay."""
    if not resume_state.is_file() or sha256(resume_state) != expected_formal_sha256:
        raise RuntimeError("deterministic negative gate observed formal-state mutation")
    evidence = [
        clean5_evidence(
            selection,
            run_root,
            accepted,
            expected_formal_sha256,
        )
        for selection, run_root in completed_diagnostics
    ]
    physical_calls = sum(int(item["physical_calls"]) for item in evidence)
    valid_replays = sum(int(item["valid_replays"]) for item in evidence)
    technical_failures = sum(int(item["technical_failures"]) for item in evidence)
    if physical_calls != 5 * len(evidence) or valid_replays != physical_calls:
        raise RuntimeError("deterministic negative gate accounting mismatch")
    return {
        "contract": LIVENESS_CONTRACT,
        "cycle": cycle,
        "status": f"reexplore_after_{stage}",
        "deterministic_negative_gate": True,
        "failure_stage": stage,
        "failure_reason": failure["reason"],
        "failure_evidence_path": failure["evidence_path"],
        "failure_evidence_sha256": failure["evidence_sha256"],
        "accepted_before": accepted,
        "accepted_after": accepted,
        "diagnostics": evidence,
        "physical_workload_calls": physical_calls,
        "valid_replays": valid_replays,
        "technical_failures": technical_failures,
        "negative_gate_new_workload_calls": 0,
        "formal_started": False,
        "formal_state_mutated": False,
        "formal_state": str(resume_state.resolve()),
        "formal_state_sha256": expected_formal_sha256,
        "accepted_counter_mutated": False,
        "manual_candidate_injection": False,
        "thresholds_relaxed": False,
        "next_cycle": cycle + 1,
        "next_action": "automatic_bounded_explain_reexploration",
        "novelty_gate_unchanged": "five_new_noncorner_three_level_fingerprints",
        "role_gate_unchanged": "trusted3_probe2",
        "per_diagnostic_physical_cap": 5,
        "qps_role": "outer_audit_only_not_inner_score",
    }


def ensure_no_formal_started(cycle_root: Path) -> None:
    forbidden = (cycle_root / "formal_package", cycle_root / "formal_run")
    if any(path.exists() for path in forbidden):
        raise RuntimeError("deterministic failure occurred after formal artifacts appeared")


def import_module(name: str, path: Path) -> Any:
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot import {name}: {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def verified_v41() -> Any:
    for path, expected in EXPECTED.items():
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"v42 dependency SHA mismatch: {path}")
    v41 = import_module("tpch_v42_v41", V41_CONTROLLER)
    v41.V32 = V32_CHECKPOINTED
    v41.EXPANDER = EXPANDER_CHECKPOINTED
    v41.BUILDER = STABILITY_ACQUISITION_BUILDER
    v41.DIAGNOSTIC_RUNNER = STABILITY_DIAGNOSTIC_RUNNER
    v41.EXPECTED[EXPANDER_CHECKPOINTED] = EXPECTED[EXPANDER_CHECKPOINTED]
    v41.EXPECTED[STABILITY_ACQUISITION_BUILDER] = EXPECTED[
        STABILITY_ACQUISITION_BUILDER
    ]
    v41.EXPECTED[STABILITY_DIAGNOSTIC_RUNNER] = EXPECTED[
        STABILITY_DIAGNOSTIC_RUNNER
    ]
    return v41


def validate_formal_authority(path: Path, expected_sha: str) -> Dict[str, Any]:
    if not path.is_file() or sha256(path) != expected_sha:
        raise RuntimeError("formal authority SHA mismatch")
    state = load(path)
    if not (
        state.get("schema_version") == "tpch_exact_plan_regime_formal_state_v1"
        and state.get("status") == "complete"
        and state.get("baseline_restored") is True
        and int(state.get("accepted_count", -1)) == 1
        and int(state.get("physical_call_count", -1)) == 5
        and int(state.get("valid_replay_count", -1)) == 5
        and int(state.get("technical_failure_count", -1)) == 0
    ):
        raise RuntimeError("formal authority is not clean accepted1 5/5/0")
    return state


def current_paths(v41: Any) -> Dict[str, Path]:
    return {
        "state": v41.V41_LOOP / "controller_state.json",
        "lock": v41.V41_LOOP / "controller.lock",
        "cycle": v41.V41_LOOP / "cycle_004",
        "counter": v41.V41_COUNTER,
        "handoff": v41.V41_ROOT / "preformal_handoff_state.json",
        "reuse_audit": v41.V41_ROOT / "v40_diagnostic_reuse_audit.json",
        "recovery_audit": v41.V41_LOOP / "v42_cycle004_recovery_audit.json",
        "clean5_recovery_audit": (
            v41.V41_LOOP / "v45_v43_clean5_zero_call_recovery.json"
        ),
    }


def validate_counter(path: Path, expected_sha: str) -> None:
    if not (
        path.is_file()
        and sha256(path) == expected_sha
        and path.read_text(encoding="utf-8").strip() == "1"
    ):
        raise RuntimeError("accepted counter is not the sealed value 1")


def validate_failed_boundary(
    v41: Any,
    paths: Mapping[str, Path],
    expected_state_sha: str,
    expected_formal_sha: str,
    expected_counter_sha: str,
) -> Dict[str, Any]:
    state_path = paths["state"]
    if not state_path.is_file() or sha256(state_path) != expected_state_sha:
        raise RuntimeError("controller_state SHA mismatch")
    state = load(state_path)
    formal_state = Path(str(state.get("resume_state") or ""))
    if not (
        state.get("schema_version") == "tpch_fresh10_target10_loop_v26"
        and state.get("status") == "preparing_cycle"
        and int(state.get("accepted_count", -1)) == 1
        and int(state.get("accepted_target", -1)) == 10
        and int(state.get("next_cycle", -1)) == 4
        and state.get("cycles") == []
    ):
        raise RuntimeError("controller is not at the exact cycle004 boundary")
    validate_counter(paths["counter"], expected_counter_sha)
    validate_formal_authority(formal_state, expected_formal_sha)

    handoff = load(paths["handoff"])
    formal_run = Path(str(handoff.get("adopted_formal_run") or ""))
    if not (
        handoff.get("adopted_formal_started") is True
        and handoff.get("adopted_formal_clean") is True
        and int(handoff.get("accepted_count", -1)) == 1
        and formal_run / "formal_state.json" == formal_state
    ):
        raise RuntimeError("v41 handoff/formal authority binding mismatch")

    cycle = paths["cycle"]
    if not cycle.is_dir():
        raise RuntimeError("failed cycle004 directory is missing")
    allowed_files = {
        "config.a.json",
        "config.b.json",
        "dynamic_expansion_v32/expand_a.log",
    }
    observed_files = {
        str(path.relative_to(cycle)) for path in cycle.rglob("*") if path.is_file()
    }
    if observed_files != allowed_files:
        raise RuntimeError(
            "cycle004 is not the exact zero-workload budget-failure shape: "
            f"{sorted(observed_files)}"
        )
    if (cycle / "config.a.json").read_bytes() != (
        cycle / "config.b.json"
    ).read_bytes():
        raise RuntimeError("cycle004 deterministic configs differ")
    failure_log = cycle / "dynamic_expansion_v32/expand_a.log"
    if "candidate-estimation budget exhausted" not in failure_log.read_text(
        encoding="utf-8"
    ):
        raise RuntimeError("cycle004 does not contain the sealed budget failure")
    forbidden_directories = [
        path
        for path in cycle.rglob("*")
        if path.is_dir()
        and (
            path.name.startswith("attempt_")
            or path.name.startswith("diagnostic_")
            or path.name.startswith("formal_")
        )
    ]
    if forbidden_directories:
        raise RuntimeError("cycle004 contains replay/workload directories")
    return {
        "state": state,
        "controller_state_sha256": expected_state_sha,
        "formal_state": formal_state,
        "formal_run": formal_run,
        "observed_files": sorted(observed_files),
    }


def preflight_report(
    v41: Any,
    boundary: Mapping[str, Any],
    paths: Mapping[str, Path],
) -> Dict[str, Any]:
    return {
        "contract": RECOVERY_CONTRACT,
        "status": "audit_passed_ready_to_apply",
        "accepted_count_before": 1,
        "accepted_count_after": 1,
        "formal_state": str(boundary["formal_state"]),
        "formal_state_sha256": sha256(boundary["formal_state"]),
        "controller_state_sha256": boundary["controller_state_sha256"],
        "accepted_counter_sha256": sha256(paths["counter"]),
        "cycle": 4,
        "failed_cycle_files": boundary["observed_files"],
        "physical_workload_calls_in_failed_cycle": 0,
        "formal_state_mutated": False,
        "accepted_counter_mutated": False,
        "manual_candidate_selection": False,
        "next_algorithm": "checkpointed_bounded_expansion_v42",
    }


def apply_recovery(
    v41: Any,
    paths: Mapping[str, Path],
    boundary: Mapping[str, Any],
) -> Dict[str, Any]:
    if paths["recovery_audit"].exists():
        raise RuntimeError("v42 recovery audit already exists; refuse reapply")
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    state_backup = paths["state"].with_name(
        f"controller_state.pre_v42.{timestamp}.json"
    )
    quarantine = paths["cycle"].with_name(
        f"cycle_004.pre_v42_budget_exhausted.{timestamp}"
    )
    if state_backup.exists() or quarantine.exists():
        raise RuntimeError("v42 recovery backup target already exists")
    shutil.copy2(paths["state"], state_backup)
    os.replace(paths["cycle"], quarantine)
    recovered_state = dict(boundary["state"])
    recovered_state["status"] = "restart_ready_v42"
    recovered_state["v42_recovery"] = {
        "contract": RECOVERY_CONTRACT,
        "state_backup": str(state_backup),
        "state_backup_sha256": sha256(state_backup),
        "quarantined_cycle": str(quarantine),
        "quarantined_cycle_has_workload_calls": False,
        "formal_state_sha256": sha256(boundary["formal_state"]),
        "accepted_counter_sha256": sha256(paths["counter"]),
    }
    atomic_json(paths["state"], recovered_state)
    audit = {
        **preflight_report(v41, boundary, paths),
        "status": "recovery_applied_zero_call",
        "state_backup": str(state_backup),
        "state_backup_sha256": sha256(state_backup),
        "quarantined_cycle": str(quarantine),
        "controller_state_sha256_after": sha256(paths["state"]),
        "new_explain_calls": 0,
        "new_physical_workload_calls": 0,
    }
    atomic_json(paths["recovery_audit"], audit)
    audit["recovery_audit_sha256"] = sha256(paths["recovery_audit"])
    return audit


def validate_clean5_boundary(
    v41: Any,
    paths: Mapping[str, Path],
    *,
    expected_state_sha: str,
    expected_formal_sha: str,
    expected_counter_sha: str,
    expected_cycle: int,
) -> Dict[str, Any]:
    """Validate the v43 post-clean5/launcher-TypeError boundary exactly."""
    state_path = paths["state"]
    if not state_path.is_file() or sha256(state_path) != expected_state_sha:
        raise RuntimeError("clean5 controller_state SHA mismatch")
    state = load(state_path)
    accepted = int(state.get("accepted_count", -1))
    resume_state = Path(str(state.get("resume_state") or ""))
    v42_recovery = state.get("v42_recovery") or {}
    if not (
        state.get("schema_version") == "tpch_fresh10_target10_loop_v26"
        and state.get("status") == "preparing_cycle"
        and accepted == 1
        and int(state.get("accepted_target", -1)) == 10
        and int(state.get("next_cycle", -1)) == expected_cycle
        and isinstance(state.get("cycles"), list)
        and v42_recovery.get("contract") == RECOVERY_CONTRACT
        and v42_recovery.get("formal_state_sha256") == expected_formal_sha
        and v42_recovery.get("accepted_counter_sha256")
        == expected_counter_sha
    ):
        raise RuntimeError("controller is not at the raw v43 clean5 boundary")
    validate_counter(paths["counter"], expected_counter_sha)
    validate_formal_authority(resume_state, expected_formal_sha)

    cycle_root = v41.V41_LOOP / f"cycle_{expected_cycle:03d}"
    config_a = cycle_root / "config.a.json"
    config_b = cycle_root / "config.b.json"
    probe_a = cycle_root / "probe_top5.a.json"
    probe_b = cycle_root / "probe_top5.b.json"
    probe_run = cycle_root / "diagnostic_probe"
    failure_log = cycle_root / "diagnostic_probe.log"
    pairs = ((config_a, config_b, "config"), (probe_a, probe_b, "probe"))
    for first, second, label in pairs:
        if not (
            first.is_file()
            and second.is_file()
            and first.read_bytes() == second.read_bytes()
        ):
            raise RuntimeError(f"v43 clean5 deterministic {label} pair mismatch")
    evidence = clean5_evidence(
        probe_a,
        probe_run,
        accepted,
        expected_formal_sha,
    )
    if not failure_log.is_file():
        raise RuntimeError("v43 post-clean5 TypeError log is missing")
    failure_text = failure_log.read_text(encoding="utf-8", errors="replace")
    if not (
        "TypeError" in failure_text
        and "NoneType" in failure_text
        and "int() argument" in failure_text
        and "original.main()" in failure_text
    ):
        raise RuntimeError("diagnostic log is not the sealed None-return TypeError")
    forbidden = (
        cycle_root / "exact_repeat_top5.a.json",
        cycle_root / "exact_repeat_top5.b.json",
        cycle_root / "diagnostic_exact_repeat",
        cycle_root / "robust_top5.a.json",
        cycle_root / "robust_top5.b.json",
        cycle_root / "diagnostic_robust_repeat",
        cycle_root / "formal_package",
        cycle_root / "formal_run",
    )
    if any(path.exists() for path in forbidden):
        raise RuntimeError("v43 clean5 boundary has downstream artifacts")
    prepared_audit: Optional[Dict[str, Any]] = None
    audit_path = paths["clean5_recovery_audit"]
    if audit_path.exists():
        candidate_audit = load(audit_path)
        backup = Path(str(candidate_audit.get("state_backup") or ""))
        if not (
            candidate_audit.get("contract") == CLEAN5_RECOVERY_CONTRACT
            and candidate_audit.get("status")
            == "prepared_existing_clean5_zero_call"
            and candidate_audit.get("controller_state_sha256_before")
            == expected_state_sha
            and candidate_audit.get("formal_state_sha256")
            == expected_formal_sha
            and candidate_audit.get("accepted_counter_sha256")
            == expected_counter_sha
            and candidate_audit.get("clean5") == evidence
            and int(candidate_audit.get("new_physical_workload_calls", -1)) == 0
            and backup.is_file()
            and candidate_audit.get("state_backup_sha256") == sha256(backup)
            and sha256(backup) == expected_state_sha
        ):
            raise RuntimeError("existing v45 clean5 journal is not resumable")
        prepared_audit = candidate_audit
    return {
        "state": state,
        "accepted": accepted,
        "resume_state": resume_state,
        "cycle_root": cycle_root,
        "config": config_a,
        "probe": probe_a,
        "probe_run": probe_run,
        "failure_log": failure_log,
        "clean5": evidence,
        "prepared_audit": prepared_audit,
        "preflight": {
            "contract": CLEAN5_RECOVERY_CONTRACT,
            "status": "ready_to_seal_existing_clean5",
            "cycle": expected_cycle,
            "accepted_before": accepted,
            "accepted_after": accepted,
            "existing_physical_workload_calls": 5,
            "existing_valid_replays": 5,
            "existing_technical_failures": 0,
            "new_physical_workload_calls": 0,
            "new_explain_calls": 0,
            "formal_state_mutated": False,
            "accepted_counter_mutated": False,
            "baseline_restored": True,
            "clean5": evidence,
            "failure_log_sha256": sha256(failure_log),
            "formal_state_sha256": expected_formal_sha,
            "accepted_counter_sha256": expected_counter_sha,
            "controller_state_sha256_before": expected_state_sha,
            "source_v42_recovery_contract": RECOVERY_CONTRACT,
            "source_v42_formal_state_sha256": expected_formal_sha,
            "source_v42_accepted_counter_sha256": expected_counter_sha,
        },
    }


def seal_clean5_boundary(
    paths: Mapping[str, Path],
    boundary: Mapping[str, Any],
    *,
    crash_after_prepared_journal: bool = False,
) -> Dict[str, Any]:
    """Seal existing clean5 evidence without generating any new workload."""
    audit_path = paths["clean5_recovery_audit"]
    prepared = boundary.get("prepared_audit")
    if prepared is None:
        if audit_path.exists():
            raise RuntimeError("unexpected v45 clean5 recovery journal")
        timestamp = time.strftime("%Y%m%dT%H%M%S")
        backup = paths["state"].with_name(
            f"controller_state.pre_v45_clean5.{timestamp}.json"
        )
        if backup.exists():
            raise RuntimeError("v45 clean5 state backup target already exists")
        shutil.copy2(paths["state"], backup)
        audit = {
            **dict(boundary["preflight"]),
            "status": "prepared_existing_clean5_zero_call",
            "state_backup": str(backup.resolve()),
            "state_backup_sha256": sha256(backup),
            "recovery_audit": str(audit_path.resolve()),
            "manual_candidate_injection": False,
            "thresholds_relaxed": False,
            "next_action": "commit_state_then_resume_at_exact_repeat",
        }
        atomic_json(audit_path, audit)
    else:
        audit = dict(prepared)
        backup = Path(str(audit["state_backup"]))
    if crash_after_prepared_journal:
        raise RuntimeError("injected crash after clean5 prepared journal")
    state = dict(boundary["state"])
    state["status"] = "v45_clean5_sealed_ready"
    state["v45_clean5_recovery"] = {
        "contract": CLEAN5_RECOVERY_CONTRACT,
        "cycle": int(boundary["preflight"]["cycle"]),
        "audit": str(audit_path.resolve()),
        "audit_sha256": sha256(audit_path),
        "state_backup": str(backup.resolve()),
        "state_backup_sha256": sha256(backup),
        "probe_selection_sha256": boundary["clean5"]["selection_sha256"],
        "diagnostic_state_sha256": boundary["clean5"]["state_sha256"],
        "diagnostic_summary_sha256": boundary["clean5"]["summary_sha256"],
        "new_physical_workload_calls": 0,
        "source_v42_recovery_contract": RECOVERY_CONTRACT,
        "source_v42_formal_state_sha256": boundary["preflight"][
            "source_v42_formal_state_sha256"
        ],
        "source_v42_accepted_counter_sha256": boundary["preflight"][
            "source_v42_accepted_counter_sha256"
        ],
    }
    atomic_json(paths["state"], state)
    return {
        **audit,
        "status": "committed_existing_clean5_zero_call",
        "controller_state_sha256_after": sha256(paths["state"]),
        "recovery_audit_sha256": sha256(audit_path),
    }


def validate_sealed_clean5_boundary(
    v41: Any,
    paths: Mapping[str, Path],
    *,
    expected_state_sha: str,
    expected_formal_sha: str,
    expected_counter_sha: str,
    expected_cycle: int,
) -> Dict[str, Any]:
    """Revalidate a sealed clean5 immediately before exact-repeat continuation."""
    state_path = paths["state"]
    if not state_path.is_file() or sha256(state_path) != expected_state_sha:
        raise RuntimeError("sealed clean5 controller_state SHA mismatch")
    state = load(state_path)
    accepted = int(state.get("accepted_count", -1))
    recovery = state.get("v45_clean5_recovery") or {}
    v42_recovery = state.get("v42_recovery") or {}
    audit_path = paths["clean5_recovery_audit"]
    if not (
        state.get("status") in {"v45_clean5_sealed_ready", "preparing_cycle"}
        and accepted == 1
        and int(state.get("accepted_target", -1)) == 10
        and int(state.get("next_cycle", -1)) == expected_cycle
        and recovery.get("contract") == CLEAN5_RECOVERY_CONTRACT
        and int(recovery.get("cycle", -1)) == expected_cycle
        and audit_path.is_file()
        and recovery.get("audit") == str(audit_path.resolve())
        and recovery.get("audit_sha256") == sha256(audit_path)
        and int(recovery.get("new_physical_workload_calls", -1)) == 0
        and recovery.get("source_v42_recovery_contract") == RECOVERY_CONTRACT
        and recovery.get("source_v42_formal_state_sha256")
        == expected_formal_sha
        and recovery.get("source_v42_accepted_counter_sha256")
        == expected_counter_sha
        and v42_recovery.get("contract") == RECOVERY_CONTRACT
        and v42_recovery.get("formal_state_sha256") == expected_formal_sha
        and v42_recovery.get("accepted_counter_sha256")
        == expected_counter_sha
    ):
        raise RuntimeError("controller is not at the sealed v45 clean5 boundary")
    resume_state = Path(str(state.get("resume_state") or ""))
    validate_counter(paths["counter"], expected_counter_sha)
    validate_formal_authority(resume_state, expected_formal_sha)
    cycle_root = v41.V41_LOOP / f"cycle_{expected_cycle:03d}"
    config = cycle_root / "config.a.json"
    probe = cycle_root / "probe_top5.a.json"
    probe_run = cycle_root / "diagnostic_probe"
    evidence = clean5_evidence(
        probe,
        probe_run,
        accepted,
        expected_formal_sha,
    )
    if not (
        (cycle_root / "config.b.json").read_bytes() == config.read_bytes()
        and (cycle_root / "probe_top5.b.json").read_bytes() == probe.read_bytes()
        and recovery.get("probe_selection_sha256") == evidence["selection_sha256"]
        and recovery.get("diagnostic_state_sha256") == evidence["state_sha256"]
        and recovery.get("diagnostic_summary_sha256") == evidence["summary_sha256"]
    ):
        raise RuntimeError("sealed clean5 evidence binding mismatch")
    downstream = (
        cycle_root / "exact_repeat_top5.a.json",
        cycle_root / "exact_repeat_top5.b.json",
        cycle_root / "exact_builder_a.log",
        cycle_root / "exact_builder_b.log",
        cycle_root / "diagnostic_exact_repeat",
        cycle_root / "robust_top5.a.json",
        cycle_root / "diagnostic_robust_repeat",
        cycle_root / "formal_package",
        cycle_root / "formal_run",
    )
    if any(path.exists() for path in downstream):
        raise RuntimeError("sealed clean5 has unexpected downstream artifacts")
    return {
        "state": state,
        "accepted": accepted,
        "resume_state": resume_state,
        "cycle_root": cycle_root,
        "config": config,
        "probe": probe,
        "probe_run": probe_run,
        "clean5": evidence,
    }


def executed_history(v41: Any, paths: Mapping[str, Path]) -> Iterable[Path]:
    audit = load(paths["reuse_audit"])
    for item in audit.get("v40_executed_selections_added_to_history") or []:
        path = Path(str(item.get("path") or ""))
        if not path.is_file() or sha256(path) != item.get("sha256"):
            raise RuntimeError(f"executed-history binding mismatch: {path}")
        yield path
    adopted = v41.ADOPTION_ROOT / "a/selection.json"
    if not adopted.is_file():
        raise RuntimeError("adopted v41 selection is missing")
    yield adopted


def commit_negative_reexploration(
    v26: Any,
    paths: Mapping[str, Path],
    state: Dict[str, Any],
    *,
    cycle: int,
    cycle_root: Path,
    stage: str,
    failure: Mapping[str, Any],
    accepted: int,
    resume_state: Path,
    expected_formal_sha256: str,
    completed_diagnostics: Sequence[Tuple[Path, Path]],
) -> Dict[str, Any]:
    ensure_no_formal_started(cycle_root)
    if v26.accepted_counter() != accepted:
        raise RuntimeError("deterministic negative gate observed accepted-counter mutation")
    if int(state.get("accepted_count", -1)) != accepted:
        raise RuntimeError("controller accepted count changed before negative gate")
    payload = negative_gate_payload(
        cycle=cycle,
        stage=stage,
        failure=failure,
        accepted=accepted,
        resume_state=resume_state,
        expected_formal_sha256=expected_formal_sha256,
        completed_diagnostics=completed_diagnostics,
    )
    audit_path = cycle_root / "v44_deterministic_negative_gate.json"
    if audit_path.exists():
        raise RuntimeError("deterministic negative gate already exists")
    v26.atomic_json(audit_path, payload)
    cycle_audit = {
        **payload,
        "negative_gate_path": str(audit_path.resolve()),
        "negative_gate_sha256": v26.sha256(audit_path),
    }
    state.setdefault("cycles", []).append(cycle_audit)
    state["status"] = str(payload["status"])
    state["next_cycle"] = cycle + 1
    v26.atomic_json(paths["state"], state)
    return cycle_audit


def run_loop(
    v41: Any,
    paths: Mapping[str, Path],
    state: Dict[str, Any],
    *,
    resume_existing_cycle004: bool = False,
    start_cycle: int = 4,
    previous_selection_override: Optional[Path] = None,
    previous_run_override: Optional[Path] = None,
    resume_completed_probe: Optional[Tuple[Path, Path, Path]] = None,
    resume_completed_gate: Optional[Mapping[str, Path]] = None,
    crash_after_preparing_completed_probe: bool = False,
) -> int:
    formal_state = Path(str(state["resume_state"]))
    handoff = load(paths["handoff"])
    formal_run = Path(str(handoff["adopted_formal_run"]))
    previous_selection = (
        previous_selection_override
        if previous_selection_override is not None
        else v41.ADOPTION_ROOT / "a/selection.json"
    )
    previous_run = (
        previous_run_override
        if previous_run_override is not None
        else previous_selection.parent
    )
    history = list(executed_history(v41, paths))
    v26, common = v41.configure_controller(
        formal_state,
        previous_selection,
        previous_run,
        history,
        formal_run,
    )
    environment = common.runtime_environment()
    accepted = 1
    resume_state = formal_state
    for cycle in range(start_cycle, start_cycle + v26.MAX_CYCLES):
        if accepted >= v26.TARGET:
            state["status"] = "target_reached"
            v26.atomic_json(paths["state"], state)
            return 0
        cycle_root = v26.LOOP_ROOT / f"cycle_{cycle:03d}"
        reusing_completed_probe = (
            resume_completed_probe is not None and cycle == start_cycle
        )
        reusing_completed_gate = (
            resume_completed_gate is not None and cycle == start_cycle
        )
        cycle_root.mkdir(
            mode=0o700,
            exist_ok=(resume_existing_cycle004 and cycle == 4)
            or reusing_completed_probe
            or reusing_completed_gate,
        )
        resume_existing_cycle004 = False
        state["status"] = "preparing_cycle"
        state["next_cycle"] = cycle
        v26.atomic_json(paths["state"], state)
        if reusing_completed_probe and crash_after_preparing_completed_probe:
            raise RuntimeError("injected crash after preparing completed clean5")
        formal_sha_before_cycle = v26.sha256(resume_state)
        if reusing_completed_gate:
            completed = dict(resume_completed_gate or {})
            required = {
                "config",
                "probe",
                "probe_run",
                "repeat",
                "repeat_run",
                "robust",
                "robust_run",
                "resealed_selection",
                "resealed_run",
                "analysis",
            }
            if set(completed) != required:
                raise RuntimeError("completed gate resume payload is incomplete")
            if any(
                path.parent != cycle_root
                for name, path in completed.items()
                if name in {"config", "probe", "repeat", "robust", "analysis"}
            ):
                raise RuntimeError("completed gate artifacts do not belong to resumed cycle")
            diagnostics = [
                (completed["probe"], completed["probe_run"]),
                (completed["repeat"], completed["repeat_run"]),
                (completed["robust"], completed["robust_run"]),
            ]
            clean_diagnostics = [
                clean5_evidence(
                    selected,
                    run_root,
                    accepted,
                    formal_sha_before_cycle,
                )
                for selected, run_root in diagnostics
            ]
            binder = getattr(v26, "bind_cycle_resume_state", None)
            reseal_binder = getattr(v26, "bind_resealed_evidence", None)
            if not callable(binder) or not callable(reseal_binder):
                raise RuntimeError("completed gate resume binders are not installed")
            binder(cycle_root, resume_state)
            reseal_binder(
                completed["robust"],
                completed["robust_run"],
                completed["resealed_selection"],
                completed["resealed_run"],
            )
            absorption, previous_selection, previous_run = (
                absorb_actual_best_probe_rank4(
                    v26,
                    cycle_root=cycle_root,
                    selection=completed["resealed_selection"],
                    validation_run=completed["resealed_run"],
                    analysis_path=completed["analysis"],
                    accepted=accepted,
                    resume_state=resume_state,
                    expected_formal_sha256=formal_sha_before_cycle,
                )
            )
            cycle_audit = {
                "cycle": cycle,
                "accepted_before": accepted,
                "accepted_after": accepted,
                "formal_started": False,
                "entry_gate_passed": False,
                "physical_workload_calls": 15,
                "valid_replays": 15,
                "technical_failures": 0,
                "new_workload_calls_during_resume": 0,
                "completed_diagnostics": clean_diagnostics,
                "v47_postclean5_resume_state_binding": {
                    "contract": POSTCLEAN5_BINDING_CONTRACT,
                    "cycle_root": str(cycle_root.resolve()),
                    "resume_state": str(resume_state.resolve()),
                    "resume_state_sha256": formal_sha_before_cycle,
                    "resealed_selection_sha256": v26.sha256(
                        completed["resealed_selection"]
                    ),
                    "state_mutation": False,
                    "new_workload_calls": 0,
                },
                "v47_rank4_probe_absorption": absorption,
                "next_reexploration_selection_sha256": v26.sha256(
                    previous_selection
                ),
            }
            state.setdefault("cycles", []).append(cycle_audit)
            state["status"] = "reexplore_after_actual_best_probe_rank4"
            state["next_cycle"] = cycle + 1
            v26.atomic_json(paths["state"], state)
            resume_completed_gate = None
            continue
        if reusing_completed_probe:
            config, probe, probe_run = resume_completed_probe
            if config.parent != cycle_root or probe.parent != cycle_root:
                raise RuntimeError("completed clean5 does not belong to resumed cycle")
            clean5_evidence(
                probe,
                probe_run,
                accepted,
                formal_sha_before_cycle,
            )
            if v26.sha256(resume_state) != formal_sha_before_cycle:
                raise RuntimeError("formal state changed while binding completed clean5")
            if v26.accepted_counter() != accepted:
                raise RuntimeError("accepted counter changed while binding completed clean5")
            binder = getattr(v26, "bind_cycle_resume_state", None)
            if not callable(binder):
                raise RuntimeError("explicit cycle resume-state binder is not installed")
            binder(cycle_root, resume_state)
            resume_completed_probe = None
        else:
            config = v26.dynamic_config(common, cycle_root, accepted)
            probe = v26.build_probe(
                common,
                cycle_root,
                previous_selection,
                previous_run,
                resume_state,
                accepted,
                environment,
            )
            probe_run = cycle_root / "diagnostic_probe"
            v26.run_clean_diagnostic(
                common,
                probe,
                probe_run,
                config,
                resume_state,
                accepted,
                cycle_root / "diagnostic_probe.log",
                environment,
            )
        try:
            repeat = v26.build_exact_repeat(
                common, cycle_root, probe, probe_run, accepted, environment
            )
        except RuntimeError as exc:
            failure = deterministic_failure(
                "exact_repeat", cycle_root, str(exc)
            )
            if failure is None:
                raise
            commit_negative_reexploration(
                v26,
                paths,
                state,
                cycle=cycle,
                cycle_root=cycle_root,
                stage="exact_repeat",
                failure=failure,
                accepted=accepted,
                resume_state=resume_state,
                expected_formal_sha256=formal_sha_before_cycle,
                completed_diagnostics=[(probe, probe_run)],
            )
            previous_selection = probe
            previous_run = probe_run
            continue
        repeat_run = cycle_root / "diagnostic_exact_repeat"
        v26.run_clean_diagnostic(
            common,
            repeat,
            repeat_run,
            config,
            resume_state,
            accepted,
            cycle_root / "diagnostic_exact_repeat.log",
            environment,
        )
        try:
            robust = v26.build_robust_repeat(
                common,
                cycle_root,
                probe,
                probe_run,
                repeat,
                repeat_run,
                accepted,
                environment,
            )
        except RuntimeError as exc:
            failure = deterministic_failure(
                "prethird_feasibility", cycle_root, str(exc)
            ) or deterministic_failure("robust_repeat", cycle_root, str(exc))
            if failure is None:
                raise
            stage = str(failure["stage"])
            commit_negative_reexploration(
                v26,
                paths,
                state,
                cycle=cycle,
                cycle_root=cycle_root,
                stage=stage,
                failure=failure,
                accepted=accepted,
                resume_state=resume_state,
                expected_formal_sha256=formal_sha_before_cycle,
                completed_diagnostics=[
                    (probe, probe_run),
                    (repeat, repeat_run),
                ],
            )
            previous_selection = repeat
            previous_run = repeat_run
            continue
        robust_run = cycle_root / "diagnostic_robust_repeat"
        v26.run_clean_diagnostic(
            common,
            robust,
            robust_run,
            config,
            resume_state,
            accepted,
            cycle_root / "diagnostic_robust_repeat.log",
            environment,
        )
        try:
            analysis_path = v26.analyze_gate(
                common, cycle_root, robust, robust_run, environment
            )
        except RuntimeError as exc:
            failure = deterministic_failure(
                "postrobust_reseal", cycle_root, str(exc)
            )
            if failure is None:
                raise
            commit_negative_reexploration(
                v26,
                paths,
                state,
                cycle=cycle,
                cycle_root=cycle_root,
                stage="postrobust_reseal",
                failure=failure,
                accepted=accepted,
                resume_state=resume_state,
                expected_formal_sha256=formal_sha_before_cycle,
                completed_diagnostics=[
                    (probe, probe_run),
                    (repeat, repeat_run),
                    (robust, robust_run),
                ],
            )
            previous_selection = robust
            previous_run = robust_run
            continue
        analysis = v26.load(analysis_path)
        cycle_audit: Dict[str, Any] = {
            "cycle": cycle,
            "accepted_before": accepted,
            "probe_selection_sha256": v26.sha256(probe),
            "repeat_selection_sha256": v26.sha256(repeat),
            "robust_selection_sha256": v26.sha256(robust),
            "entry_gate_sha256": v26.sha256(analysis_path),
            "entry_gate_passed": analysis.get("passed") is True,
            "v42_checkpointed_expansion": True,
        }
        previous_selection = robust
        previous_run = robust_run
        if analysis.get("passed") is not True:
            resolver = getattr(v26, "resolve_reexploration_evidence", None)
            if not callable(resolver):
                raise RuntimeError("re-exploration evidence resolver is not installed")
            gate_selection, gate_run = resolver(robust, robust_run)
            absorption, previous_selection, previous_run = (
                absorb_actual_best_probe_rank4(
                    v26,
                    cycle_root=cycle_root,
                    selection=gate_selection,
                    validation_run=gate_run,
                    analysis_path=analysis_path,
                    accepted=accepted,
                    resume_state=resume_state,
                    expected_formal_sha256=formal_sha_before_cycle,
                )
            )
            cycle_audit["formal_started"] = False
            cycle_audit["accepted_after"] = accepted
            cycle_audit["v47_rank4_probe_absorption"] = absorption
            cycle_audit["next_reexploration_selection_sha256"] = v26.sha256(
                previous_selection
            )
            state["status"] = "reexplore_after_actual_best_probe_rank4"
            state["cycles"].append(cycle_audit)
            state["next_cycle"] = cycle + 1
            v26.atomic_json(paths["state"], state)
            continue
        formal_selection, gate, next_formal_run = v26.promote_formal(
            common,
            cycle_root,
            robust,
            robust_run,
            analysis_path,
            accepted,
            config,
        )
        formal_error = None
        try:
            v26.run_formal(
                common,
                formal_selection,
                gate,
                next_formal_run,
                config,
                resume_state,
                environment,
            )
        except RuntimeError as exc:
            formal_error = str(exc)
        accepted_after, next_state, clean = v26.validate_formal_completion(
            next_formal_run,
            accepted,
            allow_wait=False,
        )
        cycle_audit.update(
            {
                "formal_started": True,
                "formal_selection_sha256": v26.sha256(formal_selection),
                "formal_gate_sha256": v26.sha256(gate),
                "formal_clean": clean,
                "formal_error": formal_error,
                "accepted_after": accepted_after,
                "formal_state_sha256": v26.sha256(next_state),
            }
        )
        state["cycles"].append(cycle_audit)
        if clean:
            accepted = accepted_after
            resume_state = next_state
            state["accepted_count"] = accepted
            state["resume_state"] = str(resume_state)
        elif accepted_after != accepted:
            raise RuntimeError("technical formal batch changed accepted count")
        state["next_cycle"] = cycle + 1
        v26.atomic_json(paths["state"], state)
    state["status"] = "blocked_max_cycles"
    v26.atomic_json(paths["state"], state)
    raise RuntimeError("TPC-H target10 loop exhausted maximum cycles")


def resume(
    v41: Any,
    paths: Mapping[str, Path],
    expected_state_sha: str,
    expected_formal_sha: str,
    expected_counter_sha: str,
) -> int:
    if not paths["state"].is_file() or sha256(paths["state"]) != expected_state_sha:
        raise RuntimeError("recovered controller_state SHA mismatch")
    state = load(paths["state"])
    recovery = state.get("v42_recovery") or {}
    if not (
        int(state.get("accepted_count", -1)) == 1
        and int(state.get("next_cycle", -1)) == 4
        and recovery.get("contract") == RECOVERY_CONTRACT
        and paths["recovery_audit"].is_file()
    ):
        raise RuntimeError("controller is not at the sealed v42 restart boundary")
    status = state.get("status")
    cycle_exists = paths["cycle"].exists()
    resume_existing_cycle004 = False
    if status == "restart_ready_v42" and not cycle_exists:
        pass
    elif status == "preparing_cycle" and cycle_exists:
        allowed_root_files = {
            "config.a.json",
            "config.b.json",
            "probe_top5.a.json",
            "probe_top5.b.json",
            "probe_builder_a.log",
            "probe_builder_b.log",
        }
        forbidden = []
        unexpected = []
        for path in paths["cycle"].rglob("*"):
            relative = str(path.relative_to(paths["cycle"]))
            if path.is_dir() and (
                path.name.startswith("attempt_")
                or path.name.startswith("diagnostic_")
                or path.name.startswith("formal_")
            ):
                forbidden.append(relative)
            if path.is_file() and not (
                relative in allowed_root_files
                or relative.startswith("dynamic_expansion_v32/")
            ):
                unexpected.append(relative)
        if forbidden or unexpected:
            raise RuntimeError(
                "cycle004 restart is not a zero-workload preworkload boundary: "
                f"forbidden={sorted(forbidden)} unexpected={sorted(unexpected)}"
            )
        config_a = paths["cycle"] / "config.a.json"
        config_b = paths["cycle"] / "config.b.json"
        if not (
            config_a.is_file()
            and config_b.is_file()
            and config_a.read_bytes() == config_b.read_bytes()
        ):
            raise RuntimeError("resumable cycle004 lacks deterministic configs")
        resume_existing_cycle004 = True
    else:
        raise RuntimeError(
            "controller status/cycle shape is not resumable: "
            f"status={status} cycle_exists={cycle_exists}"
        )
    formal_state = Path(str(state.get("resume_state") or ""))
    validate_counter(paths["counter"], expected_counter_sha)
    validate_formal_authority(formal_state, expected_formal_sha)
    if recovery.get("formal_state_sha256") != expected_formal_sha:
        raise RuntimeError("recovery/formal SHA binding mismatch")
    return run_loop(
        v41,
        paths,
        state,
        resume_existing_cycle004=resume_existing_cycle004,
    )


def resume_sealed_clean5(
    v41: Any,
    paths: Mapping[str, Path],
    boundary: Mapping[str, Any],
) -> int:
    """Continue at exact-repeat, consuming no second copy of the probe."""
    state = dict(boundary["state"])
    cycle = int((state.get("v45_clean5_recovery") or {})["cycle"])
    return run_loop(
        v41,
        paths,
        state,
        start_cycle=cycle,
        resume_completed_probe=(
            Path(str(boundary["config"])),
            Path(str(boundary["probe"])),
            Path(str(boundary["probe_run"])),
        ),
    )


def validate_postclean5_feasibility(
    cycle_root: Path,
    *,
    expected_formal_sha: str,
    repeat_selection: Path,
    repeat_run: Path,
    expected_primary_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    """Bind either live prethird or legacy post-reseal feasibility evidence."""

    primary = cycle_root / "prethird_acceptance_feasibility.json"
    legacy = cycle_root / "acceptance_feasibility.json"
    existing = [path for path in (primary, legacy) if path.is_file()]
    if not existing:
        raise RuntimeError("postclean5 acceptance feasibility audit is missing")
    if len(existing) == 2 and primary.read_bytes() != legacy.read_bytes():
        raise RuntimeError("postclean5 feasibility aliases disagree")
    selected_path = primary if primary.is_file() else legacy
    selected_sha256 = sha256(selected_path)
    if (
        selected_path == primary
        and expected_primary_sha256 is not None
        and selected_sha256 != expected_primary_sha256
    ):
        raise RuntimeError("postclean5 prethird feasibility SHA mismatch")
    feasibility = load(selected_path)
    common_valid = bool(
        feasibility.get("passed") is True
        and int(feasibility.get("physical_workload_calls", -1)) == 0
        and feasibility.get("formal_state_mutated") is False
        and feasibility.get("manual_candidate_injection") is False
        and feasibility.get("thresholds_relaxed") is False
    )
    if selected_path == primary:
        bounds = feasibility.get("candidate_bounds") or []
        incumbent = float(feasibility.get("incumbent_score"))
        maximum = float(feasibility.get("maximum_utility_score"))
        derived_feasible = []
        rows_valid = bool(
            len(bounds) == 5
            and all(isinstance(row, dict) for row in bounds)
            and [int(row.get("rank", -1)) for row in bounds]
            == [1, 2, 3, 4, 5]
            and [row.get("selection_role") for row in bounds]
            == ["trusted", "trusted", "trusted", "probe", "probe"]
            and math.isfinite(incumbent)
            and math.isfinite(maximum)
            and maximum > incumbent
        )
        if rows_valid:
            for row in bounds:
                first = float(row.get("first_score"))
                second = float(row.get("second_score"))
                upper = float(row.get("maximum_four_observation_median"))
                feasible = upper > incumbent
                if not (
                    math.isfinite(first)
                    and math.isfinite(second)
                    and math.isfinite(upper)
                    and math.isclose(
                        upper,
                        (max(first, second) + maximum) / 2.0,
                        rel_tol=1e-12,
                        abs_tol=1e-12,
                    )
                    and row.get("can_still_beat_incumbent") is feasible
                ):
                    rows_valid = False
                    break
                if feasible:
                    derived_feasible.append(int(row["rank"]))
        expected_repeat_summary = repeat_run / "diagnostic_summary.json"
        primary_valid = bool(
            feasibility.get("contract")
            == "tpch_bounded_four_observation_median_prethird_v1"
            and feasibility.get("complete_two_observation_binding") is True
            and feasibility.get("resume_state_sha256") == expected_formal_sha
            and feasibility.get("selection_sha256") == sha256(repeat_selection)
            and expected_repeat_summary.is_file()
            and feasibility.get("repeat_summary_sha256")
            == sha256(expected_repeat_summary)
            and feasibility.get("feasible_ranks") == derived_feasible
            and bool(derived_feasible) is feasibility.get("passed")
            and rows_valid
        )
        if not (common_valid and primary_valid):
            raise RuntimeError("postclean5 prethird feasibility binding is invalid")
        source = "live_prethird"
    else:
        if not common_valid:
            raise RuntimeError("postclean5 legacy acceptance feasibility is invalid")
        source = "legacy_postreseal_fixture"
    return {
        "contract": PRETHIRD_FEASIBILITY_CONTRACT,
        "source": source,
        "path": str(selected_path.resolve()),
        "sha256": selected_sha256,
        "both_aliases_present": len(existing) == 2,
        "aliases_byte_identical": True,
        "formal_state_mutated": False,
        "manual_candidate_injection": False,
        "thresholds_relaxed": False,
        "new_workload_calls": 0,
    }


def validate_postclean5_gate_boundary(
    v41: Any,
    paths: Mapping[str, Path],
    *,
    expected_state_sha: str,
    expected_formal_sha: str,
    expected_counter_sha: str,
    expected_cycle: int,
) -> Dict[str, Any]:
    """Validate the zero-new-call boundary left by the missing v32 binding."""

    if not paths["state"].is_file() or sha256(paths["state"]) != expected_state_sha:
        raise RuntimeError("postclean5 controller_state SHA mismatch")
    state = load(paths["state"])
    recovery = state.get("v45_clean5_recovery") or {}
    resume_state = Path(str(state.get("resume_state") or ""))
    if not (
        state.get("schema_version") == "tpch_fresh10_target10_loop_v26"
        and state.get("status") == "preparing_cycle"
        and int(state.get("accepted_count", -1)) == 1
        and int(state.get("accepted_target", -1)) == 10
        and int(state.get("next_cycle", -1)) == expected_cycle
        and recovery.get("contract") == CLEAN5_RECOVERY_CONTRACT
        and int(recovery.get("cycle", -1)) == expected_cycle
        and int(recovery.get("new_physical_workload_calls", -1)) == 0
    ):
        raise RuntimeError("controller is not at the v48 postclean5 gate boundary")
    validate_counter(paths["counter"], expected_counter_sha)
    validate_formal_authority(resume_state, expected_formal_sha)
    if recovery.get("source_v42_formal_state_sha256") != expected_formal_sha:
        raise RuntimeError("postclean5 recovery/formal binding mismatch")

    cycle_root = v41.V41_LOOP / f"cycle_{expected_cycle:03d}"
    ensure_no_formal_started(cycle_root)
    completed = {
        "config": cycle_root / "config.a.json",
        "probe": cycle_root / "probe_top5.a.json",
        "probe_run": cycle_root / "diagnostic_probe",
        "repeat": cycle_root / "exact_repeat_top5.a.json",
        "repeat_run": cycle_root / "diagnostic_exact_repeat",
        "robust": cycle_root / "robust_top5.a.json",
        "robust_run": cycle_root / "diagnostic_robust_repeat",
        "resealed_selection": (
            cycle_root / "postrobust_reseal_v32/a/selection.json"
        ),
        "resealed_run": cycle_root / "postrobust_reseal_v32/a",
        "analysis": cycle_root / "entry_gate.json",
    }
    pairs = (
        (completed["config"], cycle_root / "config.b.json", "config"),
        (completed["probe"], cycle_root / "probe_top5.b.json", "probe"),
        (
            completed["repeat"],
            cycle_root / "exact_repeat_top5.b.json",
            "exact repeat",
        ),
        (completed["robust"], cycle_root / "robust_top5.b.json", "robust"),
        (
            completed["resealed_selection"],
            cycle_root / "postrobust_reseal_v32/b/selection.json",
            "resealed selection",
        ),
        (
            completed["resealed_run"] / "diagnostic_summary.json",
            cycle_root / "postrobust_reseal_v32/b/diagnostic_summary.json",
            "resealed summary",
        ),
        (
            completed["resealed_run"] / "diagnostic_state.json",
            cycle_root / "postrobust_reseal_v32/b/diagnostic_state.json",
            "resealed state",
        ),
    )
    for first, second, label in pairs:
        if not (
            first.is_file()
            and second.is_file()
            and first.read_bytes() == second.read_bytes()
        ):
            raise RuntimeError(f"postclean5 deterministic {label} pair mismatch")
    diagnostics = [
        clean5_evidence(
            completed["probe"],
            completed["probe_run"],
            1,
            expected_formal_sha,
        ),
        clean5_evidence(
            completed["repeat"],
            completed["repeat_run"],
            1,
            expected_formal_sha,
        ),
        clean5_evidence(
            completed["robust"],
            completed["robust_run"],
            1,
            expected_formal_sha,
        ),
    ]
    resealed = resealed_clean5_evidence(
        completed["resealed_selection"],
        completed["resealed_run"],
        1,
        expected_formal_sha,
    )
    analysis = load(completed["analysis"])
    checks = analysis.get("checks") or {}
    failed_checks = sorted(name for name, value in checks.items() if value is not True)
    if not (
        analysis.get("passed") is False
        and failed_checks == ["actual_best_in_predicted_top3"]
        and int(analysis.get("actual_best_predicted_rank", -1)) == 4
        and float(analysis.get("trusted_only_spearman")) > 0.0
        and float(analysis.get("maximum_trusted_resource_factor_error")) <= 1.5
    ):
        raise RuntimeError("postclean5 gate is not the rank4-only quality miss")
    feasibility = validate_postclean5_feasibility(
        cycle_root,
        expected_formal_sha=expected_formal_sha,
        repeat_selection=completed["repeat"],
        repeat_run=completed["repeat_run"],
        expected_primary_sha256=REMOTE_PRETHIRD_FEASIBILITY_SHA256,
    )
    return {
        "state": state,
        "cycle_root": cycle_root,
        "resume_state": resume_state,
        "completed_gate": completed,
        "preflight": {
            "contract": POSTCLEAN5_BINDING_CONTRACT,
            "status": "ready_zero_call_postclean5_gate_resume",
            "cycle": expected_cycle,
            "accepted_before": 1,
            "accepted_after": 1,
            "completed_diagnostics": diagnostics,
            "resealed_diagnostic": resealed,
            "acceptance_feasibility": feasibility,
            "existing_physical_workload_calls": 15,
            "new_physical_workload_calls": 0,
            "valid_replays": 15,
            "technical_failures": 0,
            "actual_best_predicted_rank": 4,
            "failed_checks": failed_checks,
            "trusted_only_spearman": analysis["trusted_only_spearman"],
            "maximum_trusted_resource_factor_error": analysis[
                "maximum_trusted_resource_factor_error"
            ],
            "formal_started": False,
            "formal_state_mutated": False,
            "accepted_counter_mutated": False,
            "manual_candidate_injection": False,
            "top3_threshold_relaxed": False,
            "next_action": "absorb_probe_then_automatic_bounded_reexploration",
        },
    }


def resume_postclean5_gate(
    v41: Any,
    paths: Mapping[str, Path],
    boundary: Mapping[str, Any],
) -> int:
    """Consume the existing failed gate without another diagnostic call."""

    cycle = int(boundary["preflight"]["cycle"])
    return run_loop(
        v41,
        paths,
        dict(boundary["state"]),
        start_cycle=cycle,
        resume_completed_gate=dict(boundary["completed_gate"]),
    )


def liveness_boundary(
    v41: Any,
    paths: Mapping[str, Path],
    *,
    expected_state_sha: str,
    expected_formal_sha: str,
    expected_counter_sha: str,
    expected_cycle: int,
) -> Dict[str, Any]:
    state_path = paths["state"]
    if not state_path.is_file() or sha256(state_path) != expected_state_sha:
        raise RuntimeError("liveness controller_state SHA mismatch")
    state = load(state_path)
    accepted = int(state.get("accepted_count", -1))
    resume_state = Path(str(state.get("resume_state") or ""))
    if not (
        state.get("status") == "preparing_cycle"
        and accepted == 1
        and int(state.get("accepted_target", -1)) == 10
        and int(state.get("next_cycle", -1)) == expected_cycle
        and isinstance(state.get("cycles"), list)
    ):
        raise RuntimeError("controller is not at a deterministic-failure boundary")
    validate_counter(paths["counter"], expected_counter_sha)
    validate_formal_authority(resume_state, expected_formal_sha)
    cycle_root = v41.V41_LOOP / f"cycle_{expected_cycle:03d}"
    if not cycle_root.is_dir():
        raise RuntimeError("deterministic-failure cycle directory is missing")
    ensure_no_formal_started(cycle_root)

    stage = ""
    failure: Optional[Dict[str, Any]] = None
    for candidate_stage in (
        "postrobust_reseal",
        "prethird_feasibility",
        "robust_repeat",
        "exact_repeat",
    ):
        candidate = deterministic_failure(candidate_stage, cycle_root)
        if candidate is not None:
            stage = candidate_stage
            failure = candidate
            break
    if failure is None:
        raise RuntimeError("cycle does not contain an allowlisted deterministic failure")

    probe = cycle_root / "probe_top5.a.json"
    probe_run = cycle_root / "diagnostic_probe"
    repeat = cycle_root / "exact_repeat_top5.a.json"
    repeat_run = cycle_root / "diagnostic_exact_repeat"
    robust = cycle_root / "robust_top5.a.json"
    robust_run = cycle_root / "diagnostic_robust_repeat"
    if stage == "exact_repeat":
        completed = [(probe, probe_run)]
        continuation = (probe, probe_run)
        forbidden = (repeat, cycle_root / "exact_repeat_top5.b.json", repeat_run)
    elif stage in {"robust_repeat", "prethird_feasibility"}:
        completed = [(probe, probe_run), (repeat, repeat_run)]
        continuation = (repeat, repeat_run)
        forbidden = (robust, cycle_root / "robust_top5.b.json", robust_run)
    else:
        completed = [
            (probe, probe_run),
            (repeat, repeat_run),
            (robust, robust_run),
        ]
        continuation = (robust, robust_run)
        forbidden = (
            cycle_root / "postrobust_reseal_v32/a/selection.json",
            cycle_root / "postrobust_reseal_v32/b/selection.json",
        )
    if any(path.exists() for path in forbidden):
        raise RuntimeError("deterministic failure has unexpected downstream artifacts")
    payload = negative_gate_payload(
        cycle=expected_cycle,
        stage=stage,
        failure=failure,
        accepted=accepted,
        resume_state=resume_state,
        expected_formal_sha256=expected_formal_sha,
        completed_diagnostics=completed,
    )
    return {
        "state": state,
        "accepted": accepted,
        "resume_state": resume_state,
        "cycle_root": cycle_root,
        "stage": stage,
        "failure": failure,
        "completed": completed,
        "continuation_selection": continuation[0],
        "continuation_run": continuation[1],
        "preflight": payload,
    }


def resume_liveness_boundary(
    v41: Any,
    paths: Mapping[str, Path],
    boundary: Mapping[str, Any],
) -> int:
    state = dict(boundary["state"])
    cycle = int(boundary["preflight"]["cycle"])
    v26, _ = v41.configure_controller(
        Path(str(boundary["resume_state"])),
        Path(str(boundary["continuation_selection"])),
        Path(str(boundary["continuation_run"])),
        list(executed_history(v41, paths)),
        Path(str(load(paths["handoff"])["adopted_formal_run"])),
    )
    commit_negative_reexploration(
        v26,
        paths,
        state,
        cycle=cycle,
        cycle_root=Path(str(boundary["cycle_root"])),
        stage=str(boundary["stage"]),
        failure=dict(boundary["failure"]),
        accepted=int(boundary["accepted"]),
        resume_state=Path(str(boundary["resume_state"])),
        expected_formal_sha256=str(boundary["preflight"]["formal_state_sha256"]),
        completed_diagnostics=list(boundary["completed"]),
    )
    return run_loop(
        v41,
        paths,
        state,
        start_cycle=cycle + 1,
        previous_selection_override=Path(str(boundary["continuation_selection"])),
        previous_run_override=Path(str(boundary["continuation_run"])),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--apply-recovery-only", action="store_true")
    mode.add_argument("--resume", action="store_true")
    mode.add_argument("--preflight-liveness-only", action="store_true")
    mode.add_argument("--resume-liveness", action="store_true")
    mode.add_argument("--preflight-clean5-only", action="store_true")
    mode.add_argument("--apply-clean5-only", action="store_true")
    mode.add_argument("--resume-clean5", action="store_true")
    mode.add_argument("--preflight-postclean5-gate-only", action="store_true")
    mode.add_argument("--resume-postclean5-gate", action="store_true")
    parser.add_argument("--expected-controller-state-sha256", required=True)
    parser.add_argument("--expected-formal-state-sha256", required=True)
    parser.add_argument("--expected-counter-sha256", required=True)
    parser.add_argument("--expected-cycle", type=int, default=4)
    args = parser.parse_args()

    v41 = verified_v41()
    paths = current_paths(v41)
    paths["lock"].parent.mkdir(parents=True, exist_ok=True)
    with paths["lock"].open("w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.preflight_clean5_only or args.apply_clean5_only:
            boundary = validate_clean5_boundary(
                v41,
                paths,
                expected_state_sha=args.expected_controller_state_sha256,
                expected_formal_sha=args.expected_formal_state_sha256,
                expected_counter_sha=args.expected_counter_sha256,
                expected_cycle=args.expected_cycle,
            )
            if args.preflight_clean5_only:
                print(json.dumps(boundary["preflight"], sort_keys=True))
                return 0
            result = seal_clean5_boundary(paths, boundary)
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.resume_clean5:
            boundary = validate_sealed_clean5_boundary(
                v41,
                paths,
                expected_state_sha=args.expected_controller_state_sha256,
                expected_formal_sha=args.expected_formal_state_sha256,
                expected_counter_sha=args.expected_counter_sha256,
                expected_cycle=args.expected_cycle,
            )
            return resume_sealed_clean5(v41, paths, boundary)
        if args.preflight_postclean5_gate_only or args.resume_postclean5_gate:
            boundary = validate_postclean5_gate_boundary(
                v41,
                paths,
                expected_state_sha=args.expected_controller_state_sha256,
                expected_formal_sha=args.expected_formal_state_sha256,
                expected_counter_sha=args.expected_counter_sha256,
                expected_cycle=args.expected_cycle,
            )
            if args.preflight_postclean5_gate_only:
                print(json.dumps(boundary["preflight"], sort_keys=True))
                return 0
            return resume_postclean5_gate(v41, paths, boundary)
        if args.preflight_liveness_only or args.resume_liveness:
            boundary = liveness_boundary(
                v41,
                paths,
                expected_state_sha=args.expected_controller_state_sha256,
                expected_formal_sha=args.expected_formal_state_sha256,
                expected_counter_sha=args.expected_counter_sha256,
                expected_cycle=args.expected_cycle,
            )
            if args.preflight_liveness_only:
                print(json.dumps(boundary["preflight"], sort_keys=True))
                return 0
            return resume_liveness_boundary(v41, paths, boundary)
        if args.resume:
            return resume(
                v41,
                paths,
                args.expected_controller_state_sha256,
                args.expected_formal_state_sha256,
                args.expected_counter_sha256,
            )
        boundary = validate_failed_boundary(
            v41,
            paths,
            args.expected_controller_state_sha256,
            args.expected_formal_state_sha256,
            args.expected_counter_sha256,
        )
        if args.preflight_only:
            print(json.dumps(preflight_report(v41, boundary, paths), sort_keys=True))
            return 0
        result = apply_recovery(v41, paths, boundary)
        print(json.dumps(result, sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
