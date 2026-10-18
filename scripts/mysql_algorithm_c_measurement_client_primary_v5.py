#!/usr/bin/env python3
"""Restore the SHA-bound TPC-H R0 normalization at each candidate batch."""

from __future__ import annotations

import copy
import math
from pathlib import Path
from typing import Any, Mapping

from scripts import mysql_algorithm_c_measurement_client_primary_v4 as previous
from scripts import mysql_tpch_worker_qps_normalization_v1 as normalization


ORIGINAL_MEASURE_BATCH = previous.measure_batch
SCENE = "tpch_sf4_memory"
FIELDS = (
    "schema_version", "scene", "canonical_baseline_client_qps",
    "worker_baseline_client_qps", "worker_to_canonical_factors",
    "comparison_semantics",
)


def restored_normalization(state_path: Path) -> dict[str, Any] | None:
    state = previous.predecessor.predecessor.load(state_path)
    binding = state.get("formal_reused_incumbent_baseline")
    if not isinstance(binding, Mapping):
        return None
    source_path = Path(str(binding.get("source_run_state_path") or ""))
    batch_path = Path(str(binding.get("path") or ""))
    if (
        binding.get("status") != "PASS"
        or binding.get("formal_evidence_eligible") is not True
        or source_path.is_symlink() or not source_path.is_file()
        or batch_path.is_symlink() or not batch_path.is_file()
        or previous.predecessor.predecessor.sha(source_path)
        != binding.get("source_run_state_sha256")
        or previous.predecessor.predecessor.sha(batch_path) != binding.get("sha256")
    ):
        raise RuntimeError("SHA-bound formal TPC-H R0 lineage differs")
    source = previous.predecessor.predecessor.load(source_path)
    raw_path = source.get("same_worker_qps_normalization_path")
    raw_hash = source.get("same_worker_qps_normalization_sha256")
    if raw_path is None or raw_hash is None:
        return None
    audit_path = Path(str(raw_path))
    if (
        audit_path.is_symlink() or not audit_path.is_file()
        or previous.predecessor.predecessor.sha(audit_path) != raw_hash
        or source.get("freeze_sha256") != state.get("freeze_sha256")
        or source.get("status") != "COMPLETE"
        or source.get("baseline_only") is not True
    ):
        raise RuntimeError("SHA-bound TPC-H normalization audit differs")
    audit = previous.predecessor.predecessor.load(audit_path)
    batch = previous.predecessor.predecessor.load(batch_path)
    expected = normalization.baseline_contract(batch, scene=SCENE)
    if (
        audit.get("status") != "PASS"
        or audit.get("formal_scope") != "mysql_only"
        or audit.get("cross_engine_comparison_prohibited") is not True
        or Path(str(audit.get("baseline_batch_path") or "")).resolve()
        != batch_path.resolve()
        or audit.get("baseline_batch_sha256") != binding.get("sha256")
        or set(audit.get("worker_baseline_client_qps") or {})
        != {"tpch_w1", "tpch_w2"}
    ):
        raise RuntimeError("formal TPC-H normalization source differs")
    for name in FIELDS:
        actual = audit.get(name)
        target = expected[name]
        if isinstance(target, float):
            if not math.isclose(float(actual or 0), target, rel_tol=1e-12):
                raise RuntimeError(f"formal TPC-H normalization differs: {name}")
        elif isinstance(target, dict):
            if set(actual or {}) != set(target):
                raise RuntimeError(f"formal TPC-H normalization differs: {name}")
            if any(not math.isclose(float(actual[key]), value, rel_tol=1e-12)
                   for key, value in target.items()):
                raise RuntimeError(f"formal TPC-H normalization differs: {name}")
        elif actual != target:
            raise RuntimeError(f"formal TPC-H normalization differs: {name}")
    return {name: copy.deepcopy(audit[name]) for name in FIELDS}


def measure_batch(
    freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
    assignments: Mapping[str, Mapping[str, Any]], output: Path,
    reset_token: str, *, state_path: Path | None = None,
    stage_prefix: str = "FORMAL_POINT",
) -> dict[str, dict[str, Any]]:
    if (
        state_path is not None
        and output.name != "formal_incumbent_baseline"
        and any(row.get("scene") == SCENE for row in workers.values())
    ):
        contract = restored_normalization(state_path)
        if contract is not None:
            previous._NORMALIZATION.clear()
            previous._NORMALIZATION.update(contract)
    return ORIGINAL_MEASURE_BATCH(
        freeze, workers, assignments, output, reset_token,
        state_path=state_path, stage_prefix=stage_prefix,
    )


__all__ = ["measure_batch", "restored_normalization"]
