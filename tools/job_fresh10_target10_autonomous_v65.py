#!/usr/bin/env python3
"""JOB v65: sound resource-median admission and restart-safe formal packaging."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping

import job_fresh10_target10_autonomous_v64 as v64


implementation = v64.implementation
EXPECTED_V64_SHA256 = "cea34419a1c6ba790b98208dc8c80bbef51b0efdaae81865459b93638181422a"
TOOLS_DIR = Path(__file__).resolve().parent
RUNNER_V13 = TOOLS_DIR / "run_job_round8_strict_serial_robust_v13_resource_eligibility.py"
RUNNER_V12 = TOOLS_DIR / "run_job_round8_strict_serial_robust_v12_eligibility.py"
RUNNER_V11 = TOOLS_DIR / "run_job_round8_strict_serial_robust_v11_gradient_state_guard.py"
EXPECTED_V13_SHA256 = "d43309509d848683695b9efc72435ff2c05c44a27173ca77f9d34a3b76ebe407"
EXPECTED_V12_SHA256 = "f8a026053bf830bdb7e227a54876ee28aa83e23d30246cbd3ef519c5ef66be6b"
EXPECTED_V11_SHA256 = "d8919ef67c0b9fe0b9666717e54eedb8cf197b835d4a1e1a918c18674e1b3043"
ELIGIBILITY_CONTRACT = "job_v65_resource_median_reachability_v1"
RESOURCE_MEDIAN_CONTRACT = "job_three_independent_exact_point_resource_median_rescored_v3"
SCORE_EPSILON = 1e-12
CAPABILITY = {"cpu": 3.0, "io": 256.0, "memory": 2.91015625}
RESOURCES = ("cpu", "io", "memory")

if hashlib.sha256(Path(v64.__file__).read_bytes()).hexdigest() != EXPECTED_V64_SHA256:
    raise implementation.v49.FailClosed("v65 sealed v64 controller SHA mismatch")

_PREPARE_V63 = v64._PREPARE_V63
_REQUIRE_V64 = v64.require_v64_hashes
_MAIN_V50 = implementation.main
implementation.CONTROLLER_VERSION = "v65"


def _finite(value: Any, label: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise implementation.v49.FailClosed(f"v65 non-finite {label}")
    return number


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


def resource_median_score_upper_bound(
    prior_utilizations: list[Mapping[str, float]],
) -> tuple[float, dict[str, Any]]:
    """Exact maximum of the separable score over the attainable median box."""
    if len(prior_utilizations) != 2:
        raise implementation.v49.FailClosed("v65 requires two resource priors")
    weights = {"cpu": 1.0, "io": 3.0, "memory": 1.0}
    optimum = {"cpu": 0.50, "io": 0.50, "memory": 0.85}
    resource_audit = {}
    total = 0.0
    for name in RESOURCES:
        values = [
            _finite(item[name], f"prior utilization {name}")
            for item in prior_utilizations
        ]
        low, high = min(values), max(values)
        points = [low, high]
        if low <= optimum[name] <= high:
            points.append(optimum[name])
        scored = [
            directional_band_score(value, weights[name], name == "memory")
            for value in points
        ]
        best_index = max(range(len(points)), key=lambda index: scored[index])
        total += scored[best_index]
        resource_audit[name] = {
            "attainable_median_min": low,
            "attainable_median_max": high,
            "maximizer": points[best_index],
            "maximum_score_component": scored[best_index],
        }
    return float(total), resource_audit


def verified_prior_utilizations(
    candidate: Mapping[str, Any],
) -> list[dict[str, float]] | None:
    """Return exact resource priors, or None when a sound bound is unavailable."""
    v64.verified_prior_scores(candidate)
    policy = candidate["robust_acceptance_policy"]
    params = candidate["candidate_params"]
    candidate_plan = str(
        (candidate.get("three_level_fingerprint") or {}).get("plan_level") or ""
    )
    if not candidate_plan:
        return None
    result = []
    for raw in policy["prior_observations"]:
        selection_path = Path(str(raw["selection_path"])).resolve()
        summary_path = Path(str(raw["summary_path"])).resolve()
        selection = implementation.v49.read_json(selection_path)
        summary = implementation.v49.read_json(summary_path)
        candidates = [
            item
            for item in implementation.v49.candidate_list(selection)
            if isinstance(item.get("candidate_params"), Mapping)
            and v64.same_params(params, item["candidate_params"])
        ]
        rows = []
        for row in summary.get("successful") or []:
            binding = row.get("candidate_binding") if isinstance(row, Mapping) else None
            row_params = binding.get("candidate_params") if isinstance(binding, Mapping) else None
            if (
                isinstance(row_params, Mapping)
                and v64.same_params(params, row_params)
                and row.get("technical_failure") is False
            ):
                rows.append(row)
        if len(candidates) != 1 or len(rows) != 1:
            raise implementation.v49.FailClosed("v65 resource prior binding is ambiguous")
        observed_plan = str(
            (candidates[0].get("three_level_fingerprint") or {}).get("plan_level") or ""
        )
        resource = rows[0].get("actual_resource")
        utilization = rows[0].get("actual_utilization")
        if (
            observed_plan != candidate_plan
            or not isinstance(resource, Mapping)
            or not isinstance(utilization, Mapping)
        ):
            return None
        normalized = {}
        for name in RESOURCES:
            amount = _finite(resource.get(name), f"actual_resource.{name}")
            observed = _finite(utilization.get(name), f"actual_utilization.{name}")
            if amount < 0.0 or observed < 0.0:
                raise implementation.v49.FailClosed("v65 resource prior is negative")
            expected = amount / CAPABILITY[name]
            if not math.isclose(observed, expected, rel_tol=1e-9, abs_tol=1e-12):
                raise implementation.v49.FailClosed(
                    "v65 resource/utilization prior is inconsistent"
                )
            normalized[name] = observed
        result.append(normalized)
    return result


def build_resource_eligibility_selection(
    selection: Mapping[str, Any],
    *,
    incumbent_score: float,
    formal_state_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = copy.deepcopy(dict(selection))
    candidates = implementation.v49.candidate_list(result)
    if [str(item.get("selection_role") or "") for item in candidates] != [
        "trusted", "trusted", "trusted", "probe", "probe"
    ]:
        raise implementation.v49.FailClosed("v65 source formal selection is not 3T2P")
    running_upper = float(incumbent_score)
    running_bound_known = True
    formal_flags = result.get("formal_update_flags")
    formal_gradient_allowed = bool(
        result.get("gradient_update_allowed") is True
        and isinstance(formal_flags, Mapping)
        and formal_flags.get("gradient") is True
    )
    rows = []
    eligible_ranks = []
    skipped_ranks = []
    for rank, candidate in enumerate(candidates, 1):
        role = str(candidate.get("selection_role") or "")
        threshold = running_upper if running_bound_known else None
        bound = None
        box = None
        bound_proven = False
        if role == "probe":
            eligible = False
            reason = "probe_role"
        else:
            candidate["incumbent_update_eligible"] = True
            candidate["used_for_inner_incumbent_update"] = True
            algorithmic_gate = candidate.get("algorithmic_gate")
            candidate["used_for_gradient"] = formal_gradient_allowed
            gate_allows = formal_gradient_allowed and not (
                isinstance(algorithmic_gate, Mapping)
                and algorithmic_gate.get("accepted") is False
            )
            priors = verified_prior_utilizations(candidate)
            if priors is not None:
                bound, box = resource_median_score_upper_bound(priors)
                bound_proven = True
            if not gate_allows:
                eligible = False
                reason = "algorithmic_gate_rejected"
            elif running_bound_known and bound_proven and bound <= running_upper + SCORE_EPSILON:
                eligible = False
                reason = "resource_median_improvement_impossible"
            else:
                eligible = True
                reason = "eligible_trusted" if bound_proven else "unproven_bound_keep_trusted"
                if running_bound_known and bound_proven:
                    running_upper = float(bound)
                else:
                    running_bound_known = False
        gate = {
            "contract": ELIGIBILITY_CONTRACT,
            "acceptance_contract": RESOURCE_MEDIAN_CONTRACT,
            "source_rank": rank,
            "selection_role": role,
            "bound_proven": bound_proven,
            "resource_median_score_upper_bound": bound,
            "resource_median_attainable_box": box,
            "incumbent_upper_bound_before_rank": threshold,
            "eligible": eligible,
            "reason": reason,
            "physical_calls_for_skip": 0 if not eligible else None,
            "qps_used_for_inner_scoring": False,
        }
        candidate["robust_formal_eligibility"] = gate
        candidate["formal_replay_eligible"] = eligible
        if eligible:
            eligible_ranks.append(rank)
        else:
            skipped_ranks.append(rank)
            candidate["incumbent_update_eligible"] = False
            candidate["used_for_inner_incumbent_update"] = False
            candidate["used_for_gradient"] = False
        rows.append(gate)
    result.update(
        {
            "robust_formal_eligibility_contract": ELIGIBILITY_CONTRACT,
            "robust_formal_incumbent_score": float(incumbent_score),
            "robust_formal_state_sha256": formal_state_sha256,
            "formal_replay_eligible_ranks": eligible_ranks,
            "formal_zero_call_skip_ranks": skipped_ranks,
            "qps_used_for_inner_scoring": False,
        }
    )
    audit = {
        "schema_version": "job_v65_resource_median_eligibility_audit_v1",
        "contract": ELIGIBILITY_CONTRACT,
        "acceptance_contract": RESOURCE_MEDIAN_CONTRACT,
        "status": "eligible_formal_calls" if eligible_ranks else "zero_call_reexplore",
        "formal_state_sha256": formal_state_sha256,
        "incumbent_score": float(incumbent_score),
        "rows": rows,
        "eligible_ranks": eligible_ranks,
        "zero_call_skipped_ranks": skipped_ranks,
        "maximum_physical_calls": len(eligible_ranks),
        "new_workload_calls": 0,
        "formal_state_mutated": False,
        "manual_candidate_injection": False,
        "qps_used_for_inner_scoring": False,
        "no_eligible_next_action": "automatic_new_fingerprint_exploration",
    }
    return result, audit


def _restore_inherited_runner_before_prepare(cycle_root: Path) -> None:
    formal = cycle_root / "formal"
    package = formal / "package"
    runner = package / "run_job_round8_strict_serial.py"
    replay = formal / "replay"
    transaction = replay / "formal_state_commit_transaction.json"
    if replay.exists() and not transaction.exists():
        raise implementation.v49.FailClosed("v65 refuses an uncommitted partial formal replay")
    if not runner.is_file():
        return
    observed = implementation.v49.sha256_file(runner)
    if observed not in (EXPECTED_V11_SHA256, EXPECTED_V12_SHA256, EXPECTED_V13_SHA256):
        raise implementation.v49.FailClosed("v65 packaged runner SHA is unknown")
    if observed in (EXPECTED_V12_SHA256, EXPECTED_V13_SHA256):
        shutil.copy2(RUNNER_V11, runner)
        if implementation.v49.sha256_file(runner) != EXPECTED_V11_SHA256:
            raise implementation.v49.FailClosed("v65 could not restore inherited runner")


def seal_resource_eligibility(formal: Path, state_sha_before: str) -> dict[str, Any]:
    package = formal / "package"
    selection_path = package / "top5_promoted_formal.json"
    staged_path = package / "top5_promoted_formal.v65.staged.json"
    audit_path = formal / "resource_median_eligibility_audit_v65.json"
    if implementation.v49.sha256_file(implementation.v49.STATE) != state_sha_before:
        raise implementation.v49.FailClosed("v65 eligibility observed state drift")
    if audit_path.is_file():
        audit = implementation.v49.read_json(audit_path)
        source_sha = str(audit.get("source_selection_sha256") or "")
        expected_sha = str(audit.get("eligible_selection_sha256") or "")
        current_sha = implementation.v49.sha256_file(selection_path)
        if (
            audit.get("contract") != ELIGIBILITY_CONTRACT
            or audit.get("formal_state_sha256") != state_sha_before
            or current_sha not in (source_sha, expected_sha)
        ):
            raise implementation.v49.FailClosed("v65 eligibility journal is invalid")
        if current_sha == source_sha:
            if not staged_path.is_file() or implementation.v49.sha256_file(staged_path) != expected_sha:
                raise implementation.v49.FailClosed("v65 prepared staged selection is missing")
            os.replace(staged_path, selection_path)
        if implementation.v49.sha256_file(selection_path) != expected_sha:
            raise implementation.v49.FailClosed("v65 eligible selection recovery failed")
        if not staged_path.is_file():
            shutil.copy2(selection_path, staged_path)
        return audit
    state = implementation.v49.read_json(implementation.v49.STATE)
    source_sha = implementation.v49.sha256_file(selection_path)
    eligible_selection, audit = build_resource_eligibility_selection(
        implementation.v49.read_json(selection_path),
        incumbent_score=float(state["incumbent_score"]),
        formal_state_sha256=state_sha_before,
    )
    implementation.v49.atomic_json(staged_path, eligible_selection)
    audit.update(
        {
            "source_selection_sha256": source_sha,
            "eligible_selection_sha256": implementation.v49.sha256_file(staged_path),
            "staged_selection_path": str(staged_path.resolve()),
        }
    )
    implementation.v49.atomic_json(audit_path, audit)
    os.replace(staged_path, selection_path)
    shutil.copy2(selection_path, staged_path)
    if implementation.v49.sha256_file(implementation.v49.STATE) != state_sha_before:
        raise implementation.v49.FailClosed("v65 eligibility mutated formal state")
    return audit


def prepare_formal_v65(*args: Any, **kwargs: Any) -> Path:
    cycle_root = Path(args[0])
    state_sha_before = str(args[5])
    _restore_inherited_runner_before_prepare(cycle_root)
    formal = _PREPARE_V63(*args, **kwargs)
    package = formal / "package"
    for source, expected in (
        (RUNNER_V11, EXPECTED_V11_SHA256),
        (RUNNER_V12, EXPECTED_V12_SHA256),
        (RUNNER_V13, EXPECTED_V13_SHA256),
    ):
        if not source.is_file() or implementation.v49.sha256_file(source) != expected:
            raise implementation.v49.FailClosed(f"v65 runner dependency mismatch: {source}")
    shutil.copy2(RUNNER_V12, package / RUNNER_V12.name)
    shutil.copy2(RUNNER_V13, package / "run_job_round8_strict_serial.py")
    audit = seal_resource_eligibility(formal, state_sha_before)
    if not audit.get("eligible_ranks"):
        raise implementation.v49.NoveltyCollision(
            "v65 no sound resource-formal eligible trusted candidate; automatic new fingerprints"
        )
    dry_run = package / "v65_resource_eligibility_dry_run.json"
    if not dry_run.is_file():
        implementation.v49.run_logged(
            [
                sys.executable,
                str(package / "run_job_round8_strict_serial.py"),
                str(package / "top5_promoted_formal.json"),
                str(formal / "replay"),
                "--authority-root", str(implementation.v49.P / "authority"),
                "--observations", str(implementation.v49.P / "job_round8_v6_observations.json"),
                "--exclusion", str(implementation.v49.P / "round7_replayed_fingerprints.json"),
                "--state", str(implementation.v49.STATE),
                "--accepted-target", "10", "--dry-run",
            ],
            dry_run,
            timeout_seconds=600,
            cwd=package,
        )
    value = implementation.v49.read_json(dry_run)
    if (
        value.get("runner_contract")
        != "job_v65_nested_frozen_filter_dynamic_count_v1"
        or value.get("robust_formal_eligibility_contract") != ELIGIBILITY_CONTRACT
        or int(value.get("eligible_formal_call_count", -1))
        != len(audit["eligible_ranks"])
        or value.get("remote_actions_executed") is not False
        or implementation.v49.sha256_file(implementation.v49.STATE) != state_sha_before
    ):
        raise implementation.v49.FailClosed("v65 dry-run binding failed")
    return formal


def require_v65_hashes() -> None:
    _REQUIRE_V64()
    for path, expected in (
        (RUNNER_V13, EXPECTED_V13_SHA256),
        (RUNNER_V12, EXPECTED_V12_SHA256),
        (RUNNER_V11, EXPECTED_V11_SHA256),
    ):
        if not path.is_file() or implementation.v49.sha256_file(path) != expected:
            raise implementation.v49.FailClosed(f"v65 sealed dependency mismatch: {path}")


def main_v65() -> int:
    """Treat accepted=10 reached on the final allowed cycle as completion."""
    try:
        return int(_MAIN_V50())
    except implementation.v49.FailClosed as error:
        if str(error) != "autonomous cycle cap reached before accepted=10":
            raise
        state_sha, state = implementation.v49.state_pin()
        accepted = int(state.get("accepted_count", -1))
        if accepted != implementation.v49.TARGET_ACCEPTED:
            raise
        implementation.write_controller_state(
            status="complete",
            accepted_count=accepted,
            formal_state_sha256=state_sha,
            recovered_after_final_allowed_cycle=True,
            new_workload_calls=0,
        )
        return 0


implementation.prepare_formal_v50 = prepare_formal_v65
implementation.require_v50_hashes = require_v65_hashes
implementation.main = main_v65


if __name__ == "__main__":
    try:
        raise SystemExit(implementation.main())
    except implementation.v49.FailClosed as error:
        try:
            implementation.write_controller_state(status="fail_closed", error=str(error))
        except Exception:
            pass
        print(f"JOB target10 v65 fail closed: {error}", flush=True)
        raise SystemExit(2)
