#!/usr/bin/env python3
"""R0 point inventory with a bounded PG capture-wrapper exception.

This is a narrow successor to the frozen v3 inventory.  It retains every
CPU-overlap blocker except a short-lived, full-host-affinity wrapper that can
be proved to descend from the exact hashed disk1 PostgreSQL capture launcher.
It does not mutate, stop, or wait for PostgreSQL, and it does not introduce a
NUMA scheduling lock.
"""

from __future__ import annotations

from typing import Any

from scripts import mysql_co_resident_pg_isolation_v14 as allowance
from scripts import preflight_mysql_four_scene_v3 as predecessor


_docker_inventory = predecessor._docker_inventory


def _rss_bytes(pid: int) -> int:
    try:
        return allowance._proc_rss(pid)
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError, ValueError):
        return 0


def _process_inventory(
    target_cpus: set[int], target_mems: set[int] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    inventory, blockers = predecessor._process_inventory(target_cpus, target_mems)
    contract = allowance.load_contract()
    candidate_proofs: dict[int, dict[str, Any]] = {}
    for row in blockers:
        if row.get("reason") != "active_benchmark_or_controller_cpu_overlap":
            continue
        pid = int(row["pid"])
        evidence_row = {**row, "rss_bytes": _rss_bytes(pid)}
        proof = allowance.wrapper_evidence(pid, evidence_row, contract)
        if proof is not None:
            candidate_proofs[pid] = proof

    total_cpu = sum(
        float(proof["combined_lifetime_cpu_cores"])
        for proof in candidate_proofs.values()
    )
    permitted = (
        bool(candidate_proofs)
        and len(candidate_proofs) <= int(contract["maximum_recognized_processes"])
        and total_cpu <= float(contract["maximum_total_lifetime_cpu_cores"])
    )
    recognized = set(candidate_proofs) if permitted else set()
    if recognized:
        for row in inventory:
            pid = int(row["pid"])
            if pid in recognized:
                row["pg_capture_wrapper"] = True
                row["pg_capture_wrapper_evidence"] = candidate_proofs[pid]
        blockers = [row for row in blockers if int(row["pid"]) not in recognized]
    return inventory, blockers


__all__ = ["_docker_inventory", "_process_inventory"]
