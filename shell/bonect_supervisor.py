#!/usr/bin/env python3
"""Supervisor loop for Bonect bruteforce tuning runs.

The script watches optimizer JSONL history for one matrix run and emits
control decisions when the experiment is no longer moving toward the target.
It is intentionally conservative: by default it only writes decisions. Passing
--stop-on-trigger lets the supervisor terminate the current run process tree so
the main Codex agent can adjust configuration or code and restart.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HISTORY_ROOT = PROJECT_ROOT / "history" / "pg_benchmark_bottleneck_matrix"
DEFAULT_STATE_FILE = DEFAULT_HISTORY_ROOT / "bonect_supervisor_state.json"
DEFAULT_CONFIG_ROOT = PROJECT_ROOT / "config" / "generated" / "pg_benchmark_bottleneck_matrix"

COST_PARAM_KEYS = [
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
]
COST_PARAM_SET = set(COST_PARAM_KEYS)

RESOURCE_TARGETS: Dict[str, Tuple[float, float]] = {
    "cpu": (65.0, 80.0),
    "memory": (70.0, 80.0),
    "io": (50.0, 75.0),
}

SCORE_FLAT_REL_TOL = 1e-3
RESOURCE_FLAT_REL_TOL = 1e-3
REAL_NO_PROGRESS_PCT = 1.0
STUCK_SPAN_PCT = 2.0
OFF_TARGET_MARGIN_PCT = 5.0


@dataclass
class CaseState:
    """In-memory summary for one bottleneck/benchmark optimizer history."""

    bottleneck: str
    benchmark: str
    history_path: Path
    rounds: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    final_summaries: List[Dict[str, Any]] = field(default_factory=list)
    last_event_ts: Optional[str] = None
    emitted_signatures: Set[str] = field(default_factory=set)


@dataclass
class TriggerDecision:
    """A supervisor decision emitted from one or more trigger conditions."""

    run_id: str
    bottleneck: str
    benchmark: str
    history_path: str
    latest_round: int
    triggers: List[Dict[str, Any]]
    action: str
    reason: str
    created_at: str
    stopped_pids: List[int] = field(default_factory=list)
    memory_replay_calibration_injection: Dict[str, Any] = field(default_factory=dict)


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_pct(value: Any) -> Optional[float]:
    number = safe_float(value)
    if number is None:
        return None
    if 0.0 <= number <= 1.5:
        return number * 100.0
    return number


def rel_span(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    lo = min(values)
    hi = max(values)
    denom = max(1.0, abs(lo), abs(hi))
    return (hi - lo) / denom


def run_dir_for(run_id: str, history_root: Path) -> Path:
    return history_root / run_id


def labels_from_history_path(run_dir: Path, path: Path) -> Tuple[str, str]:
    try:
        rel = path.relative_to(run_dir)
        bottleneck = rel.parts[0] if len(rel.parts) >= 2 else "unknown"
    except ValueError:
        bottleneck = path.parent.name
    benchmark = path.name.split("_optimizer_history", 1)[0]
    return bottleneck, benchmark


def find_run_dirs(run_id: str, history_root: Path) -> List[Path]:
    exact = run_dir_for(run_id, history_root)
    if exact.is_dir():
        return [exact]
    if not history_root.is_dir():
        return []
    candidates = [path for path in history_root.iterdir() if path.is_dir() and run_id in path.name]
    return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)


def find_history_files(run_id: str, history_root: Path) -> List[Path]:
    latest_by_case: Dict[Tuple[str, str], Path] = {}
    for run_dir in find_run_dirs(run_id, history_root):
        for path in sorted(run_dir.glob("*/*_optimizer_history*.jsonl")):
            key = labels_from_history_path(run_dir, path)
            current = latest_by_case.get(key)
            if current is None:
                latest_by_case[key] = path
                continue
            try:
                path_mtime = path.stat().st_mtime
                current_mtime = current.stat().st_mtime
            except OSError:
                path_mtime = 0.0
                current_mtime = 0.0
            if (path_mtime, path.name) > (current_mtime, current.name):
                latest_by_case[key] = path
    return sorted(latest_by_case.values(), key=lambda path: (str(path.parent), path.name))


def config_paths_for_run(run_id: str, config_root: Path = DEFAULT_CONFIG_ROOT) -> List[Path]:
    run_dir = config_root / run_id
    if not run_dir.is_dir():
        return []
    return sorted(path for path in run_dir.glob("*/*.json") if path.is_file())


def latest_memory_optimizer_history(run_dir: Path, benchmark: Optional[str] = None) -> Optional[Path]:
    """Return the newest Memory optimizer JSONL under a run directory."""
    memory_dir = run_dir / "memory"
    if not memory_dir.is_dir():
        return None
    paths = [
        path
        for path in memory_dir.glob("*_optimizer_history*.jsonl")
        if path.is_file()
    ]
    if benchmark:
        paths = [path for path in paths if path.name.startswith(f"{benchmark}_optimizer_history")]
    if not paths:
        return None
    return sorted(paths, key=lambda path: (path.stat().st_mtime, path.name), reverse=True)[0]


def _load_memory_replay_calibration_builder():
    """Import the RC#1c evidence builder without requiring package execution."""
    try:
        from build_memory_replay_calibration_evidence import build_evidence_from_jsonl

        return build_evidence_from_jsonl
    except ImportError:
        shell_dir = str(Path(__file__).resolve().parent)
        if shell_dir not in sys.path:
            sys.path.insert(0, shell_dir)
        from build_memory_replay_calibration_evidence import build_evidence_from_jsonl

        return build_evidence_from_jsonl


