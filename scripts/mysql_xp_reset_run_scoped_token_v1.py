#!/usr/bin/env python3
"""Make XP reset quarantine tokens unique to the formal scene run."""

from __future__ import annotations

from pathlib import Path
from typing import Any


POLICY_ID = "mysql_xp_reset_run_scoped_quarantine_token_v1"


def install(alg: Any) -> None:
    original = alg.reset_xp
    if getattr(original, "_run_scoped_token_policy", None) == POLICY_ID:
        return

    def reset_xp(
        freeze, workers, output: Path, token: str, *, worker_ids=None,
    ) -> None:
        run_name = Path(output).name
        scoped = f"{run_name}_{token}"
        return original(
            freeze, workers, output, scoped, worker_ids=worker_ids,
        )

    reset_xp._run_scoped_token_policy = POLICY_ID
    alg.reset_xp = reset_xp
