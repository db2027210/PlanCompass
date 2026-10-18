#!/usr/bin/env python3
"""R0 inventory using the race-free bounded PG-wrapper allowance."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts import mysql_co_resident_pg_isolation_v15 as allowance
from scripts import preflight_mysql_four_scene_v3 as predecessor


_docker_inventory = predecessor._docker_inventory


def _rss_bytes(pid: int) -> int:
    try:
        return allowance.predecessor._proc_rss(pid)
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError, ValueError):
        return 0


def _process_gone(pid: int) -> bool:
    """Return true only when the inventoried PID no longer exists.

    A reused PID or changed command remains fail-closed as a blocker.
    """

    try:
        (
            Path(f"/proc/{pid}/cmdline")
            .read_bytes()
            .replace(b"\0", b" ")
            .decode("utf-8", errors="replace")
            .strip()
        )
    except (FileNotFoundError, ProcessLookupError):
        return True
    except (PermissionError, OSError):
        return False
    return False


def _process_inventory(
    target_cpus: set[int], target_mems: set[int] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    inventory, blockers = predecessor._process_inventory(target_cpus, target_mems)
    contract = allowance.load_contract()
    candidate_proofs: dict[int, dict[str, Any]] = {}
    vanished_pids: set[int] = set()
    for row in blockers:
        if row.get("reason") != "active_benchmark_or_controller_cpu_overlap":
            continue
        pid = int(row["pid"])
        evidence_row = {**row, "rss_bytes": _rss_bytes(pid)}
        proof = allowance.predecessor.wrapper_evidence(
            pid, evidence_row, contract,
        )
        if proof is not None:
            candidate_proofs[pid] = proof
        elif _process_gone(pid):
            vanished_pids.add(pid)

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
    removed = recognized | vanished_pids
    if removed:
        for row in inventory:
            pid = int(row["pid"])
            if pid in recognized:
                row["pg_capture_wrapper"] = True
                row["pg_capture_wrapper_evidence"] = candidate_proofs[pid]
            elif pid in vanished_pids:
                row["process_exited_before_point_evaluation"] = True
        blockers = [row for row in blockers if int(row["pid"]) not in removed]
    return inventory, blockers


__all__ = ["_docker_inventory", "_process_inventory"]
