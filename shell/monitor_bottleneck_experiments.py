#!/usr/bin/env python3
"""Monitor bottleneck construction experiments and auto-correct when off-target.

Continuously watches running or recently-completed bottleneck experiments,
checks whether resource utilization is converging toward the target band,
and applies corrections + reruns when experiments are stuck or off-track.

Target resource utilization bands:
  - CPU:    65%-80%  (hard overload at 90%)
  - Memory: 70%-80%  (hard overload at 90%)
  - IO:     50%-75%  (hard overload at 90%)

Off-target detection:
  1. Target resource is above hard overload (90%) for > N consecutive iterations
  2. Target resource is stuck outside the target band with no progress
  3. Degradation count exceeds threshold (query performance collapsed)
  4. Parameters hit boundary values and can't progress
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# ── Target bands ──────────────────────────────────────────────────────────
RESOURCE_TARGETS: Dict[str, Dict[str, float]] = {
    "cpu": {"min_pct": 65.0, "max_pct": 80.0, "hard_pct": 90.0, "target_pct": 72.5},
    "memory": {"min_pct": 70.0, "max_pct": 80.0, "hard_pct": 90.0, "target_pct": 75.0},
    "io": {"min_pct": 50.0, "max_pct": 75.0, "hard_pct": 90.0, "target_pct": 62.5},
}

# ── Detection thresholds ──────────────────────────────────────────────────
STUCK_ITERATIONS = 5          # Consecutive iterations with no improvement
DEGRADATION_MAX_COUNT = 5     # Max degraded queries before alarm
DEGRADATION_MAX_PCT = 50.0    # Max acceptable avg degradation pct
PARAM_BOUNDARY_HIT_COUNT = 2  # Times a parameter hits min/max before alarm
MIN_UTIL_PROGRESS = 0.005     # Minimum utilization change to count as progress

# ── Experiment patterns ───────────────────────────────────────────────────
HISTORY_PATTERN = re.compile(
    r"(?P<prefix>[\w_]+)_(?P<bottleneck>cpu|memory|io)"
    r"_(?P<phase>coarse|calibration|fine)"
    r"_history.*\.jsonl$"
)
RUN_LOG_PATTERN = re.compile(
    r"run_(?P<prefix>[\w_]+)_(?P<bottleneck>cpu|memory|io)"
    r"_(?P<phase>coarse|calibration|fine)"
    r"_\d{8}_\d{6}\.log$"
)
# New experiment output format under history/pg_benchmark_bottleneck_matrix/
MATRIX_DIR = PROJECT_ROOT / "history" / "pg_benchmark_bottleneck_matrix"
MATRIX_JSONL_PATTERN = re.compile(r"hybench.*_optimizer_history_.*\.jsonl$")
MATRIX_LOG_PATTERN = re.compile(r"hybench_.*\.log$")


@dataclass
class ExperimentState:
    """Snapshot of one bottleneck experiment's current state."""

    bottleneck: str
    benchmark: str
    phase: str
    history_path: Optional[Path] = None
    run_log_path: Optional[Path] = None
    pid: Optional[int] = None
    running: bool = False
    progress_file: Optional[Path] = None

    # Latest iteration data
    latest_iter: int = -1
    current_params: Dict[str, float] = field(default_factory=dict)
    current_util: Dict[str, float] = field(default_factory=dict)
    current_score: float = 0.0
    initial_util: Dict[str, float] = field(default_factory=dict)
    degraded_count: int = 0
    max_degradation_pct: float = 0.0

    # Trend tracking
    util_trend: Dict[str, List[float]] = field(default_factory=dict)
    score_trend: List[float] = field(default_factory=list)
    no_improve_iters: int = 0
    params_at_boundary: Set[str] = field(default_factory=set)

    # Alerts
    alerts: List[str] = field(default_factory=list)


