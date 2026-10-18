#!/usr/bin/env python3
"""Use a bounded 900-second timeout for read-only Algorithm C plan capture."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


PLAN_CAPTURE_TIMEOUT_SECONDS = 900


def explain(worker: Mapping[str, Any], sqls: list[str]) -> list[str]:
    """Capture the same plans as v2 with a full-measurement-sized timeout.

    Plan capture remains outside the formal workload wall time and read-only.
    The successor changes only the technical subprocess deadline after valid
    JOB and native-XP captures exceeded the original 180-second deadline.
    """
    alg.private_credential(worker)
    defaults = Path(str(worker["benchmark_defaults"]))
    runner = alg.r0mod.PinnedRunner(worker, Path(worker["formal_client_log"]))
    base = [
        "mysql", f"--defaults-file={defaults}", "--batch", "--raw",
        "--skip-column-names", "--get-server-public-key",
        "--ssl-mode=DISABLED", "-h", "127.0.0.1", "-P",
        str(worker["host_port"]), str(worker["database"]),
    ]
    plans: list[str] = []
    for index, sql in enumerate(sqls, 1):
        completed = runner(
            base, input=sql, env=dict(os.environ), text=True,
            capture_output=True, timeout=PLAN_CAPTURE_TIMEOUT_SECONDS,
            check=False,
        )
        if completed.returncode or not completed.stdout.strip():
            error = (completed.stderr or "empty EXPLAIN output").strip()[-1000:]
            raise RuntimeError(
                f"{worker['worker_id']} EXPLAIN JSON #{index} failed: "
                f"rc={completed.returncode} stderr={error!r}"
            )
        plans.append(completed.stdout.strip())
    return plans
