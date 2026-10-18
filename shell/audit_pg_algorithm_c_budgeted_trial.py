#!/usr/bin/env python3
"""Audit a completed budgeted Algorithm C pilot without changing experiment data.

The report deliberately separates pilot diagnostics from publication claims.
Historical A/B values are useful for deciding what to change next, but do not
replace an uninterrupted, matched formal comparison.
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
from algorithm_c_budget_policy import evidence_identity  # noqa: E402
from common_outer_loop import resolve_parameter_transform  # noqa: E402
from shell import run_pg_algorithm_c_scene as base  # noqa: E402


SCENES: Tuple[str, ...] = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
FORMAL_RESULTS = ROOT / "figures/editable_search_time_figures_20260830/formal_results_data.json"
FAIR_ARCHIVE_AUDIT = ROOT / "docs/pg_bc_feasible_archive_audit_20260907.json"
MECHANISM_EVENTS = (
    "algorithm_c_feasible_candidate_archived",
    "algorithm_c_complement_regions_retained",
    "algorithm_c_region_revisited",
    "algorithm_c_candidate_technical_failure",
    "algorithm_c_inflight_physical_wave_recovered",
)


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def structured_log_path(scene_dir: Path) -> Path:
    """Return the structured event stream; controller.log is intentionally mixed text."""
    return scene_dir / "algorithm_c.jsonl"


def finite_positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value)) and float(value) > 0.0


def scan_log(path: Path) -> Dict[str, Any]:
    counts: Counter = Counter()
    technical_labels = set()
    recovery_technical_labels = set()
    malformed_rows = 0
    if not path.is_file():
        return {
            "event_counts": {name: 0 for name in MECHANISM_EVENTS},
            "unique_technical_failure_slots": 0,
            "recovery_technical_failure_slots": 0,
            "malformed_jsonl_rows": 0,
            "log_present": False,
        }
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                malformed_rows += 1
                continue
            name = event.get("event")
            if name in MECHANISM_EVENTS:
                counts[name] += 1
            if name == "algorithm_c_candidate_technical_failure":
                label = event.get("label")
                worker = event.get("worker_key") or event.get("worker")
                technical_labels.add((str(label or line_number), str(worker or "unknown")))
            if name == "algorithm_c_inflight_physical_wave_recovered":
                for label in event.get("synthesized_labels") or []:
                    normalized = str(label)
                    recovery_technical_labels.add(normalized)
                    technical_labels.add((normalized, "recovered_missing_terminal_row"))
    return {
        "event_counts": {name: counts[name] for name in MECHANISM_EVENTS},
        "unique_technical_failure_slots": len(technical_labels),
        "recovery_technical_failure_slots": len(recovery_technical_labels),
        "malformed_jsonl_rows": malformed_rows,
        "log_present": True,
    }


def historical_references() -> Dict[str, Dict[str, Any]]:
    formal = read_json(FORMAL_RESULTS)["postgresql"]["scenes"]
    archive = read_json(FAIR_ARCHIVE_AUDIT)["scenes"]
    references: Dict[str, Dict[str, float]] = {}
    for scene in SCENES:
        algorithms = formal[scene]["algorithms"]
        fair_b = archive[scene]["Algorithm B"].get("observed_finalist") or {}
        fair_b_qps = fair_b.get("observed_qps")
        if not finite_positive(fair_b_qps):
            fair_b_qps = algorithms["Algorithm B"]["champion_qps"]
        references[scene] = {
            "algorithm_a_qps": float(algorithms["Algorithm A"]["champion_qps"]),
            "algorithm_b_fair_archive_qps": float(fair_b_qps),
            "algorithm_b_fair_archive_params": fair_b.get("params"),
        }
    return references


def transformed_fraction(name: str, value: float) -> float:
    low, high = base.DEFAULT_RANGES[name]
    if resolve_parameter_transform(name) == "log":
        return (math.log(float(value)) - math.log(low)) / (math.log(high) - math.log(low))
    return (float(value) - low) / (high - low)


def parameter_distance(left: Mapping[str, Any], right: Mapping[str, Any]) -> Dict[str, Any]:
    deltas = {
        name: abs(transformed_fraction(name, float(left[name]))
                  - transformed_fraction(name, float(right[name])))
        for name in base.ACTIVE_PARAMS
    }
    return {
        "linf_transformed_fraction": max(deltas.values()),
        "rms_transformed_fraction": math.sqrt(sum(value ** 2 for value in deltas.values()) / len(deltas)),
        "per_parameter_absolute_transformed_fraction": deltas,
    }


def reference_region_diagnostic(
    path: Path,
    *,
    reference_params: Optional[Mapping[str, Any]],
    feasible_archive: Mapping[str, Any],
    finalist: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Locate historical B in C's measured search points; thresholds are diagnostic only."""
    if not reference_params or any(name not in reference_params for name in base.ACTIVE_PARAMS):
        return {"available": False, "reason": "historical_b_parameters_missing"}
    unique: Dict[str, Dict[str, Any]] = {}
    if path.is_file():
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("event") != "algorithm_c_candidate_replay":
                    continue
                if not str(event.get("batch_kind") or "").startswith("trial_search"):
                    continue
                validation = event.get("validation") or {}
                ranking = validation.get("candidate_ranking") or {}
                params = ranking.get("optimized_params") or {}
                if any(name not in params for name in base.ACTIVE_PARAMS):
                    continue
                identity = evidence_identity(params)
                row = unique.setdefault(identity, {
                    "identity": identity,
                    "params": params,
                    "labels": [],
                    "workers": [],
                    "raw_qps": [],
                    "has_physically_valid_replay": False,
                })
                row["labels"].append(validation.get("label"))
                row["workers"].append(validation.get("worker_key"))
                if finite_positive(validation.get("db_qps")):
                    row["raw_qps"].append(float(validation["db_qps"]))
                row["has_physically_valid_replay"] = bool(
                    row["has_physically_valid_replay"] or base.physical_valid(validation)
                )
    candidates = [row for row in unique.values() if row["has_physically_valid_replay"]]
    if not candidates:
        return {
            "available": True,
            "tested_parameter_vectors": 0,
            "gap_stage": "no_valid_search_candidates",
            "threshold_contract": "diagnostic_linf_in_full_domain_transformed_coordinates_v1",
        }
    for row in candidates:
        row["distance_to_historical_b"] = parameter_distance(row["params"], reference_params)
        row["archived_feasible"] = row["identity"] in feasible_archive
    candidates.sort(key=lambda item: (
        item["distance_to_historical_b"]["linf_transformed_fraction"],
        item["distance_to_historical_b"]["rms_transformed_fraction"],
        item["identity"],
    ))
    nearest = candidates[0]
    exact_identity = evidence_identity(reference_params)
    near_10 = [row for row in candidates
               if row["distance_to_historical_b"]["linf_transformed_fraction"] <= 0.10]
    near_20 = [row for row in candidates
               if row["distance_to_historical_b"]["linf_transformed_fraction"] <= 0.20]
    feasible_near_10 = [row for row in near_10 if row["archived_feasible"]]
    feasible_near_20 = [row for row in near_20 if row["archived_feasible"]]
    finalist_distance = None
    if finalist and isinstance(finalist.get("params"), Mapping):
        finalist_distance = parameter_distance(finalist["params"], reference_params)
    if exact_identity in unique and unique[exact_identity]["has_physically_valid_replay"]:
        if exact_identity in feasible_archive:
            gap_stage = ("historical_b_exactly_evaluated_archived_and_selected"
                         if finalist and finalist.get("identity") == exact_identity
                         else "historical_b_exactly_evaluated_and_archived_but_not_selected")
        else:
            gap_stage = "historical_b_exactly_evaluated_but_not_semantically_feasible"
    elif feasible_near_10:
        gap_stage = ("near_region_feasible_but_finalist_selected_elsewhere"
                     if not finalist_distance
                     or finalist_distance["linf_transformed_fraction"] > 0.10
                     else "near_region_reached_and_selected")
    elif near_10:
        gap_stage = "near_region_reached_but_no_candidate_was_semantically_feasible"
    elif feasible_near_20:
        gap_stage = "coarse_region_feasible_but_no_local_refinement_reached_10pct"
    elif near_20:
        gap_stage = "coarse_region_reached_but_no_candidate_was_semantically_feasible"
    else:
        gap_stage = "historical_b_region_not_reached"
    return {
        "available": True,
        "threshold_contract": "diagnostic_linf_in_full_domain_transformed_coordinates_v1",
        "threshold_note": "10% and 20% are transparent diagnostics, not statistical equivalence claims",
        "tested_parameter_vectors": len(candidates),
        "historical_b_exactly_evaluated": exact_identity in unique
        and unique[exact_identity]["has_physically_valid_replay"],
        "within_10pct_linf_vectors": len(near_10),
        "within_20pct_linf_vectors": len(near_20),
        "feasible_within_10pct_linf_vectors": len(feasible_near_10),
        "feasible_within_20pct_linf_vectors": len(feasible_near_20),
        "nearest": nearest,
        "finalist_distance_to_historical_b": finalist_distance,
        "gap_stage": gap_stage,
    }


