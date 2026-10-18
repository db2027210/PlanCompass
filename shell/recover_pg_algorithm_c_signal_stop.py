#!/usr/bin/env python3
"""Recover an Algorithm C lane whose process exited during signal-stop restore."""

from __future__ import annotations

import argparse
import copy
import time
from pathlib import Path

from run_pg_algorithm_c_scene import LaneController


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target", choices=("cpu", "memory", "io"), required=True)
    args = parser.parse_args()
    controller = LaneController(
        args.config.resolve(),
        args.output_dir.resolve(),
        args.target,
    )
    before = {
        "phase": controller.state.get("phase"),
        "accepted": int(controller.state.get("accepted") or 0),
        "physical_workload_invocations": int(
            controller.state.get("physical_workload_invocations") or 0
        ),
        "valid_replays": int(controller.state.get("valid_replays") or 0),
        "technical_failures": int(controller.state.get("technical_failures") or 0),
        "incumbent_params": copy.deepcopy(
            controller.state.get("incumbent_params") or {}
        ),
        "ranges": copy.deepcopy(controller.state.get("ranges") or {}),
    }
    controller.log(
        "algorithm_c_signal_stop_recovery_start",
        interrupted_state=before,
        reason="controller_process_disappeared_during_signal_stop_restore",
        semantic_state_mutation=False,
    )
    receipt = controller.restore_clean_baseline(
        reason="signal_stop_process_exit_recovery"
    )
    after_invariants = {
        "accepted_unchanged": int(controller.state.get("accepted") or 0)
        == before["accepted"],
        "physical_workload_invocations_unchanged": int(
            controller.state.get("physical_workload_invocations") or 0
        )
        == before["physical_workload_invocations"],
        "valid_replays_unchanged": int(
            controller.state.get("valid_replays") or 0
        )
        == before["valid_replays"],
        "technical_failures_unchanged": int(
            controller.state.get("technical_failures") or 0
        )
        == before["technical_failures"],
        "incumbent_unchanged": controller.state.get("incumbent_params")
        == before["incumbent_params"],
        "ranges_unchanged": controller.state.get("ranges") == before["ranges"],
    }
    if not all(after_invariants.values()):
        raise RuntimeError(f"signal-stop recovery mutated formal state: {after_invariants}")
    controller.state["signal_stop_clean_receipt"] = copy.deepcopy(receipt)
    controller.state["signal_stop_recovery"] = {
        "reason": "controller_process_disappeared_during_signal_stop_restore",
        "completed_at_epoch_s": time.time(),
        "before": before,
        "invariants": after_invariants,
        "receipt": copy.deepcopy(receipt),
    }
    controller.state["phase"] = "stopped_by_signal_clean_recovered"
    controller.save_state()
    controller.log(
        "algorithm_c_signal_stop_recovery_complete",
        clean_receipt=receipt,
        invariants=after_invariants,
        formal_state_mutation=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
