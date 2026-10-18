#!/usr/bin/env python3
"""Audit exact physical/logical coverage for the PG17 full-real grid suite."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple


ROOT = Path(__file__).resolve().parent
DEFAULT_VERIFIER = ROOT / "verify_structured_fullgrid_log.py"
SEMANTIC_ERROR_EXACT = {
    "result.bad_config_not_false",
    "result.db_qps_did_not_increase",
    "result.none_not_all_three_resources_up",
    "result.none_no_resource_increased",
    "result.final_params_not_exactly_seven_cost_params",
}
SEMANTIC_ERROR_PARTS = (
    "_bottleneck_did_not_drop",
    "_drop_too_extreme_",
)
COST_PARAMS = {
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
}


@dataclass(frozen=True)
class PhysicalEntry:
    """One physical replay, possibly shared by one logical A-table row."""

    exp_id: str
    config: Path
    log: Path
    target: str
    logical_exp_ids: Tuple[str, ...]


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def semantic_only(errors: List[str]) -> bool:
    if not errors:
        return False
    return all(
        error in SEMANTIC_ERROR_EXACT
        or any(part in error for part in SEMANTIC_ERROR_PARTS)
        for error in errors
    )


def logical_alias_path(artifact: Path, logical_exp_id: str) -> Path:
    return artifact.with_name(f"{artifact.stem}.{logical_exp_id}{artifact.suffix}")


def parse_entries(config: Dict[str, Any]) -> Tuple[Dict[str, PhysicalEntry], List[str]]:
    entries: Dict[str, PhysicalEntry] = {}
    errors: List[str] = []
    for queue in config.get("queues") or []:
        for raw in queue.get("entries") or []:
            aliases: List[str] = []
            single_alias = str(raw.get("logical_exp_id") or "").strip()
            if single_alias:
                aliases.append(single_alias)
            for value in raw.get("logical_exp_ids") or []:
                alias = str(value).strip()
                if alias and alias not in aliases:
                    aliases.append(alias)
            entry = PhysicalEntry(
                exp_id=str(raw["exp_id"]),
                config=Path(str(raw["config"])).resolve(),
                log=Path(str(raw["log"])).resolve(),
                target=str(raw["target"]),
                logical_exp_ids=tuple(aliases),
            )
            previous = entries.get(entry.exp_id)
            if previous is not None and previous != entry:
                errors.append(f"conflicting_duplicate_entry:{entry.exp_id}")
            else:
                entries[entry.exp_id] = entry
    return entries, errors


def artifact_for(entry: PhysicalEntry) -> Path:
    config = read_json(entry.config)
    tuning = config.get("binary_plan_tuning") or {}
    output_dir = str(tuning.get("output_dir") or "").strip()
    if not output_dir:
        raise ValueError(f"config lacks binary_plan_tuning.output_dir: {entry.config}")
    return Path(output_dir).resolve() / "structured_experiment.json"


def config_preflight(
    entry: PhysicalEntry,
    *,
    expected_candidates: int,
    min_rounds: int,
) -> Dict[str, Any]:
    """Fail closed on configuration drift before scarce workers are assigned."""
    try:
        config = read_json(entry.config)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "errors": [f"config_read:{type(exc).__name__}:{exc}"],
        }
    tuning = config.get("binary_plan_tuning")
    tuning = tuning if isinstance(tuning, dict) else {}
    search = tuning.get("binary_search")
    search = search if isinstance(search, dict) else {}
    validation = tuning.get("validation")
    validation = validation if isinstance(validation, dict) else {}
    meta = tuning.get("experiment_meta")
    meta = meta if isinstance(meta, dict) else {}
    machine = meta.get("machine_spec")
    machine = machine if isinstance(machine, dict) else {}
    docker_workers = validation.get("docker_workers")
    docker_workers = docker_workers if isinstance(docker_workers, dict) else {}
    worker_rows = [row for row in (docker_workers.get("workers") or []) if isinstance(row, dict)]
    selected_workers = [
        row for row in worker_rows if entry.target in (row.get("targets") or [])
    ]
    backend = validation.get("hybench_xp")
    backend = backend if isinstance(backend, dict) else {}
    baseline = validation.get("parallel_baseline")
    baseline = baseline if isinstance(baseline, dict) else {}
    pruning = search.get("replay_candidate_pruning")
    pruning = pruning if isinstance(pruning, dict) else {}
    output_raw = str(tuning.get("output_dir") or "").strip()
    output_dir = Path(output_raw).resolve() if output_raw else None

    checks = {
        "meta_exp_id": meta.get("exp_id") == entry.exp_id,
        "meta_benchmark": bool(str(meta.get("benchmark") or "").strip()),
        "meta_db_pg17": meta.get("db") == "pg17",
        "meta_method_brute_force": meta.get("method") == "brute_force_grid_search",
        "meta_bottleneck": meta.get("bottleneck") == entry.target,
        "machine_spec": {"cpu_cores", "mem_gb", "disk_bw_mb"} <= set(machine),
        "initial_params_exactly_seven": set(config.get("initial_params") or {}) == COST_PARAMS,
        "param_ranges_exactly_seven": set(config.get("param_ranges") or {}) == COST_PARAMS,
        "minimum_rounds": int(search.get("max_rounds") or 0) >= min_rounds,
        "cartesian_candidates": int(search.get("max_candidates_per_round") or 0) == expected_candidates,
        "full_bruteforce": search.get("full_bruteforce") is True,
        "candidate_reduction_disabled": search.get("disable_candidate_reduction") is True,
        "replay_pruning_disabled": pruning.get("enabled") is False,
        "fullgrid_plan_probe_disabled": search.get("run_plan_probe_for_full_bruteforce") is False,
        "retry_failed_on_resume": search.get("retry_failed_validations_on_resume") is True,
        "stop_only_on_epsilon": search.get("stop_only_on_epsilon") is True,
        "docker_runtime_monitor": isinstance(validation.get("docker_runtime"), dict),
        "docker_workers_enabled": docker_workers.get("enabled") is True,
        "target_workers_present": bool(selected_workers),
        "worker_container_names": bool(selected_workers) and all(row.get("container") for row in selected_workers),
        "worker_cpusets": bool(selected_workers) and all(row.get("cpuset_cpus") for row in selected_workers),
        "worker_data_dirs": bool(selected_workers) and all(
            Path(str(row.get("data_dir") or "")).is_dir() for row in selected_workers
        ),
        "same_worker_baseline": baseline.get("required") is True and baseline.get("compare_mode") == "same_worker",
        "real_backend_enabled": backend.get("enabled") is True,
        "real_backend_launcher": Path(str(backend.get("launcher") or "")).is_file(),
        "output_dir": output_dir is not None,
        "output_log_parent_match": output_dir == entry.log.parent,
    }
    errors = sorted(name for name, ok in checks.items() if not ok)
    backend_name = str(backend.get("backend_name") or "").strip()
    if not backend_name and str(meta.get("benchmark") or "") == "hybench_xp":
        backend_name = "hybench_xp"
    return {
        "ok": not errors,
        "errors": errors,
        "benchmark": meta.get("benchmark"),
        "bottleneck": meta.get("bottleneck"),
        "repeat_id": meta.get("repeat_id"),
        "max_rounds": search.get("max_rounds"),
        "candidates_per_round": search.get("max_candidates_per_round"),
        "backend": backend_name or None,
        "backend_schema": "named_backend" if backend.get("backend_name") else "legacy_hybench_xp",
        "worker_containers": [str(row.get("container")) for row in selected_workers],
        "output_dir": str(output_dir) if output_dir is not None else None,
        "plan_estimator_config_present_but_not_runtime_proof": isinstance(
            (config.get("metrics") or {}).get("plan_estimator"),
            dict,
        ),
    }


def strict_real_candidate(row: Dict[str, Any]) -> bool:
    backend = str(row.get("validation_backend") or "").lower()
    return (
        row.get("status") == "ok"
        and row.get("returncode") == 0
        and bool(backend)
        and backend not in {"estimator", "plan_estimator", "resource_estimator", "estimated"}
        and (row.get("resource_stats") or {}).get("resource_monitor_ok") is True
        and isinstance(row.get("db_qps"), (int, float))
        and float(row.get("db_qps")) > 0.0
    )


def semantic_candidate_preview(row: Dict[str, Any]) -> Dict[str, Any]:
    score = row.get("real_score_b") if isinstance(row.get("real_score_b"), dict) else {}
    decision = row.get("decision") if isinstance(row.get("decision"), dict) else {}
    worker = row.get("worker") if isinstance(row.get("worker"), dict) else {}
    return {
        "round": row.get("round"),
        "candidate_index": row.get("candidate_index"),
        "label": row.get("label"),
        "worker": worker.get("name"),
        "db_qps": row.get("db_qps"),
        "db_qps_gain_pct": score.get("db_qps_gain_pct", decision.get("db_qps_delta_pct")),
        "target_resource": score.get("target_resource", decision.get("target_resource")),
        "target_drop_pct": score.get("target_drop_pct", decision.get("target_drop_pct")),
        "resource_up_resources": (
            score.get("resource_up_resources") or decision.get("other_up_resources") or []
        ),
        "resources": row.get("resources") or {},
        "params": row.get("params") or {},
        "timestamp": row.get("timestamp"),
    }


def scan_log_progress(path: Path, *, expected_candidates: int, min_rounds: int) -> Dict[str, Any]:
    """Summarize latest authoritative real validations without hiding raw retries."""
    if not path.is_file():
        return {
            "log_exists": False,
            "strict_complete": False,
            "completion_reason": "log_missing",
            "raw_candidate_attempts": 0,
            "authoritative_candidates": 0,
            "superseded_attempts": 0,
            "rounds": [],
        }
    latest: Dict[Tuple[int, int], Dict[str, Any]] = {}
    raw_by_round: Dict[int, int] = defaultdict(int)
    summary_rounds: Set[int] = set()
    search_complete_events = 0
    malformed_lines = 0
    raw_attempts = 0
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                malformed_lines += 1
                continue
            if not isinstance(row, dict):
                continue
            event = row.get("event")
            if event == "candidate_validation":
                raw_attempts += 1
                try:
                    round_id = int(row.get("round"))
                    candidate_index = int(row.get("candidate_index"))
                except (TypeError, ValueError):
                    continue
                raw_by_round[round_id] += 1
                latest[(round_id, candidate_index)] = row
            elif event == "round_summary":
                try:
                    summary_rounds.add(int(row.get("round")))
                except (TypeError, ValueError):
                    continue
            elif event == "search_complete":
                search_complete_events += 1

    by_round: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for (round_id, _), row in latest.items():
        by_round[round_id].append(row)
    round_rows: List[Dict[str, Any]] = []
    for round_id in sorted(by_round):
        rows = by_round[round_id]
        non_ok = sum(row.get("status") != "ok" for row in rows)
        nonzero_returncode = sum(row.get("returncode") != 0 for row in rows)
        missing_backend = sum(not row.get("validation_backend") for row in rows)
        estimated_backend = sum(
            str(row.get("validation_backend") or "").lower()
            in {"estimator", "plan_estimator", "resource_estimator", "estimated"}
            for row in rows
        )
        monitor_failed = sum(
            (row.get("resource_stats") or {}).get("resource_monitor_ok") is not True
            for row in rows
        )
        invalid_qps = sum(
            not isinstance(row.get("db_qps"), (int, float)) or float(row.get("db_qps") or 0.0) <= 0.0
            for row in rows
        )
        strict_ok = sum(strict_real_candidate(row) for row in rows)
        round_rows.append({
            "round": round_id,
            "raw_attempts": raw_by_round[round_id],
            "authoritative_candidates": len(rows),
            "strict_ok": strict_ok,
            "accepted": sum(row.get("accepted") is True for row in rows),
            "non_ok": non_ok,
            "nonzero_returncode": nonzero_returncode,
            "missing_backend": missing_backend,
            "estimated_backend": estimated_backend,
            "resource_monitor_failed": monitor_failed,
            "invalid_db_qps": invalid_qps,
            "has_round_summary": round_id in summary_rounds,
        })

    if malformed_lines:
        completion_reason = f"malformed_json_lines={malformed_lines}"
    elif not search_complete_events:
        completion_reason = "search_complete_missing"
    elif len(round_rows) < min_rounds:
        completion_reason = f"round_count={len(round_rows)}<{min_rounds}"
    elif set(by_round) != summary_rounds:
        completion_reason = "candidate_rounds_do_not_match_round_summaries"
    else:
        incomplete_round = next(
            (
                row for row in round_rows
                if row["authoritative_candidates"] != expected_candidates
                or row["strict_ok"] != expected_candidates
            ),
            None,
        )
        if incomplete_round is None:
            completion_reason = f"complete_rounds={len(round_rows)}"
        else:
            completion_reason = (
                f"round={incomplete_round['round']} "
                f"authoritative={incomplete_round['authoritative_candidates']} "
                f"strict_ok={incomplete_round['strict_ok']} expected={expected_candidates}"
            )
    strict_complete = completion_reason.startswith("complete_rounds=")
    semantic_rows: List[Dict[str, Any]] = []
    for row in latest.values():
        score = row.get("real_score_b") if isinstance(row.get("real_score_b"), dict) else {}
        decision = row.get("decision") if isinstance(row.get("decision"), dict) else {}
        if not strict_real_candidate(row) or row.get("accepted") is not True:
            continue
        qps_gain_pct = score.get("db_qps_gain_pct", decision.get("db_qps_delta_pct"))
        target_resource = str(score.get("target_resource", decision.get("target_resource")) or "")
        target_drop_pct = score.get("target_drop_pct", decision.get("target_drop_pct"))
        resource_up = {
            str(value)
            for value in (
                score.get("resource_up_resources") or decision.get("other_up_resources") or []
            )
        }
        if not isinstance(qps_gain_pct, (int, float)) or float(qps_gain_pct) <= 0.0:
            continue
        if target_resource == "none" and not {"cpu", "memory", "io"}.issubset(resource_up):
            continue
        if target_resource in {"cpu", "memory", "io"} and not (
            isinstance(target_drop_pct, (int, float))
            and 0.0 < float(target_drop_pct) <= 30.0
        ):
            continue
        semantic_rows.append(row)
    mild_rows = []
    for row in semantic_rows:
        score = row.get("real_score_b") or {}
        decision = row.get("decision") if isinstance(row.get("decision"), dict) else {}
        target_resource = str(score.get("target_resource", decision.get("target_resource")) or "")
        target_drop_pct = score.get("target_drop_pct", decision.get("target_drop_pct"))
        if target_resource not in {"cpu", "memory", "io"}:
            mild_rows.append(row)
        elif (
            isinstance(target_drop_pct, (int, float))
            and 0.0 < float(target_drop_pct) <= 10.0
        ):
            mild_rows.append(row)

    def qps_gain(row: Dict[str, Any]) -> float:
        score = row.get("real_score_b") or {}
        decision = row.get("decision") if isinstance(row.get("decision"), dict) else {}
        value = score.get("db_qps_gain_pct", decision.get("db_qps_delta_pct"))
        return float(value) if isinstance(value, (int, float)) else float("-inf")

    best_semantic = max(semantic_rows, key=qps_gain) if semantic_rows else None
    best_mild = max(mild_rows, key=qps_gain) if mild_rows else None
    return {
        "log_exists": True,
        "strict_complete": strict_complete,
        "completion_reason": completion_reason,
        "search_complete_events": search_complete_events,
        "malformed_lines": malformed_lines,
        "raw_candidate_attempts": raw_attempts,
        "authoritative_candidates": len(latest),
        "superseded_attempts": raw_attempts - len(latest),
        "round_summaries": sorted(summary_rounds),
        "rounds": round_rows,
        "semantic_candidate_count": len(semantic_rows),
        "mild_semantic_candidate_count": len(mild_rows),
        "best_semantic_candidate": (
            semantic_candidate_preview(best_semantic) if best_semantic is not None else None
        ),
        "best_mild_semantic_candidate": (
            semantic_candidate_preview(best_mild) if best_mild is not None else None
        ),
    }


def verifier_result(
    artifact: Path,
    *,
    verifier: Path,
    expected_candidates: int,
    min_rounds: int,
    max_bottleneck_drop_pct: float,
) -> Dict[str, Any]:
    try:
        document = read_json(artifact)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"status": "structural_failure", "errors": [f"artifact_read:{type(exc).__name__}:{exc}"]}
    meta = document.get("meta") if isinstance(document.get("meta"), dict) else {}
    command = [
        sys.executable,
        str(verifier),
        str(artifact),
        "--expected-candidates-per-round", str(expected_candidates),
        "--min-rounds", str(min_rounds),
        "--max-bottleneck-drop-pct", str(max_bottleneck_drop_pct),
    ]
    if str(meta.get("bottleneck") or "") == "none":
        command.append("--require-none-all-resources-up")
    proc = subprocess.run(command, check=False, capture_output=True, text=True, timeout=600)
    payload: Optional[Dict[str, Any]] = None
    for raw in reversed(((proc.stdout or "") + "\n" + (proc.stderr or "")).splitlines()):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            payload = value
            break
    if payload is None:
        return {
            "status": "structural_failure",
            "errors": [f"verifier_no_json:rc={proc.returncode}"],
        }
    errors = [str(value) for value in (payload.get("errors") or [])]
    if proc.returncode == 0 and payload.get("ok") is True:
        status = "verified"
    elif semantic_only(errors):
        status = "semantic_miss"
    else:
        status = "structural_failure"
    return {"status": status, "errors": errors, "verification": payload}


def exact_set_errors(name: str, observed: Set[str], expected: Set[str]) -> List[str]:
    errors: List[str] = []
    missing = sorted(expected - observed)
    extra = sorted(observed - expected)
    if missing:
        errors.append(f"{name}_missing:{','.join(missing)}")
    if extra:
        errors.append(f"{name}_extra:{','.join(extra)}")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verifier", type=Path, default=DEFAULT_VERIFIER)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    config = read_json(config_path)
    suite = config.get("suite_audit") if isinstance(config.get("suite_audit"), dict) else {}
    output_raw = str(args.output or suite.get("output") or "").strip()
    if not output_raw:
        raise ValueError("suite_audit.output or --output is required")
    output = Path(output_raw).resolve()
    expected_physical = {str(value) for value in suite.get("expected_physical_exp_ids") or []}
    expected_logical = {str(value) for value in suite.get("expected_logical_exp_ids") or []}
    if not expected_physical or not expected_logical:
        raise ValueError("expected physical and logical exp-id sets are required")

    expected_candidates = int(config.get("expected_candidates_per_round") or 128)
    min_rounds = int(config.get("minimum_complete_rounds") or 6)
    max_drop = float(config.get("max_bottleneck_drop_pct") or 30.0)
    entries, errors = parse_entries(config)
    observed_physical = set(entries)
    observed_logical = observed_physical | {
        alias for entry in entries.values() for alias in entry.logical_exp_ids
    }
    errors.extend(exact_set_errors("physical_exp_ids", observed_physical, expected_physical))
    errors.extend(exact_set_errors("logical_exp_ids", observed_logical, expected_logical))

    physical_rows: List[Dict[str, Any]] = []
    logical_rows: List[Dict[str, Any]] = []
    config_audits: List[Dict[str, Any]] = []
    for exp_id in sorted(entries):
        entry = entries[exp_id]
        preflight = config_preflight(
            entry,
            expected_candidates=expected_candidates,
            min_rounds=min_rounds,
        )
        config_audits.append({"exp_id": exp_id, **preflight})
        errors.extend(
            f"config_preflight:{exp_id}:{error}"
            for error in (preflight.get("errors") or [])
        )
        log_progress = scan_log_progress(
            entry.log,
            expected_candidates=expected_candidates,
            min_rounds=min_rounds,
        )
        try:
            artifact = artifact_for(entry)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            physical_rows.append({
                "exp_id": exp_id,
                "status": "structural_failure",
                "errors": [f"artifact_path:{type(exc).__name__}:{exc}"],
                "config": str(entry.config),
                "config_preflight": preflight,
                "log": str(entry.log),
                "log_progress": log_progress,
            })
            continue
        if not artifact.is_file():
            result = {"status": "incomplete", "errors": ["artifact_missing"]}
        else:
            result = verifier_result(
                artifact,
                verifier=args.verifier.resolve(),
                expected_candidates=expected_candidates,
                min_rounds=min_rounds,
                max_bottleneck_drop_pct=max_drop,
            )
        physical_row = {
            "exp_id": exp_id,
            "artifact": str(artifact),
            "config": str(entry.config),
            "config_preflight": preflight,
            "log": str(entry.log),
            "target": entry.target,
            "log_progress": log_progress,
            **result,
        }
        physical_rows.append(physical_row)
        logical_rows.append({
            "logical_exp_id": exp_id,
            "physical_exp_id": exp_id,
            "artifact": str(artifact),
            "status": result["status"],
            "errors": result.get("errors") or [],
        })
        for alias in entry.logical_exp_ids:
            alias_artifact = logical_alias_path(artifact, alias)
            if not alias_artifact.is_file():
                alias_result = {"status": "incomplete", "errors": ["alias_artifact_missing"]}
            else:
                alias_result = verifier_result(
                    alias_artifact,
                    verifier=args.verifier.resolve(),
                    expected_candidates=expected_candidates,
                    min_rounds=min_rounds,
                    max_bottleneck_drop_pct=max_drop,
                )
                try:
                    alias_document = read_json(alias_artifact)
                    alias_audit = (
                        alias_document.get("audit")
                        if isinstance(alias_document.get("audit"), dict)
                        else {}
                    )
                    if alias_audit.get("physical_exp_id") != exp_id:
                        alias_result = {
                            "status": "structural_failure",
                            "errors": ["alias_physical_exp_id_mismatch"],
                        }
                    elif Path(str(alias_audit.get("physical_artifact") or "")).resolve() != artifact:
                        alias_result = {
                            "status": "structural_failure",
                            "errors": ["alias_physical_artifact_mismatch"],
                        }
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    alias_result = {
                        "status": "structural_failure",
                        "errors": [f"alias_read:{type(exc).__name__}:{exc}"],
                    }
            logical_rows.append({
                "logical_exp_id": alias,
                "physical_exp_id": exp_id,
                "artifact": str(alias_artifact),
                "status": alias_result["status"],
                "errors": alias_result.get("errors") or [],
            })

    output_dirs = [
        str(row.get("output_dir"))
        for row in config_audits
        if row.get("output_dir")
    ]
    if len(output_dirs) != len(set(output_dirs)):
        errors.append(
            f"config_preflight:unique_output_dirs={len(set(output_dirs))} "
            f"expected={len(output_dirs)}"
        )

    physical_statuses = [str(row.get("status")) for row in physical_rows]
    logical_statuses = [str(row.get("status")) for row in logical_rows]
    all_statuses = physical_statuses + logical_statuses
    structural_complete = (
        not errors
        and len(physical_rows) == len(expected_physical)
        and len(logical_rows) == len(expected_logical)
        and all(status in {"verified", "semantic_miss"} for status in all_statuses)
    )
    semantic_misses = sum(status == "semantic_miss" for status in logical_statuses)
    if structural_complete and semantic_misses == 0:
        status = "verified"
        exit_code = 0
    elif structural_complete:
        status = "complete_with_semantic_misses"
        exit_code = 10
    elif any(value == "structural_failure" for value in all_statuses) or errors:
        status = "structural_failure"
        exit_code = 1
    else:
        status = "incomplete"
        exit_code = 2

    payload = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": status,
        "config": str(config_path),
        "requirements": {
            "expected_candidates_per_round": expected_candidates,
            "minimum_complete_rounds": min_rounds,
            "expected_physical_experiments": len(expected_physical),
            "expected_logical_experiments": len(expected_logical),
        },
        "summary": {
            "physical_verified": sum(value == "verified" for value in physical_statuses),
            "physical_semantic_miss": sum(value == "semantic_miss" for value in physical_statuses),
            "physical_incomplete": sum(value == "incomplete" for value in physical_statuses),
            "physical_structural_failure": sum(value == "structural_failure" for value in physical_statuses),
            "physical_configs_passed": sum(row.get("ok") is True for row in config_audits),
            "physical_configs_failed": sum(row.get("ok") is not True for row in config_audits),
            "unique_output_dirs": len(set(output_dirs)),
            "logical_verified": sum(value == "verified" for value in logical_statuses),
            "logical_semantic_miss": semantic_misses,
            "logical_incomplete": sum(value == "incomplete" for value in logical_statuses),
            "logical_structural_failure": sum(value == "structural_failure" for value in logical_statuses),
            "physical_logs_strict_complete": sum(
                (row.get("log_progress") or {}).get("strict_complete") is True
                for row in physical_rows
            ),
            "raw_candidate_attempts": sum(
                int((row.get("log_progress") or {}).get("raw_candidate_attempts") or 0)
                for row in physical_rows
            ),
            "authoritative_candidates": sum(
                int((row.get("log_progress") or {}).get("authoritative_candidates") or 0)
                for row in physical_rows
            ),
            "superseded_attempts": sum(
                int((row.get("log_progress") or {}).get("superseded_attempts") or 0)
                for row in physical_rows
            ),
        },
        "errors": errors,
        "configuration_audit": config_audits,
        "physical_experiments": physical_rows,
        "logical_experiments": sorted(logical_rows, key=lambda row: str(row["logical_exp_id"])),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
