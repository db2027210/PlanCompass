#!/usr/bin/env python3
"""Prepare and run only JOB/XP-I/O C, followed by frozen same-domain comparisons."""

from __future__ import annotations

import argparse
import copy
import fcntl
import json
import math
import os
import random
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_plan_resource_tuner import pg_conn_from_worker, psql_query_with_transient_retry
from shell import prepare_pg_algorithm_c_budgeted_formal as preparation
from shell import run_pg_common_domain_frozen_champions as replay
from shell.pg_algorithm_c_isolated_guard import (
    CONTRACT, UNCONTROLLED_EXTERNAL_POLICY, IsolatedWaveGuard, isolated_environment,
)
from shell.pg_algorithm_c_efficiency import EfficiencyRecorder
from shell.run_pg_algorithm_c_budgeted_scene import BudgetedLaneController

SCENES = ("xp_io", "job_memory")
TARGETS = {"xp_io": "io", "job_memory": "memory"}
PAPER_ROOT = preparation.DEFAULT_PAPER_ABC_MODEL_ROOT
R9_ROOT = Path("/disk1/resource_adaptive/pg_paper_domain_isolated_abc_20260915/r9_observed_persistence_fix")
EXTRA_SOURCES = (
    "shell/run_pg_algorithm_c_job_io_revision.py", "shell/pg_algorithm_c_isolated_guard.py",
    "shell/pg_algorithm_c_efficiency.py",
    "shell/pg_fixed_guc_guard.py", "shell/run_pg_common_domain_frozen_champions.py",
    "shell/run_pg_job_fixed_workload_isolated.sh", "shell/run_hybench_native_xp_isolated.sh",
    "shell/run_pg_full_workload_isolated.sh", "shell/run_pg_none_external_baseline.py",
)


class InstrumentedLaneController(BudgetedLaneController):
    """Keep decision logic unchanged; report costs at existing checkpoints."""

    def save_state(self) -> None:
        super().save_state()
        recorder = getattr(self, "efficiency_recorder", None)
        if recorder is not None:
            recorder.snapshot(self.state)


def prepare(root: Path) -> Dict[str, Any]:
    manifest = preparation.prepare(
        PAPER_ROOT, root, "job_io_outer_boundary_refinement_v3", plan_portfolio=True,
        resource_model_mode=preparation.RESOURCE_MODEL_PAPER_ABC,
        selected_scenes=SCENES, preserve_worker_cpus=True,
    )
    manifest["isolated_search_contract"] = CONTRACT
    manifest["scene_order"] = list(SCENES)
    manifest["physical_scenes_in_parallel"] = 1
    manifest["paper_fixed_configuration_retained"] = True
    for relative in EXTRA_SOURCES:
        manifest["source_sha256"][relative] = preparation.sha256(ROOT / relative)
    for scene in SCENES:
        entry = manifest["scenes"][scene]
        path = Path(entry["config"])
        cfg = preparation.read_json(path)
        algorithm = cfg["algorithm_c"]
        reference = preparation.read_json(R9_ROOT / scene / "authority_manifest.json")
        expected = reference["fixed_non_search_guc_sha256"][scene]
        algorithm["isolated_search"] = {
            "contract": CONTRACT, "enabled": True,
            "external_workload_guard_required": False,
            "external_workload_interference_user_accepted": True,
            "external_workload_interference_policy": UNCONTROLLED_EXTERNAL_POLICY,
            "post_restore_quiet_required": True,
            "expected_non_search_guc_sha256": expected,
            "reference_receipt": reference["final_guc_receipts"][scene]["path"],
            "reference_receipt_sha256": reference["final_guc_receipts"][scene]["receipt_sha256"],
        }
        workload = algorithm["workload_contract"]
        workload["baseline_cohort"].update({"lane_count": 1, "lanes": [scene]})
        workload["candidate_cohort"] = {
            "contract": "algorithm_c_guarded_isolated_candidate_waves_v1",
            "lane_count": 1, "lanes": [scene], "synchronization": "none_between_lanes",
        }
        entry["isolated_search"] = copy.deepcopy(algorithm["isolated_search"])
        preparation.write_json(path, cfg)
        entry["config_sha256"] = preparation.sha256(path)
    preparation.write_json(root / "budgeted_launch_manifest.json", manifest)
    preparation.write_json(root / "isolated_launch_manifest.json", manifest)
    return manifest