def timing_references(manifest: Mapping[str, Any], scene: str) -> Dict[str, float]:
    timing = manifest["timing_references"][scene]
    return {
        "algorithm_a_log_wall_s": float(timing["Algorithm A"]["log_wall_s"]),
        "algorithm_b_log_wall_s": float(timing["Algorithm B"]["log_wall_s"]),
    }


def suggested_changes(scene: str, row: Mapping[str, Any]) -> List[str]:
    changes: List[str] = []
    if not row["validation_passed"]:
        reason = row.get("validation_reason")
        if reason == "no_feasible_finalist":
            changes.append("move deterministic feasibility-first coverage earlier within the same physical budget")
        elif reason == "incomplete_or_technically_invalid_validation":
            changes.append("repair technical validation reliability without increasing candidate budget")
        else:
            changes.append("diagnose the failed semantic or drift gate before changing search ranking")
    if not row["beats_historical_a_and_b_provisionally"]:
        gap_stage = (row.get("historical_b_region_diagnostic") or {}).get("gap_stage")
        if gap_stage == "historical_b_region_not_reached":
            changes.append(
                "increase deterministic transformed-space coverage before local shrink, without adding replay slots"
            )
        elif gap_stage in {
            "historical_b_exactly_evaluated_and_archived_but_not_selected",
            "near_region_feasible_but_finalist_selected_elsewhere",
        }:
            changes.append(
                "fix archive-to-finalist selection so feasible high-QPS basins cannot be lost after discovery"
            )
        elif gap_stage in {
            "historical_b_exactly_evaluated_but_not_semantically_feasible",
            "near_region_reached_but_no_candidate_was_semantically_feasible",
            "coarse_region_reached_but_no_candidate_was_semantically_feasible",
        }:
            changes.append(
                "retain the region but use repeated evidence to distinguish semantic infeasibility from noise"
            )
        elif gap_stage == "coarse_region_feasible_but_no_local_refinement_reached_10pct":
            changes.append("allocate the next bounded local probes around the best feasible coarse-region point")
        elif row["mechanism"]["event_counts"]["algorithm_c_region_revisited"] == 0:
            changes.append("trigger bounded retained-region revisit before the final search slots")
        else:
            changes.append("rank archived feasible basins by uncertainty-aware QPS evidence before finalization")
    if row["mechanism"]["unique_technical_failure_slots"]:
        changes.append("do not infer the formal search cap from this reboot-depleted pilot")
    if not row["faster_than_historical_a_and_b_provisionally"]:
        changes.append("reduce planning overhead or replay admission count; do not relax quality gates")
    if scene == "xp_io" and row["feasible_archive_size"] == 0:
        changes.append("prioritize I/O feasibility discovery before local incumbent shrink")
    return changes


