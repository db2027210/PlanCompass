#!/usr/bin/env python3
"""XP v37 formal handoff and resource/score-aligned acceptance policy.

This module is deliberately installed as a narrow adapter patch over the
sealed v36 bundle.  Diagnostic selection and replay are unchanged.  Before a
formal batch can reach the replay runner, the two exact-point diagnostic
observations are rebound, SHA sealed, and checked.  The third observation is
then combined with them using component-wise resource medians; the stored
incumbent score is always recomputed from that stored resource vector.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import statistics
from typing import Any, Mapping


HANDOFF_CONTRACT = "xp_v37_preformal_two_actual_observation_handoff_v1"
ACCEPTANCE_CONTRACT = (
    "xp_v37_trusted_three_observation_score_median_and_resource_median_v1"
)
RESOURCE_NAMES = ("cpu", "io", "memory")
NO_PRIOR_REPEAT_NOISE_CONTRACT = (
    "xp_v37_first_exact_point_repeat_acquires_local_noise_v1"
)
EXACT_REPEAT_SELECTION_CONTRACT = (
    "xp_robust_exact_regime_top3_plus_two_uncertain_probes_v1"
)


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    return _digest_bytes(path.read_bytes())


def _canonical_digest(value: Any) -> str:
    return _digest_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"XP v37 expected JSON object: {path}")
    return value


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _same_number(left: Any, right: Any) -> bool:
    try:
        return math.isclose(
            float(left), float(right), rel_tol=0.0, abs_tol=1e-12
        )
    except (TypeError, ValueError):
        return False


def _resource(value: Any, label: str) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"XP v37 {label} resource is not an object")
    result = {}
    for name in RESOURCE_NAMES:
        number = float(value[name])
        if not math.isfinite(number) or number <= 0.0:
            raise RuntimeError(f"XP v37 {label} {name} is not positive finite")
        result[name] = number
    return result


def _params_key(value: Any) -> str:
    if not isinstance(value, Mapping) or not value:
        raise RuntimeError("XP v37 candidate lacks exact parameters")
    return _canonical_digest({str(key): float(raw) for key, raw in value.items()})


def _candidate_binding(selection: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for candidate in selection.get("top5") or []:
        fingerprint = candidate.get("three_level_fingerprint") or {}
        rows.append(
            {
                "rank": int(candidate["rank"]),
                "fingerprint_id": str(fingerprint["id"]),
                "source_fingerprint_id": str(fingerprint["source_fingerprint_id"]),
                "selection_role": str(candidate["selection_role"]),
                "candidate_params_sha256": _params_key(
                    candidate.get("candidate_params")
                ),
            }
        )
    if len(rows) != 5 or len({row["fingerprint_id"] for row in rows}) != 5:
        raise RuntimeError("XP v37 formal binding is not a unique Top-5")
    return rows


def build_preformal_handoff(
    adapter: Any,
    selection: Mapping[str, Any],
    batch_dir: Path,
    capability: Mapping[str, Any],
) -> dict[str, Any]:
    """Seal the two actual diagnostic observations before formal replay."""

    if selection.get("batch_mode") != "formal":
        raise RuntimeError("XP v37 handoff can only seal a formal selection")
    promotion = selection.get("promoted_from_diagnostic_validation") or {}
    if promotion.get("contract") != (
        "sealed_clean5_top5_two_observation_formal_confirmation_v2"
    ):
        raise RuntimeError("XP v37 formal promotion contract mismatch")
    source_selection_path = Path(str(promotion.get("selection_path") or ""))
    source_replay_path = Path(str(promotion.get("replay_path") or ""))
    if (
        not source_selection_path.is_file()
        or not source_replay_path.is_file()
        or _sha256(source_selection_path) != promotion.get("selection_sha256")
        or _sha256(source_replay_path) != promotion.get("replay_sha256")
    ):
        raise RuntimeError("XP v37 promoted diagnostic SHA mismatch")
    source_selection = _load(source_selection_path)
    source_replay = _load(source_replay_path)
    if (
        int(source_replay.get("physical_workload_invocation_count", -1)) != 5
        or int(source_replay.get("successful_real_replay_count", -1)) != 5
        or int(source_replay.get("technical_failure_count", -1)) != 0
        or source_replay.get("baseline_restore_verified") is not True
    ):
        raise RuntimeError("XP v37 second diagnostic observation is not clean5")

    calibration = source_selection.get("calibration_source") or {}
    if calibration.get("contract") not in {
        "clean_probe5_exact_plan_first_observation_repeat_v1",
        "clean_nonpositive_spearman_score_span_repeat_v1",
        "clean_actual_best_rank_miss_score_span_repeat_v1",
    }:
        raise RuntimeError("XP v37 source selection lacks actual-repeat provenance")
    first_selection_path = Path(str(calibration.get("selection_path") or ""))
    first_replay_path = Path(str(calibration.get("replay_path") or ""))
    if not first_selection_path.is_file() or not first_replay_path.is_file():
        raise RuntimeError("XP v37 first diagnostic provenance is missing")
    first_selection = _load(first_selection_path)
    first_replay = _load(first_replay_path)
    if (
        int(first_replay.get("physical_workload_invocation_count", -1)) != 5
        or int(first_replay.get("successful_real_replay_count", -1)) != 5
        or int(first_replay.get("technical_failure_count", -1)) != 0
        or first_replay.get("baseline_restore_verified") is not True
    ):
        raise RuntimeError("XP v37 first diagnostic observation is not clean5")

    first_candidates = {
        _params_key(item.get("candidate_params")): item
        for item in first_selection.get("top5") or []
    }
    first_results = {
        str(item.get("fingerprint_id") or ""): item
        for item in first_replay.get("successful") or []
    }
    second_candidates = {
        str((item.get("three_level_fingerprint") or {}).get("id") or ""): item
        for item in source_selection.get("top5") or []
    }
    second_results = {
        str(item.get("fingerprint_id") or ""): item
        for item in source_replay.get("successful") or []
    }
    binding = _candidate_binding(selection)
    records = []
    for row, promoted in zip(binding, selection.get("top5") or []):
        source_id = row["source_fingerprint_id"]
        second_candidate = second_candidates.get(source_id)
        second_result = second_results.get(source_id)
        if second_candidate is None or second_result is None:
            raise RuntimeError("XP v37 promoted point lacks second observation")
        if _params_key(second_candidate.get("candidate_params")) != row[
            "candidate_params_sha256"
        ]:
            raise RuntimeError("XP v37 promoted/source parameter mismatch")
        first_candidate = first_candidates.get(row["candidate_params_sha256"])
        if first_candidate is None:
            raise RuntimeError("XP v37 point lacks exact first-observation params")
        first_id = str(
            (first_candidate.get("three_level_fingerprint") or {}).get("id") or ""
        )
        first_result = first_results.get(first_id)
        if first_result is None:
            raise RuntimeError("XP v37 point lacks first actual observation")
        first_resource = _resource(first_result.get("actual_resource"), "first")
        second_resource = _resource(second_result.get("actual_resource"), "second")
        first_score = float(first_result["actual_score"])
        second_score = float(second_result["actual_score"])
        predicted_resource = _resource(
            second_candidate.get("predicted_resource"), "repeat-predicted"
        )
        if not _same_number(second_candidate.get("candidate_score"), first_score) or any(
            not _same_number(predicted_resource[name], first_resource[name])
            for name in RESOURCE_NAMES
        ):
            raise RuntimeError(
                "XP v37 repeat prediction is not the sealed first actual observation"
            )
        records.append(
            {
                **row,
                "observations": [
                    {
                        "ordinal": 1,
                        "fingerprint_id": first_id,
                        "actual_score": first_score,
                        "actual_resource": first_resource,
                    },
                    {
                        "ordinal": 2,
                        "fingerprint_id": source_id,
                        "actual_score": second_score,
                        "actual_resource": second_resource,
                    },
                ],
            }
        )
    cap = _resource(capability, "capability")
    handoff = {
        "contract": HANDOFF_CONTRACT,
        "status": "sealed_before_formal_workload",
        "formal_candidate_binding": binding,
        "formal_candidate_binding_sha256": _canonical_digest(binding),
        "capability": cap,
        "source_files": {
            "first_selection_path": str(first_selection_path.resolve()),
            "first_selection_sha256": _sha256(first_selection_path),
            "first_replay_path": str(first_replay_path.resolve()),
            "first_replay_sha256": _sha256(first_replay_path),
            "second_selection_path": str(source_selection_path.resolve()),
            "second_selection_sha256": _sha256(source_selection_path),
            "second_replay_path": str(source_replay_path.resolve()),
            "second_replay_sha256": _sha256(source_replay_path),
        },
        "candidates": records,
        "observation_count_per_candidate": 2,
        "workload_calls_added": 0,
        "formal_state_mutations": 0,
    }
    handoff_path = batch_dir / "preformal_robust_handoff.json"
    _atomic_json(handoff_path, handoff)
    return {
        "contract": HANDOFF_CONTRACT,
        "path": str(handoff_path.resolve()),
        "sha256": _sha256(handoff_path),
        "formal_candidate_binding_sha256": handoff[
            "formal_candidate_binding_sha256"
        ],
        "observation_count_per_candidate": 2,
        "sealed_before_formal_workload": True,
    }


def validate_preformal_handoff(
    selection: Mapping[str, Any],
) -> dict[str, Any]:
    """Fail closed on missing, changed, or incorrectly rebound handoff."""

    reference = selection.get("preformal_robust_handoff") or {}
    if (
        reference.get("contract") != HANDOFF_CONTRACT
        or reference.get("sealed_before_formal_workload") is not True
        or int(reference.get("observation_count_per_candidate", -1)) != 2
    ):
        raise RuntimeError("XP v37 formal selection lacks sealed handoff")
    path = Path(str(reference.get("path") or ""))
    if not path.is_file() or _sha256(path) != reference.get("sha256"):
        raise RuntimeError("XP v37 handoff SHA mismatch")
    handoff = _load(path)
    binding = _candidate_binding(selection)
    if (
        handoff.get("contract") != HANDOFF_CONTRACT
        or handoff.get("status") != "sealed_before_formal_workload"
        or handoff.get("formal_candidate_binding") != binding
        or handoff.get("formal_candidate_binding_sha256")
        != _canonical_digest(binding)
        or reference.get("formal_candidate_binding_sha256")
        != _canonical_digest(binding)
        or len(handoff.get("candidates") or []) != 5
    ):
        raise RuntimeError("XP v37 handoff binding mismatch")
    for label in ("first", "second"):
        for kind in ("selection", "replay"):
            path_key = f"{label}_{kind}_path"
            sha_key = f"{label}_{kind}_sha256"
            source_path = Path(str(handoff["source_files"][path_key]))
            if not source_path.is_file() or _sha256(source_path) != handoff[
                "source_files"
            ][sha_key]:
                raise RuntimeError(f"XP v37 {label} {kind} provenance changed")
    return handoff


def score_noise_bound_v37(
    original: Any,
    selection_path: Path,
    replay_path: Path,
) -> float:
    """Do not misclassify probe prediction bias as same-point replay noise.

    A five-probe batch has only one observation per exact point.  It can seed
    the exact-point repeat, but cannot estimate repeatability.  Returning zero
    here only removes the circular *acquisition* precondition; the next clean
    diagnostic replay is still evaluated by the original 2*same-point-MAE
    hard gate before any formal promotion.
    """

    selection = _load(selection_path)
    roles = [
        str(item.get("selection_role") or "")
        for item in selection.get("top5") or []
    ]
    if roles == ["maximin_uncertain_probe"] * 5:
        replay = _load(replay_path)
        if (
            int(replay.get("physical_workload_invocation_count", -1)) != 5
            or int(replay.get("successful_real_replay_count", -1)) != 5
            or int(replay.get("technical_failure_count", -1)) != 0
            or replay.get("baseline_restore_verified") is not True
        ):
            raise RuntimeError("XP v37 probe5 local-noise acquisition is not clean5")
        return 0.0
    return float(original(selection_path, replay_path))


def promote_acceptance_v37(
    adapter: Any,
    summary: dict[str, Any],
    selection: Mapping[str, Any],
    env: Mapping[str, str],
) -> dict[str, Any]:
    """Apply aligned robust formal updates to trusted candidates only."""

    handoff = validate_preformal_handoff(selection)
    try:
        current_score = float(env["XP_INCUMBENT_SCORE"])
        current_params = dict(json.loads(env["XP_INCUMBENT_PARAMS_JSON"]))
        current_ranges = dict(json.loads(env["XP_PARAMETER_RANGES_JSON"]))
        accepted_remaining = int(env["XP_ACCEPTED_REMAINING"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("XP v37 formal acceptance environment is incomplete") from exc
    if accepted_remaining <= 0:
        raise RuntimeError("XP v37 accepted remaining must be positive")
    capability = _resource(handoff.get("capability"), "handoff-capability")
    handoff_by_id = {
        str(item["fingerprint_id"]): item for item in handoff["candidates"]
    }
    candidate_by_id = {
        str((item.get("three_level_fingerprint") or {}).get("id") or ""): item
        for item in selection.get("top5") or []
    }
    accepted_in_batch = 0
    range_reentry_committed = False
    for item in summary.get("successful") or []:
        fingerprint_id = str(item.get("fingerprint_id") or "")
        candidate = candidate_by_id.get(fingerprint_id) or {}
        record = handoff_by_id.get(fingerprint_id)
        if record is None:
            raise RuntimeError("XP v37 formal result lacks sealed prior observations")
        role = str(candidate.get("selection_role") or item.get("selection_role") or "")
        if _params_key(item.get("candidate_params")) != record.get(
            "candidate_params_sha256"
        ):
            raise RuntimeError("XP v37 formal result parameter binding mismatch")
        observations = list(record.get("observations") or [])
        if len(observations) != 2:
            raise RuntimeError("XP v37 prior observation count mismatch")
        third_resource = _resource(item.get("actual_resource"), "formal-third")
        third_score = float(item["actual_score"])
        all_scores = [float(row["actual_score"]) for row in observations] + [
            third_score
        ]
        aggregate_resource = {
            name: statistics.median(
                [float(row["actual_resource"][name]) for row in observations]
                + [third_resource[name]]
            )
            for name in RESOURCE_NAMES
        }
        aggregate_utilization = {
            name: aggregate_resource[name] / capability[name]
            for name in RESOURCE_NAMES
        }
        score_median = statistics.median(all_scores)
        resource_recomputed_score = float(
            adapter.replay_driver.actual_score(aggregate_utilization, capability)
        )
        item["independent_actual_scores"] = all_scores
        item["robust_score_median"] = score_median
        item["robust_aggregate_actual_resource"] = aggregate_resource
        item["robust_aggregate_actual_utilization"] = aggregate_utilization
        item["robust_resource_recomputed_score"] = resource_recomputed_score
        item["robust_acceptance_score"] = resource_recomputed_score
        item["robust_acceptance_statistic"] = ACCEPTANCE_CONTRACT
        trusted = role.startswith("trusted")
        candidate_params = item.get("candidate_params")
        if trusted and (not isinstance(candidate_params, Mapping) or not candidate_params):
            raise RuntimeError("XP v37 trusted candidate lacks params")
        remains_in_range = bool(
            not trusted
            or adapter._params_within_ranges(current_ranges, candidate_params)
        )
        reentry_audit = (
            adapter._validated_range_reentry_audit(
                candidate,
                required_stage="three_clean_observations_aligned_formal_confirmation",
            )
            if trusted and not remains_in_range
            else None
        )
        validated_reentry = reentry_audit is not None
        admissible_range = remains_in_range or validated_reentry
        score_median_improves = score_median > current_score
        resource_score_improves = resource_recomputed_score > current_score
        eligible = bool(
            item.get("technical_failure") is not True
            and item.get("rejected") is not True
            and trusted
            and admissible_range
            and not (validated_reentry and range_reentry_committed)
            and accepted_in_batch < accepted_remaining
            and score_median_improves
            and resource_score_improves
        )
        item.update(
            {
                "selection_role": role,
                "score_median_improvement_gate": score_median_improves,
                "resource_recomputed_score_improvement_gate": resource_score_improves,
                "accepted": eligible,
                "state_commit_eligible": eligible,
                "incumbent_update_applied": eligible,
                "range_update_applied": eligible,
                "gradient_update_applied": eligible,
            }
        )
        if not eligible:
            item["state_patch"] = {}
            if trusted and item.get("technical_failure") is not True:
                item["rejected"] = True
                item["algorithmic_gate_rejection"] = True
                if not score_median_improves:
                    reason = "three_observation_score_median_not_better"
                elif not resource_score_improves:
                    reason = "aggregate_resource_recomputed_score_not_better"
                elif not admissible_range:
                    reason = "outside_current_accepted_range"
                elif validated_reentry and range_reentry_committed:
                    reason = "validated_range_reentry_already_committed_this_batch"
                else:
                    reason = "accepted_target_already_satisfied"
                item["algorithmic_rejection_reason"] = reason
            continue
        current_ranges = adapter._updated_ranges(
            current_ranges,
            current_params,
            candidate_params,
            allow_validated_reentry=validated_reentry,
        )
        if validated_reentry:
            range_reentry_committed = True
            item["validated_range_reentry_applied"] = True
            item["validated_range_reentry_audit"] = reentry_audit
        else:
            item["validated_range_reentry_applied"] = False
        current_score = resource_recomputed_score
        current_params = dict(candidate_params)
        item["state_patch"] = {
            "incumbent_score": current_score,
            "incumbent_params": dict(current_params),
            "parameter_ranges": copy.deepcopy(current_ranges),
            "range_state": copy.deepcopy(current_ranges),
            "incumbent": {
                "params": dict(current_params),
                "score": current_score,
                "actual_resource": aggregate_resource,
                "actual_utilization": aggregate_utilization,
                "source": "xp_v37_formal_trusted_aligned_resource_median",
                "independent_actual_scores": all_scores,
                "score_median": score_median,
                "resource_recomputed_score": resource_recomputed_score,
                "preformal_handoff_sha256": selection[
                    "preformal_robust_handoff"
                ]["sha256"],
            },
        }
        accepted_in_batch += 1

    by_id = {
        str(item.get("fingerprint_id") or ""): item
        for item in summary.get("successful") or []
    }
    for ledger_item in summary.get("ledger") or []:
        promoted = by_id.get(str(ledger_item.get("fingerprint_id") or ""))
        if promoted is None:
            continue
        for field in (
            "accepted",
            "state_commit_eligible",
            "rejected",
            "algorithmic_gate_rejection",
            "algorithmic_rejection_reason",
            "incumbent_update_applied",
            "range_update_applied",
            "gradient_update_applied",
        ):
            if field in promoted:
                ledger_item[field] = copy.deepcopy(promoted[field])
        ledger_item["state_patch"] = copy.deepcopy(promoted.get("state_patch") or {})
    summary.update(
        {
            "continuation_acceptance_policy": ACCEPTANCE_CONTRACT,
            "probe_updates_accepted": False,
            "probe_updates_incumbent": False,
            "probe_updates_range": False,
            "probe_updates_gradient": False,
            "accepted_count_delta": accepted_in_batch,
            "accepted_update_applied": accepted_in_batch > 0,
            "incumbent_updated": accepted_in_batch > 0,
            "range_updated": accepted_in_batch > 0,
            "gradient_update_applied": accepted_in_batch > 0,
            "state_owner": "xp_unattended_continuation",
            "preformal_robust_handoff_sha256": selection[
                "preformal_robust_handoff"
            ]["sha256"],
        }
    )
    return summary


def install(adapter: Any) -> Any:
    """Install v37 hooks before the v33 driver imports adapter callables."""

    serial_replay = importlib.import_module("xp_round9_serial_replay")
    serial_replay.SUPPORTED_SELECTION_CONTRACTS = frozenset(
        set(serial_replay.SUPPORTED_SELECTION_CONTRACTS)
        | {EXACT_REPEAT_SELECTION_CONTRACT}
    )

    original_generate_selection = adapter.generate_selection
    original_run_batch = adapter.run_batch
    original_score_noise_bound = adapter._score_noise_bound

    def score_noise_bound(selection_path: Path, replay_path: Path) -> float:
        return score_noise_bound_v37(
            original_score_noise_bound,
            selection_path,
            replay_path,
        )

    adapter._score_noise_bound = score_noise_bound

    def generate_selection(paths: Any, batch_id: str, state: Mapping[str, Any], batch_dir: Path) -> Any:
        selection = original_generate_selection(paths, batch_id, state, batch_dir)
        calibration = selection.get("calibration_source") or {}
        if (
            selection.get("batch_mode") == "diagnostic_validation"
            and calibration.get("contract")
            == "clean_probe5_exact_plan_first_observation_repeat_v1"
        ):
            selection = copy.deepcopy(selection)
            audit = {
                "contract": NO_PRIOR_REPEAT_NOISE_CONTRACT,
                "status": "ready_for_exact_point_repeat",
                "source_probe5_selection": str(calibration["selection_path"]),
                "source_probe5_selection_sha256": _sha256(
                    Path(str(calibration["selection_path"]))
                ),
                "source_probe5_replay": str(calibration["replay_path"]),
                "source_probe5_replay_sha256": _sha256(
                    Path(str(calibration["replay_path"]))
                ),
                "old_seed_model_bias_is_replay_noise": False,
                "acquisition_minimum_score_span": 0.0,
                "post_repeat_entry_noise_contract": "two_times_same_point_mae_v1",
                "post_repeat_entry_gate_relaxed": False,
                "candidate_params_changed": False,
                "candidate_order_changed": False,
                "manual_candidate_injection": False,
                "workload_calls_added": 0,
                "formal_state_mutations": 0,
            }
            _atomic_json(batch_dir / "v37_local_noise_acquisition_audit.json", audit)
            selection["v37_local_noise_acquisition"] = audit
            selection["score_span_noise_authority"] = {
                "bound": 0.0,
                "source": NO_PRIOR_REPEAT_NOISE_CONTRACT,
                "selection_path": str(calibration["selection_path"]),
                "replay_path": str(calibration["replay_path"]),
                "formal_entry_use": False,
            }
        if selection.get("batch_mode") == "formal":
            capability = (_load(paths.config).get("capability") or {})
            reference = build_preformal_handoff(
                adapter,
                selection,
                batch_dir,
                capability,
            )
            selection = copy.deepcopy(selection)
            selection["preformal_robust_handoff"] = reference
            selection["formal_acceptance_state_contract"] = ACCEPTANCE_CONTRACT
        return selection

    def run_batch(paths: Any, selection: Mapping[str, Any], batch_dir: Path, env: Mapping[str, str]) -> Any:
        if selection.get("batch_mode") == "formal":
            validate_preformal_handoff(selection)
        return original_run_batch(paths, selection, batch_dir, env)

    def promote(summary: dict[str, Any], selection: Mapping[str, Any], env: Mapping[str, str]) -> dict[str, Any]:
        return promote_acceptance_v37(adapter, summary, selection, env)

    adapter.generate_selection = generate_selection
    adapter.run_batch = run_batch
    adapter._promote_continuation_acceptance = promote
    return adapter
