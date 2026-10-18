#!/usr/bin/env python3
"""Resume TPC-H with mandatory third observation and robust 2-of-3 trust.

This fail-closed wrapper resumes only a clean cycle boundary containing two
state-neutral diagnostic batches.  It replaces the deadlocking two-observation
selector with a provisional third-observation selector, then reseals trust
using a joint CPU/IO/memory two-of-three rule.  No candidate is manually
promoted and no diagnostic result may mutate formal state.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
from pathlib import Path
from typing import Any, Dict, Mapping


V26_PATH = Path("/tmp/tpch_fresh10_target10_loop_v26.py")
V26_SHA256 = "73cfcdd2eb60d199c5b2650eb51f2b19bd4b4d845b7f6fcd9714ef2c48757976"
V27_PATH = Path("/tmp/tpch_fresh10_target10_loop_v27.py")
V27_SHA256 = "d8e1672b4aceea5065f612170d57c1b16917a1dce9bf039fb3af274be4492589"
V28_PATH = Path("/tmp/tpch_fresh10_target10_loop_v28.py")
V28_SHA256 = "c30eeb216998782c0f4d7d0e66dd5ab05458b3dcaad16b6a08167eea5b6a2096"
V30_PATH = Path("/tmp/tpch_fresh10_target10_loop_v30.py")
V30_SHA256 = "384f2ce8f944525711d706c1e85493bfe61ed075d20d6794f61caed3dd67d7f8"
ROBUST_BUILDER = Path("/tmp/build_tpch_two_observation_robust_repeat_v3.py")
ROBUST_BUILDER_SHA256 = "b124ef363035606c55bcc16a801c220b50725b18d3cbe8e98fe816fdf83195b5"
RESEALER = Path("/tmp/build_tpch_three_observation_postrobust_reseal_v2.py")
RESEALER_SHA256 = "78da3a52078bd3613ad2b33fc6c02bb8b7bff28f7133ecec5f7348a04ca57c08"


def import_module(name: str, path: Path) -> Any:
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot load {name}: {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def verify_partial_cycle(
    v26: Any,
    v27: Any,
    common: Any,
    cycle_root: Path,
    accepted: int,
) -> tuple[Path, Path, Path, Path]:
    probe = cycle_root / "probe_top5.a.json"
    probe_b = cycle_root / "probe_top5.b.json"
    probe_run = cycle_root / "diagnostic_probe"
    repeat = cycle_root / "exact_repeat_top5.a.json"
    repeat_b = cycle_root / "exact_repeat_top5.b.json"
    repeat_run = cycle_root / "diagnostic_exact_repeat"
    required = (
        cycle_root / "config.a.json",
        cycle_root / "config.b.json",
        probe,
        probe_b,
        probe_run / "diagnostic_summary.json",
        probe_run / "diagnostic_state.json",
        repeat,
        repeat_b,
        repeat_run / "diagnostic_summary.json",
        repeat_run / "diagnostic_state.json",
        cycle_root / "robust_builder_a.log",
    )
    if not all(path.is_file() for path in required):
        raise RuntimeError("TPC-H v31 partial cycle is incomplete")
    if not (
        (cycle_root / "config.a.json").read_bytes()
        == (cycle_root / "config.b.json").read_bytes()
        and probe.read_bytes() == probe_b.read_bytes()
        and repeat.read_bytes() == repeat_b.read_bytes()
    ):
        raise RuntimeError("TPC-H v31 partial cycle A/B mismatch")
    forbidden = (
        cycle_root / "robust_top5.a.json",
        cycle_root / "robust_top5.b.json",
        cycle_root / "diagnostic_robust_repeat",
        cycle_root / "entry_gate.json",
        cycle_root / "formal_package",
        cycle_root / "formal_run",
    )
    if any(path.exists() for path in forbidden):
        raise RuntimeError("TPC-H v31 partial cycle crossed the safe boundary")
    failure_log = (cycle_root / "robust_builder_a.log").read_text(
        encoding="utf-8",
        errors="replace",
    )
    if not (
        "build_tpch_two_observation_robust_repeat_v2.py" in failure_log
        and "No such file or directory" in failure_log
    ):
        raise RuntimeError("TPC-H v31 boundary hold evidence mismatch")
    v27.clean_diagnostic(v26, common, probe_run, accepted)
    v27.clean_diagnostic(v26, common, repeat_run, accepted)
    return probe, probe_run, repeat, repeat_run


def finish_partial_cycle(
    v26: Any,
    v27: Any,
    common: Any,
    state: Dict[str, Any],
    accepted: int,
    cycle: int,
    cycle_root: Path,
    probe: Path,
    probe_run: Path,
    repeat: Path,
    repeat_run: Path,
) -> tuple[Dict[str, Any], int, Path, Path, Path]:
    environment = common.runtime_environment()
    resume_state = Path(str(state["resume_state"]))
    config = cycle_root / "config.a.json"
    robust = v26.build_robust_repeat(
        common,
        cycle_root,
        probe,
        probe_run,
        repeat,
        repeat_run,
        accepted,
        environment,
    )
    robust_run = cycle_root / "diagnostic_robust_repeat"
    v26.run_clean_diagnostic(
        common,
        robust,
        robust_run,
        config,
        resume_state,
        accepted,
        cycle_root / "diagnostic_robust_repeat.log",
        environment,
    )
    analysis_path = v26.analyze_gate(
        common,
        cycle_root,
        robust,
        robust_run,
        environment,
    )
    analysis = v26.load(analysis_path)
    cycle_audit: Dict[str, Any] = {
        "cycle": cycle,
        "accepted_before": accepted,
        "probe_selection_sha256": v26.sha256(probe),
        "repeat_selection_sha256": v26.sha256(repeat),
        "robust_selection_sha256": v26.sha256(robust),
        "entry_gate_sha256": v26.sha256(analysis_path),
        "entry_gate_passed": analysis.get("passed") is True,
        "v31_boundary_resume": True,
        "two_observation_selector": (
            "provisional_slots_pending_mandatory_third_observation_v1"
        ),
        "three_observation_stability": (
            "joint_tightest_pair_cpu_io_memory_2of3_v1"
        ),
    }
    if analysis.get("passed") is not True:
        cycle_audit.update(
            {
                "formal_started": False,
                "accepted_after": accepted,
            }
        )
        state["cycles"].append(cycle_audit)
        state["accepted_count"] = accepted
        state["next_cycle"] = cycle + 1
        return state, accepted, resume_state, robust, robust_run

    formal_selection, gate, formal_run = v26.promote_formal(
        common,
        cycle_root,
        robust,
        robust_run,
        analysis_path,
        accepted,
        config,
    )
    formal_error = None
    try:
        v26.run_formal(
            common,
            formal_selection,
            gate,
            formal_run,
            config,
            resume_state,
            environment,
        )
    except RuntimeError as exc:
        formal_error = str(exc)
    accepted_after, formal_state, formal_clean = v26.validate_formal_completion(
        formal_run,
        accepted,
        allow_wait=False,
    )
    cycle_audit.update(
        {
            "formal_started": True,
            "formal_selection_sha256": v26.sha256(formal_selection),
            "formal_gate_sha256": v26.sha256(gate),
            "formal_clean": formal_clean,
            "formal_error": formal_error,
            "accepted_after": accepted_after,
            "formal_state_sha256": v26.sha256(formal_state),
        }
    )
    state["cycles"].append(cycle_audit)
    if formal_clean:
        accepted = accepted_after
        resume_state = formal_state
        state["accepted_count"] = accepted
        state["resume_state"] = str(resume_state)
    elif accepted_after != accepted:
        raise RuntimeError("technical formal batch changed accepted count")
    state["next_cycle"] = cycle + 1
    return state, accepted, resume_state, robust, robust_run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-state-sha256", required=True)
    parser.add_argument("--partial-cycle", type=int, required=True)
    args = parser.parse_args()
    if len(args.expected_state_sha256) != 64 or args.partial_cycle <= 0:
        raise ValueError("invalid recovery boundary arguments")

    v27 = import_module("tpch_target10_v27_for_v31", V27_PATH)
    v28 = import_module("tpch_target10_v28_for_v31", V28_PATH)
    v30 = import_module("tpch_target10_v30_for_v31", V30_PATH)
    v28.v27 = v27
    v30.RESEALER = RESEALER
    v30.RESEALER_SHA256 = RESEALER_SHA256

    original_verify_dependencies = v27.verify_dependencies

    def verify_dependencies(v26: Any) -> None:
        v26.ROBUST_BUILDER = ROBUST_BUILDER
        v26.EXPECTED["robust_builder"] = ROBUST_BUILDER_SHA256
        original_verify_dependencies(v26)
        for path, expected in (
            (V26_PATH, V26_SHA256),
            (V27_PATH, V27_SHA256),
            (V28_PATH, V28_SHA256),
            (V30_PATH, V30_SHA256),
            (ROBUST_BUILDER, ROBUST_BUILDER_SHA256),
            (RESEALER, RESEALER_SHA256),
        ):
            v26.verify(path, expected)

    def recover(v26: Any, common: Any) -> tuple[Dict[str, Any], int, Path, Path, Path, int]:
        state_path = v26.LOOP_ROOT / "controller_state.json"
        if v26.sha256(state_path) != args.expected_state_sha256:
            raise RuntimeError("TPC-H v31 recovery state SHA mismatch")
        state = v26.load(state_path)
        accepted = v26.accepted_counter()
        cycles = state.get("cycles") or []
        last_cycle = cycles[-1] if cycles else {}
        cycle_root = v26.LOOP_ROOT / f"cycle_{args.partial_cycle:03d}"
        if not (
            state.get("schema_version") == "tpch_fresh10_target10_loop_v30"
            and int(state.get("controller_version", -1)) == 30
            and state.get("status") == "preparing_cycle"
            and int(state.get("accepted_count", -1)) == accepted == 0
            and int(state.get("accepted_target", -1)) == v26.TARGET
            and int(state.get("historical_accepted_excluded", -1)) == 1
            and int(state.get("next_cycle", -1)) == args.partial_cycle
            and int(last_cycle.get("cycle", -1)) == args.partial_cycle - 1
            and int(last_cycle.get("accepted_after", -1)) == accepted
            and Path(str(state.get("resume_state"))).is_file()
        ):
            raise RuntimeError("TPC-H v31 controller state boundary mismatch")

        probe, probe_run, repeat, repeat_run = verify_partial_cycle(
            v26,
            v27,
            common,
            cycle_root,
            accepted,
        )
        backup = v26.LOOP_ROOT / (
            f"controller_state.pre_v31_cycle{args.partial_cycle:03d}.json"
        )
        if backup.exists():
            raise RuntimeError("TPC-H v31 recovery backup already exists")
        v27.atomic_json(backup, state)
        if v26.sha256(backup) != args.expected_state_sha256:
            raise RuntimeError("TPC-H v31 recovery backup SHA mismatch")

        v30.install_algorithm_hooks(v26, v27, v28, common)
        recovered = copy.deepcopy(state)
        recovered.update(
            {
                "schema_version": "tpch_fresh10_target10_loop_v31",
                "controller_version": 31,
                "status": "resuming_partial_cycle_with_robust_2of3",
                "v31_recovery": {
                    "contract": "exact_partial_cycle_state_sha_v1",
                    "source_state_sha256": args.expected_state_sha256,
                    "partial_cycle": args.partial_cycle,
                    "accepted_count": accepted,
                    "probe_physical_calls_reused": 5,
                    "exact_repeat_physical_calls_reused": 5,
                    "new_physical_calls_before_recovery": 0,
                    "formal_state_mutated_before_recovery": False,
                },
            }
        )
        recovered, accepted, resume_state, robust, robust_run = (
            finish_partial_cycle(
                v26,
                v27,
                common,
                recovered,
                accepted,
                args.partial_cycle,
                cycle_root,
                probe,
                probe_run,
                repeat,
                repeat_run,
            )
        )
        recovered["status"] = (
            "target_reached"
            if accepted >= v26.TARGET
            else "running_robust_2of3"
        )
        v27.atomic_json(state_path, recovered)
        return (
            recovered,
            accepted,
            resume_state,
            robust,
            robust_run,
            args.partial_cycle + 1,
        )

    v27.verify_dependencies = verify_dependencies
    v27.recover_cycle004 = recover
    return int(v27.main())


if __name__ == "__main__":
    raise SystemExit(main())
