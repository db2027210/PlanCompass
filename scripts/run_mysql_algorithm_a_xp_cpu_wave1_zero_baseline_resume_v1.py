#!/usr/bin/env python3
"""Resume CPU-A using its valid baseline and complete first physical wave."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v6 as runtime


SOURCE = ROOT / "history/mysql_four_scene_20260809/v566_to_a_cpu_top4_mixed_eight_worker_v5_recovery_20260827_xp_cpu"
SOURCE_STATE = SOURCE / "algorithm_a_state.json"
SOURCE_BASELINE = SOURCE / "formal_baseline/batch_result.json"
SOURCE_CANDIDATES = SOURCE / "round_001/candidates.json"
SOURCE_WAVE = SOURCE / "round_001/wave_001/attempt_01/batch_result.json"


def _verified(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular CPU-A continuation evidence required: {path}")
    sidecar = path.with_name(path.name + ".sha256")
    if (
        not sidecar.is_file()
        or sidecar.is_symlink()
        or sidecar.read_text(encoding="ascii").split()[0] != runtime.base.alg.sha(path)
    ):
        raise RuntimeError(f"CPU-A continuation sidecar differs: {path}")
    return runtime.base.alg.load(path)


def _validate_source() -> dict[str, dict[str, Any]]:
    state = _verified(SOURCE_STATE)
    baseline = _verified(SOURCE_BASELINE)
    candidates = _verified(SOURCE_CANDIDATES)
    wave = _verified(SOURCE_WAVE)
    baseline_rows = dict(baseline.get("measurements") or {})
    wave_rows = dict(wave.get("measurements") or {})
    expected = {f"xp_cpu_w{i}" for i in range(1, 9)}
    if (
        state.get("algorithm") != "A"
        or state.get("async_scene") != "hybench_xp_cpu"
        or state.get("status") != "FAILED_CLOSED"
        or state.get("error") != "RuntimeError: Algorithm A worker baseline is non-positive: io"
        or int(state.get("accepted") or 0) != 0
        or int(state.get("rejected_rounds") or 0) != 0
        or int(state.get("round_index") or 0) != 1
        or state.get("round_checkpoint", {}).get("attempted_ranks") != []
        or baseline.get("status") != "PASS"
        or wave.get("status") != "PASS"
        or set(baseline_rows) != expected
        or set(wave_rows) != expected
        or any(
            row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            for row in [*baseline_rows.values(), *wave_rows.values()]
        )
        or [int(row.get("rank") or 0) for row in wave_rows.values()]
        != list(range(1, 9))
        or int(candidates.get("candidate_count") or 0) != 16
    ):
        raise RuntimeError("CPU-A zero-baseline continuation source differs")
    return {"baseline": baseline_rows, "wave": wave_rows}


def _mirror_batch(
    *, source: Path, destination: Path, state_path: Path,
    state: Mapping[str, Any], role: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    document = _verified(source)
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    runtime.base.alg.atomic(destination / "batch_result.json", document)
    manifest = {
        "schema_version": "mysql_algorithm_a_technical_measurement_reuse_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "role": role,
        "source_path": str(source),
        "source_sha256": runtime.base.alg.sha(source),
        "physical_workload_invocations": 0,
        "accepted_or_rejected_credit_added_by_reuse": 0,
        "workload_or_resource_envelope_changed": False,
        "reason": "reprocess complete valid evidence after zero-baseline normalization fix",
    }
    runtime.base.alg.atomic(destination / "technical_reuse_manifest.json", manifest)
    updated = copy.deepcopy(dict(state))
    updated["stage"] = f"TECHNICAL_REUSE_{role.upper()}_COMPLETE"
    updated["technical_measurement_reuse"] = copy.deepcopy(manifest)
    runtime.base.alg.atomic(state_path, updated)
    return copy.deepcopy(dict(document["measurements"])), updated


def main() -> int:
    evidence = _validate_source()
    original_run = runtime.base.run

    def run_with_reuse(args: Any) -> int:
        physical_measure = runtime.base.measure
        baseline_reused = False
        wave_reused = False

        def measure_with_reuse(**kwargs: Any):
            nonlocal baseline_reused, wave_reused
            output = Path(kwargs["output"])
            assignments = kwargs["assignments"]
            if output.name == "formal_baseline" and not baseline_reused:
                if set(assignments) != set(evidence["baseline"]):
                    raise RuntimeError("CPU-A reused baseline worker set differs")
                baseline_reused = True
                return _mirror_batch(
                    source=SOURCE_BASELINE, destination=output,
                    state_path=Path(kwargs["state_path"]), state=kwargs["state"],
                    role="formal_baseline",
                )
            if (
                output.name == "attempt_01"
                and output.parent.name == "wave_001"
                and output.parent.parent.name == "round_001"
                and not wave_reused
            ):
                candidate_path = output.parent.parent / "candidates.json"
                current_candidates = runtime.base.alg.load(candidate_path)
                source_candidates = _verified(SOURCE_CANDIDATES)
                current_vectors = [
                    {
                        "rank": int(row["rank"]),
                        "optimized_params": row["optimized_params"],
                    }
                    for row in current_candidates.get("candidates") or []
                ]
                source_vectors = [
                    {
                        "rank": int(row["rank"]),
                        "optimized_params": row["optimized_params"],
                    }
                    for row in source_candidates.get("candidates") or []
                ]
                if (
                    current_vectors != source_vectors
                    or {
                        int(value["rank"]): value["params"]
                        for value in assignments.values()
                    }
                    != {
                        int(row["rank"]): row["params"]
                        for row in evidence["wave"].values()
                    }
                ):
                    raise RuntimeError("CPU-A reused Wave 1 candidate vectors differ")
                wave_reused = True
                return _mirror_batch(
                    source=SOURCE_WAVE, destination=output,
                    state_path=Path(kwargs["state_path"]), state=kwargs["state"],
                    role="round_001_wave_001",
                )
            return physical_measure(**kwargs)

        runtime.base.measure = measure_with_reuse
        try:
            result = int(original_run(args))
        finally:
            runtime.base.measure = physical_measure
        if not baseline_reused or not wave_reused:
            raise RuntimeError("CPU-A required technical evidence was not reused")
        return result

    runtime.base.run = run_with_reuse
    try:
        return runtime.main()
    finally:
        runtime.base.run = original_run


if __name__ == "__main__":
    raise SystemExit(main())