def verify_sealed(root: Path) -> Dict[str, Any]:
    manifest = preparation.read_json(root / "isolated_launch_manifest.json")
    if manifest.get("scene_order") != list(SCENES) or set(manifest.get("scenes", {})) != set(SCENES):
        raise RuntimeError("this entry point requires exactly XP-I/O and JOB-Memory")
    if manifest.get("isolated_search_contract") != CONTRACT:
        raise RuntimeError("isolated search contract mismatch")
    for relative, expected in manifest["source_sha256"].items():
        if preparation.sha256(ROOT / relative) != expected:
            raise RuntimeError(f"sealed runtime source changed: {relative}")
    for scene, entry in manifest["scenes"].items():
        if preparation.sha256(Path(entry["config"])) != entry["config_sha256"]:
            raise RuntimeError(f"sealed config changed: {scene}")
        cfg = preparation.read_json(Path(entry["config"]))
        if preparation.sha256(Path(cfg["algorithm_c"]["experience_table_path"])) != entry["model_sha256"]:
            raise RuntimeError(f"sealed resource model changed: {scene}")
        preparation.verify_paper_abc_config_boundary(cfg, scene)
    return manifest


def scene_contract(root: Path, scene: str) -> Dict[str, Any]:
    path = root / scene / "optimizer_config.json"
    cfg = preparation.read_json(path)
    return {
        "cfg": cfg, "config_path": str(path), "config_sha256": preparation.sha256(path),
        "target": TARGETS[scene], "workers": replay.validation_workers(cfg, TARGETS[scene]),
        "defaults": replay.exact_params(cfg["algorithm_c"]["initial_params"], source=scene),
    }


def run_scene(root: Path, scene: str) -> None:
    verify_sealed(root)
    recorder = EfficiencyRecorder(root / scene)
    phase = "failed"
    try:
        _run_scene(root, scene, recorder)
        phase = "search_completed"
    finally:
        # Preserve a final cost receipt even if startup or the controller fails.
        state_path = root / scene / "algorithm_c_state.json"
        state = preparation.read_json(state_path) if state_path.exists() else {}
        recorder.snapshot(state, phase=phase)


def _run_scene(root: Path, scene: str, recorder: EfficiencyRecorder) -> None:
    os.environ.update(isolated_environment(scene, root))
    contracts = {scene: scene_contract(root, scene)}
    output = root / scene
    stopped = [False]
    signal.signal(signal.SIGTERM, lambda *_: stopped.__setitem__(0, True))
    signal.signal(signal.SIGINT, lambda *_: stopped.__setitem__(0, True))
    recorder.snapshot({}, phase="startup")
    isolated_cfg = contracts[scene]["cfg"]["algorithm_c"]["isolated_search"]
    if isolated_cfg.get("external_workload_guard_required") is True:
        with recorder.measure("external_quiet_wait", "startup"):
            ready = replay.wait_for_external_quiet(
                output, "startup", quiet_s=120.0, should_stop=lambda: stopped[0],
            )
        if not ready:
            raise RuntimeError("startup cancelled")
    replay.write_json(output / "container_start_receipt.json", replay.ensure_declared_containers_running(contracts))
    replay.audit_container_resource_domain(contracts)
    for worker in contracts[scene]["workers"]:
        pg = pg_conn_from_worker(contracts[scene]["cfg"], worker)
        sql = ("SELECT count(*) FROM pg_stat_activity WHERE pid <> pg_backend_pid() "
               "AND backend_type = 'client backend' AND state IS DISTINCT FROM 'idle'")
        if psql_query_with_transient_retry(pg, sql, dbname="postgres", attempts=1).strip() != "0":
            raise RuntimeError(f"worker has an active client before ownership handoff: {worker['name']}")
    controller = InstrumentedLaneController(output / "optimizer_config.json", output, TARGETS[scene])
    controller.efficiency_recorder = recorder
    controller.physical_wave_guard = IsolatedWaveGuard(controller)
    controller.physical_wave_guard.audit("startup")
    signal.signal(signal.SIGTERM, lambda *_: setattr(controller, "stop_requested", True))
    signal.signal(signal.SIGINT, lambda *_: setattr(controller, "stop_requested", True))
    try:
        controller.run()
    except Exception as exc:
        controller.state["isolated_run_failure"] = {"error": str(exc), "time": time.time()}
        controller.physical_wave_guard.close()
        try:
            cleanup = controller.restore_clean_baseline(reason="isolated_search_failure")
            controller.state["isolated_failure_cleanup"] = cleanup
        except Exception as cleanup_error:
            controller.state["isolated_failure_cleanup"] = {"passed": False, "error": str(cleanup_error)}
        controller.save_state()
        raise
    finally:
        controller.physical_wave_guard.close()
    result = preparation.read_json(output / controller.execution_role.result_filename)
    if result.get("status") != "search_completed" or result["trial"].get("search_completed_successfully") is not True:
        raise RuntimeError(f"C search did not complete successfully: {scene}")


