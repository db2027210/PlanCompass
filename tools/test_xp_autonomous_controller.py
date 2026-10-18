#!/usr/bin/env python3
"""Zero-call mock tests for xp_autonomous_controller.py."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path


CONTROLLER_PATH = Path(__file__).with_name("xp_autonomous_controller.py")
spec = importlib.util.spec_from_file_location("xp_controller", CONTROLLER_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot import XP controller")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def candidate(index: int) -> dict:
    params = {
        "seq_page_cost": 0.1 + index * 0.01,
        "random_page_cost": 4.0 + index,
        "cpu_tuple_cost": 0.001 + index * 0.0001,
        "cpu_index_tuple_cost": 0.0005 + index * 0.0001,
        "cpu_operator_cost": 0.00025 + index * 0.00005,
        "parallel_setup_cost": 1000.0,
        "parallel_tuple_cost": 0.03 + index * 0.01,
    }
    return {
        "rank": index,
        "replay_rank": index if index <= 5 else None,
        "optimized_params": params,
        "three_level_fingerprint": {
            "id": "three-level-sha256:"
            + hashlib.sha256(f"candidate-{index}".encode()).hexdigest()
        },
        "workload_plan_fingerprint": f"plan-{index}",
        "feature_ratio": {"q1": 1.0 + index / 10.0},
        "predicted_resource": {"cpu": 4.0, "io": 0.8, "memory": 12.0},
        "utilization": {"cpu": 0.4, "io": 0.8, "memory": 0.4},
        "dScore/dU": {"io": 1.0},
        "T": {name: 1.0 for name in params},
        "confidence": 0.95,
        "g_j": {name: -1.0 for name in params},
        "eta": 0.0051,
        "delta_param": {name: 0.01 for name in params},
        "candidate_score": 10.0 - index,
    }


def make_source(root: Path, *, reverse_actual: bool = False) -> Path:
    source = root / "source_batch"
    (source / "state").mkdir(parents=True)
    rankings = [candidate(index) for index in range(1, 11)]
    config = {
        "formal_outer_protocol": {
            "accepted_shrink_target": 10,
            "candidate_replay_top_k": 5,
            "formal_search_initial_replay_top_k": 5,
            "formal_search_max_replay_top_k": 5,
            "excluded_prior_three_level_fingerprints": [],
            "formal_search_accepted_count_start": 4,
            "formal_search_replay_count_start": 20,
            "formal_search_maximum_replay_count": 25,
            "maximum_cumulative_real_replay_count": 25,
        },
        "logging": {"history_path": str(source / "optimizer_history.jsonl")},
        "optimizer": {
            "gradient": {
                "subset_log_dir": str(source / "subset_logs"),
                "plan_basin_escape": {
                    "state_path": str(source / "state" / "plan_basin_escape_state.json")
                },
            }
        },
        "benchmark": {
            "workload_cmd": (
                f"python {source / 'xp_candidate_exclusion_guard.py'} "
                "/bin/false"
            )
        },
    }
    write_json(source / "config.json", config)
    write_json(
        source / "mock_offline_top5.json",
        {"candidates": rankings[:5]},
    )
    (source / "parameter_optimizer_xp_cont16b.py").write_text(
        "# isolated mock optimizer\n", encoding="utf-8"
    )
    (source / "xp_candidate_exclusion_guard.py").write_text(
        (
            f'OUTPUT = "{source}"\n'
            'TOP5 = "cont16b_offline_top5.json"\n'
        ),
        encoding="utf-8",
    )
    for name, value in (
        ("formal_search_accepted_counter.txt", 5),
        ("formal_search_replay_counter.txt", 25),
        ("global_replay_counter.txt", 25),
        ("formal_startup_baseline_counter.txt", 0),
    ):
        (source / "state" / name).write_text(f"{value}\n", encoding="utf-8")
    write_json(
        source / "state" / "plan_basin_escape_state.json",
        {
            "accepted_shrinks": 5,
            "batch_index": 5,
            "batch_name": "mock",
            "status": "mock",
            "basin_exhausted": False,
        },
    )
    events = [
        {
            "event": "experience_table_all_subset_ranking",
            "round_index": 0,
            "candidate_rankings": rankings,
        }
    ]
    for index in range(1, 6):
        events.append({
            "event": "formal_feedback_physical_call_reserved",
            "candidate_index": index,
            "formal_search_replay_before": 19 + index,
            "formal_search_replay_after": 20 + index,
            "global_replay_before": 19 + index,
            "global_replay_after": 20 + index,
            "technical_failure_consumes_reserved_call": True,
            "accepted_counter_updated": False,
            "gradient_incumbent_or_range_updated": False,
        })
    evaluations = []
    for index, item in enumerate(rankings[:5], start=1):
        actual_score = (
            float(index)
            if reverse_actual
            else float(item["candidate_score"])
        )
        evaluations.append({
            "candidate_index": index,
            "estimated_rank": index,
            "estimated_score": 0.0,
            "candidate_actual_score": actual_score,
            "accepted_by_actual_score": index == 1,
            "common_outer_acceptance": {"accepted": index == 1},
            "candidate_optimized_params": copy.deepcopy(item["optimized_params"]),
            "candidate_final_validation": {
                "resource_normalized": {
                    "cpu": 4.0,
                    "io": 0.8,
                    "memory": 12.0,
                }
            },
        })
    events.extend([
        {
            "event": "experience_table_gradient_initial_real_replay_complete",
            "candidate_evaluations": evaluations,
        },
        {"event": "optimizer_process_exit", "status": "atexit"},
    ])
    history = source / "optimizer_history_2026-07-29_235959.jsonl"
    history.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    return source


def campaign(root: Path, source: Path) -> Path:
    value = {
        "contract": module.CONTROLLER_CONTRACT,
        "accepted_target": 10,
        "physical_cap_per_batch": 5,
        "cpu_affinity": module.CPU_AFFINITY,
        "qps_used_for_inner_scoring": False,
        "manual_promotion_allowed": False,
        "current_batch_root": str(source),
        "package_root": str(root / "packages"),
        "target_parent": str(root / "targets"),
        "serial_lock_path": str(root / "xp.lock"),
        "baseline_restore_argv": ["/bin/true"],
        "baseline_verify_argv": ["/bin/true"],
        "baseline_expected": {
            "cpu_index_tuple_cost": "0.005",
            "cpu_operator_cost": "0.0025",
            "cpu_tuple_cost": "0.01",
            "parallel_setup_cost": "1000",
            "parallel_tuple_cost": "0.1",
            "random_page_cost": "4",
            "seq_page_cost": "0.2",
        },
        "stability": {
            "consecutive_batch_window": 1,
            "minimum_trusted_count": 3,
            "minimum_spearman": 0.0,
            "maximum_resource_error_factor": 10.0,
            "maximum_gradient_sign_reversal_rate": 0.5,
        },
    }
    path = root / "campaign.json"
    write_json(path, value)
    return path


with tempfile.TemporaryDirectory(prefix="xp_autonomous_zero_call_") as raw:
    root = Path(raw)
    source = make_source(root)
    campaign_path = campaign(root, source)
    state_path = root / "state.json"
    controller = module.Controller(campaign_path, state_path)
    state = controller.run()
    assert state["phase"] == "serial_launch", state
    assert state["mode"] == "dry_run"
    assert state["last_package"]["zero_call_preflight"]["status"] == "passed"
    assert state["last_package"]["zero_call_preflight"]["physical_calls"] == 0
    assert not (root / "targets" / "xp_autonomous_batch_0001").exists()
    package = Path(state["last_package"]["package_root"])
    assert package.is_dir()
    selected = read_json = json.loads(
        (package / "sealed_top5.json").read_text(encoding="utf-8")
    )
    assert len(selected["candidates"]) == 5
    assert selected["candidates"][0]["source_rank"] == 6

with tempfile.TemporaryDirectory(prefix="xp_autonomous_negative_") as raw:
    root = Path(raw)
    source = make_source(root, reverse_actual=True)
    campaign_path = campaign(root, source)
    controller = module.Controller(campaign_path, root / "state.json")
    state = controller.run()
    assert state["phase"] == "diagnostic_fail_closed"
    assert state["manual_promotion_allowed"] is False
    assert "trusted_spearman" in state["last_error"]

with tempfile.TemporaryDirectory(prefix="xp_autonomous_arm_") as raw:
    root = Path(raw)
    source = make_source(root)
    campaign_path = campaign(root, source)
    bad_manifest = root / "armed.json"
    write_json(
        bad_manifest,
        {
            "contract": module.CONTROLLER_CONTRACT,
            "mode": "armed",
            "allow_local_process_execution": True,
            "cpu_affinity": module.CPU_AFFINITY,
            "manual_promotion_allowed": False,
            "controller_sha256": "0" * 64,
            "campaign_sha256": module.sha256(campaign_path),
        },
    )
    try:
        module.verify_armed_manifest(
            bad_manifest, campaign_path, CONTROLLER_PATH
        )
    except module.ContractFailure:
        pass
    else:
        raise AssertionError("bad armed manifest SHA was accepted")

for forbidden in (["ssh", "node01"], ["mysql", "-e", "select 1"]):
    try:
        module.safe_local_command(forbidden, 1)
    except module.ContractFailure:
        pass
    else:
        raise AssertionError(f"forbidden command accepted: {forbidden}")

print(json.dumps({
    "status": "passed",
    "physical_calls": 0,
    "workload_started": False,
    "dry_run_stopped_before_launch": True,
    "pass_gate_generated_novel_top5": True,
    "negative_spearman_failed_closed": True,
    "manual_promotion_forbidden": True,
    "armed_sha_mismatch_blocked": True,
    "ssh_blocked": True,
    "mysql_blocked": True,
}, sort_keys=True))
