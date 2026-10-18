#!/usr/bin/env python3
"""Wait for shared-disk isolation, then run one strict nested four-VM experiment."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.run_hybenchxp_four_vm_ab_when_disk_idle import (  # noqa: E402
    disk_device,
    record,
    run_strategy_until_complete,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sequence-id",
        default=f"nested_v1_{time.strftime('%Y%m%d_%H%M%S')}",
    )
    parser.add_argument("--sequence-root", type=Path, default=None)
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=Path("/disk2/resource_adaptive/binary_plan_tuning_hybenchxp_vm/runs"),
    )
    parser.add_argument(
        "--launcher",
        type=Path,
        default=ROOT / "shell/run_hybenchxp_four_vm_recall_full_convergence.sh",
    )
    parser.add_argument("--score-b-topk", type=int, default=20)
    parser.add_argument("--disk-sample-sec", type=float, default=5.0)
    parser.add_argument("--poll-sec", type=float, default=30.0)
    parser.add_argument("--run-poll-sec", type=float, default=60.0)
    parser.add_argument("--retry-sec", type=float, default=60.0)
    parser.add_argument("--quiet-samples", type=int, default=10)
    parser.add_argument("--max-read-mib-s", type=float, default=5.0)
    parser.add_argument("--max-write-mib-s", type=float, default=5.0)
    parser.add_argument("--max-util-pct", type=float, default=10.0)
    parser.add_argument("--max-docker-mib-s", type=float, default=1.0)
    parser.add_argument("--max-attempts", type=int, default=0, help="0 retries indefinitely")
    parser.add_argument(
        "--conflict-process-substring",
        default="hyxp_io_mild_fullsearch_disk1x2",
    )
    args = parser.parse_args()
    if args.score_b_topk <= 1:
        parser.error("--score-b-topk must be greater than one")
    if args.quiet_samples <= 0:
        parser.error("--quiet-samples must be positive")

    args.runs_root = args.runs_root.resolve()
    args.launcher = args.launcher.resolve()
    args.comparison_script = None
    sequence_root = (
        args.sequence_root.resolve()
        if args.sequence_root is not None
        else args.runs_root / "nested_sequences" / args.sequence_id
    )
    sequence_root.mkdir(parents=True, exist_ok=True)
    history_path = sequence_root / "sequence_history.jsonl"
    if history_path.exists() and history_path.stat().st_size > 0:
        raise RuntimeError(f"sequence root already has history: {sequence_root}")

    device_name, device_number = disk_device()
    record(
        sequence_root,
        "sequence_start",
        sequence_id=args.sequence_id,
        launcher=str(args.launcher),
        score_b_topk=args.score_b_topk,
        order=["NESTED"],
        disk_device_name=device_name,
        disk_device_number=device_number,
        quiet_samples=args.quiet_samples,
        conflict_process_substring=args.conflict_process_substring,
    )
    try:
        run_root = run_strategy_until_complete(
            sequence_root=sequence_root,
            strategy="NESTED",
            args=args,
            device_name=device_name,
            device_number=device_number,
        )
        record(
            sequence_root,
            "sequence_complete",
            nested_run_root=str(run_root),
            completion_audit=str(run_root / "convergence_completion_audit.json"),
            completion_report=str(run_root / "convergence_completion_audit.md"),
        )
        return 0
    except BaseException as exc:
        record(sequence_root, "sequence_failed", error=f"{type(exc).__name__}: {exc}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
