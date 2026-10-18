#!/usr/bin/env python3
"""Resume IO-B after committed Outer12 and reuse the recovered Outer13 reset."""

from __future__ import annotations

import copy
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_b_top4_mixed_eight_worker_boundary_resume_v2 as boundary_v2
from scripts import run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v11 as runtime_v11


HISTORY = ROOT / "history/mysql_four_scene_20260809"
SOURCE = HISTORY / "v735_to_b_io_outer11_entrypoint_repaired_resume_20260827_xp_io"
OUTER_1_ROOT = HISTORY / "v505_to_b_io_mixed_four_worker_boundary_resume_v71_20260827_xp_io"
OUTER_2_TO_5_ROOT = HISTORY / "v521_to_b_io_mixed_four_worker_boundary_resume_v73_20260827_xp_io"
OUTER_6_TO_7_ROOT = HISTORY / "v554_to_b_io_mixed_eight_worker_v2_recovered_20260827_xp_io"
OUTER_8_TO_9_ROOT = HISTORY / "v561_to_b_io_mixed_eight_worker_v6_outer7_resume_20260827_xp_io"
OUTER_10_ROOT = HISTORY / "v580_to_b_io_mixed_eight_worker_v7_outer9_resume_20260827_xp_io"
OUTER_11_ROOT = HISTORY / "v588_to_b_io_mixed_eight_worker_v7_outer10_resume_20260827_xp_io"
CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v11"
RECOVERED_RESET_BINDING = (
    HISTORY / "v753_to_b_io_outer13_stale_task_repaired_20260827"
    / "xp_resets/algorithm_b_outer_013_rank_1/reset_binding.json"
)
RECOVERED_AUTHORITY_ROOT = (
    HISTORY / "v754_to_b_io_outer13_stale_task_repaired_authority_20260827"
)
EXPECTED_ERROR = (
    "CalledProcessError: Command '['docker', 'start', "
    "'ra_pgssd_mysql_baseline_xp_io_w1_20260826']' returned non-zero exit status 1."
)