def find_running_experiments() -> List[ExperimentState]:
    """Find currently running bottleneck optimizer processes."""
    states: List[ExperimentState] = []
    try:
        result = subprocess.run(
            ["pgrep", "-af", "parameter_optimizer"],
            capture_output=True, text=True,
        )
        for line in result.stdout.strip().splitlines():
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            pid = int(parts[0])
            cmd = " ".join(parts[1:])

            # Extract config path from environment or command
            config_path = None
            for part in parts:
                if "OPTIMIZER_CONFIG" in part or "config" in part:
                    match = re.search(r'(?:OPTIMIZER_CONFIG(?:[_]PATH)?=)?([^\s]*\.json)', part)
                    if match:
                        config_path = Path(match.group(1))
                        break

            # Try to determine bottleneck from config path
            bottleneck = "unknown"
            benchmark = "unknown"
            phase = "coarse"
            if config_path and config_path.exists():
                try:
                    cfg = json.loads(config_path.read_text())
                    gen_from = cfg.get("_generated_from", {})
                    bottleneck = gen_from.get("bottleneck", gen_from.get("target_resource", "unknown"))
                    benchmark = gen_from.get("benchmark", "unknown")
                    if "calibration" in str(config_path):
                        phase = "calibration"
                except Exception:
                    pass

            state = ExperimentState(
                bottleneck=bottleneck,
                benchmark=benchmark,
                phase=phase,
                pid=pid,
                running=True,
            )

            # Find associated history file
            if config_path:
                try:
                    cfg = json.loads(config_path.read_text())
                    history_rel = cfg.get("logging", {}).get("history_path", "")
                    if history_rel:
                        history_path = PROJECT_ROOT / history_rel
                        if history_path.exists():
                            state.history_path = history_path
                except Exception:
                    pass

            states.append(state)
    except Exception:
        pass
    return states


