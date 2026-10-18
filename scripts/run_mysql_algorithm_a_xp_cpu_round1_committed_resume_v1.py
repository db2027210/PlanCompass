#!/usr/bin/env python3
"""Resume CPU-A after committed Round1 without replaying completed work."""

from __future__ import annotations

import copy
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_async_xp_mixed_ssd_post_reset_settle_v1 as mixed_settle
from scripts import mysql_single_scene_measurement_v1 as single_measurement
from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_scene_v2 as reset8
from scripts import run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v6 as runtime


HISTORY = ROOT / "history/mysql_four_scene_20260809"
SOURCE = HISTORY / "v581_to_a_cpu_top4_mixed_eight_worker_v6_wave1_resume_20260827_xp_cpu"
SOURCE_STATE = SOURCE / "algorithm_a_state.json"
SOURCE_BASELINE = SOURCE / "formal_baseline/batch_result.json"
SOURCE_CANDIDATES = SOURCE / "round_001/candidates.json"
SOURCE_WAVES = {
    1: SOURCE / "round_001/wave_001/attempt_01/batch_result.json",
    2: SOURCE / "round_001/wave_002/attempt_01/batch_result.json",
}
SOURCE_DECISION = SOURCE / "round_001/outer_decision.json"
RECOVERED_ROUND2_RESET = (
    SOURCE / "round_002/wave_001/xp_resets/"
    "v581_to_a_cpu_top4_mixed_eight_worker_v6_wave1_resume_20260827_"
    "xp_cpu_round_002_wave_001_attempt_01/reset_binding.json"
)
RECOVERED_AUTHORITY = (
    HISTORY / "v591_to_a_cpu_mixed_eight_worker_v6_live_authority_20260827/"
    "live_authority.json"
)


