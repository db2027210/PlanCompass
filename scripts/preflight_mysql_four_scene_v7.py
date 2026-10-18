#!/usr/bin/env python3
"""Full-parallel successor: admit exact CPU-disjoint sibling MySQL workers."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import preflight_mysql_four_scene_v6 as predecessor
from scripts.preflight_mysql_four_scene_v6 import *  # noqa: F401,F403


TRANSITION = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v99_to_a_external_diagnostic_resume_20260823_job_dynamic_slots_v1"
    / "job_memory.transition_authority.json"
)
RESOURCE_FREEZE = (
    ROOT / "config/formal"
    / "frozen_mysql_algorithm_a_job_external_diagnostic_slots_20260823_v1"
    / "job_four_worker_resource_successor.json"
)
ALLOWED_SIBLINGS = {"job_w3", "job_w4"}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def _allowed_profiles() -> dict[str, dict[str, Any]]:
    transition = _load(TRANSITION)
    freeze = _load(RESOURCE_FREEZE)
    if (
        transition.get("schema_version")
        != "mysql_algorithm_a_job_four_worker_transition_authority_v1"
        or transition.get("status") != "PASS"
        or transition.get("formal_scope") != "mysql_only"
        or transition.get("cross_engine_comparison_prohibited") is not True
        or freeze.get("schema_version")
        != "mysql_algorithm_a_job_four_worker_resource_successor_v1"
        or freeze.get("formal_scope") != "mysql_only"
        or freeze.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("full-parallel sibling authority differs")
    transition_rows = {
        str(row["worker_id"]): row for row in transition.get("workers") or []
    }
    frozen_rows = {
        str(row["worker_id"]): row for row in freeze.get("workers") or []
    }
    if not ALLOWED_SIBLINGS.issubset(transition_rows) or not ALLOWED_SIBLINGS.issubset(
        frozen_rows
    ):
        raise RuntimeError("full-parallel sibling worker set differs")
    result: dict[str, dict[str, Any]] = {}
    for worker_id in sorted(ALLOWED_SIBLINGS):
        row = transition_rows[worker_id]
        frozen = frozen_rows[worker_id]
        for field in (
            "container", "database_cpuset", "database_cpuset_mems",
            "memory_bytes", "pids_max",
        ):
            if row.get(field) != frozen.get(field):
                raise RuntimeError(f"{worker_id} sibling frozen profile differs")
        result[str(row["container"])] = row
    return result


def _profile_passes(
    row: dict[str, Any], profile: dict[str, Any], target_cpus: set[int],
) -> bool:
    if (
        row.get("container_id") != profile.get("container_id")
        or row.get("cpuset_cpus") != profile.get("database_cpuset")
        or row.get("cpuset_mems") != profile.get("database_cpuset_mems")
        or row.get("overlap_cpus") not in (None, "")
        or predecessor._cpus(str(profile["database_cpuset"])) & target_cpus
    ):
        return False
    document = json.loads(subprocess.run(
        ["docker", "inspect", str(profile["container"])],
        text=True, capture_output=True, check=True, timeout=30,
    ).stdout)[0]
    host = document.get("HostConfig") or {}
    state = document.get("State") or {}
    labels = (document.get("Config") or {}).get("Labels") or {}
    sources = {
        str(mount.get("Source") or "") for mount in document.get("Mounts") or []
        if str(mount.get("Source") or "").startswith("/disk2/")
    }
    datadir = Path(str(profile["datadir"]))
    credentials = Path(str(profile["credential_defaults"])).parent
    return bool(
        state.get("Running") is True
        and state.get("Restarting") is not True
        and document.get("Id") == profile.get("container_id")
        and host.get("CpusetCpus") == profile.get("database_cpuset")
        and host.get("CpusetMems") == profile.get("database_cpuset_mems")
        and int(host.get("Memory") or 0) == int(profile["memory_bytes"])
        and int(host.get("PidsLimit") or 0) == int(profile["pids_max"])
        and labels.get("ra.owner") == "mysql_four_scene_v5"
        and str(datadir) in sources
        and sources
        and all(
            source == str(datadir)
            or source == str(credentials)
            or source.startswith(str(credentials) + "/")
            for source in sources
        )
    )


def _docker_inventory(
    target_cpus: set[int], intended_names: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    inventory, blockers = predecessor._docker_inventory(
        target_cpus, intended_names,
    )
    profiles = _allowed_profiles()
    admitted: set[str] = set()
    retained: list[dict[str, Any]] = []
    for blocker in blockers:
        name = str(blocker.get("name") or "")
        profile = profiles.get(name)
        if (
            blocker.get("reason") == "co_resident_database_uses_target_disk2"
            and profile is not None
            and _profile_passes(blocker, profile, target_cpus)
        ):
            admitted.add(name)
        else:
            retained.append(blocker)
    for row in inventory:
        if str(row.get("name") or "") in admitted:
            row.update({
                "allowed_cpu_disjoint_sibling_mysql_database": True,
                "sibling_authority_path": str(TRANSITION),
                "sibling_resource_freeze_path": str(RESOURCE_FREEZE),
                "sibling_scene_completion_dependency": False,
                "sibling_container_mutation_performed": False,
            })
    return inventory, retained


__all__ = [
    "_compress", "_cpus", "_database_workload_processes",
    "_docker_inventory", "_process_inventory",
]
