#!/usr/bin/env python3
"""Run v49 diagnostics and retain automatic gate/formal routing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    import build_tpcc_v49_empirical_interval_successor as builder
    import run_tpcc_v48_autonomous_clean5_successor_loop as base
    import run_tpcc_v49_empirical_interval_strict_serial as diagnostic_runner
except ModuleNotFoundError:
    from tools import build_tpcc_v49_empirical_interval_successor as builder
    from tools import run_tpcc_v48_autonomous_clean5_successor_loop as base
    from tools import run_tpcc_v49_empirical_interval_strict_serial as diagnostic_runner


DIAGNOSTIC_RUNNER = Path(__file__).resolve().with_name(
    "run_tpcc_v49_empirical_interval_strict_serial.py"
)


def _install_v49() -> None:
    base.builder = builder
    base.diagnostic_runner = diagnostic_runner
    base.DIAGNOSTIC_RUNNER = DIAGNOSTIC_RUNNER


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--expected-formal-state-sha256", required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    _install_v49()
    selection = args.selection.resolve()
    initial_replay, _, _, _ = base._paths(selection, 1)
    base._verify_runtime(selection, initial_replay)
    if args.verify_only:
        print(
            json.dumps(
                {
                    "status": "tpcc_v49_empirical_interval_preflight_passed",
                    "strict_serial": True,
                    "maximum_physical_calls_per_diagnostic": 5,
                    "same_rank_retry_allowed": False,
                    "state_neutral_diagnostic": True,
                    "gate_fail_starts_fresh_v49_batch": True,
                    "gate_pass_starts_formal_target10": True,
                    "workload_calls_added": 0,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    return base.run(
        selection,
        args.campaign_root.resolve(),
        args.expected_formal_state_sha256,
    )


if __name__ == "__main__":
    raise SystemExit(main())
