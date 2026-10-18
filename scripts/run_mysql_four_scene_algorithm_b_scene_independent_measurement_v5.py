#!/usr/bin/env python3
"""Scene-scoped relational measurement without sibling worker dependency."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from scripts import run_mysql_four_scene_algorithm_b_scene_independent_measurement_v3 as v3
from scripts import run_mysql_four_scene_algorithm_b_scene_independent_measurement_v4 as v4


_ORIGINAL_PAIR_INPUTS = v3._pair_inputs


def _scene_scoped_pair_inputs(
    pair: int, workers: Mapping[str, dict[str, Any]], output: Path,
    purpose: str,
) -> tuple[dict[str, Any], dict[str, Any], Path, str, str, str, str]:
    job = workers.get(f"job_w{pair}")
    tpch = workers.get(f"tpch_w{pair}")
    if job is None and tpch is None:
        raise RuntimeError(f"pair {pair} has no scene-local worker")
    # run_job_tpch_parallel validates the complete active-workload contract,
    # but the independent controller injects a no-op runner for the sibling
    # suite.  Its endpoint must therefore be structurally valid, not backed by
    # a separately authorized sibling worker.
    job = job or tpch
    tpch = tpch or job
    assert job is not None and tpch is not None
    query_files = v3.alg.discover_job_query_files(v3.alg.JOB_QUERY_ROOT)
    manifest = v3.alg.build_job_manifest(
        query_files, output / f"pair_{pair}" / f"independent_manifest_{purpose}",
    )
    job_user, job_password = v3.alg.private_credential(job)
    tpch_user, tpch_password = v3.alg.private_credential(tpch)
    return job, tpch, manifest, job_user, job_password, tpch_user, tpch_password


def measure_batch(
    freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
    assignments: Mapping[str, Mapping[str, Any]], output: Path, reset_token: str,
    *, state_path: Path | None = None, stage_prefix: str = "FORMAL_POINT",
) -> dict[str, dict[str, Any]]:
    original = v3._pair_inputs
    v3._pair_inputs = _scene_scoped_pair_inputs
    try:
        return v4.measure_batch(
            freeze, workers, assignments, output, reset_token,
            state_path=state_path, stage_prefix=stage_prefix,
        )
    finally:
        v3._pair_inputs = original


__all__ = ["measure_batch"]
