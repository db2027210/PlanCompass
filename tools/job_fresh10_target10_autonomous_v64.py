#!/usr/bin/env python3
"""JOB v64: robust-formal eligibility and automatic liveness re-exploration."""

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

import job_fresh10_target10_autonomous_v63 as v63


implementation = v63.implementation
EXPECTED_V63_SHA256 = "15b7e1658d3328a3a92d2648ed610cf9153ec2e26e848b46fc2e756a30a74980"
TOOLS_DIR = Path(__file__).resolve().parent
RUNNER_V12 = TOOLS_DIR / "run_job_round8_strict_serial_robust_v12_eligibility.py"
RUNNER_V11 = TOOLS_DIR / "run_job_round8_strict_serial_robust_v11_gradient_state_guard.py"
EXPECTED_RUNNER_V12_SHA256 = "f8a026053bf830bdb7e227a54876ee28aa83e23d30246cbd3ef519c5ef66be6b"
EXPECTED_RUNNER_V11_SHA256 = "d8919ef67c0b9fe0b9666717e54eedb8cf197b835d4a1e1a918c18674e1b3043"
ELIGIBILITY_CONTRACT = "job_v64_robust_formal_eligibility_v1"
PRIOR_POLICY_CONTRACT = "job_three_independent_exact_point_median_acceptance_v2"
SCORE_EPSILON = 1e-12

if hashlib.sha256(Path(v63.__file__).read_bytes()).hexdigest() != EXPECTED_V63_SHA256:
    raise implementation.v49.FailClosed("v64 sealed v63 controller SHA mismatch")