def install_runtime() -> Any:
    wrapper = boundary_v2.implementation
    wrapper["runtime8"] = runtime_v11
    wrapper["CONTRACT_ROOT"] = CONTRACT_ROOT
    wrapper["EXPECTED_ERROR"] = EXPECTED_ERROR
    wrapper["CONFIG"]["hybench_xp_io"] = {
        "source": SOURCE,
        "accepted": 2,
        "rejected": 10,
        "outer": 12,
        "decision_roots": {
            1: OUTER_1_ROOT,
            **{outer: OUTER_2_TO_5_ROOT for outer in range(2, 6)},
            **{outer: OUTER_6_TO_7_ROOT for outer in range(6, 8)},
            **{outer: OUTER_8_TO_9_ROOT for outer in range(8, 10)},
            10: OUTER_10_ROOT,
            11: OUTER_11_ROOT,
            12: SOURCE,
        },
    }
    core = wrapper["install"]("hybench_xp_io")
    impl = core._impl
    alg = impl["alg"]
    original_configure_runtime = impl["configure_runtime"]
    original_state_document = impl["state_document"]

    def configure_runtime() -> Mapping[str, Any]:
        contract = original_configure_runtime()
        runtime = impl["runtime"]
        normal_reset = runtime.alg.reset_xp
        reused = False

        def reset_with_recovered_outer13(
            freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
            output: Path, token: str, *, worker_ids: set[str] | None = None,
        ) -> None:
            nonlocal reused
            if reused or token != "algorithm_b_outer_013_rank_1":
                normal_reset(freeze, workers, output, token, worker_ids=worker_ids)
                return
            expected = {f"xp_io_w{i}" for i in range(1, 9)}
            requested = set(worker_ids or ())
            authority_path = RECOVERED_AUTHORITY_ROOT / "live_authority.json"
            authority = alg.load(authority_path)
            binding = alg.load(RECOVERED_RESET_BINDING)
            rows = {
                str(row["worker_id"]): copy.deepcopy(dict(row))
                for row in binding.get("workers") or []
                if row.get("worker_id") in expected
            }
            if (
                requested != expected
                or binding.get("status") != "PASS"
                or set(rows) != expected
                or authority.get("status") != "PASS"
                or Path(str(authority.get("source_reset_binding_path"))).resolve()
                != RECOVERED_RESET_BINDING.resolve()
                or authority.get("source_reset_binding_sha256")
                != alg.sha(RECOVERED_RESET_BINDING)
            ):
                raise RuntimeError("Outer13 recovered reset reuse authority differs")
            stage = Path(output) / "xp_resets" / token
            if stage.exists():
                raise RuntimeError("Outer13 recovered reset target already exists")
            stage.mkdir(parents=True, mode=0o700)
            sync_rows: list[dict[str, Any]] = []
            for worker_id in sorted(expected):
                row = rows[worker_id]
                inspected = alg.reset._docker_json("inspect", str(row["container"]))
                if (
                    inspected.get("Id") != row.get("container_id")
                    or (inspected.get("State") or {}).get("Running") is not True
                ):
                    raise RuntimeError(f"{worker_id} recovered Outer13 live identity differs")
                anchor = Path(str(row["datadir"])).resolve().parent
                completed = subprocess.run(
                    ["sync", "-f", str(anchor)], capture_output=True, text=True,
                    check=False, timeout=300,
                )
                if completed.returncode != 0:
                    raise RuntimeError(f"{worker_id} recovered Outer13 syncfs failed")
                sync_rows.append({
                    "worker_id": worker_id,
                    "syncfs_anchor": str(anchor),
                    "syncfs_returncode": completed.returncode,
                })
                workers[worker_id] = copy.deepcopy(row)
            target_binding = copy.deepcopy(binding)
            target_binding.update({
                "schema_version": "mysql_xp8_outer13_recovered_reset_reuse_v1",
                "created_at": alg.now(),
                "source_reset_binding_path": str(RECOVERED_RESET_BINDING),
                "source_reset_binding_sha256": alg.sha(RECOVERED_RESET_BINDING),
                "source_live_authority_path": str(authority_path),
                "source_live_authority_sha256": alg.sha(authority_path),
                "candidate_measurements_reused": 0,
                "accepted_or_rejected_round_credit": 0,
                "workload_started_before_reuse": False,
            })
            alg.atomic(stage / "reset_binding.json", target_binding)
            alg.atomic(stage / "post_reset_storage_settle.json", {
                "schema_version": "mysql_xp8_outer13_recovered_reset_sync_v1",
                "status": "PASS",
                "created_at": alg.now(),
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "workers": sync_rows,
                "scene_scoped": True,
                "device_wide_quiet_wait": False,
                "sibling_scene_completion_dependency": False,
                "shared_nvme_mutual_exclusion": False,
                "source_reset_binding_path": str(RECOVERED_RESET_BINDING),
                "source_reset_binding_sha256": alg.sha(RECOVERED_RESET_BINDING),
                "source_authority_rolling_gate_observed": True,
                "controller_pid": os.getpid(),
            })
            reused = True

        runtime.alg.reset_xp = reset_with_recovered_outer13
        return contract

    def state_document(run_id: str) -> dict[str, Any]:
        value = original_state_document(run_id)
        value["outer13_reset_failure_continuation"] = {
            "source_run_root": str(SOURCE),
            "committed_outer_reused_through": 12,
            "discarded_candidate_measurements": 0,
            "accepted_or_rejected_credit_for_partial_outer": 0,
            "failed_reset_worker": "xp_io_w3",
            "docker_orphan_shim_repaired_before_launch": True,
            "recovered_reset_binding": str(RECOVERED_RESET_BINDING),
            "worker_reset_parallelism": 8,
            "cross_scene_lock_used": False,
        }
        return value

    impl["state_document"] = state_document
    impl["configure_runtime"] = configure_runtime
    return core


def main() -> int:
    return int(install_runtime()._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
