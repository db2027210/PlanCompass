#!/usr/bin/env python3
"""Retain bounded PG-wrapper proof across the inventory/evaluation race.

The v14 evaluator proved a wrapper by re-reading its live ``/proc`` ancestry
after the process inventory had completed.  The capture shim is deliberately
short lived, so it can disappear in that small interval even though the
inventory already observed the exact sealed lineage.  This successor captures
the complete v14 proof immediately after inventory and reuses only that proof
during evaluation.  Every v14 CPU, RSS, disk1-lineage, source-hash and count
bound remains unchanged.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

from scripts import mysql_co_resident_pg_isolation_v14 as predecessor
from scripts import mysql_cross_project_wave_coordination_v1 as coordination


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_pg_capture_wrapper_allowance_20260823_v2"
    / "allowance_contract.json"
)
REMOVABLE = set(predecessor.REMOVABLE)
_BASE_EXTERNAL_PROCESS_INVENTORY = coordination.external_process_inventory


def sha(path: Path) -> str:
    return predecessor.sha(path)


def load_contract() -> dict[str, Any]:
    if CONTRACT.is_symlink() or not CONTRACT.is_file():
        raise RuntimeError("PG capture wrapper snapshot contract is absent")
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError("PG capture wrapper snapshot contract is invalid")
    contract = dict(value)
    if (
        contract.get("schema_version")
        != "mysql_pg_capture_wrapper_allowance_contract_v2"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("external_mutation") is not False
        or contract.get("capture_script_path") != str(predecessor.PG_CAPTURE)
        or contract.get("capture_script_sha256")
        != sha(predecessor.PG_CAPTURE)
        or int(contract.get("maximum_recognized_processes") or 0) != 4
        or int(contract.get("maximum_wrapper_rss_bytes") or 0)
        != 8 * 1024 * 1024
        or int(contract.get("maximum_launcher_rss_bytes") or 0)
        != 32 * 1024 * 1024
        or float(contract.get("maximum_total_lifetime_cpu_cores") or 0)
        != 0.1
        or contract.get("maximum_ancestor_depth") != 4
        or contract.get("disk1_lineage_required") is not True
        or contract.get("disk2_lineage_prohibited") is not True
        or contract.get("base_container_and_workload_isolation_must_pass")
        is not True
        or contract.get("inventory_time_proof_snapshot_required") is not True
        or contract.get("post_inventory_live_process_required") is not False
        or contract.get("arbitrary_full_host_cpuset_process_allowed") is not False
    ):
        raise RuntimeError("PG capture wrapper snapshot contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != binding.get("sha256"):
            raise RuntimeError(f"PG wrapper snapshot source differs: {path}")
    return contract


def snapshot_external_process_inventory(
    run_id: str, target_mems: set[int], *,
    ignored_pids: set[int] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Attach a complete v14 proof while each inventoried PID is still live."""

    inventory = _BASE_EXTERNAL_PROCESS_INVENTORY(
        run_id, target_mems, ignored_pids=ignored_pids,
    )
    contract = load_contract()
    for row in inventory.get("workloads") or []:
        try:
            pid = int(row["pid"])
            candidate = {
                **row,
                "rss_bytes": predecessor._proc_rss(pid),
            }
            proof = predecessor.wrapper_evidence(pid, candidate, contract)
        # The wrapper may exit between the base inventory and this immediate
        # enrichment step.  That row simply remains unproved and fail-closed;
        # it must not abort the entire rolling gate.
        except (
            FileNotFoundError,
            ProcessLookupError,
            PermissionError,
            OSError,
            KeyError,
            TypeError,
            ValueError,
        ):
            proof = None
        if proof is not None:
            row["pg_capture_wrapper_inventory_proof"] = proof
    return inventory


def apply_allowance(
    workloads: list[dict[str, Any]], result: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    value = copy.deepcopy(dict(result))
    if not workloads or value.get("passed") is True:
        value["pg_capture_wrapper_allowance"] = {
            "recognized": [],
            "recognized_pid_count": 0,
            "inventory_time_proof_reused": False,
            "external_mutation": False,
        }
        return value

    by_pid = {int(row["pid"]): row for row in workloads}
    evidence: dict[int, dict[str, Any]] = {}
    snapshot_pids: set[int] = set()
    for reported in value.get("workloads") or []:
        pid = int(reported["pid"])
        if set(reported.get("errors") or []) != REMOVABLE or pid not in by_pid:
            continue
        raw = by_pid[pid]
        proof = raw.get("pg_capture_wrapper_inventory_proof")
        if isinstance(proof, Mapping):
            proof = copy.deepcopy(dict(proof))
            snapshot_pids.add(pid)
        else:
            proof = predecessor.wrapper_evidence(
                pid, {**raw, **reported}, contract,
            )
        if proof is not None:
            evidence[pid] = proof

    total_cpu = sum(
        float(row["combined_lifetime_cpu_cores"])
        for row in evidence.values()
    )
    allowed = bool(evidence) and (
        len(evidence) <= int(contract["maximum_recognized_processes"])
        and total_cpu <= float(contract["maximum_total_lifetime_cpu_cores"])
    )
    rows = copy.deepcopy(value.get("workloads") or [])
    errors = list(value.get("errors") or [])
    if allowed:
        for row in rows:
            pid = int(row["pid"])
            if pid not in evidence:
                continue
            row["errors"] = [
                name for name in row.get("errors") or []
                if name not in REMOVABLE
            ]
            row["pg_capture_wrapper"] = True
            row["pg_capture_wrapper_evidence"] = evidence[pid]
            row["inventory_time_proof_reused"] = pid in snapshot_pids
            errors = [
                name for name in errors
                if name not in {f"pid_{pid}:{item}" for item in REMOVABLE}
            ]
    value["errors"] = errors
    value["workloads"] = rows
    value["passed"] = not errors
    value["status"] = (
        "PASS_ISOLATED_CO_RESIDENT_PG" if not errors
        else "BLOCKED_FAIL_CLOSED"
    )
    value["pg_capture_wrapper_allowance"] = {
        "recognized": [evidence[pid] for pid in sorted(evidence)],
        "recognized_pid_count": len(evidence),
        "inventory_time_proof_reused": bool(snapshot_pids),
        "inventory_time_proof_pids": sorted(snapshot_pids),
        "total_lifetime_cpu_cores": total_cpu,
        "maximum_recognized_processes": contract.get(
            "maximum_recognized_processes"
        ),
        "maximum_total_lifetime_cpu_cores": contract.get(
            "maximum_total_lifetime_cpu_cores"
        ),
        "passed": allowed,
        "external_mutation": False,
    }
    return value


def evaluate_co_resident_pg_isolation(
    workloads: list[dict[str, Any]],
) -> dict[str, Any]:
    contract = load_contract()
    base = predecessor.predecessor.evaluate_co_resident_pg_isolation(workloads)
    return apply_allowance(workloads, base, contract)


def install() -> None:
    predecessor.install()
    coordination.external_process_inventory = snapshot_external_process_inventory
    coordination.evaluate_co_resident_pg_isolation = (
        evaluate_co_resident_pg_isolation
    )


__all__ = [
    "apply_allowance",
    "evaluate_co_resident_pg_isolation",
    "install",
    "load_contract",
    "sha",
    "snapshot_external_process_inventory",
]
