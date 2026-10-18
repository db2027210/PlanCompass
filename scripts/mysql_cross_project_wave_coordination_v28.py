#!/usr/bin/env python3
"""Recognize CPU-disjoint frozen sibling controller descendants in the D gate."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from scripts import mysql_cross_project_wave_coordination_v27 as v27


RELEASE = "v172-disjoint-frozen-sibling-descendant"
prior = v27
LOCK_PATH = v27.LOCK_PATH
CONTRACT_PATH = v27.CONTRACT_PATH
target_local_resource_proof = v27.target_local_resource_proof
_ORIGINAL_INVENTORY = v27._inventory
_ORIGINAL_GATE = v27.rolling_quiet_gate
_EXCLUSIONS: list[dict[str, Any]] = []


def _ancestor_scene(pid: int) -> tuple[str | None, list[int]]:
    chain: list[int] = []
    seen: set[int] = set()
    while pid > 1 and pid not in seen and len(chain) < 24:
        seen.add(pid)
        chain.append(pid)
        proc = Path("/proc") / str(pid)
        try:
            raw = (proc / "cmdline").read_bytes().split(b"\0")
            tokens = [item.decode("utf-8", errors="replace") for item in raw if item]
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            tokens = []
        if "--scene" in tokens:
            index = tokens.index("--scene")
            if index + 1 < len(tokens):
                return tokens[index + 1], chain
        status = v27._status(proc)
        try:
            pid = int(status.get("PPid") or 0)
        except ValueError:
            break
    return None, chain


def _scene_client_cpus() -> tuple[dict[str, set[int]], dict[str, str]]:
    freeze = v27._load(v27.FREEZE)
    scene_cpus: dict[str, set[int]] = {}
    worker_scene: dict[str, str] = {}
    for row in freeze.get("workers") or []:
        scene = str(row["scene"])
        worker_scene[str(row["worker_id"])] = scene
        scene_cpus.setdefault(scene, set()).update(v27._cpus(row["client_cpuset"]))
    return scene_cpus, worker_scene


def _inventory(
    target_cpus: set[int], target_worker_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[int]]:
    workloads, d_rows, own_tree = _ORIGINAL_INVENTORY(
        target_cpus, target_worker_ids
    )
    scene_cpus, worker_scene = _scene_client_cpus()
    target_scenes = {
        worker_scene[worker] for worker in target_worker_ids if worker in worker_scene
    }
    retained: list[dict[str, Any]] = []
    for raw in d_rows:
        row = copy.deepcopy(raw)
        allowed = v27._cpus(str(row.get("cpus_allowed_list") or ""))
        ancestor_scene, chain = _ancestor_scene(int(row["pid"]))
        sibling_cpus = scene_cpus.get(str(ancestor_scene), set())
        permitted = bool(
            ancestor_scene in scene_cpus
            and ancestor_scene not in target_scenes
            and allowed
            and allowed <= sibling_cpus
            and not (allowed & target_cpus)
            and not row.get("sampled_processor_on_target")
        )
        if permitted:
            row.update({
                "allowed_disjoint_frozen_mysql_sibling_controller_descendant": True,
                "sibling_scene": ancestor_scene,
                "ancestor_chain": chain,
                "classification_release": RELEASE,
                "storage_activity_bounded_by_frozen_sibling_envelope": True,
            })
            _EXCLUSIONS.append(row)
        else:
            retained.append(raw)
    return workloads, retained, own_tree


def rolling_quiet_gate(**kwargs: Any) -> dict[str, Any]:
    _EXCLUSIONS.clear()
    result = _ORIGINAL_GATE(**kwargs)
    result["disjoint_frozen_sibling_descendant_release"] = {
        "release_version": RELEASE,
        "source_path": str(Path(__file__).resolve()),
        "source_sha256": v27._sha(Path(__file__).resolve()),
        "cpu_disjoint_required": True,
        "frozen_scene_client_cpuset_required": True,
        "controller_ancestry_scene_required": True,
        "target_sampled_processor_prohibited": True,
        "external_postgresql_exempted": False,
        "kernel_d_state_exempted": False,
        "external_process_control_or_stop_performed": False,
    }
    result["excluded_disjoint_frozen_sibling_d_state_rows"] = copy.deepcopy(
        _EXCLUSIONS
    )
    return result


def install() -> None:
    v27._inventory = _inventory
    v27.rolling_quiet_gate = rolling_quiet_gate