def audit(campaign_root: Path) -> Dict[str, Any]:
    campaign_root = campaign_root.resolve()
    manifest_path = campaign_root / "budgeted_launch_manifest.json"
    launcher_path = campaign_root / "launcher_status.json"
    if not manifest_path.is_file() or not launcher_path.is_file():
        missing = [str(path) for path in (manifest_path, launcher_path) if not path.is_file()]
        return {
            "contract": "algorithm_c_budgeted_trial_audit_v1",
            "campaign_root": str(campaign_root),
            "complete": False,
            "missing": missing,
            "safe_to_modify_algorithm": False,
            "safe_to_launch_formal": False,
        }

    manifest = read_json(manifest_path)
    launcher = read_json(launcher_path)
    references = historical_references()
    missing_results = [scene for scene in SCENES
                       if not (campaign_root / scene / "budgeted_trial_result.json").is_file()]
    launcher_finished = launcher.get("phase") == "finished"
    if missing_results or not launcher_finished:
        return {
            "contract": "algorithm_c_budgeted_trial_audit_v1",
            "campaign_root": str(campaign_root),
            "complete": False,
            "launcher_phase": launcher.get("phase"),
            "missing_result_scenes": missing_results,
            "safe_to_modify_algorithm": False,
            "safe_to_launch_formal": False,
            "reason": "pilot_is_not_terminal",
        }

    scene_rows: Dict[str, Any] = {}
    for scene in SCENES:
        result = read_json(campaign_root / scene / "budgeted_trial_result.json")
        trial = result.get("trial") or {}
        validation = trial.get("validation") or {}
        policy = result.get("policy") or {}
        budget = policy.get("budget") or {}
        wall_s = trial.get("wall_s")
        qps = validation.get("normalized_qps_geomean")
        timing = timing_references(manifest, scene)
        qps_valid = finite_positive(qps)
        wall_valid = finite_positive(wall_s)
        beats_quality = bool(
            qps_valid
            and float(qps) > references[scene]["algorithm_a_qps"]
            and float(qps) > references[scene]["algorithm_b_fair_archive_qps"]
        )
        faster = bool(
            wall_valid
            and float(wall_s) < timing["algorithm_a_log_wall_s"]
            and float(wall_s) < timing["algorithm_b_log_wall_s"]
        )
        event_log = structured_log_path(campaign_root / scene)
        mechanism = scan_log(event_log)
        archive_size = len(trial.get("feasible_archive") or {})
        finalist = trial.get("finalist")
        b_region = reference_region_diagnostic(
            event_log,
            reference_params=references[scene]["algorithm_b_fair_archive_params"],
            feasible_archive=trial.get("feasible_archive") or {},
            finalist=finalist,
        )
        validation_evidence_complete = bool(
            (not finalist and validation.get("reason") == "no_feasible_finalist")
            or (
                finalist
                and validation.get("physical_replays") == 8
                and validation.get("aggregation") == "balanced_worker_repetition_geometric_mean"
                and len(validation.get("raw_finalist_qps") or []) == 4
                and len(validation.get("raw_post_baseline_qps") or []) == 4
            )
        )
        row = {
            "status": result.get("status"),
            "validation_passed": validation.get("passed") is True,
            "validation_reason": validation.get("reason"),
            "normalized_qps_geomean": qps,
            "bracketed_speedup_geomean": validation.get("bracketed_speedup_geomean"),
            "baseline_drift": validation.get("baseline_drift"),
            "wall_s": wall_s,
            "search_replays": trial.get("search_replays"),
            "search_cap": budget.get("max_search_replays"),
            "planning_s": trial.get("planning_s"),
            "host_outage_excluded_s": trial.get("host_outage_excluded_s"),
            "wall_budget_met": trial.get("wall_budget_met") is True,
            "search_budget_met": trial.get("search_budget_met") is True,
            "clean_restore_passed": (trial.get("clean_restore_receipt") or {}).get("passed") is True,
            "validation_evidence_complete": validation_evidence_complete,
            "active_time_contract_valid": trial.get("active_time_contract") == ACTIVE_TIME_CONTRACT,
            "trial_contract_valid": result.get("contract") == CONTRACT and trial.get("contract") == CONTRACT,
            "feasible_archive_size": archive_size,
            "pending_regions": len(trial.get("region_queue") or []),
            "visited_regions": len(trial.get("visited_regions") or []),
            "search_stop_reason": trial.get("search_stop_reason"),
            "historical_quality_reference": references[scene],
            "historical_timing_reference": timing,
            "beats_historical_a_and_b_provisionally": beats_quality,
            "faster_than_historical_a_and_b_provisionally": faster,
            "mechanism": mechanism,
            "historical_b_region_diagnostic": b_region,
        }
        row["suggested_changes"] = suggested_changes(scene, row)
        scene_rows[scene] = row

    evidence_integrity = all(
        row["search_budget_met"]
        and row["clean_restore_passed"]
        and row["validation_evidence_complete"]
        and row["active_time_contract_valid"]
        and row["trial_contract_valid"]
        and row["mechanism"]["log_present"]
        and row["mechanism"]["malformed_jsonl_rows"] == 0
        for row in scene_rows.values()
    )
    execution_contract = all(
        row["wall_budget_met"]
        and row["search_budget_met"]
        and row["clean_restore_passed"]
        for row in scene_rows.values()
    )
    all_validated = all(row["validation_passed"] for row in scene_rows.values())
    provisional_target_met = all(
        row["beats_historical_a_and_b_provisionally"]
        and row["faster_than_historical_a_and_b_provisionally"]
        for row in scene_rows.values()
    )
    return {
        "contract": "algorithm_c_budgeted_trial_audit_v1",
        "campaign_root": str(campaign_root),
        "source_evidence_sha256": {
            "budgeted_launch_manifest.json": sha256(manifest_path),
            "launcher_status.json": sha256(launcher_path),
            **{
                f"{scene}/budgeted_trial_result.json": sha256(
                    campaign_root / scene / "budgeted_trial_result.json"
                )
                for scene in SCENES
            },
        },
        "complete": True,
        "launcher_phase": launcher.get("phase"),
        "evidence_integrity_passed": evidence_integrity,
        "execution_budget_contract_passed": execution_contract,
        "all_scenes_validated": all_validated,
        "provisional_four_scene_quality_and_efficiency_target_met": provisional_target_met,
        "publication_claim_allowed": False,
        "publication_claim_blocker": (
            "pilot had host reboots and compares against historical A/B; "
            "a separate uninterrupted matched formal run is required"
        ),
        "safe_to_modify_algorithm": evidence_integrity,
        "safe_to_launch_formal": False,
        "formal_launch_blocker": "algorithm changes and regression/preflight gates have not yet been applied",
        "scenes": scene_rows,
    }