def find_latest_history(bottleneck: str, phase: str = "coarse") -> Optional[Path]:
    """Find the most recent history JSONL for a bottleneck/phase combo.

    Searches both the top-level history/ directory (old format) and
    history/pg_benchmark_bottleneck_matrix/* (new format).
    """
    history_dir = PROJECT_ROOT / "history"
    candidates = []

    # Old format: history/*bottleneck*_history_*.jsonl
    for path in history_dir.glob("*.jsonl"):
        match = HISTORY_PATTERN.search(path.name)
        if match and match.group("bottleneck") == bottleneck:
            if match.group("phase") == phase or phase == "any":
                candidates.append(path)

    # New format: history/pg_benchmark_bottleneck_matrix/*/bottleneck/*.jsonl
    if MATRIX_DIR.is_dir():
        for run_dir in sorted(MATRIX_DIR.iterdir(), reverse=True):
            if not run_dir.is_dir():
                continue
            bottleneck_dir = run_dir / bottleneck
            if not bottleneck_dir.is_dir():
                continue
            for path in sorted(bottleneck_dir.glob("*.jsonl"), reverse=True):
                if MATRIX_JSONL_PATTERN.search(path.name):
                    candidates.append(path)
                    break  # Only take the most recent JSONL from each run_dir

    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def find_latest_run_log(bottleneck: str, phase: str = "coarse") -> Optional[Path]:
    """Find the most recent run log for a bottleneck/phase combo.

    Searches both top-level history/ (old format) and
    history/pg_benchmark_bottleneck_matrix/* (new format).
    """
    history_dir = PROJECT_ROOT / "history"
    candidates = []

    # Old format
    for path in history_dir.glob("*.log"):
        match = RUN_LOG_PATTERN.search(path.name)
        if match and match.group("bottleneck") == bottleneck:
            if match.group("phase") == phase:
                candidates.append(path)

    # New format
    if MATRIX_DIR.is_dir():
        for run_dir in sorted(MATRIX_DIR.iterdir(), reverse=True):
            if not run_dir.is_dir():
                continue
            bottleneck_dir = run_dir / bottleneck
            if not bottleneck_dir.is_dir():
                continue
            for path in sorted(bottleneck_dir.glob("*.log"), reverse=True):
                if MATRIX_LOG_PATTERN.search(path.name):
                    candidates.append(path)
                    break

    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def find_latest_summary(bottleneck: str, phase: str = "coarse") -> Optional[Path]:
    """Find the most recent summary JSON for a bottleneck/phase.

    Searches both top-level history/ (old format) and
    history/pg_benchmark_bottleneck_matrix/* (new format, uses history JSONL as summary).
    """
    history_dir = PROJECT_ROOT / "history"
    pattern = re.compile(
        rf"summary_.*{bottleneck}.*{phase}.*\.json$"
    )
    candidates = []
    for path in history_dir.glob("summary_*.json"):
        if pattern.search(path.name):
            candidates.append(path)

    # New format: use the most recent JSONL as summary source
    jsonl = find_latest_history(bottleneck, phase)
    if jsonl and jsonl not in candidates:
        candidates.append(jsonl)

    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def parse_history_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Parse a JSONL history file into a list of iteration records."""
    records = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    return records


def compute_utilization(resource: Dict[str, float], capability: Dict[str, float]) -> Dict[str, float]:
    """Compute resource utilization as ratio of observed / capability."""
    util = {}
    for name, value in resource.items():
        cap = capability.get(name, 0)
        if cap == 0:
            continue
        util[name] = value / cap
    return util


def analyze_experiment(
    bottleneck: str,
    history_path: Optional[Path] = None,
    summary_path: Optional[Path] = None,
) -> ExperimentState:
    """Analyze one experiment's results for off-target conditions."""
    state = ExperimentState(bottleneck=bottleneck, benchmark="hybench", phase="coarse")

    # Try to load summary first (old format: summary_*.json)
    if summary_path and summary_path.exists() and summary_path.suffix == ".json":
        try:
            summary = json.loads(summary_path.read_text())
            # Check if this is old-format summary or new-format JSONL
            if summary.get("event") == "final_summary":
                state.initial_util = {
                    k: v for k, v in (summary.get("initial_utilization") or {}).items()
                }
                state.current_util = {
                    k: v for k, v in (summary.get("final_utilization") or {}).items()
                }
                state.current_score = summary.get("final_score", 0)
                state.latest_iter = summary.get("iterations", 0)
                deg = summary.get("degradation_summary") or {}
                state.degraded_count = deg.get("degraded_count", 0)
                state.max_degradation_pct = deg.get("max_degradation_pct") or 0
                state.current_params = summary.get("optimized_params") or {}
        except Exception:
            pass

    # Load history JSONL for trend analysis (works for both old and new format)
    if history_path and history_path.exists():
        records = parse_history_jsonl(history_path)
        if records:
            # Track capability for utilization calculation
            capability: Dict[str, float] = {}

            # New format: extract from resource_monitor_summary, baseline events, etc.
            for rec in records:
                event = rec.get("event", "")

                # Extract capability from calibration event
                if event == "bottleneck_calibration":
                    cap = rec.get("capability") or {}
                    if cap:
                        capability.update({k: float(v) for k, v in cap.items() if isinstance(v, (int, float))})

                # Extract initial utilization from baseline (utilization_pct is 0-100)
                if event == "baseline_runtime_resource_summary":
                    util_pct = rec.get("utilization_pct") or {}
                    if util_pct:
                        for rname, pct in util_pct.items():
                            if not state.initial_util:
                                state.initial_util[rname] = float(pct) / 100.0
                        if not state.current_util:
                            state.current_util = {k: v / 100.0 for k, v in util_pct.items()}

                # Extract per-iteration resource data
                # utilization_pct is already 0-100 scale
                if event == "resource_monitor_summary":
                    util_pct = rec.get("utilization_pct") or {}
                    it = rec.get("iteration", 0)

                    if util_pct:
                        for rname, pct in util_pct.items():
                            util_val = float(pct) / 100.0
                            if rname not in state.util_trend:
                                state.util_trend[rname] = []
                            state.util_trend[rname].append(util_val)
                            state.current_util[rname] = util_val
                        if it > state.latest_iter:
                            state.latest_iter = it

                # Extract score and degradation
                if event == "degradation_summary":
                    deg = rec.get("summary") or {}
                    state.degraded_count = max(state.degraded_count, deg.get("degraded_count", 0))
                    state.max_degradation_pct = max(state.max_degradation_pct, deg.get("max_degradation_pct") or 0)

                if event == "final_feedback_status":
                    fb = rec.get("status") or {}
                    score = fb.get("final_score", fb.get("current_score"))
                    if score is not None:
                        state.current_score = float(score)
                    util = fb.get("final_utilization") or fb.get("current_utilization") or {}
                    for rname, uval in util.items():
                        state.current_util[rname] = float(uval) / 100.0 if float(uval) > 1 else float(uval)

                # Old format iteration events
                if event == "iteration":
                    util = rec.get("utilization") or rec.get("estimated_utilization") or {}
                    score = rec.get("score")
                    it = rec.get("iteration", -1)
                    params = rec.get("params") or rec.get("current_params") or {}

                    for rname, uval in util.items():
                        if rname not in state.util_trend:
                            state.util_trend[rname] = []
                        state.util_trend[rname].append(float(uval))
                        state.current_util[rname] = float(uval)

                    if score is not None:
                        state.score_trend.append(float(score))
                        state.current_score = float(score)
                    state.latest_iter = max(state.latest_iter, it)
                    if params:
                        state.current_params = {k: float(v) for k, v in params.items() if isinstance(v, (int, float))}

    # Detect alerts
    target = RESOURCE_TARGETS.get(bottleneck)
    if target:
        target_util = state.current_util.get(bottleneck) or state.initial_util.get(bottleneck)
        if target_util is not None:
            hard_pct = target["hard_pct"] / 100.0
            max_pct = target["max_pct"] / 100.0
            min_pct = target["min_pct"] / 100.0
            target_pct_val = target["target_pct"] / 100.0

            if target_util > hard_pct:
                state.alerts.append(
                    f"CRITICAL: {bottleneck} utilization {target_util:.1%} exceeds "
                    f"hard overload threshold {hard_pct:.1%}"
                )
            elif target_util > max_pct:
                state.alerts.append(
                    f"WARNING: {bottleneck} utilization {target_util:.1%} above "
                    f"target max {max_pct:.1%}"
                )
            elif target_util < min_pct:
                state.alerts.append(
                    f"WARNING: {bottleneck} utilization {target_util:.1%} below "
                    f"target min {min_pct:.1%}"
                )

        # Check non-target resources
        for rname in ["cpu", "memory", "io"]:
            if rname == bottleneck:
                continue
            util_val = state.current_util.get(rname) or state.initial_util.get(rname)
            if util_val is not None:
                rt = RESOURCE_TARGETS.get(rname, {})
                hard = rt.get("hard_pct", 90) / 100.0
                if util_val > hard:
                    state.alerts.append(
                        f"WARNING: non-target {rname} at {util_val:.1%} exceeds hard limit {hard:.1%}"
                    )

    # Check degradation — high degradation makes any experiment off-target
    if state.degraded_count > DEGRADATION_MAX_COUNT:
        state.alerts.append(
            f"CRITICAL: {state.degraded_count} degraded queries "
            f"(threshold: {DEGRADATION_MAX_COUNT})"
        )
    if state.max_degradation_pct > DEGRADATION_MAX_PCT:
        state.alerts.append(
            f"CRITICAL: max degradation {state.max_degradation_pct:.1f}% "
            f"exceeds {DEGRADATION_MAX_PCT}%"
        )

    # Check param boundary hits — indicates optimizer is stuck
    if state.params_at_boundary:
        state.alerts.append(
            f"WARNING: params at boundary: {', '.join(sorted(state.params_at_boundary))}"
        )

    # Check param boundary hits
    if state.params_at_boundary:
        state.alerts.append(
            f"WARNING: params at boundary: {', '.join(sorted(state.params_at_boundary))}"
        )

    return state


