#!/usr/bin/env python3
"""Kernel gate bound to the invoking scene controller's live authority."""

from __future__ import annotations

import os
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts import mysql_cross_project_wave_coordination_v28 as predecessor


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
CONTRACT_PATH = ROOT / (
    "config/formal/frozen_mysql_cross_project_wave_coordination_20260907_v114/"
    "coordination_contract.json"
)
LOCK_PATH = predecessor.LOCK_PATH
kernel = predecessor.prior


def bind_current_authority() -> Path:
    raw = os.environ.get("MYSQL_TWO_WORKER_AUTHORITY_ROOT", "")
    authority = Path(raw).expanduser().resolve()
    prepared = authority / "prepared_binding.json"
    if (
        not raw
        or HISTORY.resolve() not in authority.parents
        or authority.is_symlink()
        or not prepared.is_file()
        or prepared.is_symlink()
    ):
        raise RuntimeError("kernel gate current authority root differs")
    binding = json.loads(prepared.read_text(encoding="utf-8"))
    frozen_path = Path(str(binding.get("freeze_path") or "")).expanduser()
    freeze = frozen_path.resolve()
    expected_sha = str(binding.get("freeze_sha256") or "")
    if (
        ROOT.resolve() / "config/formal" not in freeze.parents
        or frozen_path.is_symlink()
        or not freeze.is_file()
        or len(expected_sha) != 64
        or hashlib.sha256(freeze.read_bytes()).hexdigest() != expected_sha
    ):
        raise RuntimeError("kernel gate current resource freeze differs")
    kernel.PREPARED = prepared
    kernel.FREEZE = freeze
    return prepared


def rolling_quiet_gate(**kwargs: Any) -> dict[str, Any]:
    prepared = bind_current_authority()
    result = predecessor.rolling_quiet_gate(**kwargs)
    result.update({
        "release_version": "v114-live-authority-kernel-gate",
        "live_authority_prepared_path": str(prepared),
    })
    return result


def target_local_resource_proof(target_cpus: set[int]) -> dict[str, Any]:
    prepared = bind_current_authority()
    result = predecessor.target_local_resource_proof(target_cpus)
    result["live_authority_prepared_path"] = str(prepared)
    return result