def verified(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular CPU-A continuation evidence required: {path}")
    sidecar = path.with_name(path.name + ".sha256")
    if (
        not sidecar.is_file()
        or sidecar.is_symlink()
        or sidecar.read_text(encoding="ascii").split()[0]
        != runtime.base.alg.sha(path)
    ):
        raise RuntimeError(f"CPU-A continuation sidecar differs: {path}")
    value = runtime.base.alg.load(path)
    if not isinstance(value, Mapping):
        raise RuntimeError(f"CPU-A continuation object required: {path}")
    return dict(value)


def validate_source() -> dict[str, Any]:
    state = verified(SOURCE_STATE)
    baseline = verified(SOURCE_BASELINE)
    candidates = verified(SOURCE_CANDIDATES)
    decision = verified(SOURCE_DECISION)
    waves = {index: verified(path) for index, path in SOURCE_WAVES.items()}
    expected = {f"xp_cpu_w{i}" for i in range(1, 9)}
    baseline_rows = dict(baseline.get("measurements") or {})
    wave_rows = {
        index: dict(document.get("measurements") or {})
        for index, document in waves.items()
    }
    if (
        state.get("algorithm") != "A"
        or state.get("async_scene") != "hybench_xp_cpu"
        or state.get("status") != "FAILED_CLOSED"
        or int(state.get("accepted") or 0) != 1
        or int(state.get("rejected_rounds") or 0) != 0
        or int(state.get("round_index") or 0) != 2
        or state.get("round_checkpoint", {}).get("attempted_ranks") != []
        or baseline.get("status") != "PASS"
        or set(baseline_rows) != expected
        or int(candidates.get("candidate_count") or 0) != 16
        or decision.get("schema_version")
        != "mysql_algorithm_a_complete_factorial_decision_v1"
        or not decision.get("accepted")
        or any(waves[index].get("status") != "PASS" for index in waves)
        or any(set(wave_rows[index]) != expected for index in waves)
        or sorted(int(row.get("rank") or 0) for row in wave_rows[1].values())
        != list(range(1, 9))
        or sorted(int(row.get("rank") or 0) for row in wave_rows[2].values())
        != list(range(9, 17))
        or any(
            row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            for row in [
                *baseline_rows.values(), *wave_rows[1].values(),
                *wave_rows[2].values(),
            ]
        )
    ):
        raise RuntimeError("CPU-A committed Round1 continuation source differs")
    return {
        "baseline": baseline_rows,
        "waves": wave_rows,
        "candidates": candidates,
    }


def mirror_batch(
    *, source: Path, destination: Path, state_path: Path,
    state: Mapping[str, Any], role: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    document = verified(source)
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    runtime.base.alg.atomic(destination / "batch_result.json", document)
    manifest = {
        "schema_version": "mysql_algorithm_a_committed_round_reuse_v1",
        "status": "PASS", "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "role": role, "source_path": str(source),
        "source_sha256": runtime.base.alg.sha(source),
        "physical_workload_invocations": 0,
        "accepted_or_rejected_credit_added_by_reuse": 0,
        "workload_or_resource_envelope_changed": False,
        "reason": "reconstruct exact committed Round1 after pre-measure Round2 failure",
    }
    runtime.base.alg.atomic(destination / "technical_reuse_manifest.json", manifest)
    updated = copy.deepcopy(dict(state))
    updated["stage"] = f"TECHNICAL_REUSE_{role.upper()}_COMPLETE"
    updated["committed_round1_measurement_reuse"] = copy.deepcopy(manifest)
    runtime.base.alg.atomic(state_path, updated)
    return copy.deepcopy(dict(document["measurements"])), updated


def main() -> int:
    evidence = validate_source()
    original_run = runtime.base.run

    def run_with_reuse(args: Any) -> int:
        physical_measure = runtime.base.measure
        original_settle = single_measurement.async_xp_settle
        reused_roles: set[str] = set()
        reset_reused = False

        def recovered_reset(
            freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
            output: Path, token: str, *, worker_ids: set[str] | None = None,
        ) -> None:
            nonlocal reset_reused
            if reset_reused or "round_002_wave_001_attempt_01" not in token:
                mixed_settle.reset_xp(
                    freeze, workers, output, token, worker_ids=worker_ids,
                )
                return
            expected = {f"xp_cpu_w{i}" for i in range(1, 9)}
            requested = set(worker_ids or ())
            binding = verified(RECOVERED_ROUND2_RESET)
            authority = verified(RECOVERED_AUTHORITY)
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
                != RECOVERED_ROUND2_RESET.resolve()
                or authority.get("source_reset_binding_sha256")
                != runtime.base.alg.sha(RECOVERED_ROUND2_RESET)
            ):
                raise RuntimeError("CPU-A recovered Round2 reset authority differs")
            stage = Path(output) / "xp_resets" / token
            if stage.exists():
                raise RuntimeError("CPU-A recovered Round2 reset target exists")
            stage.mkdir(parents=True, mode=0o700)
            sync_rows: list[dict[str, Any]] = []
            for worker_id in sorted(expected):
                row = rows[worker_id]
                inspect = reset8._docker_json("inspect", str(row["container"]))
                if (
                    inspect.get("Id") != row.get("container_id")
                    or (inspect.get("State") or {}).get("Running") is not True
                ):
                    raise RuntimeError(
                        f"{worker_id} recovered CPU-A live identity differs"
                    )
                anchor = Path(str(row["datadir"])).resolve().parent
                completed = subprocess.run(
                    ["sync", "-f", str(anchor)], capture_output=True, text=True,
                    check=False, timeout=300,
                )
                if completed.returncode != 0:
                    raise RuntimeError(f"{worker_id} recovered CPU-A syncfs failed")
                sync_rows.append({
                    "worker_id": worker_id, "syncfs_anchor": str(anchor),
                    "syncfs_returncode": completed.returncode,
                })
                workers[worker_id] = copy.deepcopy(row)
            target = copy.deepcopy(binding)
            target.update({
                "schema_version": "mysql_algorithm_a_round2_recovered_reset_reuse_v1",
                "created_at": runtime.base.alg.now(),
                "source_reset_binding_path": str(RECOVERED_ROUND2_RESET),
                "source_reset_binding_sha256": runtime.base.alg.sha(
                    RECOVERED_ROUND2_RESET
                ),
                "source_live_authority_path": str(RECOVERED_AUTHORITY),
                "source_live_authority_sha256": runtime.base.alg.sha(
                    RECOVERED_AUTHORITY
                ),
                "candidate_measurements_reused": 0,
                "accepted_or_rejected_round_credit": 0,
                "workload_started_before_reuse": False,
            })
            runtime.base.alg.atomic(stage / "reset_binding.json", target)
            runtime.base.alg.atomic(stage / "post_reset_storage_settle.json", {
                "schema_version": "mysql_algorithm_a_round2_recovered_reset_sync_v1",
                "status": "PASS", "created_at": runtime.base.alg.now(),
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "workers": sync_rows, "scene_scoped": True,
                "device_wide_quiet_wait": False,
                "sibling_scene_completion_dependency": False,
                "shared_nvme_mutual_exclusion": False,
                "source_reset_binding_path": str(RECOVERED_ROUND2_RESET),
                "source_reset_binding_sha256": runtime.base.alg.sha(
                    RECOVERED_ROUND2_RESET
                ),
            })
            reset_reused = True

        def measure_with_reuse(**kwargs: Any):
            output = Path(kwargs["output"])
            if output.name == "formal_baseline" and "baseline" not in reused_roles:
                if set(kwargs["assignments"]) != set(evidence["baseline"]):
                    raise RuntimeError("CPU-A reused baseline worker set differs")
                reused_roles.add("baseline")
                return mirror_batch(
                    source=SOURCE_BASELINE, destination=output,
                    state_path=Path(kwargs["state_path"]), state=kwargs["state"],
                    role="formal_baseline",
                )
            if (
                output.name == "attempt_01"
                and output.parent.name in {"wave_001", "wave_002"}
                and output.parent.parent.name == "round_001"
            ):
                wave = int(output.parent.name.rsplit("_", 1)[1])
                role = f"round_001_wave_{wave:03d}"
                if role not in reused_roles:
                    current = runtime.base.alg.load(
                        output.parent.parent / "candidates.json"
                    )
                    current_vectors = [
                        {"rank": int(row["rank"]), "optimized_params": row["optimized_params"]}
                        for row in current.get("candidates") or []
                    ]
                    source_vectors = [
                        {"rank": int(row["rank"]), "optimized_params": row["optimized_params"]}
                        for row in evidence["candidates"].get("candidates") or []
                    ]
                    expected_assignments = {
                        int(row["rank"]): row["params"]
                        for row in evidence["waves"][wave].values()
                    }
                    actual_assignments = {
                        int(row["rank"]): row["params"]
                        for row in kwargs["assignments"].values()
                    }
                    if (
                        current_vectors != source_vectors
                        or actual_assignments != expected_assignments
                    ):
                        raise RuntimeError(
                            f"CPU-A reused Round1 Wave{wave} vectors differ"
                        )
                    reused_roles.add(role)
                    return mirror_batch(
                        source=SOURCE_WAVES[wave], destination=output,
                        state_path=Path(kwargs["state_path"]), state=kwargs["state"],
                        role=role,
                    )
            return physical_measure(**kwargs)

        runtime.base.measure = measure_with_reuse
        single_measurement.async_xp_settle = SimpleNamespace(reset_xp=recovered_reset)
        try:
            result = int(original_run(args))
        finally:
            runtime.base.measure = physical_measure
            single_measurement.async_xp_settle = original_settle
        required = {"baseline", "round_001_wave_001", "round_001_wave_002"}
        if not required.issubset(reused_roles):
            raise RuntimeError("CPU-A committed Round1 evidence was not fully reused")
        return result

    runtime.base.run = run_with_reuse
    try:
        return runtime.main()
    finally:
        runtime.base.run = original_run


if __name__ == "__main__":
    raise SystemExit(main())
