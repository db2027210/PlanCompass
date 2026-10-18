#!/usr/bin/env python3
"""Reproduce post-checkpoint plan pruning and report Top-5 shortages."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mysql_algorithm_c_plan_replay_pruning import rank_and_prune_candidates
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v2 as controller


RUNTIME_PREFLIGHT = ROOT / "preflight/mysql_four_scene_20260809/v18_formal_bounded_prestate_v10_20260814T1000p0800_preflight_attempt_0001.json"
PREPARED = ROOT / "history/mysql_four_scene_20260809/v18_formal_bounded_prestate_v10_20260814T1000p0800_rebind/prepared_binding.json"
ACCOUNTS = ROOT / "history/mysql_four_scene_20260809/v18_formal_bounded_prestate_v10_20260814T1000p0800_accounts/benchmark_accounts_binding.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.parent.exists():
        raise RuntimeError(f"fresh diagnostic output root required: {args.output.parent}")
    args.output.parent.mkdir(parents=True, mode=0o700)
    checkpoint = controller.checkpointmod.verify_checkpoint(
        args.checkpoint.resolve()
    )
    reconstructed = checkpoint["reconstructed_state"]
    optimization = controller.alg.load(controller.OPTIMIZATION_CONTRACT)
    freeze_path = (ROOT / optimization["bindings"]["resource_freeze_path"]).resolve()
    controller.alg.FREEZE = freeze_path
    controller.alg.r0mod.FREEZE = freeze_path
    controller.alg.r0mod.FREEZE_SCHEMA = str(
        controller.alg.load(freeze_path)["schema_version"]
    )
    controller._configure_live_authority(
        RUNTIME_PREFLIGHT, PREPARED, ACCOUNTS,
    )
    freeze, workers, authority = controller._validate_pilot_authority()
    client_log_root = args.output.parent / "plan_ranking_client_processes"
    client_log_root.mkdir(mode=0o700)
    for worker_id, worker in workers.items():
        worker["formal_client_log"] = str(client_log_root / f"{worker_id}.jsonl")
    scenes = list(controller.SCENES)
    futures = {}
    rows = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for scene in scenes:
            representative = sorted(
                worker_id for worker_id, worker in workers.items()
                if worker["scene"] == scene
            )[0]
            futures[pool.submit(
                controller._rank_scene,
                scene=scene,
                params=reconstructed["params_by_scene"][scene],
                ranges=reconstructed["ranges_by_scene"][scene],
                resources=reconstructed["incumbents"][scene]["resource_means"],
                freeze=freeze,
                worker=workers[representative],
            )] = scene
        for future in as_completed(futures):
            scene = futures[future]
            rows[scene] = future.result()

    report = {
        "schema_version": "mysql_algorithm_c_top5_shortage_diagnostic_v1",
        "status": "PASS",
        "checkpoint": str(args.checkpoint.resolve()),
        "checkpoint_sha256": controller.alg.sha(args.checkpoint.resolve()),
        "authority": authority,
        "scenes": {},
        "workload_replay_started": False,
        "accepted_state_credit_increment": 0,
        "rejected_outer_attempt_increment": 0,
    }
    for scene in scenes:
        ranked = rows[scene]["rankings"]
        kept, pruned, audit = rank_and_prune_candidates(
            ranked,
            reconstructed["full_plan_replay_history_v2"][scene],
        )
        unique = {
            controller.alg.optimizer_params_key(row["optimized_params"])
            for row in kept
        }
        representative_worker = workers[rows[scene]["representative_worker_id"]]

        def supplementary_factory(
            step_fraction: float, direction_mode: str,
        ) -> list[dict]:
            return controller.alg.candidate_rankings(
                scene,
                reconstructed["params_by_scene"][scene],
                reconstructed["ranges_by_scene"][scene],
                reconstructed["incumbents"][scene]["resource_means"],
                freeze=freeze, worker=representative_worker,
                step_fraction=step_fraction,
                direction_mode=direction_mode,
            )

        combined, selected, selection_audit, final_pruned, final_pruning_audit = (
            controller.supplement_and_select_fixed_top_k(
                base_rankings=ranked,
                plan_history=reconstructed["full_plan_replay_history_v2"][scene],
                replayed_param_keys=set(
                    reconstructed["replayed_param_keys_by_scene"][scene]
                ),
                permanently_invalid_param_keys=set(),
                supplementary_factory=supplementary_factory,
            )
        )
        selected_keys = {
            controller.alg.optimizer_params_key(row["optimized_params"])
            for row in selected
        }
        report["scenes"][scene] = {
            "representative_worker_id": rows[scene]["representative_worker_id"],
            "ranking_elapsed_seconds": rows[scene]["elapsed_seconds"],
            "raw_candidate_count": len(ranked),
            "kept_candidate_count": len(kept),
            "kept_unique_parameter_points": len(unique),
            "hard_pruned_count": len(pruned),
            "top5_shortage": max(0, 5 - len(unique)),
            "supplemented_candidate_count": len(combined),
            "supplemented_selected_count": len(selected),
            "supplemented_selected_unique_parameter_points": len(selected_keys),
            "supplementation_audit": selection_audit[
                "candidate_pool_supplementation_v1"
            ],
            "supplemented_hard_pruned_count": len(final_pruned),
            "supplemented_hard_pruning_audit": final_pruning_audit,
            "hard_pruned_plan_sha256": sorted({
                str((row.get("plan_replay_pruning_v2") or {}).get(
                    "full_plan_sha256"
                ))
                for row in pruned
            }),
            "audit": audit,
        }
    controller.alg.atomic(args.output, report)
    print(json.dumps({
        "status": report["status"],
        "output": str(args.output),
        "shortages": {
            scene: value["top5_shortage"]
            for scene, value in report["scenes"].items()
        },
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
