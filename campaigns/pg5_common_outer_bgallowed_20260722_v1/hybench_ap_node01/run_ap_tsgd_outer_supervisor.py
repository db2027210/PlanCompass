#!/usr/bin/env python3
"""Continue AP T-SGD generations until 10 accepted binary shrinks or deadline."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple


LANE_DIR = Path(__file__).resolve().parent
BASE_ROOT = LANE_DIR.parents[2]
WORKSPACE = BASE_ROOT / "workspace/pg"
PYTHON311 = BASE_ROOT / "python311_c7/bin/python"
SYSTEM_PYTHON = Path("/usr/bin/python3")
IMAGE = "sha256:6c2f8e0ad2949b4e100456f138b1184fe58cb2010cd7a9721008abb87f501458"
UNIFIED_STABILITY_CONTRACT_V5_SHA256 = (
    "4b90d0126806ac16031fafbfe905d3aa3d693ddf28b64717eb348fb0911d4692"
)
STALE_PROBE_PGDATA = (
    LANE_DIR
    / "runtime/postgresql/ap/work/postgresql/hybench/"
    "pg_hybench_ap_memory_P0_r1_111641_2060332fee17/pgdata"
)
ACTIVE_PARAMS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)


@dataclass(frozen=True)
class ReplayRange:
    min_value: float
    max_value: float


class OuterLoopRejected(RuntimeError):
    """A complete formal replay batch produced no acceptable candidate."""

    def __init__(
        self,
        *,
        generation: int,
        accepted_shrinks: int,
        replay_root: Path,
        replay_result_path: Path,
    ) -> None:
        super().__init__(
            f"generation {generation} completed with explicit outer rejection"
        )
        self.generation = generation
        self.accepted_shrinks = accepted_shrinks
        self.replay_root = replay_root
        self.replay_result_path = replay_result_path


INITIAL_RANGES = {
    "seq_page_cost": ReplayRange(0.1, 100.0),
    "random_page_cost": ReplayRange(0.1, 100.0),
    "cpu_tuple_cost": ReplayRange(0.001, 0.1),
    "cpu_operator_cost": ReplayRange(0.00025, 0.025),
    "parallel_setup_cost": ReplayRange(1.0, 10000.0),
    "parallel_tuple_cost": ReplayRange(0.001, 1.0),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON top level is not an object: {path}")
    return value


def write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def clean_env() -> Dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(WORKSPACE)
    env["PYTHONNOUSERSITE"] = "1"
    return env


def run_logged(command: List[str], log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("x", encoding="utf-8") as handle:
        subprocess.run(
            command,
            check=True,
            text=True,
            stdout=handle,
            stderr=subprocess.STDOUT,
            env=clean_env(),
        )


def materialize_next_state(replay_root: Path) -> Path:
    result_path = replay_root / "replay_result.json"
    result = read_json(result_path)
    selected = result.get("selected_candidate")
    if not isinstance(selected, dict):
        raise RuntimeError("cannot materialize state without an accepted candidate")

    import sys

    if str(WORKSPACE) not in sys.path:
        sys.path.insert(0, str(WORKSPACE))
    from common_outer_loop import binary_shrink_parameter_ranges

    updated, _ = binary_shrink_parameter_ranges(
        INITIAL_RANGES,
        selected["params"],
        ACTIVE_PARAMS,
    )
    state = {
        "schema_version": 1,
        "status": "accepted_incumbent_state",
        "accepted_shrinks": 1,
        "incumbent": {
            "db_qps": float(selected["validation"]["db_qps"]),
            "utilization_pct": {
                name: float(selected["validation"]["utilization_pct"][name])
                for name in ("cpu", "memory", "io")
            },
            "params": {
                **{name: float(selected["params"][name]) for name in ACTIVE_PARAMS},
                "cpu_index_tuple_cost": 0.005,
            },
            "source": selected["validation"]["point_dir"],
            "selected_rank": int(selected["rank"]),
            "selected_replay_position": int(selected.get("replay_position") or 0),
        },
        "ranges": {
            name: {
                "min_value": float(updated[name].min_value),
                "max_value": float(updated[name].max_value),
            }
            for name in ACTIVE_PARAMS
        },
        "source_state_path": None,
        "source_state_sha256": None,
        "source_replay_result": str(result_path),
        "source_replay_result_sha256": sha256(result_path),
        "created_at_epoch": time.time(),
    }
    state_path = replay_root / "next_incumbent_state.json"
    write_json(state_path, state)
    return state_path


def wait_for_initial_replay(
    replay_root: Path,
    *,
    progress_path: Path,
    started_at: float,
    deadline_epoch: float,
) -> Path:
    result_path = replay_root / "replay_result.json"
    while not result_path.is_file():
        progress = (
            read_json(replay_root / "replay_progress.json")
            if (replay_root / "replay_progress.json").is_file()
            else {}
        )
        write_json(
            progress_path,
            {
                "schema_version": 1,
                "status": "waiting_for_generation_1_replay",
                "pid": os.getpid(),
                "generation": 1,
                "accepted_shrinks": 0,
                "initial_replay_root": str(replay_root),
                "initial_replay_progress": progress,
                "started_at_epoch": started_at,
                "deadline_epoch": deadline_epoch,
                "updated_at_epoch": time.time(),
            },
        )
        time.sleep(20.0)
    result = read_json(result_path)
    if not isinstance(result.get("selected_candidate"), dict):
        accepted_shrinks = int(result.get("accepted_shrinks_after") or 0)
        raise OuterLoopRejected(
            generation=accepted_shrinks + 1,
            accepted_shrinks=accepted_shrinks,
            replay_root=replay_root,
            replay_result_path=result_path,
        )
    state_path = replay_root / "next_incumbent_state.json"
    return state_path if state_path.is_file() else materialize_next_state(replay_root)


def run_generation(
    *,
    lane: Path,
    root: Path,
    generation: int,
    state_path: Path,
    progress_path: Path,
    deadline_epoch: float,
    run_namespace: str,
) -> Tuple[Path, bool]:
    generation_root = root / f"generation_{generation:02d}"
    generation_root.mkdir(parents=False, exist_ok=False)
    inner_root = generation_root / "inner"
    applied_ranking = inner_root / "subset_ranking_pg_applied.json"
    plan_root = generation_root / "plans"
    replay_root = generation_root / "replay"

    state = read_json(state_path)
    write_json(
        progress_path,
        {
            "schema_version": 1,
            "status": "generating_tsgd_subsets",
            "pid": os.getpid(),
            "generation": generation,
            "accepted_shrinks": int(state["accepted_shrinks"]),
            "state_path": str(state_path),
            "state_sha256": sha256(state_path),
            "deadline_epoch": deadline_epoch,
            "updated_at_epoch": time.time(),
        },
    )
    run_logged(
        [
            str(SYSTEM_PYTHON),
            str(lane / "run_ap_experience_inner.py"),
            "--lane-root",
            str(lane),
            "--output-root",
            str(inner_root),
            "--max-workers",
            "32",
            "--round-index",
            str(generation),
            "--state-json",
            str(state_path),
        ],
        generation_root / "inner.log",
    )
    run_logged(
        [
            str(SYSTEM_PYTHON),
            str(lane / "quantize_ap6_tsgd_ranking.py"),
            "--lane-root",
            str(lane),
            "--ranking-json",
            str(inner_root / "subset_ranking.json"),
            "--image",
            IMAGE,
            "--pgdata",
            str(STALE_PROBE_PGDATA),
            "--output",
            str(applied_ranking),
            "--state-json",
            str(state_path),
        ],
        generation_root / "quantize.log",
    )

    write_json(
        progress_path,
        {
            "schema_version": 1,
            "status": "capturing_exact_plans",
            "pid": os.getpid(),
            "generation": generation,
            "accepted_shrinks": int(state["accepted_shrinks"]),
            "applied_ranking": str(applied_ranking),
            "applied_ranking_sha256": sha256(applied_ranking),
            "updated_at_epoch": time.time(),
        },
    )
    run_logged(
        [
            str(PYTHON311),
            "-S",
            str(lane / "run_ap_plan_fingerprints.py"),
            "--lane-root",
            str(lane),
            "--ranking-json",
            str(applied_ranking),
            "--output-root",
            str(plan_root),
            "--max-workers",
            "4",
        ],
        generation_root / "plans.log",
    )
    annotated_ranking = plan_root / "subset_ranking_with_plan_fingerprints.json"
    incumbent_summary = Path(str(state["incumbent"]["source"])) / "measurement_summary.json"
    run_logged(
        [
            str(PYTHON311),
            "-S",
            str(lane / "build_ap6_replay_selection.py"),
            "--ranking",
            str(annotated_ranking),
            "--expected-ranking-sha256",
            sha256(annotated_ranking),
            "--baseline",
            str(incumbent_summary),
            "--output-dir",
            str(plan_root),
        ],
        generation_root / "selection.log",
    )

    write_json(
        progress_path,
        {
            "schema_version": 1,
            "status": "real_replay_first_five_then_optional_expansion",
            "pid": os.getpid(),
            "generation": generation,
            "accepted_shrinks": int(state["accepted_shrinks"]),
            "replay_root": str(replay_root),
            "deadline_epoch": deadline_epoch,
            "updated_at_epoch": time.time(),
        },
    )
    run_logged(
        [
            str(PYTHON311),
            "-S",
            str(lane / "run_ap_ranked_replays.py"),
            "--lane-root",
            str(lane),
            "--ranking-json",
            str(annotated_ranking),
            "--selection-plan",
            str(plan_root / "ap6_replay_selection_plan.json"),
            "--state-json",
            str(state_path),
            "--output-root",
            str(replay_root),
            "--python-bin",
            str(PYTHON311),
            "--replicate-policy-v1",
            "--attempt-run-id",
            f"outer_ap6_tsgd_{run_namespace}_g{generation:02d}",
        ],
        generation_root / "replay.log",
    )
    replay_result = read_json(replay_root / "replay_result.json")
    if not isinstance(replay_result.get("selected_candidate"), dict):
        write_json(
            progress_path,
            {
                "schema_version": 1,
                "status": "outer_rejected_continue_same_incumbent",
                "pid": os.getpid(),
                "generation": generation,
                "accepted_shrinks": int(state["accepted_shrinks"]),
                "eta": eta,
                "replay_root": str(replay_root),
                "replay_result_path": str(replay_root / "replay_result.json"),
                "incumbent_preserved": True,
                "parameter_ranges_preserved": True,
                "binary_shrink_applied": False,
                "deadline_epoch": deadline_epoch,
                "updated_at_epoch": time.time(),
            },
        )
        return state_path, False
    next_state = replay_root / "next_incumbent_state.json"
    if not next_state.is_file():
        raise RuntimeError(f"generation {generation} did not emit its next state")
    return next_state, True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lane-root", type=Path, required=True)
    parser.add_argument("--initial-replay-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--max-accepted-shrinks", type=int, default=10)
    parser.add_argument("--deadline-hours", type=float, default=3.0)
    args = parser.parse_args()
    lane = args.lane_root.resolve()
    initial_replay_root = args.initial_replay_root.resolve()
    output = args.output_root.resolve()
    output.mkdir(parents=True, exist_ok=False)
    progress_path = output / "supervisor_progress.json"
    started_at = time.time()
    deadline_epoch = started_at + max(0.0, args.deadline_hours) * 3600.0
    try:
        state_path = wait_for_initial_replay(
            initial_replay_root,
            progress_path=progress_path,
            started_at=started_at,
            deadline_epoch=deadline_epoch,
        )
        state = read_json(state_path)
        accepted_shrinks = int(state["accepted_shrinks"])
        rejected_explorations = 0
        run_namespace = (
            "".join(
                character if character.isalnum() else "_"
                for character in output.name
            )[:40]
            + "_"
            + hashlib.sha256(str(output).encode("utf-8")).hexdigest()[:10]
        )
        # A continuation supervisor may start from any already accepted
        # incumbent.  Keep generation numbering aligned with the number of
        # completed binary shrinks instead of resetting it to one.
        generation = accepted_shrinks
        while (
            accepted_shrinks < args.max_accepted_shrinks
            and time.time() < deadline_epoch
        ):
            generation += 1
            state_path, generation_accepted = run_generation(
                lane=lane,
                root=output,
                generation=generation,
                state_path=state_path,
                progress_path=progress_path,
                deadline_epoch=deadline_epoch,
                run_namespace=run_namespace,
            )
            state = read_json(state_path)
            accepted_shrinks = int(state["accepted_shrinks"])
            if not generation_accepted:
                rejected_explorations += 1
        status = (
            "target_accepted_shrinks_reached"
            if accepted_shrinks >= args.max_accepted_shrinks
            else "deadline_reached_after_completed_generation"
        )
        write_json(
            progress_path,
            {
                "schema_version": 1,
                "status": status,
                "pid": os.getpid(),
                "generation": generation,
                "accepted_shrinks": accepted_shrinks,
                "max_accepted_shrinks": args.max_accepted_shrinks,
                "rejected_explorations": rejected_explorations,
                "run_namespace": run_namespace,
                "unified_stability_contract_v5_sha256": (
                    UNIFIED_STABILITY_CONTRACT_V5_SHA256
                ),
                "final_state_path": str(state_path),
                "final_state_sha256": sha256(state_path),
                "started_at_epoch": started_at,
                "deadline_epoch": deadline_epoch,
                "completed_at_epoch": time.time(),
            },
        )
        return 0
    except OuterLoopRejected as exc:
        terminal = {
            "schema_version": 1,
            "status": "outer_rejected_no_interval_shrink",
            "pid": os.getpid(),
            "generation": exc.generation,
            "accepted_shrinks": exc.accepted_shrinks,
            "max_accepted_shrinks": args.max_accepted_shrinks,
            "replay_root": str(exc.replay_root),
            "replay_result_path": str(exc.replay_result_path),
            "replay_result_sha256": sha256(exc.replay_result_path),
            "incumbent_preserved": True,
            "parameter_ranges_preserved": True,
            "binary_shrink_applied": False,
            "termination_reason": (
                "complete_formal_replay_batch_had_no_candidate_with_both_"
                "strict_db_qps_increase_and_strict_target_bottleneck_decrease"
            ),
            "unified_stability_contract_v5_sha256": (
                UNIFIED_STABILITY_CONTRACT_V5_SHA256
            ),
            "started_at_epoch": started_at,
            "deadline_epoch": deadline_epoch,
            "completed_at_epoch": time.time(),
        }
        write_json(progress_path, terminal)
        write_json(output / "supervisor_result.json", terminal)
        return 0
    except BaseException as exc:
        write_json(
            progress_path,
            {
                "schema_version": 1,
                "status": "stopped_fail_closed",
                "pid": os.getpid(),
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
                "started_at_epoch": started_at,
                "deadline_epoch": deadline_epoch,
                "failed_at_epoch": time.time(),
            },
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