def check_experiment_health(state: ExperimentState) -> Tuple[bool, str]:
    """Check if an experiment is healthy and on-target.

    Returns (healthy, reason).
    """
    if not state.alerts:
        return True, "on track"

    critical = [a for a in state.alerts if a.startswith("CRITICAL")]
    if critical:
        return False, "; ".join(critical)

    return True, "; ".join(state.alerts)


def suggest_corrections(state: ExperimentState) -> List[str]:
    """Suggest corrective actions for an off-target experiment."""
    corrections = []

    target = RESOURCE_TARGETS.get(state.bottleneck)
    if not target:
        return corrections

    target_util = state.current_util.get(state.bottleneck) or state.initial_util.get(state.bottleneck)
    if target_util is None:
        return corrections

    hard_pct = target["hard_pct"] / 100.0
    max_pct = target["max_pct"] / 100.0
    min_pct = target["min_pct"] / 100.0

    # 1. If target resource is overloaded, suggest reducing capability
    if target_util > hard_pct:
        corrections.append(
            f"Target {state.bottleneck} at {target_util:.1%} >> hard limit {hard_pct:.1%}. "
            f"Consider: (a) lowering capability to make target constraint binding, "
            f"(b) increasing --target-resource-weight to strengthen gradient pull, "
            f"(c) verifying sensitivity table signs for {state.bottleneck} bottleneck."
        )

    # 2. If target resource is above max but below hard, suggest tightening
    elif target_util > max_pct:
        corrections.append(
            f"Target {state.bottleneck} at {target_util:.1%} > max {max_pct:.1%}. "
            f"Consider: increasing --gradient-eta or adding more optimizer iterations."
        )

    # 3. If below min
    elif target_util < min_pct:
        corrections.append(
            f"Target {state.bottleneck} at {target_util:.1%} < min {min_pct:.1%}. "
            f"Consider: raising capability or reducing --target-resource-weight."
        )

    # 4. If high degradation
    if state.degraded_count > DEGRADATION_MAX_COUNT:
        corrections.append(
            f"High degradation ({state.degraded_count} queries). "
            f"Consider: (a) enabling param_ranges to bound search space, "
            f"(b) reducing --gradient-max-param-delta-rel-pct, "
            f"(c) enabling --degradation-check in final_evaluation."
        )

    # 5. If params at boundary
    if state.params_at_boundary:
        corrections.append(
            f"Params at boundary: {state.params_at_boundary}. "
            f"Consider: widening param_ranges or checking if sensitivity signs are correct."
        )

    # 6. Sensitivity table check
    if state.bottleneck in ("cpu", "memory"):
        corrections.append(
            f"Note: {state.bottleneck} sensitivity table was recently added. "
            f"Monitor to verify gradient direction pushes {state.bottleneck} toward target band."
        )

    return corrections


