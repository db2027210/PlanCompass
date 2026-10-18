"""Passive cost accounting; these observations never influence C's decisions."""

from __future__ import annotations

import math
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict

from shell import run_pg_common_domain_frozen_champions as replay


class EfficiencyRecorder:
    """Record end-to-end cost and accepted-incumbent progress without changing policy."""

    def __init__(self, output: Path) -> None:
        self.output = output
        self.started = time.perf_counter()
        self.started_epoch = time.time()
        self.durations: Dict[str, float] = {}
        self.active = None
        self.last_progress = None

    @contextmanager
    def measure(self, category: str, label: str):
        if self.active is not None:
            raise RuntimeError("efficiency spans must not overlap")
        started = time.perf_counter()
        self.active = (category, started)
        passed = False
        try:
            yield
            passed = True
        finally:
            elapsed = time.perf_counter() - started
            self.durations[category] = self.durations.get(category, 0.0) + elapsed
            self.active = None
            replay.append_jsonl(self.output / "efficiency_spans.jsonl", {
                "category": category, "label": label, "elapsed_s": elapsed,
                "completed_without_exception": passed,
                "end_to_end_elapsed_s": time.perf_counter() - self.started,
            })

    def snapshot(self, state: Dict[str, Any], *, phase: str = "running") -> Dict[str, Any]:
        elapsed = time.perf_counter() - self.started
        durations = dict(self.durations)
        if self.active is not None:
            category, started = self.active
            durations[category] = durations.get(category, 0.0) + time.perf_counter() - started
        waits = sum(durations.get(key, 0.0) for key in ("external_quiet_wait", "post_restore_quiet_wait"))
        trial = state.get("budgeted_trial") or {}
        baseline = float((state.get("canonical_baseline") or {}).get("db_qps") or 0.0)
        incumbent = float((state.get("incumbent_validation") or {}).get("db_qps") or baseline)
        gain = ((incumbent / baseline - 1.0) * 100.0
                if baseline > 0 and math.isfinite(baseline) and math.isfinite(incumbent) else None)
        progress = {
            "round_index": state.get("round_index"), "accepted_count": int(state.get("accepted") or 0),
            "search_physical_attempts": int(trial.get("search_replays") or 0),
            "search_valid_replays": int(trial.get("search_valid_replays") or 0),
            "search_technical_failures": int(trial.get("search_technical_failures") or 0),
            "total_accounted_physical_invocations": int(state.get("physical_workload_invocations") or 0),
            "initial_baseline_qps": baseline or None, "accepted_incumbent_qps": incumbent or None,
            "accepted_incumbent_gain_pct": gain,
        }
        receipt = {
            "contract": "algorithm_c_passive_efficiency_v1", "phase": phase,
            "started_at_epoch_s": self.started_epoch, "end_to_end_wall_s": elapsed,
            "environment_wait_s": waits, "measured_categories_s": durations,
            "wall_excluding_environment_wait_s": max(0.0, elapsed - waits),
            "controller_planning_s": float(trial.get("planning_s") or 0.0),
            "controller_replay_batch_wall_s_including_waits": float(trial.get("replay_batch_wall_s") or 0.0),
            "controller_baseline_wall_s_including_waits": float(trial.get("baseline_wall_s") or 0.0),
            "progress": progress,
            "timing_scope": "scene_entry_through_clean_restore_excluding_post_search_comparison",
            "wall_excluding_wait_is_not_pure_algorithm_time": True,
            "controller_timers_overlap_end_to_end_and_are_not_additive": True,
            "search_observations_independently_validated": False,
            "old_c_and_b_search_efficiency_comparable": False,
        }
        if progress != self.last_progress:
            replay.append_jsonl(self.output / "efficiency_progress.jsonl", {
                **progress, "end_to_end_wall_s": elapsed,
                "environment_wait_s": waits,
                "wall_excluding_environment_wait_s": max(0.0, elapsed - waits),
                "controller_planning_s": receipt["controller_planning_s"],
            })
            self.last_progress = progress
        replay.write_json(self.output / "efficiency.json", receipt)
        return receipt
