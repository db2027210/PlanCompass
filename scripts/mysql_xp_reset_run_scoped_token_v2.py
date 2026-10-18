#!/usr/bin/env python3
"""Install run-scoped reset tokens at the active single-scene override point."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts import mysql_single_scene_measurement_v2 as measurement


POLICY_ID = "mysql_xp_reset_run_scoped_quarantine_token_v2"


def install() -> None:
    settle = measurement.async_xp_settle
    original = settle.reset_xp
    if getattr(original, "_run_scoped_token_policy", None) == POLICY_ID:
        return

    def reset_xp(
        freeze, workers, output: Path, token: str, *, worker_ids=None,
    ) -> None:
        scoped = f"{Path(output).name}_{token}"
        return original(
            freeze, workers, output, scoped, worker_ids=worker_ids,
        )

    reset_xp._run_scoped_token_policy = POLICY_ID
    settle.reset_xp = reset_xp

