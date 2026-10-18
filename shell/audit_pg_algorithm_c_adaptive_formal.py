#!/usr/bin/env python3
"""Audit a terminal adaptive Algorithm C run and compare it only afterwards.

This module deliberately places the terminal gate before loading any Algorithm
A/B evidence.  It is therefore suitable for proving that external baseline
results could not influence Algorithm C's runtime search decisions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from algorithm_c_budget_policy import ACTIVE_TIME_CONTRACT, CONTRACT  # noqa: E402
from algorithm_c_execution_role import FORMAL, FORMAL_ROLE  # noqa: E402
from shell import run_pg_algorithm_c_scene as base  # noqa: E402
from shell.prepare_pg_algorithm_c_budgeted_formal import (  # noqa: E402
    c_only_model_source_audit,
    verify_no_external_baseline_information,
)


SCENES: Tuple[str, ...] = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
COMPARISON_PATH = ROOT / "docs/pg_abc_gptuner_aqetuner_final_comparison_20260826.json"
ARCHIVE_PATH = ROOT / "docs/pg_bc_feasible_archive_audit_20260907.json"
TIMING_PATH = ROOT / "figures/editable_search_time_figures_20260830/formal_results_data.json"
STARTUP_SEARCH_DIR = ROOT / "docs"
FORMAL_TIMING_SCOPE = (
    "controller_initialization_through_final_restore_including_"
    "baseline_planning_wait_replay_validation"
)
SEARCH_TIMING_METRIC = "sum of hard-gate-pass candidate workload elapsed seconds"
MATCHED_ABSOLUTE_QPS_CONTRACT = "algorithm_abc_pg_common_domain_final_validation_v1"
MATCHED_SOURCE_CONTRACT = "pg_four_scene_adjacent_bracketed_frozen_champion_replay_v2"


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def finite_positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value)) and float(value) > 0.0


def sha256_string(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.lower())
    )


def resolve_startup_receipt(
    campaign_root: Path,
    explicit_path: Optional[Path],
    *,
    search_dir: Path = STARTUP_SEARCH_DIR,
) -> Tuple[Optional[Path], List[str]]:
    """Resolve the startup receipt for this exact C campaign.

    Automatic resolution is intentionally keyed by the receipt's campaign root;
    a filename or an older campaign version is never accepted as a substitute.
    """
    if explicit_path is not None:
        if explicit_path.is_file():
            return explicit_path.resolve(), []
        return None, [f"startup_receipt_missing:{explicit_path}"]

    matches: List[Path] = []
    for path in sorted(search_dir.glob("pg_algorithm_c_adaptive_formal_*_startup_*.json")):
        try:
            receipt = read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        receipt_root = receipt.get("campaign_root")
        if not isinstance(receipt_root, str):
            continue
        if Path(receipt_root).resolve() == campaign_root.resolve():
            matches.append(path.resolve())
    if not matches:
        return None, ["startup_receipt_for_exact_campaign_missing"]
    if len(matches) != 1:
        return None, [
            "startup_receipt_for_exact_campaign_ambiguous:"
            + ",".join(str(path) for path in matches)
        ]
    return matches[0], []


def true_flag_paths(value: Any, names: set[str], path: str = "$") -> List[str]:
    """Find forbidden true flags recursively in structured runtime evidence."""
    found: List[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if str(key) in names and child is True:
                found.append(child_path)
            found.extend(true_flag_paths(child, names, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(true_flag_paths(child, names, f"{path}[{index}]"))
    return found


def _window_elapsed_s(validation: Mapping[str, Any]) -> Optional[float]:
    window = validation.get("window")
    if not isinstance(window, list) or len(window) != 2:
        return None
    if not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in window):
        return None
    elapsed = float(window[1]) - float(window[0])
    return elapsed if elapsed > 0.0 else None


def scan_formal_log(path: Path) -> Dict[str, Any]:
    counts: Counter[str] = Counter()
    batch_counts: Counter[str] = Counter()
    valid_batch_counts: Counter[str] = Counter()
    malformed = 0
    technical_events = 0
    qps_inner_violations: List[str] = []
    baseline_input_violations: List[str] = []
    search_elapsed_s = 0.0
    search_elapsed_rows = 0
    search_rows_missing_window = 0
    if not path.is_file():
        return {
            "present": False,
            "malformed_jsonl_rows": 0,
            "event_counts": {},
            "batch_counts": {},
            "valid_batch_counts": {},
            "technical_events": 0,
            "qps_inner_violations": [],
            "baseline_input_violations": [],
            "search_candidate_elapsed_s": None,
            "search_candidate_elapsed_hours": None,
            "search_elapsed_rows": 0,
            "search_rows_missing_window": 0,
        }
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            name = str(event.get("event") or "")
            counts[name] += 1
            qps_inner_flags = {
                "qps_used_for_inner_scoring",
                "qps_used_for_candidate_ranking",
                "qps_used_for_candidate_score",
                "qps_used_for_gradient",
                "qps_used_for_resource_prediction",
                "qps_used_for_region_priority",
            }
            qps_inner_violations.extend(
                f"line={line_number}:{name}:{path}"
                for path in true_flag_paths(event, qps_inner_flags)
            )
            decision = event.get("decision")
            if isinstance(decision, Mapping):
                if decision.get("baseline_runtime_or_candidate_inputs_used") is True:
                    baseline_input_violations.append(
                        f"line={line_number}:{name}:decision.baseline_runtime_or_candidate_inputs_used"
                    )
            if event.get("external_baseline_information_used") is True:
                baseline_input_violations.append(
                    f"line={line_number}:{name}:external_baseline_information_used"
                )
            if name in {
                "algorithm_c_candidate_technical_failure",
                "algorithm_c_inflight_physical_wave_recovered",
            }:
                technical_events += 1
            if name != "algorithm_c_candidate_replay":
                continue
            batch_kind = str(event.get("batch_kind") or "")
            batch_counts[batch_kind] += 1
            validation = event.get("validation")
            validation = validation if isinstance(validation, Mapping) else {}
            if not base.physical_valid(validation):
                continue
            valid_batch_counts[batch_kind] += 1
            if batch_kind == FORMAL.search_batch_kind:
                elapsed = _window_elapsed_s(validation)
                if elapsed is None:
                    search_rows_missing_window += 1
                else:
                    search_elapsed_s += elapsed
                    search_elapsed_rows += 1
    return {
        "present": True,
        "malformed_jsonl_rows": malformed,
        "event_counts": dict(counts),
        "batch_counts": dict(batch_counts),
        "valid_batch_counts": dict(valid_batch_counts),
        "technical_events": technical_events,
        "qps_inner_violations": qps_inner_violations,
        "baseline_input_violations": baseline_input_violations,
        "search_candidate_elapsed_s": search_elapsed_s,
        "search_candidate_elapsed_hours": search_elapsed_s / 3600.0,
        "search_elapsed_rows": search_elapsed_rows,
        "search_rows_missing_window": search_rows_missing_window,
    }


def _runtime_independence_audit(
    campaign_root: Path,
    manifest: Mapping[str, Any],
) -> Dict[str, Any]:
    errors: List[str] = []
    if manifest.get("campaign_role") != FORMAL_ROLE:
        errors.append("formal_campaign_role_required")
    if manifest.get("historical_candidates_imported") is not False:
        errors.append("historical_candidates_must_not_be_imported")
    for key in (
        "external_baseline_runtime_inputs",
        "external_baseline_candidate_inputs",
        "external_baseline_training_sources",
    ):
        if manifest.get(key) != []:
            errors.append(f"{key}_must_be_empty")
    if manifest.get("qps_target_used_for_inner_search") is not False:
        errors.append("qps_target_used_for_inner_search_must_be_false")
    if manifest.get("qps_used_for_outer_interval_control") is not True:
        errors.append("qps_outer_interval_control_must_be_true")
    if manifest.get("same_two_workers_per_scene") is not True:
        errors.append("two_workers_per_scene_required")
    if manifest.get("uninterrupted_execution_required") is not True:
        errors.append("uninterrupted_execution_required")
    try:
        verify_no_external_baseline_information(manifest, "formal_manifest")
    except RuntimeError as exc:
        errors.append(str(exc))

    source_hashes = manifest.get("source_sha256")
    if not isinstance(source_hashes, Mapping) or not source_hashes:
        errors.append("sealed_runtime_source_hashes_required")
    else:
        for relative, expected in source_hashes.items():
            source = ROOT / str(relative)
            if not source.is_file() or sha256(source) != expected:
                errors.append(f"sealed_runtime_source_changed:{relative}")

    scene_evidence: Dict[str, Any] = {}
    manifest_scenes = manifest.get("scenes")
    if not isinstance(manifest_scenes, Mapping) or set(manifest_scenes) != set(SCENES):
        errors.append("exact_four_scene_manifest_required")
        manifest_scenes = {}
    for scene in SCENES:
        entry = manifest_scenes.get(scene) if isinstance(manifest_scenes, Mapping) else None
        if not isinstance(entry, Mapping):
            errors.append(f"missing_manifest_scene:{scene}")
            continue
        config_path = Path(str(entry.get("config") or ""))
        row: Dict[str, Any] = {"config_path": str(config_path)}
        if not config_path.is_file():
            errors.append(f"missing_runtime_config:{scene}")
            scene_evidence[scene] = row
            continue
        config = read_json(config_path)
        row["config_sha256"] = sha256(config_path)
        try:
            verify_no_external_baseline_information(config, f"runtime_config:{scene}")
        except RuntimeError as exc:
            errors.append(str(exc))
        algorithm = config.get("algorithm_c")
        algorithm = algorithm if isinstance(algorithm, Mapping) else {}
        policy = algorithm.get("budgeted_trial")
        policy = policy if isinstance(policy, Mapping) else {}
        if policy.get("execution_role") != FORMAL_ROLE:
            errors.append(f"formal_execution_role_required:{scene}")
        if "budget" in policy:
            errors.append(f"fixed_budget_forbidden:{scene}")
        if not isinstance(policy.get("adaptive_search"), Mapping):
            errors.append(f"adaptive_search_required:{scene}")
        if policy.get("algorithm_revision") != manifest.get("algorithm_revision"):
            errors.append(f"algorithm_revision_mismatch:{scene}")
        if policy.get("algorithm_design_sha256") != manifest.get("algorithm_design_sha256"):
            errors.append(f"algorithm_design_sha256_mismatch:{scene}")
        model_path = Path(str(algorithm.get("experience_table_path") or ""))
        row["model_path"] = str(model_path)
        if not model_path.is_file():
            errors.append(f"missing_c_only_model:{scene}")
        else:
            row["model_sha256"] = sha256(model_path)
            if row["model_sha256"] != entry.get("model_sha256"):
                errors.append(f"c_only_model_hash_mismatch:{scene}")
            model = read_json(model_path)
            try:
                row["model_training_audit"] = c_only_model_source_audit(model)
                verify_no_external_baseline_information(model, f"resource_model:{scene}")
            except RuntimeError as exc:
                errors.append(f"{scene}:{exc}")
        scene_evidence[scene] = row
    unique_errors = sorted(set(errors))
    source_integrity_errors = [
        error for error in unique_errors
        if error.startswith("sealed_runtime_source_")
        or "model_hash_mismatch" in error
    ]
    independence_proof_errors = [
        error for error in unique_errors if error not in source_integrity_errors
    ]
    return {
        "passed": not unique_errors,
        "external_baseline_independence_passed": not independence_proof_errors,
        "sealed_runtime_integrity_passed": not source_integrity_errors,
        "errors": unique_errors,
        "independence_proof_errors": independence_proof_errors,
        "source_integrity_errors": source_integrity_errors,
        "scene_evidence": scene_evidence,
        "external_baseline_information_loaded_by_auditor_before_terminal_gate": False,
    }


def _load_post_terminal_references(
    comparison_path: Path,
    archive_path: Path,
    timing_path: Path,
) -> Dict[str, Any]:
    comparison = read_json(comparison_path)
    archive = read_json(archive_path)
    timing = read_json(timing_path)
    by_scene: Dict[str, Dict[str, Any]] = {scene: {} for scene in SCENES}
    for row in comparison.get("four_scene_results") or []:
        scene = row.get("scene")
        method = row.get("method")
        if scene in by_scene and method in {"Algorithm A", "Algorithm B"}:
            by_scene[scene][method] = row
    timing_scenes = ((timing.get("postgresql") or {}).get("scenes") or {})
    archive_scenes = archive.get("scenes") or {}
    references: Dict[str, Any] = {}
    for scene in SCENES:
        a = by_scene[scene].get("Algorithm A") or {}
        b = by_scene[scene].get("Algorithm B") or {}
        b_archive = ((archive_scenes.get(scene) or {}).get("Algorithm B") or {})
        observed = b_archive.get("observed_finalist") or {}
        b_values = [value for value in (b.get("final_qps"), observed.get("observed_qps"))
                    if finite_positive(value)]
        scene_timing = timing_scenes.get(scene) or {}
        algorithms = scene_timing.get("algorithms") or {}
        references[scene] = {
            "algorithm_a_final_qps": a.get("final_qps"),
            "algorithm_b_reported_final_qps": b.get("final_qps"),
            "algorithm_b_strongest_observed_qps": max(b_values) if b_values else None,
            "algorithm_a_relative_gain_pct": a.get("qps_improvement_pct"),
            "algorithm_b_relative_gain_pct": b.get("qps_improvement_pct"),
            "algorithm_a_search_hours": (algorithms.get("Algorithm A") or {}).get("actual_search_hours"),
            "algorithm_b_search_hours": (algorithms.get("Algorithm B") or {}).get("actual_search_hours"),
        }
    return {
        "comparison_absolute_qps_cross_method_comparable": (
            comparison.get("absolute_qps_cross_method_comparable") is True
        ),
        "comparison_metric": comparison.get("comparison_metric"),
        "normalization_contract": comparison.get("normalization_contract"),
        "timing_scope": timing.get("timing_scope"),
        "timing_metric_matches": (
            ((timing.get("timing_scope") or {}).get("actual_metric") == SEARCH_TIMING_METRIC)
        ),
        "scenes": references,
        "source_sha256": {
            str(comparison_path): sha256(comparison_path),
            str(archive_path): sha256(archive_path),
            str(timing_path): sha256(timing_path),
        },
    }


def _matched_absolute_qps(
    path: Optional[Path],
) -> Dict[str, Any]:
    if path is None or not path.is_file():
        return {
            "available": False,
            "passed_contract": False,
            "reason": "matched_A_B_C_common_domain_champion_validation_missing",
        }
    payload = read_json(path)
    errors: List[str] = []
    if payload.get("contract") != MATCHED_ABSOLUTE_QPS_CONTRACT:
        errors.append("unexpected_contract")
    if payload.get("aggregation") != "balanced_worker_repetition_geometric_mean":
        errors.append("aggregation_mismatch")
    if payload.get("same_workers_same_workload_same_baseline_brackets") is not True:
        errors.append("execution_domain_mismatch")
    if payload.get("source_common_domain_contract") != MATCHED_SOURCE_CONTRACT:
        errors.append("source_contract_mismatch")
    if payload.get("search_performed") is not False:
        errors.append("matched_validation_must_not_search")
    if payload.get("algorithm_c_was_terminal_before_a_b_evidence_loaded") is not True:
        errors.append("algorithm_c_terminal_before_a_b_load_not_proven")
    if payload.get("distinct_repetition_seeds") is not True:
        errors.append("distinct_repetition_seeds_not_proven")
    source_runs = payload.get("source_runs")
    source_runs = source_runs if isinstance(source_runs, list) else []
    if len(source_runs) < 2:
        errors.append("at_least_two_source_repetitions_required")
    observed_seeds: List[int] = []
    observed_roots: List[str] = []
    for expected_repetition, source in enumerate(source_runs, start=1):
        if not isinstance(source, Mapping):
            errors.append(f"invalid_source_run:{expected_repetition}")
            continue
        if source.get("repetition") != expected_repetition:
            errors.append(f"source_repetition_index_mismatch:{expected_repetition}")
        seed = source.get("seed")
        if isinstance(seed, bool) or not isinstance(seed, int):
            errors.append(f"invalid_source_seed:{expected_repetition}")
        else:
            observed_seeds.append(seed)
        root = source.get("root")
        if not isinstance(root, str) or not root:
            errors.append(f"invalid_source_root:{expected_repetition}")
        else:
            observed_roots.append(root)
        for name in ("authority_sha256", "state_sha256", "contract_sha256"):
            if not sha256_string(source.get(name)):
                errors.append(f"invalid_source_hash:{expected_repetition}:{name}")
    if len(set(observed_seeds)) != len(source_runs):
        errors.append("source_seeds_not_distinct")
    if len(set(observed_roots)) != len(source_runs):
        errors.append("source_roots_not_distinct")

    repetition_count = len(source_runs)
    payload_scenes = payload.get("scenes")
    payload_scenes = payload_scenes if isinstance(payload_scenes, Mapping) else {}
    if set(payload_scenes) != set(SCENES):
        errors.append("exact_four_matched_scenes_required")
    rows: Dict[str, Any] = {}
    for scene in SCENES:
        values = payload_scenes.get(scene)
        values = values if isinstance(values, Mapping) else {}
        methods = ("Algorithm A", "Algorithm B", "Algorithm C")
        if set(values) != set(methods):
            errors.append(f"exact_three_methods_required:{scene}")
        qps: Dict[str, Any] = {}
        for method in methods:
            method_row = values.get(method)
            method_row = method_row if isinstance(method_row, Mapping) else {}
            reported_qps = method_row.get("qps_geomean")
            qps[method] = reported_qps
            raw_qps = method_row.get("raw_qps")
            raw_qps = raw_qps if isinstance(raw_qps, list) else []
            if method_row.get("worker_count") != 2:
                errors.append(f"worker_count_mismatch:{scene}:{method}")
            if method_row.get("repetitions_per_worker") != repetition_count:
                errors.append(f"repetition_count_mismatch:{scene}:{method}")
            if not isinstance(method_row.get("params"), Mapping) or not method_row.get("params"):
                errors.append(f"missing_frozen_params:{scene}:{method}")
            if len(raw_qps) != 2 * repetition_count:
                errors.append(f"raw_qps_count_mismatch:{scene}:{method}")
            raw_values: List[float] = []
            grid = Counter()
            sources: List[str] = []
            for observation in raw_qps:
                if not isinstance(observation, Mapping):
                    errors.append(f"invalid_raw_qps_row:{scene}:{method}")
                    continue
                repetition = observation.get("repetition")
                worker = observation.get("worker")
                value = observation.get("qps")
                source = observation.get("source")
                if (
                    isinstance(repetition, bool)
                    or not isinstance(repetition, int)
                    or repetition < 1
                    or repetition > repetition_count
                    or worker not in {"w1", "w2"}
                    or not finite_positive(value)
                    or not isinstance(source, str)
                    or not source
                    or not sha256_string(observation.get("source_sha256"))
                ):
                    errors.append(f"invalid_raw_qps_row:{scene}:{method}")
                    continue
                grid[(repetition, worker)] += 1
                raw_values.append(float(value))
                sources.append(source)
            expected_grid = {
                (repetition, worker)
                for repetition in range(1, repetition_count + 1)
                for worker in ("w1", "w2")
            }
            if set(grid) != expected_grid or any(count != 1 for count in grid.values()):
                errors.append(f"unbalanced_worker_repetition_grid:{scene}:{method}")
            if len(set(sources)) != len(sources):
                errors.append(f"duplicate_raw_qps_source:{scene}:{method}")
            if len(raw_values) == 2 * repetition_count and finite_positive(reported_qps):
                recomputed = math.exp(sum(math.log(value) for value in raw_values) / len(raw_values))
                if not math.isclose(float(reported_qps), recomputed, rel_tol=1e-12, abs_tol=0.0):
                    errors.append(f"qps_geomean_mismatch:{scene}:{method}")
        valid = all(finite_positive(value) for value in qps.values())
        c_wins = bool(valid and float(qps["Algorithm C"]) > float(qps["Algorithm A"])
                      and float(qps["Algorithm C"]) > float(qps["Algorithm B"]))
        if not valid:
            errors.append(f"invalid_qps:{scene}")
        rows[scene] = {"qps": qps, "algorithm_c_wins": c_wins}
    return {
        "available": True,
        "passed_contract": not errors,
        "errors": errors,
        "all_four_scenes_c_win": not errors and all(row["algorithm_c_wins"] for row in rows.values()),
        "scenes": rows,
        "source": str(path),
        "source_sha256": sha256(path),
    }


def audit(
    campaign_root: Path,
    *,
    comparison_path: Path = COMPARISON_PATH,
    archive_path: Path = ARCHIVE_PATH,
    timing_path: Path = TIMING_PATH,
    startup_path: Optional[Path] = None,
    matched_absolute_qps_path: Optional[Path] = None,
) -> Dict[str, Any]:
    campaign_root = campaign_root.resolve()
    manifest_path = campaign_root / "budgeted_launch_manifest.json"
    launcher_path = campaign_root / "launcher_status.json"
    required = [manifest_path, launcher_path]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        return {
            "contract": "algorithm_c_adaptive_formal_terminal_audit_v1",
            "campaign_root": str(campaign_root),
            "complete": False,
            "reason": "formal_campaign_receipts_missing",
            "missing": missing,
            "post_terminal_baseline_evidence_loaded": False,
        }
    manifest = read_json(manifest_path)
    launcher = read_json(launcher_path)
    if manifest.get("final_validation_enabled") is False:
        return {
            "contract": "algorithm_c_adaptive_formal_terminal_audit_v1",
            "campaign_root": str(campaign_root),
            "complete": False,
            "reason": "search_only_campaign_has_no_independent_final_validation",
            "search_only_result_paths": {
                scene: str(campaign_root / scene / FORMAL.result_filename)
                for scene in SCENES
            },
            "post_terminal_baseline_evidence_loaded": False,
            "publication_claim_allowed": False,
        }
    result_paths = {scene: campaign_root / scene / FORMAL.result_filename for scene in SCENES}
    missing_results = [scene for scene, path in result_paths.items() if not path.is_file()]
    if launcher.get("phase") != "finished" or missing_results:
        return {
            "contract": "algorithm_c_adaptive_formal_terminal_audit_v1",
            "campaign_root": str(campaign_root),
            "complete": False,
            "reason": "formal_campaign_is_not_successfully_terminal",
            "launcher_phase": launcher.get("phase"),
            "missing_result_scenes": missing_results,
            "post_terminal_baseline_evidence_loaded": False,
        }

    independence = _runtime_independence_audit(campaign_root, manifest)
    formal_preflight_path = campaign_root / "formal_launch_preflight.json"
    formal_preflight = read_json(formal_preflight_path) if formal_preflight_path.is_file() else {}
    resolved_startup_path, startup_resolution_errors = resolve_startup_receipt(
        campaign_root,
        startup_path,
    )
    startup = read_json(resolved_startup_path) if resolved_startup_path is not None else {}
    preflight_passed = bool(
        formal_preflight.get("passed") is True
        and formal_preflight.get("contract") == "algorithm_c_post_pilot_formal_preflight_v1"
        and formal_preflight.get("external_baseline_information_used") is False
        and formal_preflight.get("normal_fixed_search_or_wall_budget") is False
    )
    startup_passed = bool(
        not startup_resolution_errors
        and startup.get("passed") is True
        and startup.get("contract") == "algorithm_c_formal_startup_stability_v1"
        and Path(str(startup.get("campaign_root") or campaign_root)).resolve() == campaign_root
        and set((startup.get("scene_rows") or {}).keys()) == set(SCENES)
    )

    scene_rows: Dict[str, Any] = {}
    for scene in SCENES:
        result_path = result_paths[scene]
        result = read_json(result_path)
        trial = result.get("trial")
        trial = trial if isinstance(trial, Mapping) else {}
        validation = trial.get("validation")
        validation = validation if isinstance(validation, Mapping) else {}
        policy = result.get("policy")
        policy = policy if isinstance(policy, Mapping) else {}
        adaptive_audit = result.get("adaptive_policy_audit")
        adaptive_audit = (
            adaptive_audit if isinstance(adaptive_audit, Mapping) else {}
        )
        log = scan_formal_log(campaign_root / scene / "algorithm_c.jsonl")
        expected_validation = {
            FORMAL.finalist_batch_kind(1): 2,
            FORMAL.finalist_batch_kind(2): 2,
            FORMAL.post_baseline_batch_kind(1): 2,
            FORMAL.post_baseline_batch_kind(2): 2,
        }
        validation_batches_complete = all(
            log["valid_batch_counts"].get(batch, 0) == expected
            for batch, expected in expected_validation.items()
        )
        search_counts_match = bool(
            log["batch_counts"].get(FORMAL.search_batch_kind, 0) == trial.get("search_replays")
            and log["valid_batch_counts"].get(FORMAL.search_batch_kind, 0)
            == trial.get("search_valid_replays")
        )
        forbidden_batch_kinds = sorted(
            batch for batch in log["batch_counts"]
            if batch.startswith("trial_")
        )
        evidence_errors: List[str] = []
        checks = {
            "result_contract": result.get("contract") == CONTRACT,
            "formal_execution_role": policy.get("execution_role") == FORMAL_ROLE,
            "formal_result_not_provisional": result.get("historical_timing_comparison_is_provisional") is False,
            "runtime_external_baseline_flag_false": result.get("external_baseline_information_used_at_runtime") is False,
            "timing_scope": result.get("timing_scope") == FORMAL_TIMING_SCOPE,
            "adaptive_convergence": trial.get("search_stop_reason") == "adaptive_convergence"
            and trial.get("algorithmic_convergence_reached") is True,
            "not_operational_safety_stop": trial.get("operational_safety_guard_reached") is False,
            "normal_fixed_budget_not_used": trial.get("normal_fixed_budget_used") is False,
            "dynamic_numeric_refinement_policy": bool(
                adaptive_audit.get("contract")
                == "algorithm_c_adaptive_search_policy_v4"
                and adaptive_audit.get(
                    "numeric_refinement_uses_progressive_low_discrepancy_windows"
                ) is True
                and adaptive_audit.get(
                    "same_plan_new_parameter_identity_is_not_plan_level_hard_pruned"
                ) is True
                and adaptive_audit.get(
                    "outer_feasibility_boundary_refinement_enabled"
                ) is True
                and adaptive_audit.get(
                    "outer_boundary_endpoints_use_c_current_run_evidence_only"
                ) is True
            ),
            "adaptive_champion_stability_policy": bool(
                adaptive_audit.get(
                    "feasible_champion_stops_after_outer_stability"
                ) is True
                and adaptive_audit.get(
                    "champion_stability_uses_external_baseline_time"
                ) is False
            ),
            "valid_for_claim": trial.get("valid_for_quality_or_efficiency_claim") is True,
            "no_search_technical_failures": trial.get("search_technical_failures") == 0,
            "clean_final_restore": (trial.get("clean_restore_receipt") or {}).get("passed") is True,
            "validation_passed": validation.get("passed") is True,
            "validation_aggregation": validation.get("aggregation")
            == "balanced_worker_repetition_geometric_mean",
            "validation_replay_count": validation.get("physical_replays") == 8,
            "validation_raw_counts": len(validation.get("raw_finalist_qps") or []) == 4
            and len(validation.get("raw_post_baseline_qps") or []) == 4,
            "active_time_contract": trial.get("active_time_contract") == ACTIVE_TIME_CONTRACT,
            "structured_log_present": log["present"],
            "structured_log_well_formed": log["malformed_jsonl_rows"] == 0,
            "formal_finished_event_once": log["event_counts"].get(FORMAL.finished_event, 0) == 1,
            "validation_batches_complete": validation_batches_complete,
            "search_counts_match": search_counts_match,
            "no_trial_batch_kinds": not forbidden_batch_kinds,
            "no_log_technical_events": log["technical_events"] == 0,
            "qps_excluded_from_inner_search": not log["qps_inner_violations"],
            "no_external_baseline_log_evidence": not log["baseline_input_violations"],
            "search_elapsed_metric_complete": log["search_rows_missing_window"] == 0
            and log["search_elapsed_rows"] == trial.get("search_valid_replays"),
        }
        evidence_errors.extend(name for name, passed in checks.items() if not passed)
        scene_rows[scene] = {
            "result_path": str(result_path),
            "result_sha256": sha256(result_path),
            "checks": checks,
            "evidence_errors": evidence_errors,
            "evidence_integrity_passed": not evidence_errors,
            "final_qps_geomean": validation.get("normalized_qps_geomean"),
            "relative_qps_gain_pct": (
                (float(validation["bracketed_speedup_geomean"]) - 1.0) * 100.0
                if finite_positive(validation.get("bracketed_speedup_geomean")) else None
            ),
            "controller_end_to_end_wall_s": trial.get("wall_s"),
            "search_candidate_elapsed_s": log["search_candidate_elapsed_s"],
            "search_candidate_elapsed_hours": log["search_candidate_elapsed_hours"],
            "search_replays": trial.get("search_replays"),
            "log_audit": log,
        }

    # This is intentionally the first point at which A/B evidence is opened.
    references = _load_post_terminal_references(comparison_path, archive_path, timing_path)
    matched = _matched_absolute_qps(matched_absolute_qps_path)
    for scene, row in scene_rows.items():
        reference = references["scenes"][scene]
        qps = row["final_qps_geomean"]
        a_qps = reference["algorithm_a_final_qps"]
        b_qps = reference["algorithm_b_strongest_observed_qps"]
        c_hours = row["search_candidate_elapsed_hours"]
        a_hours = reference["algorithm_a_search_hours"]
        b_hours = reference["algorithm_b_search_hours"]
        row["post_terminal_historical_reference"] = reference
        row["beats_strongest_historical_a_and_b_absolute_qps"] = bool(
            finite_positive(qps) and finite_positive(a_qps) and finite_positive(b_qps)
            and float(qps) > float(a_qps) and float(qps) > float(b_qps)
        )
        row["faster_than_a_and_b_matched_search_elapsed_metric"] = bool(
            references["timing_metric_matches"]
            and finite_positive(c_hours) and finite_positive(a_hours) and finite_positive(b_hours)
            and float(c_hours) < float(a_hours) and float(c_hours) < float(b_hours)
        )
        row["beats_a_and_b_relative_own_baseline_qps"] = bool(
            finite_positive(row["relative_qps_gain_pct"])
            and isinstance(reference["algorithm_a_relative_gain_pct"], (int, float))
            and isinstance(reference["algorithm_b_relative_gain_pct"], (int, float))
            and float(row["relative_qps_gain_pct"]) > float(reference["algorithm_a_relative_gain_pct"])
            and float(row["relative_qps_gain_pct"]) > float(reference["algorithm_b_relative_gain_pct"])
        )

    all_evidence = bool(
        independence["passed"] and preflight_passed and startup_passed
        and all(row["evidence_integrity_passed"] for row in scene_rows.values())
    )
    historical_diagnostic_target = bool(
        all_evidence
        and all(row["beats_strongest_historical_a_and_b_absolute_qps"]
                and row["faster_than_a_and_b_matched_search_elapsed_metric"]
                for row in scene_rows.values())
    )
    relative_target = bool(
        all_evidence
        and all(row["beats_a_and_b_relative_own_baseline_qps"]
                and row["faster_than_a_and_b_matched_search_elapsed_metric"]
                for row in scene_rows.values())
    )
    matched_absolute_target = bool(
        all_evidence and matched.get("passed_contract") is True
        and matched.get("all_four_scenes_c_win") is True
        and all(row["faster_than_a_and_b_matched_search_elapsed_metric"]
                for row in scene_rows.values())
    )
    missing_experiments = []
    if not matched.get("available"):
        missing_experiments.append(
            "After C is terminal, replay frozen A/B/C champions on the same two workers with adjacent "
            "baseline brackets and balanced worker-repetition geometric means."
        )
    if not all_evidence:
        missing_experiments.append("Repair every failed terminal evidence check and rerun the affected scene cleanly.")
    priority_fixes = []
    if not historical_diagnostic_target:
        priority_fixes.append(
            "Revise C using only C-internal plan/resource/outer-acceptance evidence for losing scenes, then run a new sealed campaign."
        )
    if not matched.get("available"):
        priority_fixes.append("Run the post-terminal common-domain champion validation before an absolute-QPS paper claim.")
    return {
        "contract": "algorithm_c_adaptive_formal_terminal_audit_v1",
        "campaign_root": str(campaign_root),
        "complete": True,
        "post_terminal_baseline_evidence_loaded": True,
        "source_evidence_sha256": {
            "budgeted_launch_manifest.json": sha256(manifest_path),
            "launcher_status.json": sha256(launcher_path),
            "formal_launch_preflight.json": sha256(formal_preflight_path)
            if formal_preflight_path.is_file() else None,
            "startup_receipt": sha256(resolved_startup_path)
            if resolved_startup_path is not None else None,
            **references["source_sha256"],
        },
        "runtime_independence": independence,
        "coverage_summary": {
            "four_required_scenes_present": set(scene_rows) == set(SCENES),
            "quality_and_efficiency_paired_per_scene": True,
            "terminal_evidence_integrity_passed": all_evidence,
            "startup_receipt_path": str(resolved_startup_path)
            if resolved_startup_path is not None else None,
            "startup_receipt_resolution_errors": startup_resolution_errors,
            "adaptive_convergence_required": True,
            "final_validation_replays_per_scene": 8,
        },
        "fairness_findings": {
            "algorithm_c_runtime_used_a_or_b_information": (
                not independence["external_baseline_independence_passed"]
            ),
            "qps_used_inside_inner_search": any(
                row["log_audit"]["qps_inner_violations"] for row in scene_rows.values()
            ),
            "qps_used_for_outer_interval_control": manifest.get("qps_used_for_outer_interval_control") is True,
            "search_efficiency_metric": SEARCH_TIMING_METRIC,
            "search_efficiency_metric_comparable": references["timing_metric_matches"],
            "historical_absolute_qps_cross_method_comparable": references[
                "comparison_absolute_qps_cross_method_comparable"
            ],
            "historical_absolute_qps_role": "strict_post_terminal_diagnostic_only",
            "relative_quality_metric": references["comparison_metric"],
            "relative_quality_normalization": references["normalization_contract"],
        },
        "scenes": scene_rows,
        "matched_absolute_qps": matched,
        "historical_four_scene_quality_and_efficiency_diagnostic_met": historical_diagnostic_target,
        "relative_quality_and_efficiency_four_scene_target_met": relative_target,
        "publication_absolute_qps_and_efficiency_four_scene_target_met": matched_absolute_target,
        "publication_claim_allowed": matched_absolute_target,
        "missing_experiments": missing_experiments,
        "table_figure_contract": {
            "rows": list(SCENES),
            "columns": [
                "A matched QPS geomean",
                "B matched QPS geomean",
                "C matched QPS geomean",
                "A search elapsed hours",
                "B search elapsed hours",
                "C search elapsed hours",
            ],
            "quality_aggregation": "balanced_worker_repetition_geometric_mean",
            "efficiency_aggregation": SEARCH_TIMING_METRIC,
            "claim_rule": "C must strictly exceed A and B in QPS and be strictly lower in search elapsed time in every scene",
        },
        "priority_fixes": priority_fixes,
    }


def compact_summary(report: Mapping[str, Any]) -> Dict[str, Any]:
    if not report.get("complete"):
        return dict(report)
    return {
        "complete": True,
        "runtime_independence_passed": report["runtime_independence"][
            "external_baseline_independence_passed"
        ],
        "sealed_runtime_integrity_passed": report["runtime_independence"][
            "sealed_runtime_integrity_passed"
        ],
        "terminal_evidence_integrity_passed": report["coverage_summary"]["terminal_evidence_integrity_passed"],
        "historical_diagnostic_target_met": report[
            "historical_four_scene_quality_and_efficiency_diagnostic_met"
        ],
        "publication_target_met": report[
            "publication_absolute_qps_and_efficiency_four_scene_target_met"
        ],
        "scenes": {
            scene: {
                "qps": row["final_qps_geomean"],
                "beats_historical_a_b_qps": row[
                    "beats_strongest_historical_a_and_b_absolute_qps"
                ],
                "search_hours": row["search_candidate_elapsed_hours"],
                "faster_a_b": row["faster_than_a_and_b_matched_search_elapsed_metric"],
                "evidence_ok": row["evidence_integrity_passed"],
            }
            for scene, row in report["scenes"].items()
        },
        "missing_experiments": report["missing_experiments"],
    }


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--comparison", type=Path, default=COMPARISON_PATH)
    parser.add_argument("--archive", type=Path, default=ARCHIVE_PATH)
    parser.add_argument("--timing", type=Path, default=TIMING_PATH)
    parser.add_argument("--startup-receipt", type=Path)
    parser.add_argument("--matched-absolute-qps", type=Path)
    args = parser.parse_args(argv)
    report = audit(
        args.campaign_root,
        comparison_path=args.comparison,
        archive_path=args.archive,
        timing_path=args.timing,
        startup_path=args.startup_receipt,
        matched_absolute_qps_path=args.matched_absolute_qps,
    )
    if args.output:
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(compact_summary(report), ensure_ascii=False, indent=2), flush=True)
    if not report.get("complete"):
        return 2
    return 0 if report.get("publication_claim_allowed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
