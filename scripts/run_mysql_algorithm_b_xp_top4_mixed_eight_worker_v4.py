#!/usr/bin/env python3
"""XP8 B successor that skips only hard-max resource-instability candidates."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


SOURCE = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v2.py"
)
text = SOURCE.read_text(encoding="utf-8")
old = "frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v2"
new = "frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v4"
if text.count(old) != 1:
    raise RuntimeError("XP8 B v4 predecessor differs")
text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_b_xp8_v4_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)

_predecessor_install_runtime = implementation["install_runtime"]


def _hard_max_resource_instability_scenes(
    batch: Mapping[str, Any],
) -> list[str]:
    """Identify completed XP attempts invalid only for target-resource CV.

    Coverage, positive QPS, liveness, restart/OOM, finite utilization, and the
    three-level fingerprint remain mandatory.  Measurement data from a row
    classified here is never used for scoring, acceptance, or model updates.
    """
    if (
        batch.get("status") != "FAILED_CLOSED"
        or batch.get("failures") not in ([], None)
        or float(batch.get("elapsed_seconds") or 0.0) < 900.0
        or not isinstance(batch.get("measurements"), Mapping)
    ):
        return []
    scenes: set[str] = set()
    rows = list((batch.get("measurements") or {}).values())
    if not rows:
        return []
    for raw in rows:
        if not isinstance(raw, Mapping):
            return []
        checks = raw.get("hard_gate_checks")
        stability = raw.get("resource_stability")
        if not isinstance(checks, Mapping) or not isinstance(stability, Mapping):
            return []
        failed_checks = {
            str(name) for name, passed in checks.items() if passed is not True
        }
        selected = float(stability.get("selected_measurement_seconds") or 0.0)
        maximum = float(stability.get("maximum_measurement_seconds") or 0.0)
        scene = str(raw.get("scene") or "")
        target = str(stability.get("target_resource") or "")
        if (
            failed_checks != {"target_resource_stability_passed"}
            or raw.get("coverage_complete") is not True
            or stability.get("stable") is not False
            or selected < 900.0
            or maximum != 900.0
            or selected != maximum
            or scene not in {"hybench_xp_cpu", "hybench_xp_io"}
            or target != ("cpu" if scene == "hybench_xp_cpu" else "io")
        ):
            return []
        scenes.add(scene)
    return sorted(scenes)


def install_runtime() -> Any:
    core = _predecessor_install_runtime()
    impl = core._impl
    predecessor_infeasible = impl["formally_infeasible_candidate_batch"]
    predecessor_scenes = impl["infeasible_batch_scenes"]

    def formally_infeasible_candidate_batch(batch: Mapping[str, Any]) -> bool:
        return bool(
            predecessor_infeasible(batch)
            or _hard_max_resource_instability_scenes(batch)
        )

    def infeasible_batch_scenes(batch: Mapping[str, Any]) -> list[str]:
        inherited = list(predecessor_scenes(batch))
        if inherited:
            return inherited
        return _hard_max_resource_instability_scenes(batch)

    impl["formally_infeasible_candidate_batch"] = (
        formally_infeasible_candidate_batch
    )
    impl["infeasible_batch_scenes"] = infeasible_batch_scenes
    return core


implementation["install_runtime"] = install_runtime
for name, value in implementation.items():
    if not name.startswith("__"):
        globals().setdefault(name, value)
globals()["install_runtime"] = install_runtime


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())