def build_memory_replay_calibration_injection(
    run_dir: Path,
    decision: TriggerDecision,
    *,
    baseline_memory: Optional[float] = None,
    delta: float = 0.001,
) -> Dict[str, Any]:
    """Build/reuse Memory replay calibration evidence and next-run CLI args.

    RC#1c: Memory-only stop decisions must carry a concrete evidence file and
    the exact runner argv fragment needed by the next run. This function does
    not launch a new run; it only produces deterministic decision metadata.
    """
    if decision.bottleneck != "memory":
        return {
            "enabled": False,
            "reason": "not_memory_bottleneck",
        }

    history_path = Path(decision.history_path)
    if not history_path.is_file():
        history_path = latest_memory_optimizer_history(run_dir, decision.benchmark) or history_path
    if not history_path.is_file():
        return {
            "enabled": False,
            "reason": "memory_optimizer_history_not_found",
            "searched_run_dir": str(run_dir),
            "benchmark": decision.benchmark,
        }

    evidence_path = history_path.parent / "memory_replay_calibration_evidence.json"
    evidence: Dict[str, Any] = {}
    should_rebuild = True
    if evidence_path.is_file():
        try:
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            should_rebuild = evidence_path.stat().st_mtime < history_path.stat().st_mtime
        except Exception:
            evidence = {}
            should_rebuild = True

    if should_rebuild:
        builder = _load_memory_replay_calibration_builder()
        evidence = builder(
            str(history_path),
            baseline_memory=baseline_memory,
            delta=delta,
            source_run_id=decision.run_id,
        )
        evidence_path.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    calibration_count = safe_int(evidence.get("calibration_count")) or 0
    if calibration_count <= 0 or evidence.get("warning"):
        return {
            "enabled": False,
            "reason": "memory_replay_calibration_evidence_empty",
            "source_history_path": str(history_path),
            "evidence_path": str(evidence_path),
            "evidence_warning": evidence.get("warning"),
            "calibration_count": calibration_count,
        }

    next_run_args = [
        "--memory-replay-calibration-enabled",
        "--memory-replay-calibration-evidence-path",
        str(evidence_path),
    ]
    return {
        "enabled": True,
        "source_history_path": str(history_path),
        "evidence_path": str(evidence_path),
        "evidence_rebuilt": should_rebuild,
        "calibration_count": calibration_count,
        "predicted_down_count": safe_int(evidence.get("predicted_down_count")),
        "actual_down_count": safe_int(evidence.get("actual_down_count")),
        "predicted_down_actual_up_count": safe_int(
            evidence.get("predicted_down_actual_up_count")
        ),
        "baseline_memory": safe_float(evidence.get("baseline_memory")),
        "delta": safe_float(evidence.get("delta")),
        "next_run_args": next_run_args,
        "next_run_shell_fragment": shell_command(next_run_args),
    }


def load_json_file(path: Path) -> Optional[Dict[str, Any]]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def config_scope_violations(cfg: Dict[str, Any]) -> List[str]:
    violations: List[str] = []

    def check_keys(path: str, value: Any) -> None:
        if value is None:
            return
        if isinstance(value, dict):
            keys = value.keys()
        elif isinstance(value, list):
            keys = [str(item) for item in value]
        elif isinstance(value, str):
            keys = [part.strip() for part in value.split(",") if part.strip()]
        else:
            return
        invalid = sorted(str(key) for key in keys if str(key) not in COST_PARAM_SET)
        if invalid:
            violations.append(f"{path}: {invalid}")

    check_keys("initial_params", cfg.get("initial_params"))
    check_keys("p_real", cfg.get("p_real"))
    check_keys("p_candidate", cfg.get("p_candidate"))
    check_keys("param_ranges", cfg.get("param_ranges"))
    check_keys("param_step_scale", cfg.get("param_step_scale"))
    check_keys("sensitivity_table", cfg.get("sensitivity_table"))
    check_keys("fixed_params", cfg.get("fixed_params"))

    optimizer = cfg.get("optimizer")
    if isinstance(optimizer, dict):
        check_keys("optimizer.active_params", optimizer.get("active_params"))
        check_keys("optimizer.param_step_scale", optimizer.get("param_step_scale"))
        check_keys("optimizer.sensitivity_table", optimizer.get("sensitivity_table"))
        gradient = optimizer.get("gradient")
        if isinstance(gradient, dict):
            check_keys("optimizer.gradient.active_params", gradient.get("active_params"))
            check_keys("optimizer.gradient.boosted_params", gradient.get("boosted_params"))
            trial = gradient.get("aggressive_trial")
            if isinstance(trial, dict):
                check_keys("optimizer.gradient.aggressive_trial.params", trial.get("params"))
        bruteforce = optimizer.get("bruteforce")
        if isinstance(bruteforce, dict):
            check_keys("optimizer.bruteforce.active_params", bruteforce.get("active_params"))
    return violations


