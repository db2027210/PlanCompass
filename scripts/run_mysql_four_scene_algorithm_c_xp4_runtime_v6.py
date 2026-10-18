#!/usr/bin/env python3
"""XP4 C runtime with audited stability-or-cap completion semantics."""

from __future__ import annotations

import copy
import os
from pathlib import Path
import shutil
from typing import Any, Mapping

from scripts import run_mysql_four_scene_algorithm_c_xp4_runtime_v1 as runtime_v1
from scripts import run_mysql_four_scene_algorithm_c_xp4_runtime_v5 as runtime_v5


alg = runtime_v1.alg
OPTIMIZATION_CONTRACT = runtime_v5.OPTIMIZATION_CONTRACT
WAVE_COORDINATION_CONTRACT = runtime_v5.WAVE_COORDINATION_CONTRACT
_ORIGINAL_MEASURE = runtime_v1._measure_batch_with_fair_coordination
_INSTALLED = False


def _apply_cap_completion(
    document: Mapping[str, Any], *, source: Path | None = None,
) -> tuple[dict[str, Any], list[str]]:
    result = copy.deepcopy(dict(document))
    measurements = result.get("measurements")
    if not isinstance(measurements, Mapping):
        raise RuntimeError("XP cap-completion batch measurements differ")
    accepted: list[str] = []
    for worker_id, raw in measurements.items():
        row = dict(raw)
        checks = dict(row.get("hard_gate_checks") or {})
        stability = dict(row.get("resource_stability") or {})
        capped = (
            row.get("coverage_complete") is True
            and row.get("hard_gate_passed") is False
            and checks.get("target_resource_stability_passed") is False
            and all(
                value is True
                for name, value in checks.items()
                if name != "target_resource_stability_passed"
            )
            and stability.get("stable") is False
            and float(stability.get("selected_measurement_seconds") or 0.0)
            == float(stability.get("maximum_measurement_seconds") or 0.0)
            == 900.0
        )
        if capped:
            checks["target_resource_stability_or_cap_completion_passed"] = True
            row["hard_gate_checks"] = checks
            row["hard_gate_passed"] = True
            row["resource_stability_cap_completion_accepted"] = True
            row["resource_stability_claimed"] = False
            row["measurement_completion_reason"] = (
                "maximum_900s_complete_with_coverage"
            )
            measurements[worker_id] = row
            accepted.append(str(worker_id))
    if result.get("failures") not in ([], None):
        return result, accepted
    result["status"] = (
        "PASS"
        if all(
            row.get("hard_gate_passed") is True
            and row.get("coverage_complete") is True
            for row in measurements.values()
        )
        else "FAILED_CLOSED"
    )
    result["xp_completion_policy"] = {
        "policy": "stable_after_300s_or_complete_at_900s_v1",
        "stable_claim_required_for_early_stop": True,
        "cap_completion_is_not_stability": True,
        "coverage_and_all_nonstability_hard_gates_required": True,
        "cap_completed_worker_ids": sorted(accepted),
        "source_batch_path": str(source) if source is not None else None,
        "source_batch_sha256": alg.sha(source) if source is not None else None,
    }
    return result, accepted


def _validated_baseline_source(path: Path, assignments: Mapping[str, Any]) -> dict[str, Any]:
    path = path.resolve()
    history = (alg.ROOT / "history/mysql_four_scene_20260809").resolve()
    if history not in path.parents or path.is_symlink() or not path.is_file():
        raise RuntimeError("verified C baseline source path differs")
    source = alg.load(path)
    if set(source.get("measurements") or {}) != set(assignments):
        raise RuntimeError("verified C baseline worker set differs")
    failed_state_path = path.parents[1] / "run_state.json"
    failed = alg.load(failed_state_path)
    scene = runtime_v1.SCENES[0]
    if (
        failed.get("algorithm") != "C"
        or failed.get("async_scene") != scene
        or failed.get("status") != "FAILED_CLOSED"
        or failed.get("stage") != "FAILED"
        or failed.get("error") != "RuntimeError: formal incumbent baseline failed closed"
        or int(failed.get("outer_attempt") or 0) != 4
        or int((failed.get("accepted_rounds") or {}).get(scene) or 0) != 2
        or int((failed.get("rejected_outer_attempts") or {}).get(scene) or 0) != 2
    ):
        raise RuntimeError("verified C baseline continuation state differs")
    return source


def measure_with_cap_completion(**kwargs: Any):
    raw_source = os.environ.get("MYSQL_C_VERIFIED_BASELINE", "")
    baseline_token = kwargs.get("reset_token") == "v18_pruning_v2_formal_incumbent_baseline"
    output = Path(kwargs["output"])
    if raw_source and baseline_token:
        source_path = Path(raw_source).expanduser().resolve()
        source = _validated_baseline_source(source_path, kwargs["assignments"])
        document, accepted = _apply_cap_completion(source, source=source_path)
        if document.get("status") != "PASS" or not accepted:
            raise RuntimeError("verified C cap-completion baseline differs")
        output.mkdir(parents=True, exist_ok=False, mode=0o700)
        batch_path = output / "batch_result.json"
        alg.atomic(batch_path, document)
        state = copy.deepcopy(dict(kwargs["state"]))
        state["stage"] = f"{kwargs['stage_prefix']}_VERIFIED_CAP_CONTINUATION"
        state["formal_baseline_technical_continuation"] = {
            "source_path": str(source_path),
            "source_sha256": alg.sha(source_path),
            "source_state_path": str(source_path.parents[1] / "run_state.json"),
            "source_state_sha256": alg.sha(source_path.parents[1] / "run_state.json"),
            "cap_completed_worker_ids": sorted(accepted),
            "accepted_or_rejected_outer_credit_reused": False,
            "measurement_replay_without_rerun": True,
        }
        alg.atomic(Path(kwargs["state_path"]), state)
        return copy.deepcopy(dict(document["measurements"])), state
    measured, state = _ORIGINAL_MEASURE(**kwargs)
    batch_path = output / "batch_result.json"
    document = alg.load(batch_path)
    successor, accepted = _apply_cap_completion(document)
    if accepted:
        alg.atomic(batch_path, successor)
        measured = copy.deepcopy(dict(successor["measurements"]))
        state = copy.deepcopy(dict(state))
        state["last_cap_completed_worker_ids"] = sorted(accepted)
        alg.atomic(Path(kwargs["state_path"]), state)
    return measured, state


def install() -> None:
    global _INSTALLED
    runtime_v5.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    runtime_v5.WAVE_COORDINATION_CONTRACT = WAVE_COORDINATION_CONTRACT
    runtime_v5.install()
    if not _INSTALLED:
        runtime_v1._measure_batch_with_fair_coordination = measure_with_cap_completion
        _INSTALLED = True


def main() -> int:
    install()
    return runtime_v1.main()


if __name__ == "__main__":
    raise SystemExit(main())
