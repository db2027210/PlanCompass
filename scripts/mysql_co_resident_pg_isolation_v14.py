#!/usr/bin/env python3
"""Admit bounded disk1 PG capture wrappers with live ancestry proof.

The PG capture process launches a tiny full-host-cpuset shim before the actual
database/client work, whose CPU affinity is already sealed and disjoint.  The
predecessor correctly rejects arbitrary full-host processes.  This successor
removes only the two CPU-mask errors for an exact, hashed disk1 capture
lineage when both the shim and its launcher have bounded live CPU/RSS usage.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from scripts import mysql_co_resident_pg_isolation_v13 as predecessor
from scripts import mysql_cross_project_wave_coordination_v1 as coordination


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_pg_capture_wrapper_allowance_20260823_v1"
    / "allowance_contract.json"
)
PG_CAPTURE = Path(
    "/home/anonymous/program/resource_adaptive/shell/run_job_fixed_benchmark_capture.py"
)
REMOVABLE = {
    "external_cpu_not_in_frozen_union",
    "external_cpu_overlaps_mysql",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_contract() -> dict[str, Any]:
    if CONTRACT.is_symlink() or not CONTRACT.is_file():
        raise RuntimeError("PG capture wrapper allowance contract is absent")
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError("PG capture wrapper allowance contract is invalid")
    contract = dict(value)
    if (
        contract.get("schema_version")
        != "mysql_pg_capture_wrapper_allowance_contract_v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("external_mutation") is not False
        or contract.get("capture_script_path") != str(PG_CAPTURE)
        or contract.get("capture_script_sha256") != sha(PG_CAPTURE)
        or int(contract.get("maximum_recognized_processes") or 0) != 4
        or int(contract.get("maximum_wrapper_rss_bytes") or 0) != 8 * 1024 * 1024
        or int(contract.get("maximum_launcher_rss_bytes") or 0) != 32 * 1024 * 1024
        or float(contract.get("maximum_total_lifetime_cpu_cores") or 0) != 0.1
        or contract.get("disk1_lineage_required") is not True
        or contract.get("disk2_lineage_prohibited") is not True
        or contract.get("base_container_and_workload_isolation_must_pass") is not True
    ):
        raise RuntimeError("PG capture wrapper allowance contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != binding.get("sha256"):
            raise RuntimeError(f"PG wrapper allowance source differs: {path}")
    return contract


def _proc_cmdline(pid: int) -> str:
    raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    return " ".join(part.decode("utf-8", "replace") for part in raw.split(b"\0") if part)


def _proc_parent(pid: int) -> int:
    for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
        if line.startswith("PPid:"):
            return int(line.split(":", 1)[1].strip())
    raise RuntimeError(f"PPid absent: {pid}")


def _proc_rss(pid: int) -> int:
    for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024
    return 0


def _lifetime_cpu_cores(pid: int) -> float:
    fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
    ticks = int(fields[11]) + int(fields[12])
    start_ticks = int(fields[19])
    hz = float(os.sysconf("SC_CLK_TCK"))
    uptime = float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
    elapsed = max(uptime - start_ticks / hz, 1e-9)
    return ticks / hz / elapsed


def _capture_ancestor(pid: int, maximum_depth: int) -> tuple[int, str] | None:
    current = pid
    for _depth in range(maximum_depth + 1):
        command = _proc_cmdline(current)
        if str(PG_CAPTURE) in command:
            return current, command
        current = _proc_parent(current)
        if current <= 1:
            return None
    return None


def wrapper_evidence(pid: int, row: Mapping[str, Any], contract: Mapping[str, Any]) -> dict[str, Any] | None:
    try:
        full_host = set(range(os.cpu_count() or 1))
        row_cpus = coordination.cpus(str(row.get("cpus_allowed_list") or ""))
        if row_cpus != full_host or int(row.get("rss_bytes") or 0) > int(
            contract["maximum_wrapper_rss_bytes"]
        ):
            return None
        ancestor = _capture_ancestor(pid, int(contract["maximum_ancestor_depth"]))
        if ancestor is None:
            return None
        ancestor_pid, command = ancestor
        if (
            "/disk1/resource_adaptive/" not in command
            or "/disk2/" in command
            or " shim -t runxp " not in f" {command} "
            or _proc_rss(ancestor_pid) > int(contract["maximum_launcher_rss_bytes"])
        ):
            return None
        wrapper_cpu = _lifetime_cpu_cores(pid)
        launcher_cpu = _lifetime_cpu_cores(ancestor_pid)
        return {
            "pid": pid,
            "launcher_pid": ancestor_pid,
            "launcher_command_sha256": hashlib.sha256(command.encode()).hexdigest(),
            "wrapper_rss_bytes": int(row.get("rss_bytes") or 0),
            "launcher_rss_bytes": _proc_rss(ancestor_pid),
            "wrapper_lifetime_cpu_cores": wrapper_cpu,
            "launcher_lifetime_cpu_cores": launcher_cpu,
            "combined_lifetime_cpu_cores": wrapper_cpu + launcher_cpu,
            "capture_script_sha256": sha(PG_CAPTURE),
            "disk1_lineage": True,
            "disk2_lineage": False,
            "external_mutation": False,
        }
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError, ValueError):
        return None


def apply_allowance(
    workloads: list[dict[str, Any]], result: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    value = copy.deepcopy(dict(result))
    if not workloads or value.get("passed") is True:
        value["pg_capture_wrapper_allowance"] = {
            "recognized": [], "recognized_pid_count": 0,
            "external_mutation": False,
        }
        return value
    by_pid = {int(row["pid"]): row for row in workloads}
    evidence: dict[int, dict[str, Any]] = {}
    for reported in value.get("workloads") or []:
        pid = int(reported["pid"])
        if set(reported.get("errors") or []) != REMOVABLE or pid not in by_pid:
            continue
        proof = wrapper_evidence(pid, reported, contract)
        if proof is not None:
            evidence[pid] = proof
    total_cpu = sum(row["combined_lifetime_cpu_cores"] for row in evidence.values())
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
            row["errors"] = [name for name in row.get("errors") or [] if name not in REMOVABLE]
            row["pg_capture_wrapper"] = True
            row["pg_capture_wrapper_evidence"] = evidence[pid]
            errors = [
                name for name in errors
                if name not in {f"pid_{pid}:{item}" for item in REMOVABLE}
            ]
    value["errors"] = errors
    value["workloads"] = rows
    value["passed"] = not errors
    value["status"] = "PASS_ISOLATED_CO_RESIDENT_PG" if not errors else "BLOCKED_FAIL_CLOSED"
    value["pg_capture_wrapper_allowance"] = {
        "recognized": [evidence[pid] for pid in sorted(evidence)],
        "recognized_pid_count": len(evidence),
        "total_lifetime_cpu_cores": total_cpu,
        "maximum_recognized_processes": contract.get("maximum_recognized_processes"),
        "maximum_total_lifetime_cpu_cores": contract.get("maximum_total_lifetime_cpu_cores"),
        "passed": allowed,
        "external_mutation": False,
    }
    return value


def evaluate_co_resident_pg_isolation(workloads: list[dict[str, Any]]) -> dict[str, Any]:
    contract = load_contract()
    return apply_allowance(workloads, predecessor.evaluate_co_resident_pg_isolation(workloads), contract)


def install() -> None:
    predecessor.install()
    coordination.evaluate_co_resident_pg_isolation = evaluate_co_resident_pg_isolation


__all__ = ["apply_allowance", "evaluate_co_resident_pg_isolation", "install", "load_contract", "wrapper_evidence"]