def generate_rerun_command(
    bottleneck: str,
    args: argparse.Namespace,
    extra_args: Optional[List[str]] = None,
) -> List[str]:
    """Generate the command to rerun a specific bottleneck experiment."""
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "shell/run_pg_benchmark_bottleneck_matrix.py"),
        "--scenario-set", "bonect",
        "--run",
        "--use-resource-estimator",
        "--run-id", datetime.now().strftime("%Y%m%d_%H%M%S"),
        "--optimizer-mode", "gradient",
        "--score-mode", "band_target",
        "--target-resource-weight", str(args.target_resource_weight),
        "--optimizer-max-iter", str(args.optimizer_max_iter),
        "--post-convergence-explore-iter", str(args.post_convergence_explore_iter),
        "--gradient-eta", str(args.gradient_eta),
        "--gradient-max-param-delta-rel-pct", str(args.gradient_max_param_delta_rel_pct),
        "--final-feedback-max-rounds", str(args.final_feedback_max_rounds),
        "--warmup-timeout-sec", str(args.warmup_timeout_sec),
        "--validation-warmup-timeout-sec", str(args.validation_warmup_timeout_sec),
        "--hybench-run-mins", str(args.hybench_run_mins),
    ]
    if extra_args:
        cmd.extend(extra_args)
    return cmd


