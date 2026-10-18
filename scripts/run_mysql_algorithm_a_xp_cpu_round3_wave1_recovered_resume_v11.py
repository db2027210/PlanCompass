#!/usr/bin/env python3
"""Resume CPU-A through committed Round2 and reuse the recovered Round3 reset."""

from __future__ import annotations

import copy
import os
from pathlib import Path
import subprocess
import sys
import traceback
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_a_xp_cpu_round2_wave1_committed_resume_v10 as current


impl = current.predecessor.implementation
runtime = current.runtime_v10
mixed_settle = impl["mixed_settle"]
reset8 = impl["reset8"]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
SOURCE = HISTORY / "v739_to_a_cpu_refreshed_contract_resume_20260827_xp_cpu"
SOURCE_ROUND2_CANDIDATES = SOURCE / "round_002/candidates.json"
SOURCE_ROUND2_WAVE2 = SOURCE / "round_002/wave_002/attempt_01/batch_result.json"
RECOVERED_RESET = (
    HISTORY / "v759_to_a_cpu_round3_wave1_partial_recovery_20260827"
    / "xp_resets"
    / "v739_to_a_cpu_refreshed_contract_resume_20260827_xp_cpu_"
      "round_003_wave_001_attempt_01"
    / "reset_binding.json"
)
RECOVERED_AUTHORITY = (
    HISTORY / "v760_to_a_cpu_round3_wave1_recovered_authority_20260827"
    / "live_authority.json"
)


def main() -> int:
    original_measure = runtime.base.measure
    original_reset = mixed_settle.reset_xp
    round2_wave2_reused = False
    round3_reset_reused = False

    def measure_with_round2_wave2_reuse(**kwargs: Any):
        nonlocal round2_wave2_reused
        output = Path(kwargs["output"])
        if (
            not round2_wave2_reused
            and output.name == "attempt_01"
            and output.parent.name == "wave_002"
            and output.parent.parent.name == "round_002"
        ):
            candidates = impl["verified"](SOURCE_ROUND2_CANDIDATES)
            source_batch = impl["verified"](SOURCE_ROUND2_WAVE2)
            source_rows = dict(source_batch.get("measurements") or {})
            current_candidates = runtime.base.alg.load(
                output.parent.parent / "candidates.json"
            )
            current_vectors = [
                {"rank": int(row["rank"]), "optimized_params": row["optimized_params"]}
                for row in current_candidates.get("candidates") or []
            ]
            source_vectors = [
                {"rank": int(row["rank"]), "optimized_params": row["optimized_params"]}
                for row in candidates.get("candidates") or []
            ]
            expected_assignments = {
                int(row["rank"]): row["params"] for row in source_rows.values()
            }
            actual_assignments = {
                int(row["rank"]): row["params"]
                for row in kwargs["assignments"].values()
            }
            if (
                source_batch.get("status") != "PASS"
                or sorted(expected_assignments) != list(range(9, 17))
                or current_vectors != source_vectors
                or actual_assignments != expected_assignments
            ):
                raise RuntimeError("CPU-A committed Round2 Wave2 differs")
            round2_wave2_reused = True
            return impl["mirror_batch"](
                source=SOURCE_ROUND2_WAVE2,
                destination=output,
                state_path=Path(kwargs["state_path"]),
                state=kwargs["state"],
                role="round_002_wave_002",
            )
        return original_measure(**kwargs)

    def reset_with_round3_recovery(
        freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
        output: Path, token: str, *, worker_ids: set[str] | None = None,
    ) -> None:
        nonlocal round3_reset_reused
        if round3_reset_reused or "round_003_wave_001_attempt_01" not in token:
            original_reset(freeze, workers, output, token, worker_ids=worker_ids)
            return
        expected = {f"xp_cpu_w{i}" for i in range(1, 9)}
        requested = set(worker_ids or ())
        binding = impl["verified"](RECOVERED_RESET)
        authority = impl["verified"](RECOVERED_AUTHORITY)
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
            != RECOVERED_RESET.resolve()
            or authority.get("source_reset_binding_sha256")
            != runtime.base.alg.sha(RECOVERED_RESET)
        ):
            raise RuntimeError("CPU-A recovered Round3 reset authority differs")
        stage = Path(output) / "xp_resets" / token
        if stage.exists():
            raise RuntimeError("CPU-A recovered Round3 reset target exists")
        stage.mkdir(parents=True, mode=0o700)
        sync_rows: list[dict[str, Any]] = []
        for worker_id in sorted(expected):
            row = rows[worker_id]
            inspect = reset8._docker_json("inspect", str(row["container"]))
            if (
                inspect.get("Id") != row.get("container_id")
                or (inspect.get("State") or {}).get("Running") is not True
            ):
                raise RuntimeError(f"{worker_id} recovered CPU-A live identity differs")
            anchor = Path(str(row["datadir"])).resolve().parent
            completed = subprocess.run(
                ["sync", "-f", str(anchor)], capture_output=True, text=True,
                check=False, timeout=300,
            )
            if completed.returncode != 0:
                raise RuntimeError(f"{worker_id} recovered CPU-A syncfs failed")
            sync_rows.append({
                "worker_id": worker_id,
                "syncfs_anchor": str(anchor),
                "syncfs_returncode": completed.returncode,
            })
            workers[worker_id] = copy.deepcopy(row)
        target = copy.deepcopy(binding)
        target.update({
            "schema_version": "mysql_algorithm_a_round3_recovered_reset_reuse_v1",
            "created_at": runtime.base.alg.now(),
            "source_reset_binding_path": str(RECOVERED_RESET),
            "source_reset_binding_sha256": runtime.base.alg.sha(RECOVERED_RESET),
            "source_live_authority_path": str(RECOVERED_AUTHORITY),
            "source_live_authority_sha256": runtime.base.alg.sha(RECOVERED_AUTHORITY),
            "candidate_measurements_reused": 0,
            "accepted_or_rejected_round_credit": 0,
            "workload_started_before_reuse": False,
        })
        runtime.base.alg.atomic(stage / "reset_binding.json", target)
        runtime.base.alg.atomic(stage / "post_reset_storage_settle.json", {
            "schema_version": "mysql_algorithm_a_round3_recovered_reset_sync_v1",
            "status": "PASS",
            "created_at": runtime.base.alg.now(),
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "workers": sync_rows,
            "scene_scoped": True,
            "device_wide_quiet_wait": False,
            "sibling_scene_completion_dependency": False,
            "shared_nvme_mutual_exclusion": False,
            "source_reset_binding_path": str(RECOVERED_RESET),
            "source_reset_binding_sha256": runtime.base.alg.sha(RECOVERED_RESET),
        })
        round3_reset_reused = True

    runtime.base.measure = measure_with_round2_wave2_reuse
    mixed_settle.reset_xp = reset_with_round3_recovery
    try:
        result = int(impl["main"]())
    finally:
        runtime.base.measure = original_measure
        mixed_settle.reset_xp = original_reset
    if not round2_wave2_reused or not round3_reset_reused:
        raise RuntimeError("CPU-A Round2/Round3 technical continuation was incomplete")
    return result


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException:
        diagnostic = os.environ.get("MYSQL_XP_CPU_A_FAILURE_DIAGNOSTIC", "")
        if diagnostic:
            Path(diagnostic).write_text(traceback.format_exc(), encoding="utf-8")
        raise
