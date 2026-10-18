#!/usr/bin/env python3
"""Client-primary XP reset with genuinely run-scoped quarantine paths.

The predecessor passed ``.../quarantine/<run-id>/<reset-token>`` to the
low-level reset, but that implementation intentionally retained only
``Path.name``.  Interrupted attempts therefore collided across formal runs at
``.../quarantine/<reset-token>/<worker>``.  This successor encodes both path
components into one safe directory name before entering the low-level reset
and uses the identical transformed root for cleanup.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping

from scripts import reset_mysql_xp_data_planes_client_primary_v3 as predecessor
from scripts import reset_mysql_xp_data_planes_v6 as implementation


_ORIGINAL_PREPARE = implementation._prepare_fresh_xp
_ORIGINAL_PURGE = implementation.purge_completed_discard_quarantine


def install() -> None:
    implementation._prepare_fresh_xp = _prepare_fresh_xp
    implementation.purge_completed_discard_quarantine = (
        purge_completed_discard_quarantine
    )


def uninstall() -> None:
    implementation.purge_completed_discard_quarantine = _ORIGINAL_PURGE
    implementation._prepare_fresh_xp = _ORIGINAL_PREPARE


def _run_scoped_root(quarantine_root: Path) -> Path:
    root = Path(quarantine_root)
    run_id = root.parent.name
    token = root.name
    if (
        root.parent.parent.name != "quarantine"
        or re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", run_id) is None
        or re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", token) is None
    ):
        raise RuntimeError("run-scoped XP quarantine input differs")
    scoped = root.parent.parent / f"{run_id}__{token}"
    if scoped.parent != root.parent.parent or scoped.parent.name != "quarantine":
        raise RuntimeError("run-scoped XP quarantine target differs")
    return scoped


def _prepare_fresh_xp(
    freeze: dict[str, Any], worker: dict[str, Any], prior: dict[str, Any],
    quarantine_root: Path, output_root: Path, resume_stalled_copy: bool,
    reset_context: str = "invalid_r0",
    allow_unresponsive_old_container: bool = False,
    allow_bootstrap_touched_staging: bool = False,
    force_fresh_copy_on_resume: bool = False,
    allow_absent_predecessor_after_interrupted_discard: bool = False,
) -> dict[str, Any]:
    return _ORIGINAL_PREPARE(
        freeze, worker, prior, _run_scoped_root(quarantine_root), output_root,
        resume_stalled_copy, reset_context,
        allow_unresponsive_old_container=allow_unresponsive_old_container,
        allow_bootstrap_touched_staging=allow_bootstrap_touched_staging,
        force_fresh_copy_on_resume=force_fresh_copy_on_resume,
        allow_absent_predecessor_after_interrupted_discard=(
            allow_absent_predecessor_after_interrupted_discard
        ),
    )


def purge_completed_discard_quarantine(
    *, quarantine_root: Path, reset_workers: Mapping[str, Mapping[str, Any]],
    binding_path: Path, cleanup_path: Path,
) -> dict[str, Any]:
    return _ORIGINAL_PURGE(
        quarantine_root=_run_scoped_root(quarantine_root),
        reset_workers=reset_workers,
        binding_path=binding_path,
        cleanup_path=cleanup_path,
    )


def configure(
    *, freeze: Path, prepared: Path,
    xp_workers: Mapping[str, Mapping[str, Any]],
) -> None:
    install()
    predecessor.configure(
        freeze=freeze, prepared=prepared, xp_workers=xp_workers,
    )
    implementation.AUTHORITY_VERSION = "client_primary_run_scoped_quarantine_v4"
    implementation.OUTPUT_SCHEMA = "mysql_client_primary_xp_reset_v4"


def __getattr__(name: str) -> Any:
    return getattr(predecessor, name)


for _name in dir(predecessor):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(predecessor, _name))
