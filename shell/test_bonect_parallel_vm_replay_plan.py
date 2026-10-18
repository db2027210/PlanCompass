#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.bonect_parallel_vm_replay import DEFAULT_PARAMS, load_pool, run, vm_memory_size


CONFIG = ROOT / (
    "config/generated/pg_benchmark_bottleneck_matrix_local/"
    "pg_bonect_phase2_vm_estimator_fix_v86_cpu_20260711_225538/"
    "cpu/hybench_xp.json"
)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def main() -> None:
    assert vm_memory_size({"memory_gb": 4}) == "4G"
    assert vm_memory_size({"memory_gb": 2.5, "memory_size": "2560M"}) == "2560M"
    with tempfile.TemporaryDirectory(prefix="bonect_parallel_plan_") as tmp:
        root = Path(tmp)
        candidates_path = root / "candidates.json"
        write_json(
            candidates_path,
            [
                {
                    "candidate_id": f"cand{index}",
                    "scenario": "cpu",
                    "params": {
                        **DEFAULT_PARAMS,
                        "seq_page_cost": 1.0 + index / 10.0,
                    },
                }
                for index in range(4)
            ],
        )
        workers = {
            "workers": [
                {
                    "worker_id": f"vm{index}",
                    "vm_name": f"bonect-replay-{index}",
                    "scenarios": ["cpu", "io"],
                    "scenario_configs": {"cpu": str(CONFIG), "io": str(CONFIG)},
                    "config_override": {
                        "postgres": {"host": f"192.0.2.{index + 1}", "port": 25432},
                        "metrics": {
                            "vm_runtime": {
                                "host": f"192.0.2.{index + 1}",
                                "ssh_target": f"anonymous@192.0.2.{index + 1}",
                            }
                        },
                    },
                    "vm_resource": {"vm_name": f"bonect-replay-{index}"},
                }
                for index in range(2)
            ]
        }
        pool_path = root / "workers.json"
        write_json(pool_path, workers)
        output_dir = root / "output"
        args = argparse.Namespace(
            candidates_json=str(candidates_path),
            worker_pool_json=str(pool_path),
            output_dir=str(output_dir),
            scenario="cpu",
            max_workers=2,
            anchor_candidate_id="cand1",
            run_mins=1.0,
            workload_timeout_s=120.0,
            apply_vm_profile=False,
            force_vm_destroy=False,
            plan_only=True,
            baseline_only=False,
            calibration_cache_json="",
            calibration_cache_max_age_s=3600.0,
            snapshot_restore_timeout_s=600.0,
            drop_cache_timeout_s=120.0,
            python=sys.executable,
            max_baseline_qps_drift_pct=5.0,
            max_baseline_resource_drift_pp=3.0,
            max_anchor_qps_delta_spread_pp=5.0,
            max_anchor_resource_delta_spread_pp=3.0,
            allow_goal_rich_anchor_qps_spread=False,
            candidate_limit=None,
        )
        assert run(args) == 0
        plan = json.loads((output_dir / "parallel_replay_plan.json").read_text())
        assert plan["worker_count"] == 2
        assert plan["candidate_count"] == 4
        assert plan["anchor_candidate_id"] == "cand1"
        assert plan["fixed_snapshot_waves"] is True
        assert plan["wave_count"] == 4
        assert [item["candidate_id"] for item in plan["waves"][0]["items"]] == [
            "__baseline__vm0",
            "__baseline__vm1",
        ]
        assert [item["candidate_id"] for item in plan["waves"][1]["items"]] == [
            "__anchor__vm0",
            "__anchor__vm1",
        ]
        assert [item["candidate_id"] for item in plan["waves"][2]["items"]] == [
            "cand0",
            "cand2",
        ]
        assert [item["candidate_id"] for item in plan["waves"][3]["items"]] == ["cand3"]
        for worker in plan["workers"]:
            config = json.loads(Path(worker["worker_config"]).read_text())
            command = config["scenario_overrides"]["cpu"]["benchmark"]["workload_cmd"]
            assert str(output_dir / "workers") in command
            props_path = Path(command.split(" -c ", 1)[1].split(" ", 1)[0])
            assert "xpRunMins=1" in props_path.read_text()
        for wave in plan["waves"]:
            for item in wave["items"]:
                manifest = json.loads(Path(item["manifest"]).read_text())
                assert len(manifest) == 1
                assert manifest[0]["candidate_id"] == item["candidate_id"]

        args.apply_vm_profile = True
        profile_output_dir = root / "profile-output"
        args.output_dir = str(profile_output_dir)
        assert run(args) == 0
        profile_plan = json.loads((profile_output_dir / "parallel_replay_plan.json").read_text())
        assert profile_plan["profile_preflight"]["enabled"] is True
        assert profile_plan["profile_preflight"]["barrier_before_snapshot_restore"] is True
        for wave in profile_plan["waves"]:
            for item in wave["items"]:
                has_apply = "--apply-vm-profile" in item["command"]
                assert has_apply is False, item["command"]

        # baseline-only plan: only baseline waves, no anchor/candidates.
        baseline_out = root / "baseline-only"
        args.output_dir = str(baseline_out)
        args.baseline_only = True
        args.apply_vm_profile = False
        args.candidates_json = ""
        assert run(args) == 0
        baseline_plan = json.loads((baseline_out / "parallel_replay_plan.json").read_text())
        assert baseline_plan["baseline_only"] is True
        assert baseline_plan["stops_before_anchor_and_candidates"] is True
        assert baseline_plan["wave_count"] == 1
        assert baseline_plan["candidate_count"] == 0
        for item in baseline_plan["waves"][0]["items"]:
            assert item["candidate_id"].startswith("__baseline__")
            assert not item["candidate_id"].startswith("__anchor__")

        try:
            load_pool(pool_path, "io", 2)
        except ValueError as exc:
            assert "parallel IO replay requires" in str(exc)
        else:
            raise AssertionError("IO pool without verified disk isolation must fail")
    print("bonect parallel VM replay plan tests passed", flush=True)


if __name__ == "__main__":
    main()