def config_vm_profile_reapply_requirement(cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    vm_resource = cfg.get("vm_resource")
    if not isinstance(vm_resource, dict):
        return None
    adaptive_source = vm_resource.get("adaptive_source")
    if not isinstance(adaptive_source, dict):
        return None
    if adaptive_source.get("requires_vm_profile_reapply") is not True:
        return None
    return {
        "vm_resource": {
            key: vm_resource.get(key)
            for key in ("vm_name", "cpu", "memory_gb", "read_mb", "write_mb", "disk_dev")
            if key in vm_resource
        },
        "adaptive_source": adaptive_source,
    }


def preflight_triggers(run_id: str, history_root: Path) -> List[Dict[str, Any]]:
    run_dir = run_dir_for(run_id, history_root)
    triggers: List[Dict[str, Any]] = []
    reference_md = run_dir / "bonect_reference_summary.md"
    reference_json = run_dir / "bonect_reference_summary.json"
    if not reference_md.exists() or not reference_json.exists():
        triggers.append(
            {
                "name": "missing_bonect_reference_summary",
                "rounds": [],
                "required_action": "run python3 shell/prepare_bonect_reference.py --run-id <run_id> before tuning",
                "missing_files": [
                    str(path)
                    for path in (reference_md, reference_json)
                    if not path.exists()
                ],
            }
        )

    bad_configs: List[Dict[str, Any]] = []
    vm_reapply_configs: List[Dict[str, Any]] = []
    for path in config_paths_for_run(run_id):
        cfg = load_json_file(path)
        if cfg is None:
            continue
        violations = config_scope_violations(cfg)
        if violations:
            bad_configs.append({"config": str(path), "violations": violations})
        vm_reapply = config_vm_profile_reapply_requirement(cfg)
        if vm_reapply is not None:
            vm_reapply_configs.append({"config": str(path), **vm_reapply})
    if bad_configs:
        triggers.append(
            {
                "name": "non_cost_param_tuning_scope",
                "rounds": [],
                "allowed_params": list(COST_PARAM_KEYS),
                "required_action": "remove non-cost PostgreSQL parameters from tunable/fixed optimizer fields",
                "bad_configs": bad_configs,
            }
        )
    if vm_reapply_configs:
        triggers.append(
            {
                "name": "vm_profile_reapply_required",
                "rounds": [],
                "required_action": (
                    "stop current optimization, apply the generated vm_resource profile, "
                    "then restart with a fresh baseline; do not continue with capability-only calibration"
                ),
                "configs": vm_reapply_configs,
            }
        )
    return triggers


def load_json_line(raw: bytes) -> Optional[Dict[str, Any]]:
    try:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            return None
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def read_incremental_events(path: Path, offset: int) -> Tuple[int, List[Dict[str, Any]]]:
    size = path.stat().st_size
    if size < offset:
        offset = 0
    events: List[Dict[str, Any]] = []
    with path.open("rb") as fp:
        fp.seek(offset)
        for raw in fp:
            obj = load_json_line(raw)
            if obj is not None:
                events.append(obj)
        return fp.tell(), events


def event_round_index(event: Dict[str, Any]) -> Optional[int]:
    value = event.get("round_index")
    if value is None:
        value = event.get("iteration")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def target_failed_reason(round_record: Dict[str, Any]) -> bool:
    reasons = [
        round_record.get("actual_accept_reason"),
        round_record.get("raw_actual_accept_reason"),
        round_record.get("target_direction_reason"),
    ]
    return any(
        failed_reason in str(reason or "")
        for reason in reasons
        for failed_reason in (
            "target_resource_direction_failed",
            "resource_iteration_direction_failed",
        )
    )


def round_accepted(round_record: Dict[str, Any]) -> bool:
    return bool(round_record.get("accepted_by_actual_score"))


def candidate_estimated_scores(round_record: Dict[str, Any]) -> List[float]:
    values: List[float] = []
    for item in round_record.get("candidate_evaluations") or []:
        if not isinstance(item, dict):
            continue
        value = safe_float(item.get("estimated_score"))
        if value is not None:
            values.append(value)
    return values


def candidate_estimated_resources(round_record: Dict[str, Any], resource: str) -> List[float]:
    values: List[float] = []
    for item in round_record.get("candidate_evaluations") or []:
        if not isinstance(item, dict):
            continue
        estimated = item.get("candidate_estimated_resource") or {}
        if not isinstance(estimated, dict):
            continue
        value = safe_float(estimated.get(resource))
        if value is not None:
            values.append(value)
    return values


def round_topk_flat(round_record: Dict[str, Any], resource: str) -> bool:
    scores = candidate_estimated_scores(round_record)
    resources = candidate_estimated_resources(round_record, resource)
    score_flat = len(scores) >= 2 and rel_span(scores) <= SCORE_FLAT_REL_TOL
    resource_flat = len(resources) >= 2 and rel_span(resources) <= RESOURCE_FLAT_REL_TOL
    return score_flat or resource_flat


def round_topk_flat_for_resources(round_record: Dict[str, Any], resources: Sequence[str]) -> bool:
    if not resources:
        return False
    return all(round_topk_flat(round_record, resource) for resource in resources)


def validation_util(round_record: Dict[str, Any], resource: str) -> Optional[float]:
    for key in ("candidate_final_validation", "final_validation"):
        validation = round_record.get(key) or {}
        if not isinstance(validation, dict):
            continue
        util = validation.get("utilization_pct") or {}
        if not isinstance(util, dict):
            continue
        value = normalize_pct(util.get(resource))
        if value is not None:
            return value
    return None


def round_numbers(rounds: Sequence[Dict[str, Any]]) -> List[int]:
    numbers: List[int] = []
    for item in rounds:
        value = event_round_index(item)
        if value is not None:
            numbers.append(value)
    return numbers


def no_real_progress(rounds: Sequence[Dict[str, Any]], resource: str) -> bool:
    utils = [value for value in (validation_util(item, resource) for item in rounds) if value is not None]
    if len(utils) < 2:
        return all(not round_accepted(item) for item in rounds)
    return max(utils) - min(utils) <= REAL_NO_PROGRESS_PCT or utils[-1] >= utils[0] - REAL_NO_PROGRESS_PCT


def no_real_progress_for_resources(rounds: Sequence[Dict[str, Any]], resources: Sequence[str]) -> bool:
    if not resources:
        return False
    return all(no_real_progress(rounds, resource) for resource in resources)


def summarize_round(round_record: Dict[str, Any], resource: str) -> Dict[str, Any]:
    scores = candidate_estimated_scores(round_record)
    resources = candidate_estimated_resources(round_record, resource)
    return {
        "round_index": event_round_index(round_record),
        "accepted": round_accepted(round_record),
        "reason": round_record.get("actual_accept_reason"),
        "raw_reason": round_record.get("raw_actual_accept_reason"),
        "target_util_pct": validation_util(round_record, resource),
        "candidate_top_k": round_record.get("candidate_top_k"),
        "validated_candidate_count": round_record.get("validated_candidate_count"),
        "estimated_score_span_rel": rel_span(scores) if scores else None,
        "estimated_resource_span_rel": rel_span(resources) if resources else None,
    }


def small_loop_goal_summary(round_record: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("small_loop_ranking_goal_summary", "short_term_goal_summary"):
        value = round_record.get(key)
        if isinstance(value, dict):
            return value
    if round_record.get("event") == "small_loop_ranking_goal_summary":
        return round_record
    return {}


def small_loop_gate_failed_trigger(round_record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    summary = small_loop_goal_summary(round_record)
    if not summary:
        return None

    event_name = str(round_record.get("event") or "")
    short_term_goal_met = summary.get("short_term_goal_met")
    if short_term_goal_met is True and event_name != "small_loop_first_stage_ranking_gate_stop":
        return None

    failures: List[str] = []
    validated_count = safe_int(summary.get("validated_candidate_count"))
    required_count = safe_int(summary.get("required_validated_candidate_count"))
    actual_replayed_count = safe_int(summary.get("actual_replayed_candidate_count"))
    effective_required_count = safe_int(
        summary.get("effective_required_validated_candidate_count_after_pruning")
    )
    validation_count_for_coverage = (
        actual_replayed_count
        if effective_required_count is not None and actual_replayed_count is not None
        else validated_count
    )
    required_count_for_coverage = (
        effective_required_count
        if effective_required_count is not None
        else required_count
    )
    effective_coverage_ready = (
        validation_count_for_coverage is not None
        and required_count_for_coverage is not None
        and validation_count_for_coverage >= required_count_for_coverage
    )
    if (
        (
            summary.get("topk_validation_ready") is False
            and not effective_coverage_ready
        )
        or (
            validation_count_for_coverage is not None
            and required_count_for_coverage is not None
            and validation_count_for_coverage < required_count_for_coverage
        )
    ):
        failures.append("top20_validation_not_ready")

    pairwise_rate = safe_float(summary.get("pairwise_inversion_rate"))
    pairwise_threshold = safe_float(summary.get("pairwise_inversion_threshold"))
    if (
        pairwise_rate is not None
        and pairwise_threshold is not None
        and pairwise_rate > pairwise_threshold
    ):
        failures.append("pairwise_inversion_rate_above_threshold")

    long_term_hits = safe_int(summary.get("long_term_goal_hit_count_in_validated_prefix"))
    min_long_term_hits = safe_int(summary.get("min_long_term_goal_hits"))
    if (
        summary.get("long_term_goal_hit_count_met") is False
        or (
            long_term_hits is not None
            and min_long_term_hits is not None
            and long_term_hits < min_long_term_hits
        )
    ):
        failures.append("long_term_goal_hit_count_below_minimum")

    if not failures and (
        summary.get("first_stage_ranking_validation_pass") is False
        or summary.get("ranking_close") is False
    ):
        # When adaptive replay controller is enabled and explainable lane is
        # empty, _apply_lane_aware_replay_summary sets first_stage_ranking_validation_pass
        # = False + next_action_hint = "stop_fix_ranking_explainable_lane_empty".
        # This is a valid STOP_FIX_RANKING trigger (override §490 #4, §533-535).
        if (
            summary.get("explainable_lane_empty_stop_fix") is True
            or summary.get("next_action_hint")
            == "stop_fix_ranking_explainable_lane_empty"
        ):
            failures.append("explainable_lane_empty_stop_fix_ranking")
        else:
            failures.append("first_stage_ranking_validation_failed")

    if not failures and event_name != "small_loop_first_stage_ranking_gate_stop":
        return None

    return {
        "name": "small_loop_ranking_gate_failed",
        "rounds": [event_round_index(round_record)],
        "failures": failures,
        "next_action_hint": summary.get("next_action_hint"),
        "evidence": [
            {
                "event": event_name or None,
                "validated_candidate_count": validated_count,
                "required_validated_candidate_count": required_count,
                "actual_replayed_candidate_count": actual_replayed_count,
                "effective_required_validated_candidate_count_after_pruning": (
                    effective_required_count
                ),
                "validation_count_for_coverage": validation_count_for_coverage,
                "required_count_for_coverage": required_count_for_coverage,
                "pairwise_inversion_rate": pairwise_rate,
                "pairwise_inversion_threshold": pairwise_threshold,
                "long_term_goal_hit_count_in_validated_prefix": long_term_hits,
                "min_long_term_goal_hits": min_long_term_hits,
                "topk_validation_ready": summary.get("topk_validation_ready"),
                "first_stage_ranking_validation_pass": summary.get(
                    "first_stage_ranking_validation_pass"
                ),
                "short_term_goal_met": summary.get("short_term_goal_met"),
                "lane_aware_replay_validation": summary.get(
                    "lane_aware_replay_validation"
                ),
                "explainable_lane_empty": summary.get("explainable_lane_empty"),
                "explainable_lane_hit_count": summary.get(
                    "explainable_lane_hit_count"
                ),
                "lane_counts": summary.get("lane_counts"),
                "explainable_lane_empty_stop_fix": summary.get(
                    "explainable_lane_empty_stop_fix"
                ),
            }
        ],
        "required_action": (
            "fix small-loop ranking before outer replay: ensure pruning-aware effective replay "
            "coverage is sufficient, calibrate resource scoring/error bounds, and do not mark "
            "the cost-parameter space unreachable from this failure"
        ),
    }


def resource_model_quality_gate_trigger(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Detect required-resource quality gates that passed with invalid Memory evidence."""
    if event.get("event") != "resource_model_quality_gate":
        return None
    summary = event.get("summary")
    if not isinstance(summary, dict):
        return None

    required_resources = {
        str(item).strip().lower()
        for item in (summary.get("required_resources") or [])
        if str(item).strip()
    }
    if "memory" not in required_resources:
        return None

    memory_failures: List[Dict[str, Any]] = []
    for check in summary.get("checks") or []:
        if not isinstance(check, dict):
            continue
        if str(check.get("resource") or "").strip().lower() != "memory":
            continue
        source = str(check.get("source") or "").strip()
        train_samples = safe_int(check.get("train_samples"))
        test_samples = safe_int(check.get("test_samples"))
        reasons: List[str] = []
        if source == "baseline_memory_single_sample_retrain":
            reasons.append("memory_source_is_baseline_single_sample_retrain")
        if train_samples is not None and train_samples < 5:
            reasons.append("memory_train_samples_below_5")
        if test_samples is not None and test_samples < 5:
            reasons.append("memory_test_samples_below_5")
        if reasons:
            memory_failures.append(
                {
                    "model": check.get("model"),
                    "source": source,
                    "train_samples": train_samples,
                    "test_samples": test_samples,
                    "reasons": reasons,
                }
            )

    if not memory_failures:
        return None

    return {
        "name": "resource_model_quality_gate_invalid_memory_single_sample",
        "rounds": [],
        "event_timestamp": event.get("timestamp"),
        "quality_gate_passed": summary.get("passed"),
        "required_resources": sorted(required_resources),
        "memory_failures": memory_failures,
        "required_action": (
            "stop before continuing optimization; implement real Memory train/test retraining "
            "or make the quality gate fail, because baseline single-sample Memory calibration "
            "cannot satisfy required_resources=['cpu','io','memory']"
        ),
    }


def resource_model_baseline_sanity_gate_trigger(
    event: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Stop a run whose current-workload baseline prediction is not credible."""
    if event.get("event") != "resource_model_baseline_sanity_gate":
        return None
    summary = event.get("summary")
    if not isinstance(summary, dict) or not summary.get("enabled"):
        return None
    if summary.get("passed"):
        return None
    failures = [
        item
        for item in (summary.get("failures") or [])
        if isinstance(item, dict)
    ]
    return {
        "name": "resource_model_baseline_sanity_gate_failed",
        "rounds": [],
        "event_timestamp": event.get("timestamp"),
        "required_resources": summary.get("required_resources") or [],
        "failures": failures,
        "required_action": (
            "stop before candidate search; fix or retrain the current-workload resource "
            "model, or use a validated non-degenerate plan-formula fallback. Do not use "
            "baseline-anchor calibration to hide a zero or implausible raw prediction."
        ),
    }


def baseline_strong_constraint_gate_trigger(
    event: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Stop before search when the measured baseline is not the requested scene."""
    if event.get("event") != "baseline_strong_constraint_gate":
        return None
    if event.get("passed") is not False:
        return None
    failures = [
        item
        for item in (event.get("checks") or [])
        if isinstance(item, dict) and item.get("passed") is False
    ]
    return {
        "name": "baseline_strong_constraint_gate_failed",
        "rounds": [],
        "event_timestamp": event.get("timestamp"),
        "target_resource": event.get("target_resource"),
        "gate_type": event.get("gate_type"),
        "failures": failures,
        "required_action": (
            "stop before candidate search; verify runtime metric sources and adjust only "
            "the VM resource profile or scenario configuration until exactly the requested "
            "baseline bottleneck passes. Do not bypass the gate."
        ),
    }


def evaluate_case(run_id: str, state: CaseState) -> Optional[TriggerDecision]:
    ordered_rounds = [state.rounds[key] for key in sorted(state.rounds)]
    if not ordered_rounds:
        return None

    triggers: List[Dict[str, Any]] = []
    resources = list(RESOURCE_TARGETS.keys()) if state.bottleneck == "none" else [state.bottleneck]
    resource = state.bottleneck if state.bottleneck in RESOURCE_TARGETS else resources[0]
    latest_round_value = event_round_index(ordered_rounds[-1])
    latest_round = int(latest_round_value if latest_round_value is not None else len(ordered_rounds))

    small_loop_trigger = small_loop_gate_failed_trigger(ordered_rounds[-1])
    if small_loop_trigger is not None:
        triggers.append(small_loop_trigger)

    if len(ordered_rounds) >= 3:
        last_three = ordered_rounds[-3:]
        if all(not round_accepted(item) and target_failed_reason(item) for item in last_three):
            triggers.append(
                {
                    "name": "reject_streak_target_direction",
                    "rounds": round_numbers(last_three),
                    "evidence": [summarize_round(item, resource) for item in last_three],
                    "required_action": (
                        "stop current blind validation, diagnose estimator/parameter sensitivity, "
                        "then try workload construction from plan-operator changes before fallback"
                    ),
                }
            )

    if len(ordered_rounds) >= 2:
        last_two = ordered_rounds[-2:]
        if (
            all(round_topk_flat_for_resources(item, resources) for item in last_two)
            and no_real_progress_for_resources(last_two, resources)
        ):
            triggers.append(
                {
                    "name": "topk_flat_no_real_progress",
                    "rounds": round_numbers(last_two),
                    "evidence": [summarize_round(item, resource) for item in last_two],
                    "required_action": (
                        "adjust candidate_top_k, bruteforce.max_iter, param_ranges discrete_count/step, "
                        "active_params, range thresholds, or fix the resource estimator"
                    ),
                }
            )

    if len(ordered_rounds) >= 5 and resource in RESOURCE_TARGETS:
        last_five = ordered_rounds[-5:]
        utils = [value for value in (validation_util(item, resource) for item in last_five) if value is not None]
        if len(utils) >= 5:
            _, target_max = RESOURCE_TARGETS[resource]
            if utils[-1] > target_max + OFF_TARGET_MARGIN_PCT and max(utils) - min(utils) < STUCK_SPAN_PCT:
                triggers.append(
                    {
                        "name": "resource_stuck_outside_target",
                        "rounds": round_numbers(last_five),
                        "target_max_pct": target_max,
                        "utilization_pct": utils,
                        "required_action": "mark current active params as insensitive/unreachable or switch strategy",
                    }
                )

    if not triggers:
        return None

    signature = f"{latest_round}:{','.join(item['name'] for item in triggers)}"
    if signature in state.emitted_signatures:
        return None
    state.emitted_signatures.add(signature)

    if any(item["name"] == "resource_stuck_outside_target" for item in triggers):
        action = "stop_and_mark_active_params_unreachable"
    elif any(item["name"] == "topk_flat_no_real_progress" for item in triggers):
        action = "stop_and_adjust_topk_or_estimator"
    elif any(item["name"] == "small_loop_ranking_gate_failed" for item in triggers):
        action = "stop_and_fix_small_loop_ranking"
    else:
        action = "stop_and_diagnose"

    reason = "; ".join(item["name"] for item in triggers)
    return TriggerDecision(
        run_id=run_id,
        bottleneck=state.bottleneck,
        benchmark=state.benchmark,
        history_path=str(state.history_path),
        latest_round=latest_round,
        triggers=triggers,
        action=action,
        reason=reason,
        created_at=datetime.now().isoformat(timespec="seconds"),
    )


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def decision_to_dict(decision: TriggerDecision) -> Dict[str, Any]:
    data = {
        "run_id": decision.run_id,
        "bottleneck": decision.bottleneck,
        "benchmark": decision.benchmark,
        "history_path": decision.history_path,
        "latest_round": decision.latest_round,
        "triggers": decision.triggers,
        "action": decision.action,
        "reason": decision.reason,
        "created_at": decision.created_at,
        "stopped_pids": decision.stopped_pids,
    }
    if decision.memory_replay_calibration_injection:
        data["memory_replay_calibration_injection"] = (
            decision.memory_replay_calibration_injection
        )
    return data


def write_decision(run_dir: Path, decision: TriggerDecision) -> Tuple[Path, Path]:
    base = run_dir / f"control_decision_{now_stamp()}"
    json_path = base.with_suffix(".json")
    md_path = base.with_suffix(".md")
    if not decision.memory_replay_calibration_injection:
        try:
            decision.memory_replay_calibration_injection = (
                build_memory_replay_calibration_injection(run_dir, decision)
            )
        except Exception as exc:
            decision.memory_replay_calibration_injection = {
                "enabled": False,
                "reason": "memory_replay_calibration_injection_failed",
                "error": repr(exc),
            }
    data = decision_to_dict(decision)
    write_json(json_path, data)

    lines = [
        "# Bonect Supervisor Control Decision",
        "",
        f"- run_id: `{decision.run_id}`",
        f"- bottleneck: `{decision.bottleneck}`",
        f"- benchmark: `{decision.benchmark}`",
        f"- latest_round: `{decision.latest_round}`",
        f"- action: `{decision.action}`",
        f"- reason: `{decision.reason}`",
        f"- history_path: `{decision.history_path}`",
        f"- stopped_pids: `{decision.stopped_pids}`",
        "",
        "## Triggers",
        "",
    ]
    for item in decision.triggers:
        lines.append(f"### {item.get('name')}")
        lines.append("")
        lines.append(f"- rounds: `{item.get('rounds')}`")
        lines.append(f"- required_action: `{item.get('required_action')}`")
        if "target_max_pct" in item:
            lines.append(f"- target_max_pct: `{item.get('target_max_pct')}`")
        if "utilization_pct" in item:
            lines.append(f"- utilization_pct: `{item.get('utilization_pct')}`")
        evidence = item.get("evidence")
        if evidence:
            lines.append("- evidence:")
            for row in evidence:
                lines.append(f"  - `{json.dumps(row, ensure_ascii=False, sort_keys=True)}`")
        lines.append("")
    injection = decision.memory_replay_calibration_injection
    if injection:
        lines.extend(
            [
                "## Memory Replay Calibration Injection",
                "",
                f"- enabled: `{injection.get('enabled')}`",
                f"- reason: `{injection.get('reason', '')}`",
                f"- source_history_path: `{injection.get('source_history_path', '')}`",
                f"- evidence_path: `{injection.get('evidence_path', '')}`",
                f"- calibration_count: `{injection.get('calibration_count', '')}`",
                f"- predicted_down_count: `{injection.get('predicted_down_count', '')}`",
                f"- actual_down_count: `{injection.get('actual_down_count', '')}`",
                f"- predicted_down_actual_up_count: `{injection.get('predicted_down_actual_up_count', '')}`",
                "",
            ]
        )
        next_run_args = injection.get("next_run_args")
        if next_run_args:
            lines.extend(
                [
                    "### Next-run runner args",
                    "",
                    f"`{json.dumps(next_run_args, ensure_ascii=False)}`",
                    "",
                    "### Next-run shell fragment",
                    "",
                    f"`{injection.get('next_run_shell_fragment', '')}`",
                    "",
                ]
            )
    lines.extend(
        [
            "## Main-Agent Required Follow-up",
            "",
            "1. Do not continue the same blind validation loop.",
            "2. Inspect resource model retraining artifacts and feedback estimation error first.",
            (
                "3. If the estimator is healthy, adjust candidate_top_k, bruteforce.max_iter, "
                "param_ranges discrete_count/step, max_param_delta_rel_pct, range_shrink_factor, "
                "range_min_width, final feedback max_rounds, or active params."
            ),
            (
                "3a. For `small_loop_ranking_gate_failed`, fix top20 prefix coverage, resource "
                "direction scoring, model calibration, and anchor/path seeds before any outer "
                "long replay; do not mark the search space unreachable from that trigger alone."
            ),
            (
                "4. If no explainable point is found, inspect plan-operator changes and attempt workload "
                "construction by changing query mix, concurrency, TP/AP ratio, or AP query subset before "
                "choosing any fallback point."
            ),
            (
                "5. Use fallback only after documenting workload-construction failure: best DB-QPS-increasing "
                "point, no severe latency collapse, bottleneck utilization not decreasing or only mildly "
                "increasing, and all non-bottleneck resources increasing."
            ),
            "6. Resume or restart only after recording the concrete adjustment and avoiding duplicate runs.",
            "",
        ]
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path, json_path


def parse_process_table() -> Dict[int, Tuple[int, str]]:
    proc = subprocess.run(
        ["ps", "-eo", "pid=,ppid=,command="],
        cwd=str(PROJECT_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    table: Dict[int, Tuple[int, str]] = {}
    for line in proc.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        table[pid] = (ppid, parts[2])
    return table


def process_environ(pid: int) -> str:
    path = Path("/proc") / str(pid) / "environ"
    try:
        return path.read_bytes().decode("utf-8", errors="replace").replace("\x00", " ")
    except Exception:
        return ""


def process_cmdline(pid: int) -> str:
    path = Path("/proc") / str(pid) / "cmdline"
    try:
        return path.read_bytes().decode("utf-8", errors="replace").replace("\x00", " ")
    except Exception:
        return ""


def process_matches_run(pid: int, command: str, run_id: str) -> bool:
    if "bonect_supervisor.py" in command or "bonect_supervisor_hook.py" in command:
        return False
    if run_id in command:
        return True
    env = process_environ(pid)
    return run_id in env


def existing_supervisor_pids(run_id: str) -> List[int]:
    table = parse_process_table()
    pids: List[int] = []
    for pid, (_, command) in table.items():
        if pid == os.getpid():
            continue
        if "bonect_supervisor.py" not in command:
            continue
        if run_id in command or run_id in process_environ(pid):
            pids.append(pid)
    return sorted(pids)


def descendants(root_pids: Iterable[int], table: Dict[int, Tuple[int, str]]) -> Set[int]:
    children: Dict[int, List[int]] = {}
    for pid, (ppid, _) in table.items():
        children.setdefault(ppid, []).append(pid)

    found: Set[int] = set()
    stack = list(root_pids)
    while stack:
        pid = stack.pop()
        if pid in found:
            continue
        found.add(pid)
        stack.extend(children.get(pid, []))
    return found


def stop_run_processes(run_id: str, *, dry_run: bool) -> List[int]:
    table = parse_process_table()
    root_pids = [
        pid
        for pid, (_, command) in table.items()
        if pid != os.getpid() and process_matches_run(pid, command, run_id)
    ]
    target_pids = sorted(descendants(root_pids, table), reverse=True)
    target_pids = [pid for pid in target_pids if pid != os.getpid()]
    if dry_run:
        return target_pids

    stopped: List[int] = []
    for pid in target_pids:
        try:
            os.kill(pid, signal.SIGTERM)
            stopped.append(pid)
        except ProcessLookupError:
            continue
        except PermissionError:
            continue
    return stopped


def load_state_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state_file(
    path: Path,
    *,
    run_id: str,
    poll_sec: float,
    history_root: Path,
    stop_on_trigger: bool,
    enabled: bool,
    status: str,
    last_decision: Optional[Path] = None,
) -> None:
    previous = load_state_file(path)
    data = {
        **previous,
        "enabled": enabled,
        "run_id": run_id,
        "poll_sec": poll_sec,
        "history_root": str(history_root),
        "stop_on_trigger": stop_on_trigger,
        "status": status,
        "last_heartbeat": datetime.now().isoformat(timespec="seconds"),
        "supervisor_pid": os.getpid(),
    }
    if last_decision is not None:
        data["last_decision"] = str(last_decision)
    write_json(path, data)


def shell_command(parts: Sequence[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def tmux_session_name(run_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", run_id)
    return f"{cleaned}_supervisor"


def ensure_tmux_supervisor(args: argparse.Namespace) -> int:
    state = load_state_file(Path(args.state_file))
    run_id = args.run_id or str(state.get("run_id") or "")
    if not run_id:
        print("[bonect-supervisor] no run_id in args or state; skip ensure", flush=True)
        return 0
    if state and state.get("enabled") is False:
        print(f"[bonect-supervisor] state disabled for run_id={run_id}; skip ensure", flush=True)
        return 0

    state_pid = safe_int(state.get("supervisor_pid")) if state else None
    if state_pid and state_pid != os.getpid():
        cmdline = process_cmdline(state_pid)
        if "bonect_supervisor.py" in cmdline and run_id in cmdline:
            print(
                "[bonect-supervisor] existing supervisor pid from state is running: "
                f"{state_pid}",
                flush=True,
            )
            return 0

    existing_pids = existing_supervisor_pids(run_id)
    if existing_pids:
        print(
            "[bonect-supervisor] existing supervisor process is running for run_id="
            f"{run_id}: {existing_pids}",
            flush=True,
        )
        return 0

    session = tmux_session_name(run_id)
    check = subprocess.run(
        ["tmux", "has-session", "-t", session],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if check.returncode == 0:
        print(f"[bonect-supervisor] tmux session already running: {session}", flush=True)
        return 0

    poll_sec = float(args.poll_sec or state.get("poll_sec") or 60.0)
    history_root = str(args.history_root or state.get("history_root") or DEFAULT_HISTORY_ROOT)
    stop_on_trigger = bool(args.stop_on_trigger or state.get("stop_on_trigger"))
    cmd = [
        sys.executable or "python3",
        str(PROJECT_ROOT / "shell" / "bonect_supervisor.py"),
        "--run-id",
        run_id,
        "--poll-sec",
        str(poll_sec),
        "--history-root",
        history_root,
        "--state-file",
        str(args.state_file),
    ]
    if stop_on_trigger:
        cmd.append("--stop-on-trigger")
    if args.dry_run:
        cmd.append("--dry-run")
    tmux_cmd = [
        "tmux",
        "new-session",
        "-d",
        "-s",
        session,
        f"cd {shlex.quote(str(PROJECT_ROOT))} && {shell_command(cmd)}",
    ]
    proc = subprocess.run(tmux_cmd, cwd=str(PROJECT_ROOT), text=True, check=False)
    if proc.returncode == 0:
        print(f"[bonect-supervisor] started tmux session: {session}", flush=True)
    return proc.returncode


class Supervisor:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.run_id = args.run_id
        self.history_root = Path(args.history_root)
        self.state_file = Path(args.state_file)
        self.offsets: Dict[Path, int] = {}
        self.cases: Dict[Path, CaseState] = {}
        self.preflight_emitted: Set[str] = set()

    def scan_once(self) -> List[TriggerDecision]:
        decisions: List[TriggerDecision] = []
        run_dir = run_dir_for(self.run_id, self.history_root)
        triggers = preflight_triggers(self.run_id, self.history_root)
        if triggers:
            signature = ",".join(item["name"] for item in triggers)
            if signature not in self.preflight_emitted:
                self.preflight_emitted.add(signature)
                if any(item["name"] == "non_cost_param_tuning_scope" for item in triggers):
                    action = "stop_and_fix_non_cost_param_scope"
                elif any(item["name"] == "vm_profile_reapply_required" for item in triggers):
                    action = "stop_apply_vm_profile_and_rebaseline"
                else:
                    action = "stop_and_build_bonect_reference"
                decision = TriggerDecision(
                    run_id=self.run_id,
                    bottleneck="preflight",
                    benchmark="all",
                    history_path=str(run_dir),
                    latest_round=0,
                    triggers=triggers,
                    action=action,
                    reason=signature,
                    created_at=datetime.now().isoformat(timespec="seconds"),
                )
                if self.args.stop_on_trigger:
                    decision.stopped_pids = stop_run_processes(self.run_id, dry_run=self.args.dry_run)
                if not self.args.no_write_decision:
                    md_path, _ = write_decision(run_dir, decision)
                    write_state_file(
                        self.state_file,
                        run_id=self.run_id,
                        poll_sec=self.args.poll_sec,
                        history_root=self.history_root,
                        stop_on_trigger=self.args.stop_on_trigger,
                        enabled=not self.args.stop_on_trigger,
                        status="triggered",
                        last_decision=md_path,
                    )
                decisions.append(decision)
                return decisions

        files = find_history_files(self.run_id, self.history_root)
        for path in files:
            if path not in self.cases:
                bottleneck, benchmark = labels_from_history_path(run_dir, path)
                self.cases[path] = CaseState(
                    bottleneck=bottleneck,
                    benchmark=benchmark,
                    history_path=path,
                )
            state = self.cases[path]
            next_offset, events = read_incremental_events(path, self.offsets.get(path, 0))
            self.offsets[path] = next_offset
            for event in events:
                event_name = event.get("event")
                if "timestamp" in event:
                    state.last_event_ts = str(event.get("timestamp"))
                if event_name in {
                    "final_feedback_round_complete",
                    "final_feedback_initial_validation_rejected_report",
                    "small_loop_first_stage_ranking_gate_stop",
                    "small_loop_ranking_goal_summary",
                }:
                    index = event_round_index(event)
                    if index is not None:
                        state.rounds[index] = event
                elif event_name in {"optimization_summary", "final_summary"}:
                    state.final_summaries.append(event)
                elif event_name in {
                    "baseline_strong_constraint_gate",
                    "resource_model_quality_gate",
                    "resource_model_baseline_sanity_gate",
                }:
                    if event_name == "baseline_strong_constraint_gate":
                        trigger = baseline_strong_constraint_gate_trigger(event)
                    elif event_name == "resource_model_quality_gate":
                        trigger = resource_model_quality_gate_trigger(event)
                    else:
                        trigger = resource_model_baseline_sanity_gate_trigger(event)
                    if trigger is not None:
                        signature = f"resource_gate:{trigger['name']}:{event.get('timestamp')}"
                        if signature in state.emitted_signatures:
                            continue
                        state.emitted_signatures.add(signature)
                        decision = TriggerDecision(
                            run_id=self.run_id,
                            bottleneck=state.bottleneck,
                            benchmark=state.benchmark,
                            history_path=str(path),
                            latest_round=0,
                            triggers=[trigger],
                            action=(
                                "stop_and_fix_baseline_strong_constraint"
                                if event_name == "baseline_strong_constraint_gate"
                                else (
                                    "stop_and_fix_resource_model_baseline_sanity_gate"
                                    if event_name == "resource_model_baseline_sanity_gate"
                                    else "stop_and_fix_resource_model_quality_gate"
                                )
                            ),
                            reason=trigger["name"],
                            created_at=datetime.now().isoformat(timespec="seconds"),
                        )
                        if self.args.stop_on_trigger:
                            decision.stopped_pids = stop_run_processes(
                                self.run_id,
                                dry_run=self.args.dry_run,
                            )
                        if not self.args.no_write_decision:
                            md_path, _ = write_decision(run_dir, decision)
                            write_state_file(
                                self.state_file,
                                run_id=self.run_id,
                                poll_sec=self.args.poll_sec,
                                history_root=self.history_root,
                                stop_on_trigger=self.args.stop_on_trigger,
                                enabled=not self.args.stop_on_trigger,
                                status="triggered",
                                last_decision=md_path,
                            )
                        decisions.append(decision)
                        return decisions

            decision = evaluate_case(self.run_id, state)
            if decision is not None:
                if self.args.stop_on_trigger:
                    decision.stopped_pids = stop_run_processes(self.run_id, dry_run=self.args.dry_run)
                if not self.args.no_write_decision:
                    md_path, _ = write_decision(run_dir, decision)
                    write_state_file(
                        self.state_file,
                        run_id=self.run_id,
                        poll_sec=self.args.poll_sec,
                        history_root=self.history_root,
                        stop_on_trigger=self.args.stop_on_trigger,
                        enabled=not self.args.stop_on_trigger,
                        status="triggered",
                        last_decision=md_path,
                    )
                decisions.append(decision)
        return decisions

    def run(self) -> int:
        write_state_file(
            self.state_file,
            run_id=self.run_id,
            poll_sec=self.args.poll_sec,
            history_root=self.history_root,
            stop_on_trigger=self.args.stop_on_trigger,
            enabled=True,
            status="running",
        )
        while True:
            decisions = self.scan_once()
            if decisions:
                for decision in decisions:
                    print(
                        "[bonect-supervisor] trigger "
                        f"{decision.bottleneck}/{decision.benchmark} "
                        f"round={decision.latest_round} action={decision.action} "
                        f"stopped_pids={decision.stopped_pids}",
                        flush=True,
                    )
                if self.args.once or self.args.stop_on_trigger:
                    break
            elif self.args.once:
                break
            write_state_file(
                self.state_file,
                run_id=self.run_id,
                poll_sec=self.args.poll_sec,
                history_root=self.history_root,
                stop_on_trigger=self.args.stop_on_trigger,
                enabled=True,
                status="running",
            )
            time.sleep(max(1.0, float(self.args.poll_sec)))
        return 2 if decisions else 0


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor Bonect bruteforce tuning and emit control decisions.")
    parser.add_argument("--run-id", default="", help="Matrix run_id under history/pg_benchmark_bottleneck_matrix.")
    parser.add_argument("--history-root", default=str(DEFAULT_HISTORY_ROOT))
    parser.add_argument("--poll-sec", type=float, default=60.0)
    parser.add_argument("--once", action="store_true", help="Scan once and exit.")
    parser.add_argument("--stop-on-trigger", action="store_true", help="Terminate current run processes when a trigger fires.")
    parser.add_argument("--dry-run", action="store_true", help="Do not terminate processes; only report target PIDs.")
    parser.add_argument("--no-write-decision", action="store_true", help="Do not write control_decision files.")
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE))
    parser.add_argument("--ensure-tmux", action="store_true", help="Ensure a tmux supervisor is running for the current state.")
    args = parser.parse_args(argv)
    if args.ensure_tmux:
        return args
    if not args.run_id:
        parser.error("--run-id is required unless --ensure-tmux is used")
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.ensure_tmux:
        return ensure_tmux_supervisor(args)
    supervisor = Supervisor(args)
    return supervisor.run()


if __name__ == "__main__":
    raise SystemExit(main())
