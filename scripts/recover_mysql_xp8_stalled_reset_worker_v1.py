#!/usr/bin/env python3
"""Recover one exact XP8 worker after Docker left a stopped ghost task."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_v1 as reset8


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--quarantine-anchor", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    freeze = load(args.freeze.resolve())
    workers = {str(row["worker_id"]): dict(row) for row in freeze["workers"]}
    prepared = load(args.prepared.resolve())
    prior_rows = {str(row["worker_id"]): dict(row) for row in prepared["workers"]}
    worker_id = args.worker_id
    if worker_id not in workers or worker_id not in prior_rows:
        raise RuntimeError("stalled worker authority differs")
    reset8.install_for_workers(workers)
    row = reset8.prepare_fresh_worker(
        freeze=freeze, worker=workers[worker_id], prior=prior_rows[worker_id],
        quarantine_root=args.quarantine_anchor.resolve(),
        output_root=args.output.resolve(), reset_context="formal_point",
        resume_stalled_copy=True,
    )
    print(json.dumps({"status": "PASS", "worker_id": worker_id,
                      "container_id": row["container_id"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
