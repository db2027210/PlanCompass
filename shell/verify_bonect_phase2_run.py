#!/usr/bin/env python3
"""Verify Bonect phase-2 run invariants and replay evidence."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ALLOWED_COST_PARAMS = [
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
]
ALLOWED_COST_PARAM_SET = set(ALLOWED_COST_PARAMS)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_error": str(exc)}


def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if isinstance(item, dict):
                    yield item
    except FileNotFoundError:
        return


def _process_alive(pid: Optional[int]) -> bool:
    return bool(pid and Path(f"/proc/{pid}").exists())


def _read_pid(path: Path) -> Optional[int]:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _status(ok: bool, detail: str) -> Dict[str, Any]:
    return {"ok": bool(ok), "detail": detail}


def _param_names(value: Any) -> List[str]:
    if isinstance(value, dict):
        return [str(name) for name in value.keys()]
    if isinstance(value, list):
        return [str(name) for name in value]
    return []


def _get_path(obj: Dict[str, Any], path: List[str]) -> Any:
    cur: Any = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _torch_runtime_status() -> Dict[str, Any]:
    try:
        import torch  # type: ignore

        return {
            "available": True,
            "version": str(getattr(torch, "__version__", "")),
            "error": None,
        }
    except Exception as exc:
        return {
            "available": False,
            "version": None,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _check_param_scope(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    paths = [
        ["initial_params"],
        ["param_ranges"],
        ["optimizer", "gradient", "active_params"],
        ["optimizer", "bruteforce", "active_params"],
    ]
    checks: List[Dict[str, Any]] = []
    for path in paths:
        value = _get_path(cfg, path)
        names = _param_names(value)
        bad = [name for name in names if name not in ALLOWED_COST_PARAM_SET]
        missing = [name for name in ALLOWED_COST_PARAMS if name not in names]
        checks.append(
            {
                "path": ".".join(path),
                "count": len(names),
                "names": names,
                "bad": bad,
                "missing": missing,
                "ok": len(names) == 7 and not bad and not missing,
            }
        )
    return checks


def _event_records(history_paths: List[Path]) -> Tuple[Counter, Dict[str, List[Dict[str, Any]]]]:
    counts: Counter = Counter()
    records: Dict[str, List[Dict[str, Any]]] = {}
    for path in history_paths:
        for item in _iter_jsonl(path):
            event = str(item.get("event") or "")
            if not event:
                continue
            counts[event] += 1
            records.setdefault(event, []).append(item)
    return counts, records


def _latest(records: Dict[str, List[Dict[str, Any]]], event: str) -> Dict[str, Any]:
    items = records.get(event) or []
    return dict(items[-1]) if items else {}


def build_report(args: argparse.Namespace) -> Tuple[Dict[str, Any], str]:
    root = Path(args.history_root)
    run_dir = root / args.run_id
    bottleneck_dir = run_dir / args.bottleneck
    config_path = (
        Path(args.config_path)
        if args.config_path
        else Path("config/generated/pg_benchmark_bottleneck_matrix")
        / args.run_id
        / args.bottleneck
        / f"{args.benchmark}.json"
    )
    history_paths = sorted(bottleneck_dir.glob(f"{args.benchmark}_optimizer_history_*.jsonl"))
    cfg = _read_json(config_path) if config_path.exists() else {}
    bf = _get_path(cfg, ["optimizer", "bruteforce"]) or {}
    feedback = _get_path(cfg, ["final_evaluation", "feedback_loop"]) or {}
    rig = feedback.get("resource_iteration_goal") if isinstance(feedback, dict) else {}
    if not isinstance(rig, dict):
        rig = {}
    pruning_cfg = feedback.get("replay_candidate_pruning") if isinstance(feedback, dict) else {}
    if not isinstance(pruning_cfg, dict):
        pruning_cfg = {}
    candidate_pair_probe_cfg = (
        bf.get("candidate_pair_probe") if isinstance(bf.get("candidate_pair_probe"), dict) else {}
    )
    surrogate_probe_cfg = {}
    if isinstance(candidate_pair_probe_cfg.get("surrogate_probe"), dict):
        surrogate_probe_cfg = dict(candidate_pair_probe_cfg.get("surrogate_probe") or {})
    elif isinstance(candidate_pair_probe_cfg.get("backprop_surrogate_probe"), dict):
        surrogate_probe_cfg = dict(candidate_pair_probe_cfg.get("backprop_surrogate_probe") or {})
    surrogate_backend = str(surrogate_probe_cfg.get("backend") or "")
    torch_runtime = _torch_runtime_status()

    launcher_pid = _read_pid(run_dir / "launcher.pid")
    runner_pid = _read_pid(run_dir / "runner.pid")
    supervisor_pid = _read_pid(run_dir / "supervisor.pid")
    # v122 launcher was started before launcher.pid existed; keep the configured fallback.
    fallback_launcher = int(args.launcher_pid) if args.launcher_pid else None
    launcher_alive = _process_alive(launcher_pid) or _process_alive(fallback_launcher)

    counts, records = _event_records(history_paths)
    replay_queue = _latest(records, "bruteforce_initial_topk_replay_pruned")
    replay_start = _latest(records, "bruteforce_initial_topk_replay_start")
    small_loop = _latest(records, "small_loop_ranking_goal_summary")
    baseline_runtime = _latest(records, "baseline_runtime_resource_summary")
    baseline_top_sql = _latest(records, "baseline_top_sql")
    best_primary = _latest(records, "final_feedback_best_primary_by_db_qps_updated")
    round_complete = _latest(records, "final_feedback_round_complete")
    surrogate_probe_start = _latest(records, "candidate_surrogate_probe_start")
    surrogate_probe_end = _latest(records, "candidate_surrogate_probe_end")
    surrogate_start_backend = surrogate_probe_start.get("backend") if surrogate_probe_start else {}
    surrogate_end_backend = surrogate_probe_end.get("backend") if surrogate_probe_end else {}
    surrogate_runtime_backend = (
        surrogate_end_backend.get("selected_backend")
        if isinstance(surrogate_end_backend, dict)
        else None
    )

    checks: Dict[str, Any] = {
        "run_dir_exists": _status(run_dir.exists(), str(run_dir)),
        "config_exists": _status(config_path.exists(), str(config_path)),
        "runner_pid": runner_pid,
        "runner_alive": _process_alive(runner_pid),
        "supervisor_pid": supervisor_pid,
        "supervisor_alive": _process_alive(supervisor_pid),
        "launcher_pid": launcher_pid or fallback_launcher,
        "launcher_alive": launcher_alive,
        "history_file_count": len(history_paths),
        "history_paths": [str(path) for path in history_paths],
        "param_scope": _check_param_scope(cfg) if cfg else [],
        "topk": {
            "candidate_top_k": bf.get("candidate_top_k"),
            "candidate_replay_top_k": bf.get("candidate_replay_top_k"),
            "small_loop_min_validated_candidate_count": bf.get("small_loop_min_validated_candidate_count"),
            "small_loop_abort_on_prefix_insufficient": bf.get("small_loop_abort_on_prefix_insufficient"),
            "range_update_strategy": bf.get("range_update_strategy"),
            "discrete_counts": {
                name: item.get("discrete_count")
                for name, item in (cfg.get("param_ranges") or {}).items()
                if isinstance(item, dict)
            },
        },
        "resource_iteration_goal": rig or {},
        "replay_candidate_pruning": pruning_cfg or {},
        "surrogate_backprop": {
            "candidate_pair_probe_enabled": bool(candidate_pair_probe_cfg.get("enabled", False)),
            "enabled": bool(surrogate_probe_cfg.get("enabled", False)),
            "backend": surrogate_backend,
            "max_candidates": surrogate_probe_cfg.get("max_candidates"),
            "optimization_steps": surrogate_probe_cfg.get("optimization_steps"),
            "seed_top_k": surrogate_probe_cfg.get("seed_top_k"),
            "snap_to_grid": surrogate_probe_cfg.get("snap_to_grid"),
            "db_gain_weight": surrogate_probe_cfg.get("db_gain_weight"),
            "direction_reward_weight": surrogate_probe_cfg.get("direction_reward_weight"),
            "uncertainty_penalty_weight": surrogate_probe_cfg.get("uncertainty_penalty_weight"),
            "direction_miss_penalty_weight": surrogate_probe_cfg.get("direction_miss_penalty_weight"),
            "catastrophic_risk_penalty_weight": surrogate_probe_cfg.get(
                "catastrophic_risk_penalty_weight"
            ),
            "torch_runtime": torch_runtime,
            "has_start_event": bool(surrogate_probe_start),
            "has_end_event": bool(surrogate_probe_end),
            "start_backend": surrogate_start_backend,
            "end_backend": surrogate_end_backend,
            "runtime_selected_backend": surrogate_runtime_backend,
            "generated_candidates": surrogate_probe_end.get("generated_candidates"),
            "candidate_records": surrogate_probe_end.get("candidate_records"),
        },
        "event_counts": dict(counts),
        "baseline": {
            "has_baseline_runtime_resource_summary": bool(baseline_runtime),
            "has_baseline_top_sql": bool(baseline_top_sql),
            "db_qps": ((baseline_top_sql.get("baseline_db_view_monitor") or {}).get("db_qps")),
            "utilization_pct": baseline_runtime.get("utilization_pct"),
            "window": baseline_top_sql.get("window"),
        },
        "initial_top20_replay": {
            "has_queue_event": bool(replay_queue),
            "prefix_count": replay_queue.get("prefix_count"),
            "admitted_count": replay_queue.get("admitted_count"),
            "pruned_count": replay_queue.get("pruned_count"),
            "pre_real_validation_label_pruning": replay_queue.get("pre_real_validation_label_pruning"),
            "has_replay_start": bool(replay_start),
            "unique_candidate_count": replay_start.get("unique_candidate_count"),
            "pruned_inside_replay_loop_count": counts.get("bruteforce_replay_candidate_pruned", 0),
        },
        "small_loop_ranking": {
            "has_summary": bool(small_loop),
            "validated_candidate_count": small_loop.get("validated_candidate_count"),
            "required_validated_candidate_count": small_loop.get("required_validated_candidate_count"),
            "pairwise_inversion_rate": small_loop.get("pairwise_inversion_rate"),
            "first_stage_ranking_validation_pass": small_loop.get("first_stage_ranking_validation_pass"),
            "long_term_goal_hit_count_in_validated_prefix": small_loop.get("long_term_goal_hit_count_in_validated_prefix"),
            "min_long_term_goal_hits": small_loop.get("min_long_term_goal_hits"),
        },
        "best_primary": {
            "has_best_primary_update": bool(best_primary),
            "primary_target_status": best_primary.get("primary_target_status"),
            "optimized_params": best_primary.get("optimized_params"),
        },
        "round_complete": {
            "has_round_complete": bool(round_complete),
            "round_index": round_complete.get("round_index"),
            "best_primary_by_db_qps": round_complete.get("best_primary_by_db_qps"),
        },
    }

    adaptive_replay_cfg = bf.get("adaptive_replay_controller") or {}
    adaptive_replay_enabled = bool(adaptive_replay_cfg.get("enabled", False))
    adaptive_replay_max_budget = int(adaptive_replay_cfg.get("max_budget", 24) or 24)
    adaptive_replay_min_evidence = int(
        adaptive_replay_cfg.get("min_evidence", 12) or 12
    )

    required_checks = {
        "exactly_7_cost_params": all(item.get("ok") for item in checks["param_scope"]),
        "top20_replay20": (
            bf.get("candidate_top_k") == 20
            and bf.get("candidate_replay_top_k") == 20
        ),
        "adaptive_replay_controller_configured": (
            not adaptive_replay_enabled
            or (
                bf.get("candidate_replay_top_k") <= adaptive_replay_max_budget
                and int(
                    bf.get("small_loop_min_validated_candidate_count", 0) or 0
                )
                <= adaptive_replay_max_budget
                and int(
                    bf.get("small_loop_min_validated_candidate_count", 0) or 0
                )
                >= adaptive_replay_min_evidence
                and bf.get("small_loop_abort_on_prefix_insufficient") is True
            )
        ),
        "binary_interval": bf.get("range_update_strategy") == "binary_interval",
        "prefix_insufficient_does_not_abort": (
            bf.get("small_loop_abort_on_prefix_insufficient") is False
        ),
        "adaptive_prefix_insufficient_aborts_when_lane_empty": (
            not adaptive_replay_enabled
            or bf.get("small_loop_abort_on_prefix_insufficient") is True
        ),
        "not_first_accepted": (
            rig.get("stop_after_primary_acceptance") is False
            and int(rig.get("continue_after_primary_acceptance_rounds") or 0) >= 5
            and rig.get("select_best_primary_by_db_qps") is True
        ),
        "surrogate_probe_enabled": bool(surrogate_probe_cfg.get("enabled", False)),
        "surrogate_prefers_torch": "torch" in surrogate_backend.lower(),
        "torch_runtime_available": bool(torch_runtime.get("available")),
        "surrogate_runtime_autograd_seen": surrogate_runtime_backend == "torch_adam_autograd",
        "top20_entered_real_queue": (
            replay_queue.get("prefix_count") == 20
            and replay_queue.get("admitted_count") == 20
            and replay_queue.get("pruned_count") == 0
            and replay_queue.get("pre_real_validation_label_pruning") is False
        ),
        "adaptive_replay_queue_respects_lane_aware_skip": (
            not adaptive_replay_enabled
            or small_loop.get("lane_aware_replay_validation") is True
            or small_loop.get("explainable_lane_empty") in (True, False)
        ),
        "small_loop_real_ranking_ready": bool(small_loop.get("topk_validation_ready")),
    }
    checks["required_checks"] = required_checks
    # When adaptive replay controller is enabled, the legacy fixed-20/20 checks
    # (top20_replay20, prefix_insufficient_does_not_abort, top20_entered_real_queue)
    # are N/A — replaced by adaptive_replay_controller_configured +
    # adaptive_prefix_insufficient_aborts_when_lane_empty +
    # adaptive_replay_queue_respects_lane_aware_skip (override §378-413, §505-544).
    if adaptive_replay_enabled:
        required_checks_for_claim = {
            k: v
            for k, v in required_checks.items()
            if k
            not in {
                "top20_replay20",
                "prefix_insufficient_does_not_abort",
                "top20_entered_real_queue",
            }
        }
    else:
        required_checks_for_claim = dict(required_checks)
    checks["phase2_ready_for_claim"] = all(required_checks_for_claim.values())

    lines = [
        f"# Bonect Phase-2 Run Verification: {args.run_id}",
        "",
        f"- run_dir: `{run_dir}`",
        f"- config: `{config_path}`",
        f"- history_file_count: `{len(history_paths)}`",
        f"- runner_pid: `{runner_pid}` alive=`{_process_alive(runner_pid)}`",
        f"- supervisor_pid: `{supervisor_pid}` alive=`{_process_alive(supervisor_pid)}`",
        f"- launcher_pid: `{launcher_pid or fallback_launcher}` alive=`{launcher_alive}`",
        "",
        "## Required Checks",
    ]
    for name, ok in required_checks.items():
        lines.append(f"- `{name}`: `{ok}`")
    lines.extend(
        [
            "",
            "## Config",
            f"- active/initial/range param scope all 7 cost params: `{required_checks['exactly_7_cost_params']}`",
            f"- candidate_top_k: `{bf.get('candidate_top_k')}`",
            f"- candidate_replay_top_k: `{bf.get('candidate_replay_top_k')}`",
            f"- range_update_strategy: `{bf.get('range_update_strategy')}`",
            f"- small_loop_abort_on_prefix_insufficient: `{bf.get('small_loop_abort_on_prefix_insufficient')}`",
            f"- stop_after_primary_acceptance: `{rig.get('stop_after_primary_acceptance')}`",
            f"- continue_after_primary_acceptance_rounds: `{rig.get('continue_after_primary_acceptance_rounds')}`",
            f"- select_best_primary_by_db_qps: `{rig.get('select_best_primary_by_db_qps')}`",
            "",
            "## Surrogate / Backprop Backend",
            f"- candidate_pair_probe.enabled: `{candidate_pair_probe_cfg.get('enabled')}`",
            f"- surrogate_probe.enabled: `{surrogate_probe_cfg.get('enabled')}`",
            f"- surrogate_probe.backend: `{surrogate_backend}`",
            f"- surrogate_probe.max_candidates: `{surrogate_probe_cfg.get('max_candidates')}`",
            f"- surrogate_probe.optimization_steps: `{surrogate_probe_cfg.get('optimization_steps')}`",
            f"- surrogate_probe.seed_top_k: `{surrogate_probe_cfg.get('seed_top_k')}`",
            f"- surrogate_probe.snap_to_grid: `{surrogate_probe_cfg.get('snap_to_grid')}`",
            f"- torch_available: `{torch_runtime.get('available')}` version=`{torch_runtime.get('version')}`",
            f"- candidate_surrogate_probe_start: `{bool(surrogate_probe_start)}`",
            f"- candidate_surrogate_probe_end: `{bool(surrogate_probe_end)}`",
            f"- runtime_selected_backend: `{surrogate_runtime_backend}`",
            "",
            "## Evidence Events",
            f"- baseline_runtime_resource_summary: `{bool(baseline_runtime)}`",
            f"- baseline_top_sql: `{bool(baseline_top_sql)}`",
            f"- bruteforce_initial_topk_replay_pruned: `{bool(replay_queue)}`",
            f"- bruteforce_initial_topk_replay_start: `{bool(replay_start)}`",
            f"- bruteforce_replay_candidate_pruned count: `{counts.get('bruteforce_replay_candidate_pruned', 0)}`",
            f"- candidate_surrogate_probe_start count: `{counts.get('candidate_surrogate_probe_start', 0)}`",
            f"- candidate_surrogate_probe_end count: `{counts.get('candidate_surrogate_probe_end', 0)}`",
            f"- small_loop_ranking_goal_summary: `{bool(small_loop)}`",
            f"- final_feedback_best_primary_by_db_qps_updated: `{bool(best_primary)}`",
            f"- final_feedback_round_complete: `{bool(round_complete)}`",
            "",
            "## Interpretation",
        ]
    )
    if checks["phase2_ready_for_claim"]:
        lines.append("- The run has the expected config and real-replay evidence for the Memory small-loop gate.")
    elif not history_paths:
        lines.append("- The run has not started optimizer history yet; config checks can pass, but real baseline/top20 evidence is still missing.")
    else:
        lines.append("- The run has partial evidence; inspect failed required checks before claiming phase-2 progress.")
    lines.append("")
    return checks, "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--history-root", default="history/pg_benchmark_bottleneck_matrix")
    parser.add_argument("--bottleneck", default="memory")
    parser.add_argument("--benchmark", default="hybench_ap")
    parser.add_argument("--config-path", default="")
    parser.add_argument("--launcher-pid", default="")
    parser.add_argument("--json-output", default="")
    parser.add_argument("--md-output", default="")
    args = parser.parse_args()

    result, markdown = build_report(args)
    if args.json_output:
        path = Path(args.json_output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.md_output:
        path = Path(args.md_output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
