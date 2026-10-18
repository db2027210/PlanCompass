#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


SCENES: Tuple[str, ...] = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
QPS_CONTRACT = "candidate_qps_strictly_above_previous_accepted_round_v1"
ACCEPTANCE_TIERS = {"strict", "secondary", "fallback"}
REQUIRED_TOPK_FIELDS = {
    "feature_ratio",
    "predicted_resource",
    "utilization",
    "dScore/dU",
    "T",
    "confidence",
    "g_j",
    "eta",
    "delta_param",
    "candidate_score",
}


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def read_events(path: Path) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
            if isinstance(payload, dict):
                events.append(payload)
    return events


def state_and_history_names(algorithm: str) -> Tuple[str, str]:
    if algorithm == "B":
        return "algorithm_b_state.json", "algorithm_b.jsonl"
    if algorithm == "C":
        return "algorithm_c_state.json", "algorithm_c.jsonl"
    raise ValueError(f"unsupported algorithm: {algorithm}")


def audit_source_hashes(manifest: Mapping[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    checks: List[Dict[str, Any]] = []
    errors: List[str] = []
    scenes = manifest.get("scenes") if isinstance(manifest.get("scenes"), dict) else {}
    for scene in SCENES:
        row = scenes.get(scene) if isinstance(scenes.get(scene), dict) else {}
        source = row.get("source_authority") if isinstance(row.get("source_authority"), dict) else {}
        for key in ("config", "state", "history"):
            path = Path(str(source.get(key) or ""))
            expected = str(source.get(f"{key}_sha256") or "")
            exists = path.is_file()
            actual = sha256_file(path) if exists else ""
            passed = bool(exists and expected and actual == expected)
            checks.append(
                {
                    "scene": scene,
                    "artifact": key,
                    "path": str(path),
                    "expected_sha256": expected,
                    "actual_sha256": actual,
                    "passed": passed,
                }
            )
            if not passed:
                errors.append(f"{scene}: source {key} hash mismatch or missing")
    return checks, errors


def audit_new_config(scene: str, manifest_row: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    errors: List[str] = []
    path = Path(str(manifest_row.get("new_config") or ""))
    expected = str(manifest_row.get("new_config_sha256") or "")
    exists = path.is_file()
    actual = sha256_file(path) if exists else ""
    passed = bool(exists and expected and actual == expected)
    if not passed:
        errors.append(f"{scene}: new config hash mismatch or missing")
    qps_reference_mode: Optional[str] = None
    if exists:
        cfg = read_json(path)
        outer = (
            cfg.get("binary_plan_tuning", {})
            .get("binary_search", {})
            .get("nested_loop", {})
            .get("outer_acceptance", {})
        )
        qps_reference_mode = str(outer.get("qps_reference_mode") or "")
        if qps_reference_mode != "last_accepted":
            errors.append(f"{scene}: qps_reference_mode is not last_accepted")
    return {
        "scene": scene,
        "path": str(path),
        "expected_sha256": expected,
        "actual_sha256": actual,
        "qps_reference_mode": qps_reference_mode,
        "passed": not errors,
    }, errors


def accepted_decision(event: Mapping[str, Any]) -> Mapping[str, Any]:
    decision = event.get("decision")
    return decision if isinstance(decision, dict) else {}


def audit_candidate_ranking_contract(
    scene: str,
    events: Sequence[Mapping[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Audit formal Top-K telemetry, plan dedupe, and zero-gradient safety."""
    errors: List[str] = []
    rows: List[Dict[str, Any]] = []
    for event in events:
        if event.get("event") != "algorithm_c_subset_ranking":
            continue
        round_index = int(event.get("round_index") or 0)
        if round_index < 1:
            continue
        topk = event.get("top5")
        topk = topk if isinstance(topk, list) else []
        missing_fields = 0
        zero_gradient_violations = 0
        for candidate in topk:
            if not isinstance(candidate, dict):
                missing_fields += 1
                continue
            if not REQUIRED_TOPK_FIELDS.issubset(candidate):
                missing_fields += 1
            gradient = candidate.get("g_j")
            delta = candidate.get("delta_param")
            gradient = gradient if isinstance(gradient, dict) else {}
            delta = delta if isinstance(delta, dict) else {}
            gradient_is_zero = bool(gradient) and all(
                abs(safe_float(value)) <= 1e-15
                for value in gradient.values()
            )
            nonzero_delta = any(
                isinstance(value, dict)
                and abs(safe_float(value.get("absolute"))) > 1e-15
                for value in delta.values()
            )
            if gradient_is_zero and nonzero_delta:
                zero_gradient_violations += 1
        checks = {
            "topk_nonempty": bool(topk),
            "topk_at_most_five": len(topk) <= 5,
            "required_fields_present": missing_fields == 0,
            "zero_gradient_has_zero_delta": zero_gradient_violations == 0,
            "qps_not_used_for_inner_scoring": (
                event.get("qps_used_for_inner_scoring") is False
            ),
            "uncertain_probe_not_used": event.get("uncertain_probe_used") is False,
            "behavior_cluster_required": (
                event.get("behavior_cluster_required_for_formal_top5") is True
            ),
            "full_plan_dedupe_key": (
                event.get("base_topk_dedupe_key")
                == "validated_full_physical_plan_fingerprint"
            ),
        }
        passed = all(checks.values())
        row = {
            "round_index": round_index,
            "topk_count": len(topk),
            "missing_required_field_count": missing_fields,
            "zero_gradient_violation_count": zero_gradient_violations,
            "checks": checks,
            "passed": passed,
        }
        rows.append(row)
        if not passed:
            failed = sorted(key for key, value in checks.items() if not value)
            errors.append(
                f"{scene}: round {round_index} ranking failed {','.join(failed)}"
            )

    for event in events:
        if event.get("event") != "algorithm_c_outer_replay_admission":
            continue
        round_index = int(event.get("round_index") or 0)
        if round_index < 1:
            continue
        selected = event.get("selected")
        selected = selected if isinstance(selected, list) else []
        fingerprints: List[str] = []
        exact_parameter_plan_identities: List[str] = []
        for candidate in selected:
            assessment = (
                candidate.get("assessment")
                if isinstance(candidate, dict)
                and isinstance(candidate.get("assessment"), dict)
                else {}
            )
            fingerprint = str(assessment.get("full_plan_fingerprint") or "")
            fingerprints.append(fingerprint)
            params = (
                candidate.get("optimized_params")
                if isinstance(candidate, dict)
                and isinstance(candidate.get("optimized_params"), dict)
                else {}
            )
            exact_parameter_plan_identities.append(
                json.dumps(params, sort_keys=True, separators=(",", ":"))
                + "|"
                + fingerprint
            )
        selected_count = int(event.get("selected_count") or 0)
        plan_counts = {
            fingerprint: fingerprints.count(fingerprint)
            for fingerprint in set(fingerprints)
        }
        numeric_duplicate_rows = sum(
            count for count in plan_counts.values() if count > 1
        )
        numeric_refinement_evidence = []
        for candidate_event in events:
            if candidate_event.get("event") != "algorithm_c_exhausted_partial_topk":
                continue
            if candidate_event.get("round_index") != event.get("round_index"):
                continue
            if safe_float(candidate_event.get("time")) > safe_float(event.get("time")):
                continue
            declared_count = int(
                candidate_event.get("same_plan_numeric_refinement_count") or 0
            )
            evidence_valid = bool(
                candidate_event.get("plan_space_saturated") is True
                and candidate_event.get("same_plan_numeric_refinement_enabled")
                is True
                and candidate_event.get(
                    "same_plan_numeric_refinement_requires_plan_space_saturation"
                )
                is True
                and candidate_event.get(
                    "exact_parameter_plan_identity_deduplication"
                )
                is True
                and candidate_event.get("baseline_padding_allowed") is False
                and candidate_event.get("uncertain_probe_used") is False
                and declared_count > 0
                and 0 < numeric_duplicate_rows <= declared_count
                and len(set(exact_parameter_plan_identities))
                == len(exact_parameter_plan_identities)
            )
            numeric_refinement_evidence.append(
                {
                    "declared_numeric_refinement_count": declared_count,
                    "selected_numeric_duplicate_rows": numeric_duplicate_rows,
                    "evidence_valid": evidence_valid,
                }
            )
        numeric_refinement_valid = any(
            row["evidence_valid"] for row in numeric_refinement_evidence
        )
        checks = {
            "selected_count_matches": selected_count == len(selected),
            "selected_at_most_five": selected_count <= 5,
            "full_plan_fingerprints_present": all(fingerprints),
            "full_plan_deduped_or_audited_numeric_refinement": (
                len(set(fingerprints)) == len(fingerprints)
                or numeric_refinement_valid
            ),
            "exact_parameter_plan_identities_distinct": (
                len(set(exact_parameter_plan_identities))
                == len(exact_parameter_plan_identities)
            ),
            "qps_not_used_for_inner_scoring": (
                event.get("qps_used_for_inner_scoring") is False
            ),
            "technical_failures_not_rules": (
                event.get("technical_failures_used_as_rules") is False
            ),
            "technical_failures_not_valid_identities": (
                event.get("technical_failures_used_as_valid_replay_identities")
                is False
            ),
        }
        passed = all(checks.values())
        rows.append(
            {
                "round_index": round_index,
                "admission_selected_count": selected_count,
                "admission_distinct_full_plan_count": len(set(fingerprints)),
                "same_plan_numeric_refinement_exception": (
                    numeric_refinement_valid
                ),
                "same_plan_numeric_refinement_evidence": (
                    numeric_refinement_evidence
                ),
                "checks": checks,
                "passed": passed,
            }
        )
        if not passed:
            failed = sorted(key for key, value in checks.items() if not value)
            errors.append(
                f"{scene}: round {round_index} admission failed {','.join(failed)}"
            )

    for event in events:
        if event.get("event") != "algorithm_c_fallback_plan_change_expansion":
            continue
        round_index = int(event.get("round_index") or 0)
        admission = event.get("outer_replay_admission")
        admission = admission if isinstance(admission, dict) else {}
        selected = admission.get("selected")
        selected = selected if isinstance(selected, list) else []
        fingerprints: List[str] = []
        for candidate in selected:
            assessment = (
                candidate.get("assessment")
                if isinstance(candidate, dict)
                and isinstance(candidate.get("assessment"), dict)
                else {}
            )
            fingerprints.append(str(assessment.get("full_plan_fingerprint") or ""))
        selected_count = int(admission.get("selected_count") or 0)
        extra_count = int(event.get("extra_candidate_count") or 0)
        raw_count = int(event.get("raw_extra_candidate_count") or 0)
        input_count = int(admission.get("input_candidate_count") or 0)
        exact_valid_excluded = admission.get("exact_valid_replay_excluded")
        exact_valid_excluded = (
            exact_valid_excluded
            if isinstance(exact_valid_excluded, list)
            else []
        )
        all_input_already_valid = bool(
            input_count > 0
            and raw_count == input_count == len(exact_valid_excluded)
            and int(admission.get("exact_valid_replay_excluded_count") or 0)
            == input_count
            and all(
                isinstance(candidate, dict)
                and isinstance(candidate.get("identity_assessment"), dict)
                and candidate["identity_assessment"].get("seen_valid_replay")
                is True
                and candidate["identity_assessment"].get(
                    "technical_failures_used"
                )
                is False
                for candidate in exact_valid_excluded
            )
        )
        checks = {
            "fallback_trigger": (
                event.get("trigger") == "fallback_tier_base_top5_all_missed"
            ),
            "plan_tree_change_required": (
                event.get("plan_tree_change_required") is True
            ),
            "unique_plan_fingerprint_required": (
                event.get("unique_plan_fingerprint_required") is True
            ),
            "selected_count_matches": (
                selected_count == extra_count == len(selected)
            ),
            # A saturated plan space may legitimately yield no new full-plan
            # candidate.  The controller must record that exhaustion as an
            # empty expansion instead of padding the batch with baseline,
            # duplicate, or uncertain probes.
            "selected_zero_to_five": 0 <= selected_count <= 5,
            "empty_expansion_is_true_exhaustion": (
                selected_count > 0
                or (
                    selected_count == 0
                    and extra_count == 0
                    and (
                        (raw_count == 0 and input_count == 0)
                        or all_input_already_valid
                    )
                    and not selected
                )
            ),
            "no_candidate_padding": raw_count >= extra_count,
            "full_plan_fingerprints_present": all(fingerprints),
            "full_plan_fingerprints_distinct": (
                len(set(fingerprints)) == len(fingerprints)
            ),
            "qps_not_used_for_inner_scoring": (
                admission.get("qps_used_for_inner_scoring") is False
            ),
            "technical_failures_not_rules": (
                admission.get("technical_failures_used_as_rules") is False
            ),
            "technical_failures_not_valid_identities": (
                admission.get("technical_failures_used_as_valid_replay_identities")
                is False
            ),
        }
        passed = all(checks.values())
        rows.append(
            {
                "round_index": round_index,
                "fallback_plan_change_selected_count": selected_count,
                "fallback_plan_change_raw_count": raw_count,
                "fallback_plan_change_distinct_count": len(set(fingerprints)),
                "fallback_plan_change_all_input_already_valid": (
                    all_input_already_valid
                ),
                "checks": checks,
                "passed": passed,
            }
        )
        if not passed:
            failed = sorted(key for key, value in checks.items() if not value)
            errors.append(
                f"{scene}: round {round_index} fallback expansion failed "
                f"{','.join(failed)}"
            )
    return rows, errors


def audit_rejected_round_contract(
    scene: str,
    events: Sequence[Mapping[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Prove that rejected rounds do not mutate formal optimizer state."""
    errors: List[str] = []
    rows: List[Dict[str, Any]] = []
    accepted_count = 0
    accepted_rounds = {
        int(event.get("round_index") or 0)
        for event in events
        if event.get("event") == "algorithm_c_round_accepted"
    }
    for event in events:
        event_name = event.get("event")
        if event_name == "algorithm_c_round_accepted":
            accepted_count += 1
            continue
        if event_name != "algorithm_c_round_rejected":
            continue
        round_index = int(event.get("round_index") or 0)
        checks = {
            "accepted_count_unchanged": (
                int(event.get("accepted") or 0) == accepted_count
            ),
            "incumbent_unchanged": event.get("incumbent_unchanged") is True,
            "ranges_unchanged": event.get("ranges_unchanged") is True,
            "gradient_state_unchanged": (
                event.get("gradient_state_unchanged") is True
            ),
            "no_accepted_event_in_same_round": round_index not in accepted_rounds,
        }
        passed = all(checks.values())
        rows.append(
            {
                "round_index": round_index,
                "accepted_before_and_after": accepted_count,
                "checks": checks,
                "passed": passed,
            }
        )
        if not passed:
            failed = sorted(key for key, value in checks.items() if not value)
            errors.append(
                f"{scene}: rejected round {round_index} failed {','.join(failed)}"
            )
    return rows, errors


def audit_physical_replay_contract(
    scene: str,
    events: Sequence[Mapping[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Audit per-batch budget and physical/valid/technical accounting."""
    errors: List[str] = []
    rows: List[Dict[str, Any]] = []
    for event in events:
        if event.get("event") == "algorithm_c_physical_batch_start":
            candidate_count = int(event.get("candidate_count") or 0)
            checks = {
                "candidate_count_nonnegative": candidate_count >= 0,
                "diagnostic_batch_at_most_five": candidate_count <= 5,
            }
            passed = all(checks.values())
            rows.append(
                {
                    "kind": "physical_batch_start",
                    "round_index": int(event.get("round_index") or 0),
                    "batch_kind": event.get("batch_kind"),
                    "candidate_count": candidate_count,
                    "checks": checks,
                    "passed": passed,
                }
            )
            if not passed:
                errors.append(
                    f"{scene}: round {event.get('round_index')} physical batch "
                    f"exceeds five candidates"
                )
            continue
        if event.get("event") != "algorithm_c_candidate_replay":
            continue
        validation = event.get("validation")
        validation = validation if isinstance(validation, dict) else {}
        physical = int(validation.get("physical_workload_invocations") or 0)
        valid = int(validation.get("valid_replay_count") or 0)
        technical = validation.get("technical_failure") is True
        checks = {
            "one_physical_invocation": physical == 1,
            "used_real_replay": validation.get("used_real_replay") is True,
            "valid_count_binary": valid in {0, 1},
            "technical_failure_not_valid": not technical or valid == 0,
            "successful_replay_is_valid": technical or valid == 1,
        }
        passed = all(checks.values())
        rows.append(
            {
                "kind": "candidate_replay",
                "round_index": int(event.get("round_index") or 0),
                "batch_kind": event.get("batch_kind"),
                "physical_slot": event.get("physical_slot"),
                "label": validation.get("label"),
                "physical_workload_invocations": physical,
                "valid_replay_count": valid,
                "technical_failure": technical,
                "checks": checks,
                "passed": passed,
            }
        )
        if not passed:
            failed = sorted(key for key, value in checks.items() if not value)
            errors.append(
                f"{scene}: round {event.get('round_index')} candidate replay "
                f"failed {','.join(failed)}"
            )
    return rows, errors


def audit_accepted_sequence(
    scene: str,
    events: Sequence[Mapping[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    errors: List[str] = []
    accepted_events = [event for event in events if event.get("event") == "algorithm_c_round_accepted"]
    rows: List[Dict[str, Any]] = []
    previous_qps: Optional[float] = None
    for accepted_index, event in enumerate(accepted_events, start=1):
        decision = accepted_decision(event)
        qps = safe_float(decision.get("db_qps"))
        reference_qps = safe_float(decision.get("qps_gate_reference_db_qps"))
        min_gain_pct = safe_float(decision.get("min_db_qps_gain_pct"))
        role = str(decision.get("qps_gate_reference_role") or "")
        tier = str(decision.get("acceptance_tier") or "")
        expected_role = "canonical_baseline_first_round" if accepted_index == 1 else "previous_accepted_round"
        threshold_qps = (
            reference_qps * (1.0 + min_gain_pct / 100.0)
            if math.isfinite(reference_qps) and math.isfinite(min_gain_pct)
            else math.nan
        )
        previous_matches = bool(
            accepted_index == 1
            or (
                previous_qps is not None
                and math.isfinite(reference_qps)
                and math.isclose(reference_qps, previous_qps, rel_tol=0.0, abs_tol=1e-12)
            )
        )
        evidence_rows = []
        for candidate_event in events:
            if candidate_event.get("event") != "algorithm_c_outer_candidate_decision":
                continue
            if candidate_event.get("round_index") != event.get("round_index"):
                continue
            candidate_decision = accepted_decision(candidate_event)
            candidate_qps = safe_float(candidate_decision.get("db_qps"))
            if not (
                candidate_decision.get("accepted") is True
                and math.isfinite(candidate_qps)
                and math.isfinite(qps)
                and math.isclose(candidate_qps, qps, rel_tol=0.0, abs_tol=1e-12)
            ):
                continue
            source = str(candidate_event.get("evidence_source") or "")
            physical = int(candidate_event.get("physical_workload_invocations") or 0)
            valid_added = int(candidate_event.get("valid_replays_added") or 0)
            cached = candidate_event.get("cached_valid_replay_redecision") is True
            source_valid = bool(
                (
                    source == "physical_replay"
                    and physical == 1
                    and valid_added == 1
                    and not cached
                )
                or (
                    source == "cached_valid_replay"
                    and physical == 0
                    and valid_added == 0
                    and cached
                )
            )
            evidence_rows.append(
                {
                    "label": candidate_event.get("label"),
                    "evidence_source": source,
                    "cached_valid_replay_redecision": cached,
                    "physical_workload_invocations": physical,
                    "valid_replays_added": valid_added,
                    "qps_outer_only": candidate_event.get("qps_outer_only"),
                    "inner_candidate_scores_modified": candidate_event.get(
                        "inner_candidate_scores_modified"
                    ),
                    "source_valid": source_valid,
                }
            )
        evidence_valid = any(
            row["source_valid"]
            and row["qps_outer_only"] is True
            and row["inner_candidate_scores_modified"] is False
            for row in evidence_rows
        )
        checks = {
            "accepted_flag": decision.get("accepted") is True,
            "qps_up_flag": decision.get("qps_up") is True,
            "qps_reference_mode": decision.get("qps_reference_mode") == "last_accepted",
            "qps_contract": decision.get("qps_progress_contract") == QPS_CONTRACT,
            "reference_role": role == expected_role,
            "reference_is_previous_accepted": previous_matches,
            "strict_qps_increase": bool(
                math.isfinite(qps) and math.isfinite(threshold_qps) and qps > threshold_qps
            ),
            "three_tier_member": tier in ACCEPTANCE_TIERS,
            "tier_passed": decision.get("acceptance_tier_passed") is True,
            "accepted_candidate_decision_present": bool(evidence_rows),
            "accepted_candidate_evidence_valid": evidence_valid,
        }
        passed = all(checks.values())
        rows.append(
            {
                "accepted_index": accepted_index,
                "outer_round_index": event.get("round_index"),
                "qps": qps,
                "reference_qps": reference_qps,
                "minimum_gain_pct": min_gain_pct,
                "reference_role": role,
                "acceptance_tier": tier,
                "accepted_candidate_evidence": evidence_rows,
                "checks": checks,
                "passed": passed,
            }
        )
        if not passed:
            failed = sorted(key for key, value in checks.items() if not value)
            errors.append(f"{scene}: accepted {accepted_index} failed {','.join(failed)}")
        if math.isfinite(qps):
            previous_qps = qps
    return rows, errors


def audit_lane(
    algorithm: str,
    root: Path,
    scene: str,
    *,
    require_complete: bool,
) -> Dict[str, Any]:
    state_name, history_name = state_and_history_names(algorithm)
    state_path = root / scene / state_name
    history_path = root / scene / history_name
    errors: List[str] = []
    if not state_path.is_file():
        errors.append(f"{scene}: missing state")
        return {"scene": scene, "passed": False, "errors": errors}
    if not history_path.is_file():
        errors.append(f"{scene}: missing history")
        return {"scene": scene, "passed": False, "errors": errors}

    state = read_json(state_path)
    events = read_events(history_path)
    accepted_rows, accepted_errors = audit_accepted_sequence(scene, events)
    errors.extend(accepted_errors)
    ranking_rows, ranking_errors = audit_candidate_ranking_contract(scene, events)
    errors.extend(ranking_errors)
    rejected_rows, rejected_errors = audit_rejected_round_contract(scene, events)
    errors.extend(rejected_errors)
    physical_rows, physical_errors = audit_physical_replay_contract(scene, events)
    errors.extend(physical_errors)
    contract = state.get("outer_qps_progress_contract")
    contract = contract if isinstance(contract, dict) else {}
    if contract.get("passed") is not True:
        errors.append(f"{scene}: state QPS progress contract did not pass")
    if contract.get("contract") != QPS_CONTRACT:
        errors.append(f"{scene}: state QPS progress contract id mismatch")
    if contract.get("qps_reference_mode") != "last_accepted":
        errors.append(f"{scene}: state QPS reference mode mismatch")

    accepted = int(state.get("accepted") or 0)
    if accepted != len(accepted_rows):
        errors.append(f"{scene}: state accepted={accepted}, history accepted={len(accepted_rows)}")
    if require_complete:
        if state.get("phase") != "complete":
            errors.append(f"{scene}: phase is not complete")
        if accepted != 7:
            errors.append(f"{scene}: accepted is not 7")
        if not isinstance(state.get("final_champion"), dict):
            errors.append(f"{scene}: final_champion missing")

    accepted_technical_failures = 0
    for event in events:
        if event.get("event") != "algorithm_c_round_accepted":
            continue
        candidate = event.get("selected") if isinstance(event.get("selected"), dict) else {}
        normalized = candidate.get("normalized") if isinstance(candidate.get("normalized"), dict) else {}
        if candidate.get("technical_failure") is True or normalized.get("technical_failure") is True:
            accepted_technical_failures += 1
    if accepted_technical_failures:
        errors.append(f"{scene}: technical failure polluted accepted state")

    return {
        "scene": scene,
        "state_path": str(state_path),
        "history_path": str(history_path),
        "phase": state.get("phase"),
        "accepted": accepted,
        "technical_failures": state.get("technical_failures"),
        "physical_workload_invocations": state.get("physical_workload_invocations"),
        "valid_replays": state.get("valid_replays"),
        "final_champion_present": isinstance(state.get("final_champion"), dict),
        "accepted_sequence": accepted_rows,
        "candidate_ranking_contract": ranking_rows,
        "rejected_round_contract": rejected_rows,
        "physical_replay_contract": physical_rows,
        "accepted_technical_failures": accepted_technical_failures,
        "passed": not errors,
        "errors": errors,
    }


def audit_campaign(root: Path, *, require_complete: bool) -> Dict[str, Any]:
    manifest_path = root / "fresh_campaign_manifest.json"
    manifest = read_json(manifest_path)
    algorithm = str(manifest.get("algorithm") or "")
    errors: List[str] = []
    if manifest.get("contract") != "pg_algorithm_bc_last_accepted_qps_fresh_campaign_v1":
        errors.append("fresh campaign manifest contract mismatch")
    if manifest.get("qps_progress_contract") != QPS_CONTRACT:
        errors.append("fresh campaign QPS progress contract mismatch")
    if manifest.get("accepted_state_inherited") is not False:
        errors.append("accepted state was inherited")
    if manifest.get("range_state_inherited") is not False:
        errors.append("range state was inherited")
    if manifest.get("valid_replay_state_inherited") is not False:
        errors.append("valid replay state was inherited")
    if manifest.get("source_authority_read_only") is not True:
        errors.append("source authority was not declared read-only")

    controller_checks: List[Dict[str, Any]] = []
    repair = manifest.get("basin_escape_repair")
    if isinstance(repair, dict):
        for role in ("algorithm_controller", "shared_outer_framework_controller"):
            path = Path(str(repair.get(role) or ""))
            expected = str(repair.get(f"{role}_sha256") or "")
            exists = path.is_file()
            actual = sha256_file(path) if exists else ""
            passed = bool(exists and expected and actual == expected)
            controller_checks.append(
                {
                    "role": role,
                    "path": str(path),
                    "expected_sha256": expected,
                    "actual_sha256": actual,
                    "passed": passed,
                }
            )
            if not passed:
                errors.append(f"{role} hash mismatch or missing")
        provenance = manifest.get("repair_provenance")
        provenance = provenance if isinstance(provenance, dict) else {}
        for key in ("state_inherited", "range_inherited", "valid_replay_inherited"):
            if provenance.get(key) is not False:
                errors.append(f"repair provenance {key} is not false")

    source_checks, source_errors = audit_source_hashes(manifest)
    errors.extend(source_errors)
    config_checks: List[Dict[str, Any]] = []
    manifest_scenes = manifest.get("scenes") if isinstance(manifest.get("scenes"), dict) else {}
    for scene in SCENES:
        manifest_row = manifest_scenes.get(scene) if isinstance(manifest_scenes.get(scene), dict) else {}
        config_check, config_errors = audit_new_config(scene, manifest_row)
        config_checks.append(config_check)
        errors.extend(config_errors)

    lanes = [audit_lane(algorithm, root, scene, require_complete=require_complete) for scene in SCENES]
    for lane in lanes:
        errors.extend(str(error) for error in lane.get("errors", []))
    return {
        "contract": "pg_algorithm_bc_last_accepted_qps_campaign_audit_v1",
        "algorithm": algorithm,
        "campaign_root": str(root),
        "require_complete": require_complete,
        "manifest_path": str(manifest_path),
        "source_authority_hash_checks": source_checks,
        "controller_hash_checks": controller_checks,
        "new_config_checks": config_checks,
        "lanes": lanes,
        "passed": not errors,
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign_roots", nargs="+", type=Path)
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    audits = [
        audit_campaign(root.resolve(), require_complete=not args.allow_incomplete)
        for root in args.campaign_roots
    ]
    payload = {
        "contract": "pg_algorithm_bc_last_accepted_qps_suite_audit_v1",
        "require_complete": not args.allow_incomplete,
        "campaigns": audits,
        "passed": all(audit.get("passed") is True for audit in audits),
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered, flush=True)
    if not payload["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
