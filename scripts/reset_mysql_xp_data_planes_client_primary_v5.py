#!/usr/bin/env python3
"""Client-primary XP reset with bounded stale-containerd-task recovery.

Docker can occasionally report an exited, PID-zero container while retaining
the containerd task with the same container ID.  A subsequent ``docker start``
then fails with ``task ... already exists``.  This successor recovers only that
exact, fully verified state: it removes the experiment-owned container record
(the bind-mounted datadir is preserved) and re-enters the existing interrupted
reset path, which recreates the container from the already staged data plane.
All other Docker failures remain fail-closed.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any, Mapping

from scripts import reset_mysql_xp_data_planes_client_primary_v4 as predecessor
from scripts import reset_mysql_xp_data_planes_v6 as implementation


_ORIGINAL_IMPLEMENTATION_PREPARE = implementation._prepare_fresh_xp
_PREDECESSOR_PREPARE = predecessor._prepare_fresh_xp


def _is_stale_task_start(error: subprocess.CalledProcessError, container: str) -> bool:
    command = [str(value) for value in (error.cmd or [])]
    detail = "\n".join(
        str(value or "") for value in (error.stdout, error.stderr, str(error))
    ).lower()
    return command == ["docker", "start", container] and "already exists" in detail


def _verified_stale_task(
    *, container: str, worker_id: str, datadir: Path,
    prior_container_id: str,
) -> dict[str, Any]:
    inspected = implementation._docker_json("inspect", container)
    state = dict(inspected.get("State") or {})
    labels = dict((inspected.get("Config") or {}).get("Labels") or {})
    mounts = {
        str(row.get("Destination") or ""): str(row.get("Source") or "")
        for row in (inspected.get("Mounts") or [])
    }
    if (
        inspected.get("Id") != prior_container_id
        or state.get("Running") is not False
        or int(state.get("Pid") or 0) != 0
        or "already exists" not in str(state.get("Error") or "").lower()
        or labels.get("ra.owner") != "mysql_four_scene_v5"
        or labels.get("ra.worker_id") != worker_id
        or mounts.get("/var/lib/mysql") != str(datadir)
    ):
        raise RuntimeError(f"{worker_id} stale containerd task evidence differs")
    return {
        "container": container,
        "container_id": prior_container_id,
        "state_error": state.get("Error"),
        "datadir_preserved": str(datadir),
    }


def _prepare_fresh_xp(
    freeze: dict[str, Any], worker: dict[str, Any], prior: dict[str, Any],
    quarantine_root: Path, output_root: Path, resume_stalled_copy: bool,
    reset_context: str = "invalid_r0",
    allow_unresponsive_old_container: bool = False,
    allow_bootstrap_touched_staging: bool = False,
    force_fresh_copy_on_resume: bool = False,
    allow_absent_predecessor_after_interrupted_discard: bool = False,
) -> dict[str, Any]:
    kwargs = {
        "allow_unresponsive_old_container": allow_unresponsive_old_container,
        "allow_bootstrap_touched_staging": allow_bootstrap_touched_staging,
        "force_fresh_copy_on_resume": force_fresh_copy_on_resume,
        "allow_absent_predecessor_after_interrupted_discard": (
            allow_absent_predecessor_after_interrupted_discard
        ),
    }
    try:
        return _PREDECESSOR_PREPARE(
            freeze, worker, prior, quarantine_root, output_root,
            resume_stalled_copy, reset_context, **kwargs,
        )
    except subprocess.CalledProcessError as error:
        container = str(worker.get("container") or "")
        worker_id = str(worker.get("worker_id") or "")
        if not _is_stale_task_start(error, container):
            raise
        runtime_root = Path(str(worker.get("runtime_root") or ""))
        if not runtime_root.is_absolute():
            raise RuntimeError(f"{worker_id} stale-task runtime root differs") from error
        prior_container_id = str(prior.get("container_id") or "")
        evidence = _verified_stale_task(
            container=container, worker_id=worker_id,
            datadir=runtime_root / "datadir",
            prior_container_id=prior_container_id,
        )
        implementation._docker_run(
            ["docker", "rm", "-f", container], timeout=300,
        )
        remaining = implementation._docker_run(
            ["docker", "ps", "-aq", "--filter", f"name=^{container}$"],
            timeout=60,
        ).stdout.strip()
        if remaining:
            raise RuntimeError(f"{worker_id} stale container removal differs")
        recovered = _PREDECESSOR_PREPARE(
            freeze, worker, prior, quarantine_root, output_root,
            True, reset_context,
            allow_unresponsive_old_container=True,
            allow_bootstrap_touched_staging=allow_bootstrap_touched_staging,
            force_fresh_copy_on_resume=force_fresh_copy_on_resume,
            allow_absent_predecessor_after_interrupted_discard=True,
        )
        recovered["stale_containerd_task_recovery"] = {
            **evidence,
            "container_record_removed": True,
            "bind_mounted_datadir_deleted": False,
            "recreated_from_staged_data_plane": True,
        }
        return recovered


def install() -> None:
    implementation._prepare_fresh_xp = _prepare_fresh_xp


def uninstall() -> None:
    implementation._prepare_fresh_xp = _ORIGINAL_IMPLEMENTATION_PREPARE
    implementation.purge_completed_discard_quarantine = predecessor._ORIGINAL_PURGE


def configure(
    *, freeze: Path, prepared: Path,
    xp_workers: Mapping[str, Mapping[str, Any]],
) -> None:
    predecessor.configure(
        freeze=freeze, prepared=prepared, xp_workers=xp_workers,
    )
    install()
    implementation.AUTHORITY_VERSION = "client_primary_stale_task_recovery_v5"
    implementation.OUTPUT_SCHEMA = "mysql_client_primary_xp_reset_v5"


def __getattr__(name: str) -> Any:
    return getattr(predecessor, name)


for _name in dir(predecessor):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(predecessor, _name))