def comparison_methods(root: Path, scene: str) -> Dict[str, Any]:
    """Read frozen B/old-C points only after the two new C searches finish."""
    result_path = root / scene / "budgeted_formal_result.json"
    result = preparation.read_json(result_path)
    if result.get("status") != "search_completed" or result["trial"].get("search_completed_successfully") is not True:
        raise RuntimeError("cannot compare an incomplete C search")
    old_b_root = Path("/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/20260823_algorithm_b_training_boundary_clean_r12")
    paths = {"algorithm_b": old_b_root / scene / "authority_manifest.json",
             "algorithm_c_previous": PAPER_ROOT / scene / "authority_manifest.json"}
    methods = {}
    for method, path in paths.items():
        authority = preparation.read_json(path)
        if authority.get("scene") != scene:
            raise RuntimeError("comparison authority scene mismatch")
        methods[method] = {"params": replay.champion_params(authority, "algorithm_c" if method.endswith("previous") else method),
                           "authority_path": str(path), "authority_file_sha256": preparation.sha256(path)}
    methods["algorithm_c"] = {
        "params": replay.exact_params(result["trial"]["selection"]["params"], source="new C frozen selection"),
        "authority_path": str(result_path), "authority_file_sha256": preparation.sha256(result_path),
    }
    return methods


def compare(root: Path, scene: str, repetition: int, should_stop) -> Dict[str, Any]:
    contract = scene_contract(root, scene)
    contract["methods"] = comparison_methods(root, scene)
    contracts = {scene: contract}
    output = root / "comparison" / scene / f"repetition_{repetition:02d}"
    if output.exists():
        raise RuntimeError("refusing to overwrite a comparison repetition")
    output.mkdir(parents=True)
    methods = list(contract["methods"])
    seed = 20260915 + repetition
    random.Random(seed).shuffle(methods)
    stages, brackets = ["common_baseline_00"], {}
    for index, method in enumerate(methods):
        after = f"common_baseline_{index + 1:02d}"
        brackets[method] = (stages[-1], after)
        stages.extend([method, after])
    expected = {scene: contract["cfg"]["algorithm_c"]["isolated_search"]["expected_non_search_guc_sha256"]}
    isolated_cfg = contract["cfg"]["algorithm_c"]["isolated_search"]
    external_guard_enabled = isolated_cfg.get("external_workload_guard_required") is True
    declaration = {"scene": scene, "seed": seed, "stages": stages, "methods": contract["methods"],
                   "initial_baseline_reference": "common_baseline_00", "search_performed": False,
                   "cfg_sha256": contract["config_sha256"], "workers": [w["name"] for w in contract["workers"]],
                   "external_workload_guard_enabled": external_guard_enabled,
                   "external_workload_interference_policy": isolated_cfg.get("external_workload_interference_policy")}
    replay.write_json(output / "contract.json", declaration)
    state: Dict[str, Any] = {"phase": "running", "stage_results": {}, "physical_replays": 0, "technical_failures": 0}
    for stage in stages:
        params = contract["defaults"] if stage.startswith("common_baseline_") else contract["methods"][stage]["params"]
        for attempt in range(1, 4):
            if should_stop():
                raise RuntimeError("comparison stopped")
            if external_guard_enabled and not replay.wait_for_external_quiet(
                output, stage, quiet_s=120.0, should_stop=should_stop,
            ):
                raise RuntimeError("comparison stopped")
            replay.audit_all_scenes(contracts, output, f"{stage}_a{attempt}_before", expected)
            replay.audit_container_resource_domain(contracts)
            result = replay.run_stage_attempt(
                contracts, output, stage, attempt, {scene: params}, ssd_device="nvme1n1",
                post_restore_ssd_sample_s=15.0, maximum_post_restore_ssd_util_pct=5.0,
                post_restore_ssd_maximum_wait_s=600.0,
                external_workload_guard_enabled=external_guard_enabled,
            )
            state["physical_replays"] += result["scene_results"][scene]["physical_invocations"]
            state["technical_failures"] += result["scene_results"][scene]["technical_failures"]
            replay.append_jsonl(output / "attempts.jsonl", result)
            replay.audit_all_scenes(contracts, output, f"{stage}_a{attempt}_after", expected)
            replay.audit_container_resource_domain(contracts)
            if result["all_valid"]:
                state["stage_results"][stage] = result
                replay.write_json(output / "state.json", state)
                break
        else:
            raise RuntimeError(f"comparison failed after three attempts: {scene}/{stage}")
    comparison, errors = replay.build_scene_comparison(
        state=state, scene=scene, stages=stages, methods=methods,
        method_baseline_brackets=brackets, maximum_baseline_drift_pct=10.0,
    )
    if errors:
        raise RuntimeError(str(errors))
    state.update({"phase": "complete", "comparison": comparison})
    replay.restore_defaults(contracts, output)
    replay.audit_all_scenes(contracts, output, "final_clean_restore", expected)
    replay.write_json(output / "state.json", state)
    return comparison