def monitor_loop(args: argparse.Namespace) -> int:
    """Main monitoring loop."""
    print("=" * 80)
    print(f"Bottleneck Experiment Monitor — started {datetime.now().isoformat()}")
    print(f"Poll interval: {args.poll_interval_sec}s")
    print(f"Auto-correct: {'enabled' if args.auto_correct else 'disabled'}")
    print(f"Auto-rerun: {'enabled' if args.auto_rerun else 'disabled'}")
    print("=" * 80)

    bottlenecks = ["io", "memory", "cpu"]
    last_check: Dict[str, datetime] = {}
    rerun_cooldown: Dict[str, datetime] = {}
    rerun_count: Dict[str, int] = {b: 0 for b in bottlenecks}
    MAX_RERUNS = args.max_reruns

    while True:
        now = datetime.now()
        print(f"\n[{now.strftime('%H:%M:%S')}] --- Polling ---")

        # 1. Check for running experiments
        running = find_running_experiments()
        running_bottlenecks = {r.bottleneck for r in running if r.bottleneck != "unknown"}

        if running:
            print(f"Running experiments: {len(running)}")
            for r in running:
                print(f"  PID {r.pid}: {r.bottleneck}/{r.benchmark} ({r.phase})")
                if r.history_path:
                    print(f"    history: {r.history_path}")
        else:
            print("No running experiments detected.")

        # 2. Analyze each bottleneck
        for bottleneck in bottlenecks:
            # Skip if cooldown active
            cooldown_until = rerun_cooldown.get(bottleneck)
            if cooldown_until and now < cooldown_until:
                remaining = (cooldown_until - now).total_seconds()
                print(f"  [{bottleneck}] cooldown: {remaining:.0f}s remaining")
                continue

            # Skip if already running
            if bottleneck in running_bottlenecks:
                print(f"  [{bottleneck}] running — monitoring via live process")
                continue

            # Find latest results
            history_path = find_latest_history(bottleneck)
            summary_path = find_latest_summary(bottleneck)

            if not history_path and not summary_path:
                print(f"  [{bottleneck}] no results found — experiment may not have run yet")
                continue

            # Analyze
            state = analyze_experiment(bottleneck, history_path, summary_path)
            healthy, reason = check_experiment_health(state)

            # Print status
            target_util = state.current_util.get(bottleneck) or state.initial_util.get(bottleneck, 0)
            target = RESOURCE_TARGETS.get(bottleneck, {})
            print(
                f"  [{bottleneck}] util={target_util:.1%} "
                f"(target {target.get('min_pct',0):.0f}-{target.get('max_pct',0):.0f}%) "
                f"score={state.current_score:.3f} "
                f"degraded={state.degraded_count} "
                f"status={'✓' if healthy else '✗'}"
            )
            if not healthy:
                print(f"    Reason: {reason}")
            for alert in state.alerts:
                print(f"    Alert: {alert}")

            # 3. Take corrective action if needed
            if not healthy and args.auto_correct:
                corrections = suggest_corrections(state)
                print(f"    Corrections suggested:")
                for c in corrections:
                    print(f"      → {c}")

                if args.auto_rerun and rerun_count[bottleneck] < MAX_RERUNS:
                    print(f"    Triggering auto-rerun for {bottleneck} "
                          f"(attempt {rerun_count[bottleneck] + 1}/{MAX_RERUNS})")

                    # Build rerun command with corrections
                    extra = []
                    # If target util is way above hard limit, increase weight
                    hard_pct = target.get("hard_pct", 90) / 100.0
                    if target_util > hard_pct:
                        extra.extend(["--target-resource-weight", str(args.target_resource_weight * 1.5)])
                        extra.extend(["--gradient-eta", str(args.gradient_eta * 0.8)])

                    # If high degradation, reduce step size
                    if state.degraded_count > DEGRADATION_MAX_COUNT:
                        extra.extend([
                            "--gradient-max-param-delta-rel-pct",
                            str(max(5, args.gradient_max_param_delta_rel_pct * 0.5)),
                        ])

                    rerun_cmd = generate_rerun_command(bottleneck, args, extra)
                    print(f"    Command: {' '.join(rerun_cmd)}")

                    if not args.dry_run:
                        try:
                            subprocess.Popen(
                                rerun_cmd,
                                cwd=str(PROJECT_ROOT),
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                            rerun_count[bottleneck] += 1
                            rerun_cooldown[bottleneck] = now + timedelta(
                                seconds=args.rerun_cooldown_sec
                            )
                            print(f"    ✓ Rerun launched (PID will be captured next poll)")
                        except Exception as exc:
                            print(f"    ✗ Failed to launch rerun: {exc}")

            last_check[bottleneck] = now

        # 4. Wait for next poll
        print(f"\nNext poll in {args.poll_interval_sec}s...")
        time.sleep(args.poll_interval_sec)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitor bottleneck experiments and auto-correct off-target runs."
    )
    parser.add_argument(
        "--poll-interval-sec",
        type=int,
        default=int(os.getenv("MONITOR_POLL_SEC", "120")),
        help="Seconds between status checks.",
    )
    parser.add_argument(
        "--auto-correct",
        action="store_true",
        default=True,
        help="Enable auto-correction suggestions.",
    )
    parser.add_argument(
        "--no-auto-correct",
        dest="auto_correct",
        action="store_false",
        help="Disable auto-correction.",
    )
    parser.add_argument(
        "--auto-rerun",
        action="store_true",
        default=False,
        help="Automatically relaunch experiments when corrections are needed.",
    )
    parser.add_argument(
        "--max-reruns",
        type=int,
        default=int(os.getenv("MONITOR_MAX_RERUNS", "3")),
        help="Maximum auto-reruns per bottleneck before giving up.",
    )
    parser.add_argument(
        "--rerun-cooldown-sec",
        type=int,
        default=int(os.getenv("MONITOR_RERUN_COOLDOWN_SEC", "1800")),
        help="Minimum seconds between auto-reruns of the same bottleneck.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report actions but do not launch anything.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one check and exit (no loop).",
    )

    # Pass-through args for rerun command generation
    parser.add_argument("--target-resource-weight", type=float, default=3.0)
    parser.add_argument("--optimizer-max-iter", type=int, default=100)
    parser.add_argument("--post-convergence-explore-iter", type=int, default=20)
    parser.add_argument("--gradient-eta", type=float, default=0.2)
    parser.add_argument("--gradient-max-param-delta-rel-pct", type=float, default=20)
    parser.add_argument("--final-feedback-max-rounds", type=int, default=30)
    parser.add_argument("--warmup-timeout-sec", type=int, default=600)
    parser.add_argument("--validation-warmup-timeout-sec", type=int, default=600)
    parser.add_argument(
        "--hybench-run-mins",
        type=int,
        default=int(os.getenv("HYBENCH_RUN_MINS", "5")),
        help="Measured HyBench workload minutes for each real validation/rerun. Use 10 for final confirmation.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.once:
        # Single check mode
        print("=== Bottleneck Experiment Status Check ===\n")
        bottlenecks = ["cpu", "memory", "io"]
        all_healthy = True
        for bottleneck in bottlenecks:
            history_path = find_latest_history(bottleneck)
            summary_path = find_latest_summary(bottleneck)
            run_log = find_latest_run_log(bottleneck)

            print(f"--- {bottleneck.upper()} bottleneck ---")
            if run_log:
                print(f"  Latest run log: {run_log}")
            if history_path:
                print(f"  Latest history: {history_path}")
            if summary_path:
                print(f"  Latest summary: {summary_path}")

            if not history_path and not summary_path:
                print(f"  No results found")
                print()
                continue

            state = analyze_experiment(bottleneck, history_path, summary_path)
            healthy, reason = check_experiment_health(state)

            target_util = state.current_util.get(bottleneck) or state.initial_util.get(bottleneck, 0)
            target = RESOURCE_TARGETS.get(bottleneck, {})
            print(f"  Utilization ({bottleneck}): {target_util:.1%} "
                  f"[target: {target.get('min_pct',0):.0f}-{target.get('max_pct',0):.0f}%]")
            print(f"  Score: {state.current_score:.4f}")
            print(f"  Iterations: {state.latest_iter}")
            print(f"  Degraded queries: {state.degraded_count} "
                  f"(max degradation: {state.max_degradation_pct:.1f}%)")

            for rname, util in state.current_util.items():
                if rname != bottleneck:
                    rt = RESOURCE_TARGETS.get(rname)
                    if rt:
                        in_band = rt["min_pct"] / 100 <= util <= rt["max_pct"] / 100
                        print(f"  {rname}: {util:.1%} "
                              f"[{rt['min_pct']:.0f}-{rt['max_pct']:.0f}%] "
                              f"{'✓' if in_band else '✗'}")

            print(f"  Status: {'✓ HEALTHY' if healthy else '✗ OFF-TARGET'}")
            if state.alerts:
                for alert in state.alerts:
                    print(f"    {alert}")

            if not healthy:
                all_healthy = False
                corrections = suggest_corrections(state)
                print(f"  Suggested corrections:")
                for c in corrections:
                    print(f"    → {c}")

                # Auto-rerun in --once mode (skip if already running)
                running_exps = find_running_experiments()
                running_bn = {r.bottleneck for r in running_exps if r.bottleneck != "unknown"}
                if args.auto_rerun and args.auto_correct:
                    if bottleneck in running_bn or "all" in running_bn:
                        print(f"  Skipping auto-rerun: experiment already running for {bottleneck}")
                        continue
                    extra = []
                    target_util = state.current_util.get(bottleneck) or state.initial_util.get(bottleneck, 0)
                    hard_pct = target.get("hard_pct", 90) / 100.0
                    if target_util > hard_pct:
                        extra.extend(["--target-resource-weight", str(args.target_resource_weight * 1.5)])
                        extra.extend(["--gradient-eta", str(round(args.gradient_eta * 0.8, 3))])
                    if state.degraded_count > DEGRADATION_MAX_COUNT:
                        extra.extend([
                            "--gradient-max-param-delta-rel-pct",
                            str(max(5, int(args.gradient_max_param_delta_rel_pct * 0.5))),
                        ])
                    rerun_cmd = generate_rerun_command(bottleneck, args, extra)
                    print(f"  Auto-rerun command: {' '.join(rerun_cmd)}")
                    if not args.dry_run:
                        try:
                            subprocess.Popen(
                                rerun_cmd,
                                cwd=str(PROJECT_ROOT),
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                            print(f"  ✓ Rerun launched for {bottleneck}")
                        except Exception as exc:
                            print(f"  ✗ Failed to launch rerun: {exc}")

            print()

        if all_healthy:
            print("✓ All experiments on target.")
        else:
            print("✗ Some experiments are off-target. Reruns launched if --auto-rerun was set.")
            return 1
    else:
        # Continuous monitoring
        try:
            monitor_loop(args)
        except KeyboardInterrupt:
            print("\nMonitoring stopped by user.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
