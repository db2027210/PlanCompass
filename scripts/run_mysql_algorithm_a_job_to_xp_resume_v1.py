#!/usr/bin/env python3
"""Resume an XP lane on four workers released by completed four-worker JOB.

The predecessor v1 controller is stopped only after a complete accepted round
has been persisted.  Any work from the following partial round is archived and
receives no credit.  This adapter reuses the frozen Algorithm A implementation
while replacing only its execution cohort authority and replay parallelism.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_a_job_to_xp_slots_v1 as slots
from scripts import run_mysql_algorithm_a_async_scene_v1 as base


EXPECTED_WORKERS = {
    "hybench_xp_cpu": {f"xp_cpu_w{index}" for index in range(1, 5)},
    "hybench_xp_io": {f"xp_io_w{index}" for index in range(1, 5)},
}
TRANSITION_CONTRACT = slots.TRANSITION_CONTRACT


def regular_file(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path.is_symlink() or not path.is_file():
        raise argparse.ArgumentTypeError(f"regular file required: {path}")
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def average_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows or not all(base.pg_outer.physical_valid(row) for row in rows):
        raise RuntimeError("four-worker transition replay hard gate failed")
    return {
        "db_qps": sum(float(row["db_qps"]) for row in rows) / len(rows),
        "p95_ms": sum(float(row["p95_ms"]) for row in rows) / len(rows),
        "p99_ms": sum(float(row["p99_ms"]) for row in rows) / len(rows),
        "resource_means": {
            name: sum(float(row["resource_means"][name]) for row in rows)
            / len(rows)
            for name in ("cpu", "memory", "io")
        },
        "hard_gate_passed": True,
        "coverage_complete": True,
    }


def average_worker_repetitions(
    repetitions: Sequence[Mapping[str, Mapping[str, Any]]],
) -> dict[str, dict[str, Any]]:
    if len(repetitions) != 2 or set(repetitions[0]) != set(repetitions[1]):
        raise RuntimeError("transition baseline requires two full repetitions")
    result: dict[str, dict[str, Any]] = {}
    for worker_id in sorted(repetitions[0]):
        rows = [dict(repetition[worker_id]) for repetition in repetitions]
        aggregate = average_rows(rows)
        result[worker_id] = {
            **copy.deepcopy(rows[-1]),
            **aggregate,
            "worker_id": worker_id,
            "transition_baseline_repetitions": 2,
            "transition_baseline_raw_rows": rows,
        }
    return result


def validate_authority(
    authority_path: Path, predecessor_path: Path, scene: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    authority = load(authority_path)
    predecessor = load(predecessor_path)
    freeze_path = Path(str(authority.get("successor_freeze_path") or ""))
    if not freeze_path.is_absolute():
        freeze_path = ROOT / freeze_path
    freeze_path = freeze_path.resolve()
    freeze = load(freeze_path)
    workers = {
        str(row.get("worker_id") or ""): copy.deepcopy(dict(row))
        for row in (authority.get("workers") or [])
    }
    gate = authority.get("dynamic_noninterference_gate") or {}
    memory = authority.get("memory_terminal_evidence") or {}
    transition_contract_path = Path(str(
        authority.get("transition_contract_path") or ""
    ))
    if (
        authority.get("schema_version")
        != "mysql_algorithm_a_job_to_xp_transition_authority_v1"
        or authority.get("status") != "PASS"
        or authority.get("formal_scope") != "mysql_only"
        or authority.get("cross_engine_comparison_prohibited") is not True
        or authority.get("contract") != TRANSITION_CONTRACT
        or not transition_contract_path.is_file()
        or transition_contract_path.is_symlink()
        or authority.get("transition_contract_sha256")
        != sha(transition_contract_path)
        or authority.get("scene") != scene
        or authority.get("predecessor_snapshot_path") != str(predecessor_path)
        or authority.get("predecessor_snapshot_sha256") != sha(predecessor_path)
        or authority.get("preserved_search_state_sha256")
        != slots.canonical_sha256(slots.search_state_payload(predecessor))
        or authority.get("successor_freeze_sha256") != sha(freeze_path)
        or freeze.get("schema_version")
        != "mysql_algorithm_a_xp_four_worker_after_job_resource_successor_v1"
        or freeze.get("formal_scope") != "mysql_only"
        or freeze.get("cross_engine_comparison_prohibited") is not True
        or set(workers) != EXPECTED_WORKERS[scene]
        or any(row.get("scene") != scene for row in workers.values())
        or gate.get("status") != "PASS"
        or float(gate.get("observed_seconds") or 0.0) < 60.0
        or gate.get("target_worker_ids") != sorted(workers)
        or set(memory) != {"job_memory"}
        or memory["job_memory"].get("complete") is not True
        or authority.get("native_xp_workload_unchanged") is not True
        or authority.get("mixed_worker_cohort_round_prohibited") is not True
        or authority.get("fresh_baseline_repetitions_per_worker") != 2
        or authority.get("incumbent_replay_required") is not True
    ):
        raise RuntimeError("four-worker Algorithm A transition authority differs")
    frozen_workers = {
        str(row.get("worker_id") or ""): row
        for row in freeze.get("workers") or []
        if row.get("scene") == scene
    }
    if set(workers) != set(frozen_workers) or any(
        any(
            workers[worker_id].get(field) != frozen_workers[worker_id].get(field)
            for field in (
                "scene", "container", "host_port", *slots.POSITION_FIELDS,
                *slots.RESOURCE_FIELDS,
            )
        )
        for worker_id in workers
    ):
        raise RuntimeError("transition worker rows differ from successor freeze")
    return freeze, workers, {
        "transition_authority_path": str(authority_path),
        "transition_authority_sha256": sha(authority_path),
        "resource_freeze_path": str(freeze_path),
        "resource_freeze_sha256": sha(freeze_path),
        "resource_freeze_schema": freeze["schema_version"],
        "coordination_contract": base.c49.validate_v65_wave_coordination_contract(),
        "worker_cohort_transition": copy.deepcopy(authority),
    }


def configure_runtime(
    *, freeze_path: Path, workers: Mapping[str, Mapping[str, Any]], scene: str,
) -> None:
    base.c49.validate_contract()
    base.c49.install()
    base.alg.run_hybench = base.workload_adapter_v2.run_hybench
    base.alg.HYBENCH_ADAPTIVE_RUNNER_JAR = base.HYBENCH_ADAPTIVE_RUNNER
    base.alg.reset.prep._kernel_resource_readback = (
        base.cgroup_readback_v2.kernel_resource_readback
    )
    base.runtime.INDEPENDENT_SCENE_EXECUTION = True
    base.runtime.SCENES = (scene,)
    base.alg.FREEZE = freeze_path
    base.alg.r0mod.FREEZE = freeze_path
    base.alg.r0mod.FREEZE_SCHEMA = load(freeze_path)["schema_version"]
    worker_ids = tuple(sorted(workers))
    base.alg.XP_IDS = worker_ids
    base.alg.WORKER_IDS = worker_ids
    base.alg.reset.XP_IDS = set(worker_ids)
    base.alg.reset.XP_CONTAINER_ALLOWLIST = {
        worker_id: str(workers[worker_id]["container"])
        for worker_id in worker_ids
    }


def run(args: argparse.Namespace) -> int:
    scene = str(args.scene)
    if scene not in slots.XP_SCENES:
        raise RuntimeError("dynamic slot resume is XP-only")
    predecessor = load(args.predecessor_state)
    if (
        predecessor.get("algorithm") != "A"
        or predecessor.get("async_scene") != scene
        or predecessor.get("formal_scope") != "mysql_only"
        or int(predecessor.get("accepted") or 0) >= 10
        or predecessor.get("round_checkpoint") is not None
        or predecessor.get("transition_boundary_snapshot") is not True
    ):
        raise RuntimeError("predecessor is not an incomplete round-boundary snapshot")
    freeze, workers, transition_authority = validate_authority(
        args.transition_authority, args.predecessor_state, scene,
    )
    freeze_path = Path(transition_authority["resource_freeze_path"])
    configure_runtime(freeze_path=freeze_path, workers=workers, scene=scene)

    base.REPLAY_PARALLELISM = 4
    original_fresh_state = base.fresh_state
    original_measure = base.measure
    original_atomic = base.atomic
    preserved = slots.search_state_payload(predecessor)
    preserved_canonical = copy.deepcopy(preserved["canonical_baseline"])
    transition_context: dict[str, Any] = {
        "baseline_by_worker": None,
        "incumbent_validation": None,
        "incumbent_replay_rows": None,
    }
    state_path = args.output_dir.resolve() / "algorithm_a_state.json"

    def resumed_fresh_state(
        *, run_id: str, scene: str, active: Sequence[str],
        ranges: Mapping[str, Any], authority: Mapping[str, Any],
    ) -> dict[str, Any]:
        value = original_fresh_state(
            run_id=run_id, scene=scene, active=active, ranges=ranges,
            authority=authority,
        )
        value.update(copy.deepcopy(preserved))
        value.update({
            "accepted": int(predecessor["accepted"]),
            "accepted_target": base.TARGET_ACCEPTED,
            "maximum_rejected_outer_attempts":
                base.MAX_REJECTED_OUTER_ATTEMPTS,
            "round_index": int(predecessor.get("round_index") or 0),
            "rejected_rounds": int(predecessor.get("rejected_rounds") or 0),
            "physical_replay_parallelism": 4,
            "predecessor_run_id": predecessor.get("run_id"),
            "predecessor_snapshot_path": str(args.predecessor_state),
            "predecessor_snapshot_sha256": sha(args.predecessor_state),
            "worker_cohort_transition_contract": TRANSITION_CONTRACT,
            "execution_evidence_epoch": int(
                predecessor.get("execution_evidence_epoch") or 1
            ) + 1,
            "partial_predecessor_round_credited": False,
            "round_checkpoint": None,
            "status": "RUNNING",
            "stage": "TRANSITION_AUTHORITY_VALIDATED",
            "converged": False,
        })
        return value

    def dynamic_aggregate(rows: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        if len(rows) != 4 or not all(
            base.pg_outer.physical_valid(row) for row in rows.values()
        ):
            raise RuntimeError("four-worker fresh baseline hard gate failed")
        # Preserve the original canonical comparison scale exactly.  New
        # observations are normalized by their same-worker fresh baselines.
        return copy.deepcopy(preserved_canonical)

    baseline_done = False

    def transition_measure(**kwargs: Any):
        nonlocal baseline_done
        if baseline_done or Path(kwargs["output"]).name != "formal_baseline":
            return original_measure(**kwargs)
        baseline_done = True
        baseline_root = Path(kwargs["output"])
        repetitions = []
        latest_state = kwargs["state"]
        for repetition in (1, 2):
            call = dict(kwargs)
            call["output"] = baseline_root / f"repetition_{repetition}"
            call["reset_token"] = (
                str(kwargs["reset_token"]) + f"_repetition_{repetition}"
            )
            call["stage_prefix"] = (
                str(kwargs["stage_prefix"]) + f"_REPETITION_{repetition}"
            )
            call["state"] = latest_state
            rows, latest_state = original_measure(**call)
            repetitions.append(rows)
        baseline_rows = average_worker_repetitions(repetitions)
        incumbent_assignments = {
            worker_id: {
                "rank": index + 1,
                "params": copy.deepcopy(predecessor["incumbent_params"]),
            }
            for index, worker_id in enumerate(sorted(workers))
        }
        incumbent_rows, latest_state = original_measure(
            freeze=kwargs["freeze"], workers=kwargs["workers"],
            assignments=incumbent_assignments,
            output=baseline_root.parent / "transition_incumbent_replay",
            reset_token=str(kwargs["reset_token"]) + "_incumbent_replay",
            state_path=kwargs["state_path"], state=latest_state,
            stage_prefix="TRANSITION_INCUMBENT_REPLAY",
            run_id=kwargs["run_id"], contract=kwargs["contract"],
        )
        normalized_rows = [
            base.normalize_to_worker_baseline(
                incumbent_rows[worker_id], baseline_rows[worker_id],
                preserved_canonical,
            )
            for worker_id in sorted(workers)
        ]
        incumbent_validation = average_rows(normalized_rows)
        prior = predecessor["incumbent_validation"]
        qps_ratio = float(incumbent_validation["db_qps"]) / float(prior["db_qps"])
        target = base.alg.TARGET[scene]
        target_delta = abs(
            float(incumbent_validation["resource_means"][target])
            - float(prior["resource_means"][target])
        )
        if not 0.5 <= qps_ratio <= 1.5 or target_delta > 0.35:
            raise RuntimeError(
                "four-worker incumbent equivalence gate failed: "
                f"qps_ratio={qps_ratio:.6f} target_delta={target_delta:.6f}"
            )
        transition_context.update({
            "baseline_by_worker": baseline_rows,
            "incumbent_validation": incumbent_validation,
            "incumbent_replay_rows": incumbent_rows,
            "qps_ratio_to_predecessor": qps_ratio,
            "target_resource_absolute_delta": target_delta,
        })
        return baseline_rows, latest_state

    def transition_atomic(path: Path, value: Mapping[str, Any]) -> None:
        document = copy.deepcopy(dict(value))
        if (
            path == state_path
            and document.get("formal_started") is True
            and transition_context.get("incumbent_validation") is not None
            and document.get("worker_cohort_transition_completed") is not True
        ):
            document["canonical_baseline"] = copy.deepcopy(preserved_canonical)
            document["baseline_by_worker"] = copy.deepcopy(
                transition_context["baseline_by_worker"]
            )
            document["incumbent_validation"] = copy.deepcopy(
                transition_context["incumbent_validation"]
            )
            document["worker_cohort_transition_completed"] = True
            document["worker_cohort_transition"] = {
                **copy.deepcopy(transition_authority),
                "fresh_baseline_repetitions_per_worker": 2,
                "incumbent_replay_rows": copy.deepcopy(
                    transition_context["incumbent_replay_rows"]
                ),
                "qps_ratio_to_predecessor": transition_context[
                    "qps_ratio_to_predecessor"
                ],
                "target_resource_absolute_delta": transition_context[
                    "target_resource_absolute_delta"
                ],
                "canonical_baseline_preserved": True,
                "preserved_search_state_sha256": slots.canonical_sha256(
                    preserved
                ),
            }
        original_atomic(path, document)

    base.fresh_state = resumed_fresh_state
    base.aggregate_baseline = dynamic_aggregate
    base.measure = transition_measure
    base.atomic = transition_atomic
    base.validate_a_contract = lambda _scene, _active: {
        "release_version": "job_to_xp_slots_v1",
        "physical_replay_parallelism_per_scene": 4,
    }
    base.validate_fresh_authority = lambda **_kwargs: (
        freeze, copy.deepcopy(workers), copy.deepcopy(transition_authority)
    )
    args.runtime_preflight = args.transition_authority
    args.prepared_binding = args.transition_authority
    args.benchmark_accounts = args.transition_authority
    args.fresh_r0 = args.transition_authority
    return base.run(args)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scene", choices=slots.XP_SCENES, required=True)
    parser.add_argument("--predecessor-state", type=regular_file, required=True)
    parser.add_argument("--transition-authority", type=regular_file, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
