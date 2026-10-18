#!/usr/bin/env python3
"""Scene-independent measurement with all-worker pre/post session drains.

The v3 release drained only relational workers assigned to the current
physical candidate wave.  In a 2+2+1 schedule, rank 5 uses only ``w1`` and a
timed-out query left by rank 4 on ``w2`` could therefore keep consuming the
shared storage device.  This wrapper retains the v3 measurement semantics but
expands the pre-wave drain to every formal JOB/TPC-H worker and performs the
same scoped drain immediately after the measurement windows close.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from scripts import (
    run_mysql_four_scene_algorithm_b_scene_independent_measurement_v3 as v3,
)


def _relational_worker_ids(
    workers: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    return sorted(
        worker_id for worker_id in workers
        if worker_id.startswith("job_w") or worker_id.startswith("tpch_w")
    )


def _empty_relational_drain() -> dict[str, Any]:
    """Represent a valid no-op drain for an XP-only scene controller."""
    return {
        "schema_version": "mysql_algorithm_b_empty_relational_session_drain_v1",
        "status": "PASS",
        "created_at": v3.alg.now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "benchmark_user": "ra_benchmark",
        "external_container_mutation": False,
        "workers": [],
        "relational_workers_present": False,
        "no_op_reason": "independent_xp_scene_has_no_job_or_tpch_workers",
        "all_workers_zero_sessions_after": True,
        "all_tpch_revenue0_views_absent_after": True,
    }


def measure_batch(
    freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
    assignments: Mapping[str, Mapping[str, Any]], output: Path, reset_token: str,
    *, state_path: Path | None = None, stage_prefix: str = "FORMAL_POINT",
) -> dict[str, dict[str, Any]]:
    """Run v3 with complete pre-wave and immediate post-wave cleanup."""
    all_relational = _relational_worker_ids(workers)
    original_drain = v3.session_drain.drain_workers
    original_stage = v3.alg.set_runtime_stage
    post_drain_done = False

    def complete_pre_drain(
        selected_workers: Mapping[str, Mapping[str, Any]],
        _worker_ids: Any,
    ) -> dict[str, Any]:
        if not all_relational:
            return _empty_relational_drain()
        return original_drain(selected_workers, all_relational)

    def stage_with_post_drain(path: Path | None, stage: str) -> None:
        nonlocal post_drain_done
        if stage.endswith("_PLAN_CAPTURE") and not post_drain_done:
            original_stage(path, f"{stage_prefix}_POST_MEASURE_SESSION_DRAIN")
            document = (
                original_drain(workers, all_relational)
                if all_relational else _empty_relational_drain()
            )
            drain_path = output / "post_measure_benchmark_session_drain.json"
            v3.alg.atomic(drain_path, document)
            if (
                document.get("status") != "PASS"
                or document.get("all_workers_zero_sessions_after") is not True
                or document.get("all_tpch_revenue0_views_absent_after") is not True
            ):
                raise RuntimeError("post-measure benchmark session drain failed closed")
            post_drain_done = True
        original_stage(path, stage)

    v3.session_drain.drain_workers = complete_pre_drain
    v3.alg.set_runtime_stage = stage_with_post_drain
    try:
        result = v3.measure_batch(
            freeze, workers, assignments, output, reset_token,
            state_path=state_path, stage_prefix=stage_prefix,
        )
        if not post_drain_done:
            raise RuntimeError("post-measure benchmark session drain was not reached")
        return result
    finally:
        v3.session_drain.drain_workers = original_drain
        v3.alg.set_runtime_stage = original_stage


__all__ = ["measure_batch"]