def launch(root: Path) -> None:
    manifest = verify_sealed(root)
    if (root / "launcher_status.json").exists():
        raise RuntimeError("fresh launcher state required")
    lock = open("/tmp/resource_adaptive_job_io_isolated_search.lock", "a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    status: Dict[str, Any] = {"phase": "search", "started_at_epoch_s": time.time(), "returncodes": {}}
    stopped, active = [False], [None]
    def stop(*_args):
        stopped[0] = True
        if active[0] is not None and active[0].poll() is None:
            active[0].terminate()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    def save():
        replay.write_json(root / "launcher_status.json", status)
    try:
        for scene in SCENES:
            if stopped[0]:
                raise RuntimeError("launcher stopped")
            status["active_scene"] = scene
            entry = manifest["scenes"][scene]
            with (root / scene / "controller.log").open("a") as log:
                args = ["taskset", "-c", entry["controller_cpus"], sys.executable, "-u", __file__,
                        "--output-root", str(root), "--scene", scene]
                active[0] = subprocess.Popen(args, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                status["active_pid"] = active[0].pid
                save()
                status["returncodes"][scene] = active[0].wait()
                save()
            if status["returncodes"][scene] != 0:
                raise RuntimeError(f"search controller failed: {scene}")
        status["phase"] = "comparison"
        active[0] = None
        status.pop("active_pid", None)
        save()
        comparisons = {}
        for scene in SCENES:
            status["active_scene"] = scene
            comparisons[scene] = []
            for repetition in range(1, 4):
                status["active_repetition"] = repetition
                save()
                comparisons[scene].append(compare(root, scene, repetition, lambda: stopped[0]))
        summary = {"contract": "job_io_frozen_comparison_three_repetitions_v1", "scenes": {},
                   "tuning_efficiency_comparison_available": False,
                   "efficiency_limitation": "B and previous C are frozen-point replays, not matched search runs",
                   "external_workloads_controlled": False,
                   "external_workload_interference_policy": UNCONTROLLED_EXTERNAL_POLICY,
                   "external_interference_limitation":
                       "user authorized direct execution without monitoring or excluding external workloads"}
        for scene, results in comparisons.items():
            methods = {}
            for method in ("algorithm_b", "algorithm_c_previous", "algorithm_c"):
                ratios = [1.0 + row["methods"][method]["qps_improvement_pct"] / 100.0 for row in results]
                methods[method] = {"gain_pct_by_repetition": [(value - 1.0) * 100.0 for value in ratios],
                                   "geomean_gain_pct": (math.exp(sum(map(math.log, ratios)) / len(ratios)) - 1.0) * 100.0}
            relative = [row["methods"]["algorithm_c"]["normalized_db_qps"] /
                        row["methods"]["algorithm_b"]["normalized_db_qps"] for row in results]
            summary["scenes"][scene] = {
                "methods": methods,
                "new_c_search_efficiency": preparation.read_json(root / scene / "efficiency.json"),
                "new_c_vs_b_pct_by_repetition": [(value - 1.0) * 100.0 for value in relative],
                "new_c_observed_above_b_each_repetition": all(value > 1.0 for value in relative),
                "statistical_significance_not_assessed": True,
            }
        replay.write_json(root / "comparison_summary.json", summary)
        status["phase"] = "complete"
        status["finished_at_epoch_s"] = time.time()
        save()
    except Exception as exc:
        status.update({"phase": "failed", "error": str(exc), "failed_at_epoch_s": time.time()})
        save()
        raise
    finally:
        lock.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--scene", choices=SCENES)
    args = parser.parse_args()
    root = args.output_root.resolve()
    if args.prepare:
        prepare(root)
    if args.scene:
        run_scene(root, args.scene)
    elif args.launch:
        launch(root)


if __name__ == "__main__":
    main()
