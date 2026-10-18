#!/usr/bin/env python3
"""Prepare a fresh four-scene pilot and audited historical timing references."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from algorithm_c_budget_policy import CONTRACT

SOURCE = Path("/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/20260823_algorithm_c_training_boundary_clean_r75")
SUMMARY = ROOT / "figures/editable_search_time_figures_20260830/formal_results_data.json"
SCENES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def timing_reference(algorithm):
    start, end = math.inf, -math.inf
    baseline_count = 0
    with Path(algorithm["log_path"]).open() as stream:
        for line in stream:
            event = json.loads(line)
            stamp = event.get("time")
            if isinstance(stamp, (float, int)):
                start, end = min(start, stamp), max(end, stamp)
            if event.get("event") == "algorithm_c_baseline_worker_validation":
                baseline_count += 1
    if not math.isfinite(end - start) or end <= start:
        raise ValueError("missing measured historical wall time")
    return {"log_path": algorithm["log_path"], "log_wall_s": end - start,
            "workload_sum_s": algorithm["actual_search_hours"] * 3600,
            "physical_invocations": algorithm["physical_workload_invocations"],
            "baseline_replays": baseline_count,
            "display_time_used": False, "wall_includes_waits_and_restarts": True}


def prepare(destination):
    if destination.exists():
        raise RuntimeError(f"refusing to overwrite existing trial: {destination}")
    data = json.loads(SUMMARY.read_text())["postgresql"]["scenes"]
    timing = {s: {name: timing_reference(value) for name, value in data[s]["algorithms"].items()}
              for s in SCENES}
    destination.mkdir(parents=True)
    (destination / "cohort_support").mkdir()
    (destination / "benchbase_runtime").mkdir()
    manifest = {"contract": CONTRACT, "created_at_epoch_s": time.time(), "source_campaign": str(SOURCE),
                "campaign_root": str(destination), "scenes": {}, "timing_references": timing,
                "fresh_baseline_required": True, "historical_candidates_imported": False,
                "algorithm_a_b_parameters_used": False, "qps_target_used_for_inner_search": False,
                "same_two_workers_per_scene": True, "historical_qps_comparison_provisional": True}
    for scene in SCENES:
        folder = destination / scene
        folder.mkdir()
        cfg = json.loads((SOURCE / scene / "optimizer_config.json").read_text())
        source_model = Path(cfg["algorithm_c"]["experience_table_path"])
        model = folder / source_model.name
        shutil.copy2(source_model, model)
        config = copy.deepcopy(cfg)
        # Original JOB controller CPUs 176-179 are now used by another MySQL
        # experiment. Keep every DB-worker binding unchanged; move only clients.
        client_cpus = {"job_memory": "120-123", "tpch_memory": "124-127",
                       "xp_cpu": "128-131", "xp_io": "144-159"}[scene]
        def update_client_bindings(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == "controller_client_cpuset":
                        value[key] = client_cpus
                    else:
                        update_client_bindings(child)
            elif isinstance(value, list):
                for child in value:
                    update_client_bindings(child)
        update_client_bindings(config)
        algo = config["algorithm_c"]
        algo["experience_table_path"] = str(model)
        for policy_name in ("offline_resource_training_audit", "runtime_resource_model_policy"):
            for key in ("artifact", "experience_table_path"):
                if key in algo.get(policy_name, {}):
                    algo[policy_name][key] = str(model)
        config["binary_plan_tuning"]["output_dir"] = str(folder)
        a, b, c = (timing[scene][f"Algorithm {letter}"] for letter in "ABC")
        wall = .8 * min(a["log_wall_s"], b["log_wall_s"])
        search_cap = min(c["physical_invocations"] - c["baseline_replays"],
                         math.floor(.8 * (b["physical_invocations"] - b["baseline_replays"])))
        timeout = float(config["binary_plan_tuning"]["validation"]["hybench_xp"].get("timeout_s", 1020))
        # Reserve all 4 finalization waves (2 finalist + 2 post-baseline),
        # including restores. Reservations are conservative admission estimates,
        # not a false promise that external waits cannot exceed the deadline.
        reserve = 4 * (timeout + 300.) + 600.
        batch = 3 * (timeout + 300.)
        if wall <= reserve + batch:
            raise RuntimeError(f"{scene}: historical time cannot fit a validated pilot")
        algo["budgeted_trial"] = {
            "contract": CONTRACT, "region_plan_probes": 32, "baseline_drift_limit": .15,
            "budget": {"max_search_replays": max(1, search_cap), "max_wall_s": wall,
                       "max_planning_s": min(1800., wall * .1),
                       "finalization_reserve_s": reserve, "batch_reserve_s": batch},
            "finalist_replays": 4, "post_baseline_replays": 4,
            "budget_scope": "search physical attempts including failures; validation separately charged in wall time",
            "runtime_boundary": "no historical parameters, plans, rankings, or QPS labels imported",
            "termination_is_budget_not_convergence": True,
        }
        algo.pop("historical_valid_replay_seed_path", None)
        write(folder / "optimizer_config.json", config)
        manifest["scenes"][scene] = {"config": str(folder / "optimizer_config.json"),
                                     "policy": algo["budgeted_trial"],
                                     "controller_cpus": algo["controller_client_cpuset"]}
    # Cover executable Python dependencies, including modules imported later.
    sources = sorted(ROOT.glob("*.py")) + sorted((ROOT / "shell").glob("*.py"))
    manifest["source_sha256"] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in sources}
    write(destination / "budgeted_launch_manifest.json", manifest)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--refresh-unstarted-sources", action="store_true")
    args = parser.parse_args()
    if args.refresh_unstarted_sources:
        destination = args.output_root.resolve()
        if any((destination / s / "algorithm_c_state.json").exists() for s in SCENES):
            raise RuntimeError("cannot refresh source declarations after any lane state exists")
        manifest = json.loads((destination / "budgeted_launch_manifest.json").read_text())
        if manifest.get("contract") != CONTRACT:
            raise ValueError("unexpected trial contract")
        previous = manifest["source_sha256"]
        sources = sorted(ROOT.glob("*.py")) + sorted((ROOT / "shell").glob("*.py"))
        current = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
        manifest.setdefault("prebaseline_source_repairs", []).append({
            "time": time.time(), "changed_files": [k for k, v in current.items() if previous.get(k) != v],
            "reason": "startup timeout repair before baseline or search", "previous_source_sha256": previous})
        manifest["source_sha256"] = current
        write(destination / "budgeted_launch_manifest.json", manifest)
    else:
        manifest = prepare(args.output_root.resolve())
    print(json.dumps({"campaign_root": manifest["campaign_root"], "scenes": manifest["scenes"]}, indent=2), flush=True)
