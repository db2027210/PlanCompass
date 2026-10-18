#!/usr/bin/env python3
"""Recover selected dedicated external-baseline XP workers from the seal."""

from __future__ import annotations

import argparse
import copy
from pathlib import Path
import re
from typing import Any, Dict, Set

from shell import run_mysql_four_scene_external_baselines as external


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker-binding", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--worker-id", action="append", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", args.token) is None:
        parser.error("unsafe token")

    binding: Dict[str, Any] = external.load(args.worker_binding.resolve())
    workers = {
        str(row["worker_id"]): copy.deepcopy(row)
        for row in binding.get("workers") or []
    }
    requested: Set[str] = set(args.worker_id)
    if not requested or not requested.issubset(external.mysql_alg.XP_IDS):
        parser.error(f"worker IDs must be XP workers: {sorted(requested)}")

    external.validate_live_affinity(binding, workers)
    for worker in workers.values():
        benchmark = Path(str(worker["credential_benchmark"])).resolve()
        worker["benchmark_defaults"] = str(benchmark)

    external.mysql_alg.reset = external.EXTERNAL_RESET
    external.mysql_alg.users._run_worker = external.external_provision_worker
    external.mysql_alg.classify_xp_mysql_liveness = (
        external.external_classify_xp_mysql_liveness
    )
    external.mysql_alg.reset.XP_CONTAINER_ALLOWLIST = {
        worker_id: str(workers[worker_id]["container"])
        for worker_id in external.mysql_alg.XP_IDS
    }
    selected_workers = {
        worker_id: workers[worker_id] for worker_id in sorted(requested)
    }
    external.mysql_alg.reset_xp(
        copy.deepcopy(binding["freeze"]),
        selected_workers,
        args.output_dir.resolve(),
        args.token,
        worker_ids=requested,
    )
    for worker_id in sorted(requested):
        if not external.mysql_alg.mysql_server_liveness(
            selected_workers[worker_id]
        ):
            raise RuntimeError(f"{worker_id}: MySQL liveness failed after recovery")
    print(
        f"recovered external XP workers: {','.join(sorted(requested))}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