def compact_summary(report: Mapping[str, Any]) -> Dict[str, Any]:
    if not report.get("complete"):
        return dict(report)
    return {
        "complete": True,
        "evidence_integrity_passed": report["evidence_integrity_passed"],
        "execution_budget_contract_passed": report["execution_budget_contract_passed"],
        "all_scenes_validated": report["all_scenes_validated"],
        "provisional_target_met": report["provisional_four_scene_quality_and_efficiency_target_met"],
        "scenes": {
            scene: {
                "validated": row["validation_passed"],
                "qps": row["normalized_qps_geomean"],
                "beats_a_b": row["beats_historical_a_and_b_provisionally"],
                "wall_s": row["wall_s"],
                "faster_a_b": row["faster_than_historical_a_and_b_provisionally"],
                "archive": row["feasible_archive_size"],
                "revisits": row["mechanism"]["event_counts"]["algorithm_c_region_revisited"],
                "historical_b_gap_stage": row["historical_b_region_diagnostic"].get("gap_stage"),
                "changes": row["suggested_changes"],
            }
            for scene, row in report["scenes"].items()
        },
    }


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = audit(args.campaign_root.resolve())
    if args.output:
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(compact_summary(report), ensure_ascii=False, indent=2), flush=True)
    return 0 if report.get("complete") else 2


if __name__ == "__main__":
    raise SystemExit(main())
