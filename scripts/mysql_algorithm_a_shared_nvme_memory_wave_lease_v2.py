#!/usr/bin/env python3
"""Successor shared-NVMe lease binding for corrected cache adapters."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from scripts import mysql_algorithm_a_shared_nvme_memory_wave_lease_v1 as v1


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_memory_nvme_wave_20260823_v2"
    / "memory_wave_contract.json"
)
LOCK_PATH = v1.LOCK_PATH
MEMORY_SCENES = v1.MEMORY_SCENES


def validate_contract() -> dict[str, Any]:
    if CONTRACT.is_symlink() or not CONTRACT.is_file():
        raise RuntimeError("memory shared-NVMe v2 contract is absent")
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if (
        not isinstance(contract, dict)
        or contract.get("schema_version")
        != "mysql_algorithm_a_memory_shared_nvme_wave_contract_v2"
        or contract.get("release_version") != "v2"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("algorithm") != "A"
        or contract.get("scenes") != sorted(MEMORY_SCENES)
        or contract.get("shared_device") != "/dev/nvme2n1"
        or contract.get("physical_wave_mutual_exclusion") is not True
        or contract.get("per_scene_outer_state_machine_unchanged") is not True
        or contract.get("candidate_and_acceptance_policy_unchanged") is not True
        or contract.get("cached_valid_prefix_reused") is not True
        or contract.get("generic_measurement_fallback_prohibited") is not True
        or contract.get("cache_adapter_rebound_after_authority_validation") is not True
        or contract.get("wall_time_limit_seconds") != 900
    ):
        raise RuntimeError("memory shared-NVMe v2 contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        source = ROOT / str(binding.get("path") or "")
        if (
            source.is_symlink()
            or not source.is_file()
            or v1.sha(source) != binding.get("sha256")
        ):
            raise RuntimeError(f"memory shared-NVMe v2 source differs: {source}")
    return contract


def install(base: Any) -> Callable[..., Any]:
    validate_contract()
    v1.CONTRACT = CONTRACT
    v1.validate_contract = validate_contract
    return v1.install(base)


__all__ = ["CONTRACT", "LOCK_PATH", "MEMORY_SCENES", "install", "validate_contract"]
