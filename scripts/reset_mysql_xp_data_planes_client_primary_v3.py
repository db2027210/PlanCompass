#!/usr/bin/env python3
"""v20 XP reset with run-scoped quarantine cleanup resolution."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from scripts import reset_mysql_xp_data_planes_client_primary_v2 as predecessor


_original_purge = predecessor.purge_completed_discard_quarantine


def purge_completed_discard_quarantine(
    *, quarantine_root: Path, reset_workers: Mapping[str, Mapping[str, Any]],
    binding_path: Path, cleanup_path: Path,
) -> dict[str, Any]:
    effective = quarantine_root
    if not effective.exists():
        # v6 passes .../quarantine/<run>/<token>, while _prepare_fresh_xp
        # intentionally stores the discarded trees at
        # .../quarantine/<token>/<worker>.  Resolve that exact existing root;
        # never create, guess, or delete a broader directory.
        alternate = quarantine_root.parent.parent / quarantine_root.name
        if (
            alternate.is_symlink() or not alternate.is_dir()
            or alternate.parent.name != "quarantine"
        ):
            raise RuntimeError("run-scoped quarantine cleanup authority differs")
        effective = alternate
    return _original_purge(
        quarantine_root=effective, reset_workers=reset_workers,
        binding_path=binding_path, cleanup_path=cleanup_path,
    )


def configure(
    *, freeze: Path, prepared: Path,
    xp_workers: Mapping[str, Mapping[str, Any]],
) -> None:
    predecessor.configure(
        freeze=freeze, prepared=prepared, xp_workers=xp_workers,
    )
    predecessor.predecessor.predecessor._v6.AUTHORITY_VERSION = (
        "client_primary_run_scoped_cleanup_v3"
    )
    predecessor.predecessor.predecessor._v6.OUTPUT_SCHEMA = (
        "mysql_client_primary_xp_reset_v3"
    )


def __getattr__(name: str) -> Any:
    return getattr(predecessor, name)


for _name in dir(predecessor):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(predecessor, _name))

