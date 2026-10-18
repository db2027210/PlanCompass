#!/usr/bin/env python3
"""HyBench XP binary_plan long-term goal supervisor.

Read-only control loop that observes `/disk2/.../<run_dir>/<target>_binary_search_hybenchxp.jsonl`
for the cpu -> memory -> none -> io binary_plan stage sequence and emits
`control_decision_*.md/json` plus a Chinese progress report per poll iteration.

The supervisor MUST NOT start/stop the tuner, modify configs, or run the tuner.
It only emits decisions, resumes dead tmux helper sessions, and reports state.
The main agent (human or AI) reads decisions and executes adjustments.

Patience rules follow `.agents/bonect-bruteforce-tuning-agent-prompt.md` and
`.agents/bonect-main-goal.md`:
  - hard error: immediate decision (stop_run required)
  - 3 connected rounds with reject-dominated target_direction_failed and 0 accepted
  - 2 connected rounds top-k estimated/actual plateau (rel diff <= 1e-3)
  - 5 rounds target resource >5pp from band and <2pp real delta -> mark unreachable
  - stage converged -> revalidate (continue script already handles this)
The supervisor itself keeps the long-lived goal tracker JSON up to date.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path("/home/anonymous/program/resource_adaptive")
DEFAULT_RUN_DIR = Path(
    "/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs/hyxp_docker_full_bruteforce_warmup_20260704_014438"
)
STAGE_ORDER = ["cpu", "memory", "none", "io"]
ALLOWED_COST_PARAMS = {
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
}
REPORT_DIR = ROOT / "report" / "bonect"
HEARTBEAT_SEC = 60
PLATEAU_REL = 1e-3
NO_ACCEPT_REJECT_REASON = "target_resource_direction_failed"


def now_ts() -> str:
    return datetime.now().strftime("%Y%m%dT%H%M%S")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def is_current_metric_candidate(row: Dict[str, Any], target: str) -> bool:
    """Return True when a candidate belongs to the current reporting metric."""
    if target != "memory":
        return True
    resource_metric = row.get("resource_metric") if isinstance(row.get("resource_metric"), dict) else {}
    memory_metric = resource_metric.get("memory") if isinstance(resource_metric.get("memory"), dict) else {}
    return (
        memory_metric.get("mode_effective") == "cgroup_working_set"
        and memory_metric.get("source") == "docker_cgroup_working_set"
    )


def candidate_status_summary(row: Dict[str, Any], target: str) -> Dict[str, Any]:
    decision = row.get("decision") if isinstance(row.get("decision"), dict) else {}
    resources = row.get("resources") if isinstance(row.get("resources"), dict) else {}
    hybench = row.get("hybench") if isinstance(row.get("hybench"), dict) else {}
    gate_names = (
        "status_ok",
        "target_ok",
        "db_qps_ok",
        "hybench_qps_guard_ok",
        "hybench_tps_guard_ok",
        "p95_guard_ok",
        "p99_guard_ok",
        "non_target_ok",
        "resource_explanation_ok",
    )
    return {
        "label": row.get("label"),
        "round": row.get("round"),
        "candidate_index": row.get("candidate_index"),
        "db_qps": safe_float(row.get("db_qps"), None),
        "target_value": safe_float(resources.get(target), None),
        "target_delta_pct": safe_float(decision.get("target_delta_pct"), None),
        "db_qps_delta_pct": safe_float(decision.get("db_qps_delta_pct"), None),
        "xp_qps": safe_float(hybench.get("xp_qps"), None),
        "xp_tps": safe_float(hybench.get("xp_tps"), None),
        "xp_qps_delta_pct": safe_float(decision.get("hybench_xp_qps_delta_pct"), None),
        "xp_tps_delta_pct": safe_float(decision.get("hybench_xp_tps_delta_pct"), None),
        "other_up_resources": decision.get("other_up_resources") or [],
        "other_up_details": decision.get("other_up_details") or [],
        "resource_explanation_ok": bool(decision.get("resource_explanation_ok")),
        "reject_reason": (
            row.get("accept_reject_reason")
            or row.get("reject_reason")
            or decision.get("reason")
        ),
        "failed_checks": [name for name in gate_names if decision.get(name) is not True],
        "params": row.get("effective_params") or row.get("params") or {},
        "comparison_baseline_key": decision.get("comparison_baseline_key"),
        "comparison_baseline_label": decision.get("comparison_baseline_label"),
    }


def is_fallback_high_dbqps_candidate(row: Dict[str, Any]) -> bool:
    """Keep rejected trade-off points that only miss the explanation gate."""
    if row.get("accepted"):
        return False
    decision = row.get("decision") if isinstance(row.get("decision"), dict) else {}
    required_guards = (
        "status_ok",
        "target_ok",
        "db_qps_ok",
        "hybench_qps_guard_ok",
        "hybench_tps_guard_ok",
        "p95_guard_ok",
        "p99_guard_ok",
        "non_target_ok",
    )
    return (
        all(decision.get(name) is True for name in required_guards)
        and decision.get("resource_explanation_ok") is not True
        and safe_float(decision.get("db_qps_delta_pct"), -1.0) > 0.0
        and safe_float(decision.get("target_delta_pct"), 1.0) < 0.0
    )


def write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def run_cmd(cmd: List[str], timeout: int = 5) -> Tuple[int, str]:
    try:
        r = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            text=True,
        )
        return r.returncode, r.stdout
    except Exception as exc:
        return 1, f"cmd_failed: {exc!r}"


def tmux_has(session: str) -> bool:
    code, _ = run_cmd(["tmux", "has-session", "-t", session])
    return code == 0


def respawn_tmux(session: str, command: str) -> bool:
    if tmux_has(session):
        return False
    full = f"cd {ROOT} && exec {command}"
    code, _ = run_cmd(["tmux", "new-session", "-d", "-s", session, full], timeout=10)
    return code == 0


@dataclasses.dataclass
class StageState:
    target: str
    has_log: bool = False
    has_best: bool = False
    has_baseline: bool = False
    converged: bool = False
    ranges_within_epsilon: bool = False
    latest_stop: str = ""
    latest_summary_stop: str = ""
    rounds: int = 0
    candidates: int = 0
    accepted: int = 0
    max_round: int = 0
    max_rounds_config: Optional[int] = None
    max_candidates_per_round: Optional[int] = None
    topk_limit: Optional[int] = None
    updated_ranges: Dict[str, List[float]] = dataclasses.field(default_factory=dict)
    latest_round_summary: Dict[str, Any] = dataclasses.field(default_factory=dict)
    reject_reasons: Dict[str, int] = dataclasses.field(default_factory=dict)
    latest_round_accepted: int = 0
    current_metric_candidates: int = 0
    current_metric_accepted: int = 0
    legacy_or_mixed_candidates: int = 0
    legacy_or_mixed_accepted: int = 0
    baseline_db_qps: Optional[float] = None
    best_db_qps: Optional[float] = None
    best_target_delta_pct: Optional[float] = None
    best_other_up: List[str] = dataclasses.field(default_factory=list)
    best_explainable: Optional[bool] = None
    best_label: str = ""
    accepted_dbqps_best: Dict[str, Any] = dataclasses.field(default_factory=dict)
    fallback_high_dbqps: List[Dict[str, Any]] = dataclasses.field(default_factory=list)
    best_p99_ms: Optional[float] = None
    active_params: List[str] = dataclasses.field(default_factory=list)
    reached_unreachable: bool = False
    plateau_round_seen: int = 0
    no_accept_round_seen: int = 0


def parse_stage(run_dir: Path, target: str) -> StageState:
    log_path = run_dir / f"{target}_binary_search_hybenchxp.jsonl"
    best_path = run_dir / f"best_for_{target}.json"
    state = StageState(target=target)
    if not log_path.exists():
        return state
    state.has_log = True
    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                row = json.loads(line)
            except Exception:
                continue
            ev = row.get("event")
            if ev == "stage_start" or ev == "stage_resume":
                if not state.active_params:
                    state.active_params = list(row.get("active_params") or [])
                if state.max_rounds_config is None:
                    state.max_rounds_config = row.get("max_rounds") or row.get(
                        "configured_max_rounds"
                    )
                if state.max_candidates_per_round is None:
                    state.max_candidates_per_round = row.get("max_candidates_per_round")
            elif ev == "baseline_validation":
                state.has_baseline = True
                if state.baseline_db_qps is None:
                    state.baseline_db_qps = safe_float(row.get("db_qps"), None) if row.get("db_qps") is not None else None
                    state.baseline_db_qps = safe_float(row.get("db_qps"))
                    if state.baseline_db_qps <= 0:
                        state.baseline_db_qps = None
            elif ev == "round_summary":
                state.rounds += 1
                state.latest_round_summary = row
                state.updated_ranges = row.get("updated_ranges") or state.updated_ranges
                state.latest_round_accepted = int(row.get("accepted_count") or 0)
                state.reject_reasons = row.get("reject_reasons") or {}
                rcount = int(row.get("round") or 0)
                if rcount > state.max_round:
                    state.max_round = rcount
                bc = row.get("best_candidate") or {}
                if bc:
                    state.best_db_qps = safe_float(bc.get("db_qps"))
                    d = bc.get("decision") or {}
                    state.best_target_delta_pct = (
                        safe_float(d.get("target_delta_pct")) if d else None
                    )
                    state.best_other_up = d.get("other_up_resources", []) if d else []
                    state.best_explainable = bool(
                        d.get("resource_explanation_ok")
                    ) if d else None
                    state.best_label = str(bc.get("label") or "")
                    state.best_p99_ms = safe_float(bc.get("p99_ms"), None) if bc.get("p99_ms") is not None else None
                    state.best_p99_ms = safe_float(bc.get("p99_ms"))
            elif ev == "candidate_validation":
                state.candidates += 1
                current_metric = is_current_metric_candidate(row, target)
                if current_metric:
                    state.current_metric_candidates += 1
                else:
                    state.legacy_or_mixed_candidates += 1
                d = row.get("decision") or {}
                if row.get("accepted"):
                    state.accepted += 1
                    if current_metric:
                        state.current_metric_accepted += 1
                        summary = candidate_status_summary(row, target)
                        if (
                            not state.accepted_dbqps_best
                            or safe_float(summary.get("db_qps"), -1.0)
                            > safe_float(state.accepted_dbqps_best.get("db_qps"), -1.0)
                        ):
                            state.accepted_dbqps_best = summary
                    else:
                        state.legacy_or_mixed_accepted += 1
                elif current_metric and is_fallback_high_dbqps_candidate(row):
                    state.fallback_high_dbqps.append(candidate_status_summary(row, target))
            elif ev == "stage_stop":
                state.latest_stop = str(row.get("reason") or "")
                if isinstance(row.get("final_ranges"), dict):
                    state.updated_ranges = row.get("final_ranges") or state.updated_ranges
            elif ev == "stage_summary":
                state.latest_summary_stop = str(row.get("stop_reason") or state.latest_summary_stop)
                if isinstance(row.get("final_ranges"), dict):
                    state.updated_ranges = row.get("final_ranges") or state.updated_ranges
    if best_path.exists():
        state.has_best = True
        try:
            best = json.loads(best_path.read_text())
            state.latest_summary_stop = str(
                best.get("stop_reason") or state.latest_summary_stop
            )
            if isinstance(best.get("final_ranges"), dict):
                state.updated_ranges = best.get("final_ranges") or state.updated_ranges
            bc = best.get("best_candidate") or {}
            if bc:
                state.best_db_qps = safe_float(bc.get("db_qps")) or state.best_db_qps
                state.best_label = str(bc.get("label") or state.best_label)
                d = bc.get("decision") or {}
                if d:
                    state.best_explainable = bool(d.get("resource_explanation_ok"))
        except Exception:
            pass
    state.fallback_high_dbqps.sort(
        key=lambda item: safe_float(item.get("db_qps"), -1.0),
        reverse=True,
    )
    state.fallback_high_dbqps = state.fallback_high_dbqps[:10]
    state.ranges_within_epsilon = _ranges_within_epsilon(state.updated_ranges)
    state.converged = (
        state.latest_stop == "epsilon_converged"
        or state.latest_summary_stop == "epsilon_converged"
        or state.ranges_within_epsilon
    )
    return state


def _ranges_within_epsilon(ranges: Dict[str, List[float]]) -> bool:
    if not ranges:
        return False
    eps_default = 0.001
    eps_overrides = {"parallel_setup_cost": 1.0}
    for name, raw in ranges.items():
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            return False
        width = safe_float(raw[1]) - safe_float(raw[0])
        eps = eps_overrides.get(name, eps_default)
        if width > eps:
            return False
    return True


def find_tuner_pid(run_dir: Path, target: str) -> Optional[int]:
    code, out = run_cmd(["ps", "-eo", "pid=,cmd="], timeout=5)
    if code != 0:
        return None
    needle = f"{run_dir}"
    target_arg = f"--target {target}"
    for line in out.splitlines():
        if "binary_plan_resource_tuner" in line and needle in line and target_arg in line:
            parts = line.split()
            if parts:
                try:
                    return int(parts[0])
                except Exception:
                    pass
    return None


def load_goal_tracker(run_dir: Path) -> Dict[str, Any]:
    p = run_dir / "hybenchxp_goal_tracker.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {
        "created_at": now_ts(),
        "stages": {},
        "final_report": None,
        "final_status": "in_progress",
    }


def save_goal_tracker(run_dir: Path, tracker: Dict[str, Any]) -> None:
    p = run_dir / "hybenchxp_goal_tracker.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(tracker, ensure_ascii=False, indent=2), encoding="utf-8")


def evaluate_patience(
    state: StageState, tracker_stage: Dict[str, Any]
) -> List[Dict[str, Any]]:
    decisions: List[Dict[str, Any]] = []
    # hard error: extra active param
    extra = sorted(set(state.active_params) - ALLOWED_COST_PARAMS)
    if extra:
        decisions.append(
            {
                "severity": "hard_error",
                "rule": "non_cost_param_tuning_scope",
                "action": "stop_run_and_fix_active_params",
                "evidence": {"extra_params": extra, "active_params": state.active_params},
                "message": f" 7  cost {extra}",
            }
        )
    # vm profile reapply check via sanity jsonl
    san = state  # placeholder, sanity handled separately
    # no-accept plateau
    if state.latest_round_summary:
        rr = state.reject_reasons or {}
        accepted = int(state.latest_round_summary.get("accepted_count") or 0)
        if accepted == 0 and rr:
            dominant = max(rr, key=lambda k: rr[k])
            if dominant == NO_ACCEPT_REJECT_REASON:
                tracker_stage.setdefault("no_accept_rounds", 0)
                tracker_stage["no_accept_rounds"] += 1
                state.no_accept_round_seen = tracker_stage["no_accept_rounds"]
                if tracker_stage["no_accept_rounds"] >= 3:
                    decisions.append(
                        {
                            "severity": "soft_trigger",
                            "rule": "connected_no_accept_target_direction_failed",
                            "action": "diagnose_or_change_workload",
                            "evidence": {
                                "no_accept_rounds": tracker_stage["no_accept_rounds"],
                                "reject_reasons": rr,
                            },
                            "message": (
                                f"{state.target}  {tracker_stage['no_accept_rounds']}  0 accepted  reject  "
                                f"{dominant} workload  active_params/top-k/"
                            ),
                        }
                    )
        else:
            tracker_stage["no_accept_rounds"] = 0
    # plateau detection: append to per-round history only when max_round advanced
    tracker_best_hist = tracker_stage.setdefault("best_db_qps_history", [])
    tracker_last_round = tracker_stage.setdefault("last_round_sampled", 0)
    if state.max_round > tracker_last_round:
        report_best = state.accepted_dbqps_best or {"db_qps": state.best_db_qps, "label": state.best_label}
        tracker_stage["last_round_sampled"] = state.max_round
        tracker_best_hist.append(
            {
                "round": state.max_round,
                "best_db_qps": report_best.get("db_qps"),
                "label": report_best.get("label"),
                "selection_policy": (
                    "accepted_dbqps_best_clean_working_set"
                    if state.accepted_dbqps_best
                    else "policy_target_drop_first_fallback"
                ),
            }
        )
    if len(tracker_best_hist) >= 2:
        recent = tracker_best_hist[-3:]
        if all(isinstance(v, dict) and v.get("best_db_qps") is not None for v in recent):
            qps_vals = [float(v["best_db_qps"]) for v in recent]
            denom = max(abs(qps_vals[-1]), 1.0)
            rel = abs(qps_vals[-1] - qps_vals[0]) / denom
            if rel <= PLATEAU_REL:
                state.plateau_round_seen = tracker_stage.get("plateau_rounds", 0) + 1
                tracker_stage["plateau_rounds"] = state.plateau_round_seen
                if state.plateau_round_seen >= 2:
                    decisions.append(
                        {
                            "severity": "soft_trigger",
                            "rule": "topk_estimated_actual_plateau",
                            "action": "adjustcandidate_topk_max_iter_step_ranges",
                            "evidence": {
                                "recent_best_db_qps": recent[-2:],
                                "rel_diff": rel,
                                "plateau_rounds": state.plateau_round_seen,
                            },
                            "message": (
                                f"{state.target}  {state.plateau_round_seen}  best DB-QPS  "
                                f"(rel_diff={rel:.4g}) candidate_top_k / max_iter / discrete_count / step / active ranges"
                            ),
                        }
                    )
    # convered but unexplainable
    if state.converged and state.best_explainable is False:
        decisions.append(
            {
                "severity": "soft_trigger",
                "rule": "converged_unexplainable",
                "action": "workload_construction_or_mark_fallback",
                "evidence": {
                    "best_label": state.best_label,
                    "best_db_qps": state.best_db_qps,
                    "best_target_delta_pct": state.best_target_delta_pct,
                    "best_other_up": state.best_other_up,
                },
                "message": (
                    f"{state.target}  ε  best  resource_explanation_ok=False workload "
                    f" fallback_best_dbqps_not_explainable  {state.best_label}"
                ),
            }
        )
    return decisions


def check_sanity_hard(run_dir: Path, target: str) -> List[Dict[str, Any]]:
    p = run_dir / "resource_monitor_sanity_hybenchxp.jsonl"
    if not p.exists():
        return []
    decisions: List[Dict[str, Any]] = []
    last = None
    with p.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                last = json.loads(line)
            except Exception:
                continue
    if last:
        for a in last.get("anomalies") or []:
            sev = str(a.get("severity") or "").lower()
            kind = str(a.get("kind") or a.get("id") or "")
            if sev == "critical" and kind in (
                "container_missing",
                "validation_cpu_over_cpuset_limit",
                "resource_scope_mismatch",
            ):
                decisions.append(
                    {
                        "severity": "hard_error",
                        "rule": "resource_monitor_sanity_critical",
                        "action": "stop_run_and_fix_monitoring",
                        "evidence": a,
                        "message": f"{target} sanity watcher  critical  {kind} run ",
                    }
                )
                break
    return decisions


def write_control_decision(run_dir: Path, decisions: List[Dict[str, Any]], tracker: Dict[str, Any]) -> Optional[Path]:
    if not decisions:
        return None
    ts = now_ts()
    path_md = run_dir / f"control_decision_{ts}.md"
    path_json = run_dir / f"control_decision_{ts}.json"
    hard = [d for d in decisions if d.get("severity") == "hard_error"]
    soft = [d for d in decisions if d.get("severity") != "hard_error"]
    md_lines = [
        f"# HyBench XP binary_plan control decision {ts}",
        "",
        f"**run**: `{run_dir}`",
        "",
        f"- hard_error: {len(hard)}",
        f"- soft_trigger: {len(soft)}",
        "",
        "## Decisions",
        "",
    ]
    for i, d in enumerate(decisions, 1):
        md_lines.append(f"### {i}. [{d.get('severity')}] {d.get('rule')}")
        md_lines.append("")
        md_lines.append(f"- action: `{d.get('action')}`")
        md_lines.append(f"- message: {d.get('message')}")
        md_lines.append(f"- evidence: `{json.dumps(d.get('evidence'), ensure_ascii=False)}`")
        md_lines.append("")
    payload = {
        "timestamp": ts,
        "run_dir": str(run_dir),
        "decisions": decisions,
        "tracker_status": tracker.get("final_status"),
    }
    path_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    path_md.write_text("\n".join(md_lines), encoding="utf-8")
    # also append to a long-running decisions log
    append_jsonl(run_dir / "control_decisions.jsonl", payload)
    return path_md


def build_stage_progress_md(stages: Dict[str, StageState]) -> str:
    lines = ["# HyBench XP binary_plan ", "", f": {now_ts()}", ""]
    for tgt in STAGE_ORDER:
        s = stages.get(tgt)
        if s is None:
            lines.append(f"## {tgt}: ")
            lines.append("")
            continue
        conv = "✓ε" if s.converged else "✗"
        live = "live" if find_tuner_pid(Path(""), tgt) else "no-tuner"
        lines.append(f"## {tgt}  {conv}  (rounds={s.max_round}/{s.max_rounds_config}, candidates={s.candidates})")
        lines.append("")
        lines.append(f"- active_params: `{s.active_params}`")
        lines.append(f"- converged: {s.converged}; ranges_within_epsilon: {s.ranges_within_epsilon}")
        lines.append(f"- latest_stop: `{s.latest_stop}` / summary_stop: `{s.latest_summary_stop}`")
        lines.append(
            "- accepted: "
            f"current_metric={s.current_metric_accepted}, total={s.accepted}, "
            f"latest_round={s.latest_round_accepted}, reject_reasons={s.reject_reasons}"
        )
        if s.target == "memory":
            lines.append(
                "- memory metric split: "
                f"clean_working_set_candidates={s.current_metric_candidates}, "
                f"legacy_or_mixed_candidates={s.legacy_or_mixed_candidates}"
            )
        if s.best_db_qps is not None:
            lines.append(
                f"- policy best (target-drop first): label=`{s.best_label}` db_qps={s.best_db_qps:.4g} "
                f"target_delta_pct={s.best_target_delta_pct} other_up={s.best_other_up} "
                f"explainable={s.best_explainable}"
            )
        if s.accepted_dbqps_best:
            b = s.accepted_dbqps_best
            lines.append(
                f"- accepted DB-QPS best: label=`{b.get('label')}` db_qps={b.get('db_qps')} "
                f"target_delta_pct={b.get('target_delta_pct')} xp_qps_delta_pct={b.get('xp_qps_delta_pct')} "
                f"xp_tps_delta_pct={b.get('xp_tps_delta_pct')}"
            )
        if s.fallback_high_dbqps:
            b = s.fallback_high_dbqps[0]
            lines.append(
                f"- fallback high DB-QPS best (not accepted): label=`{b.get('label')}` "
                f"db_qps={b.get('db_qps')} db_qps_delta_pct={b.get('db_qps_delta_pct')} "
                f"target_delta_pct={b.get('target_delta_pct')} failed_checks={b.get('failed_checks')}"
            )
        if s.updated_ranges:
            lines.append("- latest ranges:")
            for k, v in s.updated_ranges.items():
                lines.append(f"    - `{k}`: `{v}`")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR))
    ap.add_argument("--poll-sec", type=int, default=HEARTBEAT_SEC)
    ap.add_argument("--progress-every", type=int, default=5)
    ap.add_argument("--sanity-session", default="hyxp_sanity_resume_20260704_1415")
    ap.add_argument("--sanity-cmd", default="python3 shell/monitor_hybenchxp_resource_sanity.py --run-dir {run} --interval-sec 15")
    ap.add_argument("--sequence-session", default="hyxp_full_sequence_20260704_1415")
    ap.add_argument("--sequence-cmd", default="bash shell/continue_hybenchxp_binary_plan_run.sh")
    ap.add_argument("--no-tmux-keepalive", action="store_true")
    args = ap.parse_args()

    run_dir = Path(args.run_dir)
    if not run_dir.exists():
        print(f"run_dir not found: {run_dir}", file=sys.stderr)
        return 2

    state_path = run_dir / "hybenchxp_goal_supervisor_state.json"
    tracker = load_goal_tracker(run_dir)
    iteration = 0
    base_log = run_dir / "hybenchxp_goal_supervisor.log"
    while True:
        iteration += 1
        now = now_ts()
        stages: Dict[str, StageState] = {}
        all_decisions: List[Dict[str, Any]] = []
        for tgt in STAGE_ORDER:
            st = parse_stage(run_dir, tgt)
            stages[tgt] = st
            tracker_stage = tracker.setdefault("stages", {}).setdefault(
                tgt,
                {"status": "pending", "no_accept_rounds": 0, "plateau_rounds": 0, "best_db_qps_history": []},
            )
            # update stage status
            if st.converged:
                if st.best_explainable is True:
                    tracker_stage["status"] = "converged_explainable"
                elif st.best_explainable is False:
                    tracker_stage["status"] = "converged_unexplainable"
                else:
                    tracker_stage["status"] = "converged_no_best"
            elif st.has_log and st.has_baseline:
                tracker_stage["status"] = "in_progress"
            elif st.has_log:
                tracker_stage["status"] = "staged_not_baseline"
            else:
                tracker_stage["status"] = "pending"
            decs = evaluate_patience(st, tracker_stage)
            decs += check_sanity_hard(run_dir, tgt)
            all_decisions.extend(decs)
        # find current forward stage
        forward_target = next(
            (t for t in STAGE_ORDER if stages[t].has_log and not stages[t].converged),
            None,
        )
        tracker["forward_stage"] = forward_target
        # if all converged -> final
        all_converged = all(stages[t].converged for t in STAGE_ORDER)
        if all_converged and tracker.get("final_status") != "done":
            tracker["final_status"] = "done"
            tracker["final_report"] = (
                f"report/bonect/binary_plan_resource_tuning_hybenchxp_FINAL_{now}.md"
            )
            all_decisions.append(
                {
                    "severity": "info",
                    "rule": "all_stages_converged",
                    "action": "write_final_report",
                    "evidence": {t: stages[t].converged for t in STAGE_ORDER},
                    "message": " ε  FINAL  supervisor",
                }
            )
        # tmux keepalive
        keepalive_notes = []
        if not args.no_tmux_keepalive:
            if not tmux_has(args.sequence_session):
                respawn_tmux(
                    args.sequence_session,
                    f'BINARY_PLAN_TUNING_HYBENCHXP_RUN_DIR="{run_dir}" {args.sequence_cmd}',
                )
                keepalive_notes.append(f"respawned sequence session {args.sequence_session}")
            if not tmux_has(args.sanity_session):
                respawn_tmux(
                    args.sanity_session,
                    args.sanity_cmd.replace("{run}", str(run_dir)),
                )
                keepalive_notes.append(f"respawned sanity session {args.sanity_session}")
        if keepalive_notes:
            all_decisions.append(
                {
                    "severity": "info",
                    "rule": "tmux_keepalive_respawn",
                    "action": "no_action_needed",
                    "evidence": {"notes": keepalive_notes},
                    "message": "".join(keepalive_notes),
                }
            )
        # write decisions
        decision_path = write_control_decision(run_dir, all_decisions, tracker)
        # update tracker file
        tracker["last_heartbeat"] = now
        tracker["iteration"] = iteration
        tracker.setdefault("stage_summary", {})
        for tgt in STAGE_ORDER:
            s = stages[tgt]
            report_best = s.accepted_dbqps_best or {
                "label": s.best_label,
                "db_qps": s.best_db_qps,
                "target_delta_pct": s.best_target_delta_pct,
            }
            tracker["stage_summary"][tgt] = {
                "status": tracker["stages"][tgt]["status"],
                "converged": s.converged,
                "rounds": s.max_round,
                "candidates": s.candidates,
                "accepted": s.current_metric_accepted if tgt == "memory" else s.accepted,
                "accepted_total": s.accepted,
                "latest_round_accepted": s.latest_round_accepted,
                "current_metric_candidates": s.current_metric_candidates,
                "current_metric_accepted": s.current_metric_accepted,
                "legacy_or_mixed_candidates": s.legacy_or_mixed_candidates,
                "legacy_or_mixed_accepted": s.legacy_or_mixed_accepted,
                "best_db_qps": report_best.get("db_qps"),
                "best_label": report_best.get("label"),
                "best_selection_policy": (
                    "accepted_dbqps_best_clean_working_set_for_reporting"
                    if s.accepted_dbqps_best
                    else "policy_target_drop_first_fallback"
                ),
                "accepted_dbqps_best": s.accepted_dbqps_best or None,
                "fallback_high_dbqps_policy": (
                    "current_metric_rejected_target_down_dbqps_up_all_guards_pass_except_resource_explanation"
                ),
                "fallback_high_dbqps_best": s.fallback_high_dbqps[0] if s.fallback_high_dbqps else None,
                "fallback_high_dbqps_top": s.fallback_high_dbqps,
                "policy_best_db_qps": s.best_db_qps,
                "policy_best_label": s.best_label,
                "policy_best_target_delta_pct": s.best_target_delta_pct,
                "best_explainable": s.best_explainable,
                "no_accept_rounds": tracker["stages"][tgt].get("no_accept_rounds", 0),
                "plateau_rounds": tracker["stages"][tgt].get("plateau_rounds", 0),
            }
            if s.has_log:
                write_json_atomic(
                    run_dir / f"fallback_high_dbqps_for_{tgt}.json",
                    {
                        "generated_at": now,
                        "target": tgt,
                        "classification": "fallback_not_formally_accepted",
                        "metric_scope": "current_metric_only",
                        "policy": tracker["stage_summary"][tgt]["fallback_high_dbqps_policy"],
                        "candidate_count": len(s.fallback_high_dbqps),
                        "candidates": s.fallback_high_dbqps,
                    },
                )
        # only persist a limited best_db_qps_history to avoid unbounded growth
        for tgt in STAGE_ORDER:
            hist = tracker["stages"][tgt].get("best_db_qps_history", [])
            if len(hist) > 12:
                tracker["stages"][tgt]["best_db_qps_history"] = hist[-12:]
        save_goal_tracker(run_dir, tracker)
        # state file
        state_path.write_text(json.dumps(tracker, ensure_ascii=False, indent=2), encoding="utf-8")
        # progress report
        if iteration % args.progress_every == 0 or decision_path:
            md = build_stage_progress_md(stages)
            md_path = REPORT_DIR / f"binary_plan_tuning_hybenchxp_goal_progress_{now}.md"
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(md, encoding="utf-8")
        # log line
        log_line = (
            f"[{now}] iter={iteration} forward={forward_target} "
            f"stage_status="
            + ",".join(f"{t}:{tracker['stages'][t]['status']}" for t in STAGE_ORDER)
            + (f" decision={decision_path.name}" if decision_path else "")
            + (f" keepalive={keepalive_notes}" if keepalive_notes else "")
        )
        append_jsonl(base_log, {"ts": now, "iter": iteration, "line": log_line})
        print(log_line, flush=True)
        if all_converged and tracker.get("final_status") == "done":
            # one more pass so main agent can act, then stop
            return 0
        time.sleep(args.poll_sec)


if __name__ == "__main__":
    raise SystemExit(main())