_PREPARE_V63 = implementation.prepare_formal_v50
_REQUIRE_V63 = implementation.require_v50_hashes
implementation.CONTROLLER_VERSION = "v64"


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def same_params(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    if set(left) != set(right):
        return False
    return all(
        math.isclose(
            float(left[name]),
            float(right[name]),
            rel_tol=0.0,
            abs_tol=max(1e-15, abs(float(left[name])) * 1e-12),
        )
        for name in left
    )


def maximum_attainable_three_score_median(prior_scores: list[float]) -> float:
    """Return sup_x median(prior_1, prior_2, x)."""
    if len(prior_scores) != 2 or not all(math.isfinite(value) for value in prior_scores):
        raise implementation.v49.FailClosed("v64 requires two finite prior scores")
    return max(prior_scores)


def verified_prior_scores(candidate: Mapping[str, Any]) -> list[float]:
    policy = candidate.get("robust_acceptance_policy")
    params = candidate.get("candidate_params")
    if (
        not isinstance(policy, Mapping)
        or policy.get("contract") != PRIOR_POLICY_CONTRACT
        or not isinstance(params, Mapping)
        or policy.get("candidate_params_sha256") != canonical_sha256(params)
    ):
        raise implementation.v49.FailClosed("v64 trusted prior policy is invalid")
    observations = policy.get("prior_observations")
    if not isinstance(observations, list) or len(observations) != 2:
        raise implementation.v49.FailClosed("v64 trusted candidate lacks two priors")
    observed_paths: set[str] = set()
    observed_hashes: set[str] = set()
    scores: list[float] = []
    for ordinal, raw in enumerate(observations, 1):
        if not isinstance(raw, Mapping):
            raise implementation.v49.FailClosed("v64 prior observation is malformed")
        selection_path = Path(str(raw.get("selection_path") or "")).resolve()
        summary_path = Path(str(raw.get("summary_path") or "")).resolve()
        if (
            not selection_path.is_file()
            or not summary_path.is_file()
            or raw.get("selection_sha256")
            != implementation.v49.sha256_file(selection_path)
            or raw.get("summary_sha256")
            != implementation.v49.sha256_file(summary_path)
            or str(summary_path) in observed_paths
            or str(raw.get("summary_sha256")) in observed_hashes
        ):
            raise implementation.v49.FailClosed("v64 prior SHA/independence binding failed")
        observed_paths.add(str(summary_path))
        observed_hashes.add(str(raw.get("summary_sha256")))
        selection = implementation.v49.read_json(selection_path)
        summary = implementation.v49.read_json(summary_path)
        candidates = [
            item
            for item in implementation.v49.candidate_list(selection)
            if isinstance(item.get("candidate_params"), Mapping)
            and same_params(params, item["candidate_params"])
        ]
        rows = []
        for row in summary.get("successful") or []:
            binding = row.get("candidate_binding") if isinstance(row, Mapping) else None
            row_params = binding.get("candidate_params") if isinstance(binding, Mapping) else None
            if (
                isinstance(row_params, Mapping)
                and same_params(params, row_params)
                and row.get("technical_failure") is False
            ):
                rows.append(row)
        if len(candidates) != 1 or len(rows) != 1:
            raise implementation.v49.FailClosed("v64 prior parameter binding is ambiguous")
        score = float(rows[0].get("actual_score"))
        sealed = float(raw.get("actual_score"))
        if (
            not math.isfinite(score)
            or not math.isclose(score, sealed, rel_tol=0.0, abs_tol=SCORE_EPSILON)
        ):
            raise implementation.v49.FailClosed("v64 sealed prior score mismatch")
        scores.append(score)
    return scores


def build_eligibility_selection(
    selection: Mapping[str, Any],
    *,
    incumbent_score: float,
    formal_state_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = copy.deepcopy(dict(selection))
    candidates = implementation.v49.candidate_list(result)
    roles = [str(item.get("selection_role") or "") for item in candidates]
    if roles != ["trusted", "trusted", "trusted", "probe", "probe"]:
        raise implementation.v49.FailClosed("v64 source formal selection is not 3T2P")
    running_incumbent_upper_bound = float(incumbent_score)
    rows = []
    eligible_ranks = []
    skipped_ranks = []
    for rank, candidate in enumerate(candidates, 1):
        role = str(candidate.get("selection_role") or "")
        threshold = running_incumbent_upper_bound
        if role == "trusted":
            prior_scores = verified_prior_scores(candidate)
            maximum = maximum_attainable_three_score_median(prior_scores)
            algorithmic_gate = candidate.get("algorithmic_gate")
            gate_allows = not (
                isinstance(algorithmic_gate, Mapping)
                and algorithmic_gate.get("accepted") is False
            )
            eligible = bool(maximum > threshold + SCORE_EPSILON and gate_allows)
            reason = "eligible_trusted" if eligible else (
                "algorithmic_gate_rejected" if not gate_allows else "median_improvement_impossible"
            )
            if eligible:
                running_incumbent_upper_bound = maximum
        else:
            prior_scores = []
            maximum = None
            eligible = False
            reason = "probe_role"
        gate = {
            "contract": ELIGIBILITY_CONTRACT,
            "source_rank": rank,
            "selection_role": role,
            "prior_actual_scores": prior_scores,
            "maximum_attainable_three_score_median": maximum,
            "incumbent_threshold_before_rank": threshold,
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
        "schema_version": "job_v64_robust_formal_eligibility_audit_v1",
        "contract": ELIGIBILITY_CONTRACT,
        "status": "eligible_formal_calls" if eligible_ranks else "zero_call_reexplore",
        "formal_state_sha256": formal_state_sha256,
        "incumbent_score": float(incumbent_score),
        "rows": rows,
        "eligible_ranks": eligible_ranks,
        "zero_call_skipped_ranks": skipped_ranks,
        "maximum_physical_calls": len(eligible_ranks),
        "new_workload_calls": 0,
        "formal_state_mutated": False,
        "accepted_update_applied": False,
        "incumbent_update_applied": False,
        "range_update_applied": False,
        "gradient_update_applied": False,
        "manual_candidate_injection": False,
        "qps_used_for_inner_scoring": False,
        "no_eligible_next_action": "automatic_new_fingerprint_exploration",
    }
    return result, audit


def seal_formal_eligibility(formal: Path, state_sha_before: str) -> dict[str, Any]:
    package = formal / "package"
    selection_path = package / "top5_promoted_formal.json"
    audit_path = formal / "robust_formal_eligibility_audit.json"
    staged_path = package / "top5_promoted_formal.v64.staged.json"
    if implementation.v49.sha256_file(implementation.v49.STATE) != state_sha_before:
        raise implementation.v49.FailClosed("v64 eligibility observed formal-state drift")
    state = implementation.v49.read_json(implementation.v49.STATE)
    incumbent = float(state.get("incumbent_score"))
    if audit_path.is_file():
        audit = implementation.v49.read_json(audit_path)
        expected_after = str(audit.get("eligible_selection_sha256") or "")
        if (
            audit.get("contract") != ELIGIBILITY_CONTRACT
            or audit.get("formal_state_sha256") != state_sha_before
            or not staged_path.is_file()
            or implementation.v49.sha256_file(staged_path) != expected_after
        ):
            raise implementation.v49.FailClosed("v64 eligibility journal is invalid")
        current_sha = implementation.v49.sha256_file(selection_path)
        if current_sha == str(audit.get("source_selection_sha256")):
            os.replace(staged_path, selection_path)
            shutil.copy2(selection_path, staged_path)
        elif current_sha != expected_after:
            raise implementation.v49.FailClosed("v64 eligibility selection is ambiguous")
        return audit

    source_sha = implementation.v49.sha256_file(selection_path)
    eligible_selection, audit = build_eligibility_selection(
        implementation.v49.read_json(selection_path),
        incumbent_score=incumbent,
        formal_state_sha256=state_sha_before,
    )
    implementation.v49.atomic_json(staged_path, eligible_selection)
    audit.update(
        {
            "journal_status": "prepared_before_selection_replace",
            "source_selection_sha256": source_sha,
            "eligible_selection_sha256": implementation.v49.sha256_file(staged_path),
            "staged_selection_path": str(staged_path.resolve()),
        }
    )
    implementation.v49.atomic_json(audit_path, audit)
    os.replace(staged_path, selection_path)
    shutil.copy2(selection_path, staged_path)
    if implementation.v49.sha256_file(implementation.v49.STATE) != state_sha_before:
        raise implementation.v49.FailClosed("v64 eligibility mutated formal state")
    return audit


def prepare_formal_v64(*args: Any, **kwargs: Any) -> Path:
    state_sha_before = str(args[5])
    formal = _PREPARE_V63(*args, **kwargs)
    package = formal / "package"
    for source, expected in (
        (RUNNER_V12, EXPECTED_RUNNER_V12_SHA256),
        (RUNNER_V11, EXPECTED_RUNNER_V11_SHA256),
    ):
        if not source.is_file() or implementation.v49.sha256_file(source) != expected:
            raise implementation.v49.FailClosed(f"v64 runner dependency mismatch: {source}")
    shutil.copy2(RUNNER_V11, package / RUNNER_V11.name)
    shutil.copy2(RUNNER_V12, package / "run_job_round8_strict_serial.py")
    audit = seal_formal_eligibility(formal, state_sha_before)
    if not audit.get("eligible_ranks"):
        raise implementation.v49.NoveltyCollision(
            "v64 no robust-formal eligible trusted candidate; automatic new fingerprints"
        )
    dry_run = package / "v64_eligibility_dry_run.json"
    if not dry_run.is_file():
        implementation.v49.run_logged(
            [
                sys.executable,
                str(package / "run_job_round8_strict_serial.py"),
                str(package / "top5_promoted_formal.json"),
                str(formal / "replay"),
                "--authority-root",
                str(implementation.v49.P / "authority"),
                "--observations",
                str(implementation.v49.P / "job_round8_v6_observations.json"),
                "--exclusion",
                str(implementation.v49.P / "round7_replayed_fingerprints.json"),
                "--state",
                str(implementation.v49.STATE),
                "--accepted-target",
                "10",
                "--dry-run",
            ],
            dry_run,
            timeout_seconds=600,
            cwd=package,
        )
    return formal


def require_v64_hashes() -> None:
    _REQUIRE_V63()
    for path, expected in (
        (RUNNER_V12, EXPECTED_RUNNER_V12_SHA256),
        (RUNNER_V11, EXPECTED_RUNNER_V11_SHA256),
    ):
        if not path.is_file() or implementation.v49.sha256_file(path) != expected:
            raise implementation.v49.FailClosed(f"v64 sealed dependency mismatch: {path}")


def validate_pending_diagnostic_counters_v64(
    latest_formal: Path,
    first_cycle: int,
) -> None:
    """Accept completed v63 repeat evidence; reject every unsealed partial."""
    v49 = implementation.v49
    _state_sha, state = v49.state_pin()
    running_global = int(state.get("global_ordinal", -1))
    formal_replay = int(state.get("formal_replay_count", -1))
    accepted = int(state.get("accepted_count", -1))
    base = implementation.HISTORICAL_INITIAL_FORMAL
    latest_cycle = (
        0 if latest_formal == base else int(latest_formal.parent.name.rsplit("_", 1)[-1])
    )
    roots = [
        v49.ROOT / f"cycle_{ordinal:03d}"
        for ordinal in range(latest_cycle + 1, first_cycle + 1)
        if (v49.ROOT / f"cycle_{ordinal:03d}").is_dir()
    ]
    pending = []
    for root in roots:
        for before_path in root.rglob("counters_before.json"):
            phase = before_path.parent
            before = v49.read_json(before_path)
            summary_path = phase / "replay/summary.json"
            audit_path = phase / "diagnostic_audit.json"
            if not summary_path.is_file():
                raise v49.FailClosed(
                    f"partial diagnostic has no sealed summary; refusing replay: {phase}"
                )
            summary = v49.read_json(summary_path)
            physical = int(summary.get("physical_invocation_count", -1))
            if not (0 <= physical <= 5):
                raise v49.FailClosed(f"diagnostic physical count is invalid: {phase}")
            if audit_path.is_file():
                sealed = v49.read_json(audit_path)
                if sealed.get("summary_sha256") != v49.sha256_file(summary_path):
                    raise v49.FailClosed(f"diagnostic summary SHA mismatch: {phase}")
            sequence_path = phase / "v50_batch_sequence.json"
            sequence = (
                int(v49.read_json(sequence_path).get("sequence", -1))
                if sequence_path.is_file()
                else None
            )
            pending.append((phase, before, physical, sequence))
    unused = list(pending)
    sequenced = [item for item in pending if item[3] is not None]
    if sequenced:
        legacy = sorted(
            (item for item in pending if item[3] is None),
            key=lambda item: int(item[1].get("global", -1)),
        )
        ordered = sorted(sequenced, key=lambda item: int(item[3]))
        unused = [*legacy, *ordered] if legacy else ordered
        sequences = [int(item[3]) for item in sequenced]
        if len(set(sequences)) != len(sequences):
            raise v49.FailClosed("duplicate v50 diagnostic batch sequence")
        for phase, before, physical, _sequence in unused:
            if (
                int(before.get("global", -1)) != running_global
                or int(before.get("formal_replay", -1)) != formal_replay
                or int(before.get("accepted", -1)) != accepted
            ):
                raise v49.FailClosed(f"sequenced diagnostic counter chain mismatch: {phase}")
            running_global += int(physical)
        unused = []
    while unused:
        matches = [
            item
            for item in unused
            if int(item[1].get("global", -1)) == running_global
            and int(item[1].get("formal_replay", -1)) == formal_replay
            and int(item[1].get("accepted", -1)) == accepted
        ]
        if len(matches) != 1:
            raise v49.FailClosed("interleaved diagnostic counter chain is ambiguous or broken")
        selected = matches[0]
        unused.remove(selected)
        running_global += int(selected[2])
    if (
        running_global != v49.counter(v49.GLOBAL_COUNTER)
        or formal_replay != v49.counter(v49.FORMAL_REPLAY_COUNTER)
        or accepted != v49.counter(v49.ACCEPTED_COUNTER)
    ):
        raise v49.FailClosed("pending diagnostic/global counter accounting mismatch")


def resume_position_v64() -> tuple[Path, int]:
    """Use v63 only for its raw zero-call boundary, then switch to sealed replay."""
    summary = v63._repeat_root() / "replay/summary.json"
    if not summary.is_file():
        return v63.resume_position_v63()
    v63.v62.install_live_network_preflight(
        implementation.v49.ROOT / "preflights/v64_completed_repeat_resume"
    )
    return v63._RESUME_V60()


implementation.prepare_formal_v50 = prepare_formal_v64
implementation.require_v50_hashes = require_v64_hashes
implementation.validate_pending_diagnostic_counters_v50 = (
    validate_pending_diagnostic_counters_v64
)
implementation.resume_position_v50 = resume_position_v64


if __name__ == "__main__":
    try:
        raise SystemExit(implementation.main())
    except implementation.v49.FailClosed as error:
        try:
            implementation.write_controller_state(status="fail_closed", error=str(error))
        except Exception:
            pass
        print(f"JOB target10 v64 fail closed: {error}", flush=True)
        raise SystemExit(2)
