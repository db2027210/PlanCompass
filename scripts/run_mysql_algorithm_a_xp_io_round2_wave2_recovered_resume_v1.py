#!/usr/bin/env python3
"""Resume IO-A at Round2 Wave2 from its exact recovered XP8 data plane."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_a_dynamic_slots_v2 as slots
from scripts import mysql_single_scene_measurement_v1 as single_measurement
from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_scene_v6 as reset8
from scripts import run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v12 as runtime


HISTORY = ROOT / "history/mysql_four_scene_20260809"
SOURCE_RUN = HISTORY / "v853_to_a_io_round1_w5_recovered_resume_20260828_xp_io"
SOURCE_STATE = SOURCE_RUN / "algorithm_a_state.json"
SOURCE_CANDIDATES = SOURCE_RUN / "round_002/candidates.json"
SOURCE_WAVE1 = SOURCE_RUN / "round_002/wave_001/attempt_01/batch_result.json"
RECOVERED_RESET = (
    HISTORY / "v856_to_a_io_v855_interrupted_baseline_recovery_20260828"
    / "xp_resets/v856_to_a_io_v855_interrupted_baseline_recovery_20260828"
    / "reset_binding.json"
)
RECOVERED_SETTLE = RECOVERED_RESET.with_name("post_reset_storage_settle.json")
AUTHORITY = (
    HISTORY / "v857_to_a_io_v856_recovery_authority_20260828"
    / "live_authority.json"
)
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_xp_io_round2_wave2_resume_20260828_v1"
    / "transition_contract.json"
)
SCENE = "hybench_xp_io"
EXPECTED = {f"xp_io_w{i}" for i in range(1, 9)}


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def sha(path: Path) -> str:
    return runtime.base.alg.sha(path)


def candidate_vectors(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "rank": int(row["rank"]),
            "optimized_params": copy.deepcopy(row["optimized_params"]),
        }
        for row in document.get("candidates") or []
    ]


def validate() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    contract = load(CONTRACT)
    state = load(SOURCE_STATE)
    candidates = load(SOURCE_CANDIDATES)
    wave1 = load(SOURCE_WAVE1)
    recovered = load(RECOVERED_RESET)
    settle = load(RECOVERED_SETTLE)
    authority = load(AUTHORITY)
    rows = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in recovered.get("workers") or []
    }
    checkpoint = state.get("round_checkpoint") or {}
    wave1_rows = dict(wave1.get("measurements") or {})
    required_true = (
        "round1_committed_state_retained",
        "round2_wave1_physical_evidence_reused",
        "round2_wave2_recovered_reset_reused",
        "completed_candidate_measurements_replayed_false",
        "accepted_or_rejected_credit_for_recovery_zero",
        "candidate_set_unchanged",
        "resource_envelope_unchanged",
        "native_xp_workload_unchanged",
        "physical_replay_parallelism_eight",
        "external_process_control_or_stop_prohibited",
    )
    if (
        contract.get("schema_version")
        != "mysql_algorithm_a_xp_io_round2_wave2_resume_contract_v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("scene") != SCENE
        or any(contract.get(key) is not True for key in required_true)
        or state.get("status") != "FAILED_CLOSED"
        or int(state.get("round_index") or 0) != 2
        or int(state.get("accepted") or 0) != 1
        or int(state.get("rejected_rounds") or 0) != 0
        or list(checkpoint.get("attempted_ranks") or []) != list(range(1, 9))
        or int(candidates.get("candidate_count") or 0) != 16
        or set(wave1_rows) != EXPECTED
        or sorted(int(row.get("rank") or 0) for row in wave1_rows.values())
        != list(range(1, 9))
        or recovered.get("status") != "PASS"
        or set(rows) != EXPECTED
        or recovered.get("physical_template_resume_worker") != "xp_io_w5"
        or recovered.get("candidate_measurements_replayed") != 0
        or recovered.get("accepted_or_rejected_round_credit") != 0
        or settle.get("status") != "PASS"
        or set(settle.get("selected_worker_ids") or []) != EXPECTED
        or authority.get("status") != "PASS"
        or authority.get("scene") != SCENE
        or Path(str(authority.get("source_reset_binding_path"))).resolve()
        != RECOVERED_RESET.resolve()
        or authority.get("source_reset_binding_sha256") != sha(RECOVERED_RESET)
    ):
        raise RuntimeError("IO-A Round2 Wave2 recovered resume boundary differs")
    bindings = {
        "controller": Path(__file__).resolve(),
        "runtime_v12": Path(runtime.__file__).resolve(),
        "source_state": SOURCE_STATE,
        "source_candidates": SOURCE_CANDIDATES,
        "source_wave1": SOURCE_WAVE1,
        "recovered_reset": RECOVERED_RESET,
        "recovered_settle": RECOVERED_SETTLE,
        "live_authority": AUTHORITY,
    }
    for label, path in bindings.items():
        frozen = (contract.get("source_bindings") or {}).get(label) or {}
        if (
            frozen.get("path") != str(path.relative_to(ROOT))
            or frozen.get("sha256") != sha(path)
        ):
            raise RuntimeError(f"IO-A Round2 Wave2 binding differs: {label}")
    return state, rows


def main() -> int:
    predecessor, recovered_rows = validate()
    base = runtime.base
    preserved = slots.search_state_payload(predecessor)
    source_wave1 = load(SOURCE_WAVE1)
    source_wave1_rows = copy.deepcopy(dict(source_wave1.get("measurements") or {}))
    original_fresh_state = base.fresh_state
    original_measure = base.measure
    original_aggregate = base.aggregate_baseline
    original_atomic = base.atomic
    state_path_holder: dict[str, Path] = {}
    baseline_reused = False
    wave1_reused = False
    wave2_reset_reused = False
    patched_no_lease_measure: list[tuple[dict[str, Any], Any]] = []
    physical_measure: dict[str, Any] = {}

    def resumed_fresh_state(
        *, run_id: str, scene: str, active: Sequence[str],
        ranges: Mapping[str, Any], authority: Mapping[str, Any],
    ) -> dict[str, Any]:
        fresh = original_fresh_state(
            run_id=run_id, scene=scene, active=active, ranges=ranges,
            authority=authority,
        )
        fresh.update(copy.deepcopy(preserved))
        fresh.update({
            "accepted": 1,
            "round_index": 1,
            "rejected_rounds": 0,
            "consecutive_rejected_rounds": 0,
            "status": "RUNNING",
            "stage": "ROUND2_WAVE2_RECOVERY_AUTHORITY_VALIDATED",
            "round_checkpoint": None,
            "converged": False,
            "predecessor_run_id": predecessor.get("run_id"),
            "predecessor_snapshot_path": str(SOURCE_STATE),
            "predecessor_snapshot_sha256": sha(SOURCE_STATE),
            "round2_wave1_physical_evidence_reused": True,
            "round2_wave2_recovered_reset_reused": True,
            "completed_candidate_measurements_replayed": False,
            "accepted_or_rejected_credit_for_recovery": 0,
        })
        return fresh

    def preserved_aggregate(rows: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        if rows != predecessor["baseline_by_worker"]:
            raise RuntimeError("IO-A preserved baseline differs")
        return copy.deepcopy(predecessor["canonical_baseline"])

    def reuse_recovered_reset(
        freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
        output: Path, token: str, *, worker_ids: set[str] | None = None,
    ) -> None:
        nonlocal wave2_reset_reused
        selected = set(worker_ids or workers)
        if wave2_reset_reused or selected != EXPECTED or set(workers) != EXPECTED:
            raise RuntimeError("IO-A recovered Wave2 reset reuse scope differs")
        impl = reset8.predecessor.predecessor.predecessor.base._impl
        for worker_id, row in sorted(recovered_rows.items()):
            inspected = impl["_docker_json"]("inspect", str(row["container"]))
            if (
                inspected.get("Id") != row.get("container_id")
                or (inspected.get("State") or {}).get("Running") is not True
                or row.get("ready") is not True
            ):
                raise RuntimeError(f"{worker_id} recovered Wave2 identity differs")
            impl["prep"]._kernel_resource_readback(inspected, row)
            impl["prep"]._enforce_blkio(inspected, freeze, row)
            workers[worker_id] = copy.deepcopy(row)
        stage = output / "xp_resets" / token
        stage.mkdir(parents=True, mode=0o700)
        liveness = {
            worker_id: base.alg.classify_xp_mysql_liveness(workers[worker_id])
            for worker_id in sorted(EXPECTED)
        }
        drain = base.alg.wait_for_xp_session_drain(workers, liveness=liveness)
        base.alg.atomic(stage / "pre_reset_session_drain.json", {
            "schema_version": "mysql_algorithm_a_recovered_wave2_reset_drain_v1",
            "created_at": base.alg.now(),
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            **drain,
        })
        document = copy.deepcopy(load(RECOVERED_RESET))
        document.update({
            "schema_version": "mysql_algorithm_a_xp_io_round2_wave2_recovered_reset_reuse_v1",
            "created_at": base.alg.now(),
            "source_recovered_binding": str(RECOVERED_RESET),
            "source_recovered_binding_sha256": sha(RECOVERED_RESET),
            "source_post_reset_settle": str(RECOVERED_SETTLE),
            "source_post_reset_settle_sha256": sha(RECOVERED_SETTLE),
            "physical_reset_performed_in_this_resume": False,
            "completed_candidate_measurements_replayed": False,
            "accepted_or_rejected_round_credit": 0,
        })
        base.alg.atomic(stage / "reset_binding.json", document)
        settle_document = copy.deepcopy(load(RECOVERED_SETTLE))
        settle_document.update({
            "schema_version": "mysql_algorithm_a_xp_io_round2_wave2_recovered_settle_reuse_v1",
            "created_at": base.alg.now(),
            "source_settle_path": str(RECOVERED_SETTLE),
            "source_settle_sha256": sha(RECOVERED_SETTLE),
            "physical_reset_performed_in_this_resume": False,
        })
        base.alg.atomic(stage / "post_reset_storage_settle.json", settle_document)
        wave2_reset_reused = True

    def resumed_measure(**kwargs: Any):
        nonlocal baseline_reused, wave1_reused
        output = Path(kwargs["output"])
        if output.name == "formal_baseline" and not baseline_reused:
            baseline_reused = True
            ranking = output.parent / "plan_ranking_client_processes"
            ranking.mkdir(parents=True, mode=0o700)
            for worker_id, worker in kwargs["workers"].items():
                worker["formal_client_log"] = str(ranking / f"{worker_id}.jsonl")
            return copy.deepcopy(predecessor["baseline_by_worker"]), kwargs["state"]

        if (
            output.name == "attempt_01"
            and output.parent.name == "wave_001"
            and output.parent.parent.name == "round_002"
            and not wave1_reused
        ):
            current = load(output.parent.parent / "candidates.json")
            if candidate_vectors(current) != candidate_vectors(load(SOURCE_CANDIDATES)):
                raise RuntimeError("IO-A Round2 candidate vectors differ")
            assignments = kwargs["assignments"]
            if sorted(int(row["rank"]) for row in assignments.values()) != list(range(1, 9)):
                raise RuntimeError("IO-A Round2 Wave1 assignment ranks differ")
            output.mkdir(parents=True, mode=0o700)
            document = copy.deepcopy(source_wave1)
            document.update({
                "schema_version": "mysql_algorithm_a_xp_io_round2_wave1_evidence_reuse_v1",
                "source_batch_path": str(SOURCE_WAVE1),
                "source_batch_sha256": sha(SOURCE_WAVE1),
                "new_physical_measurement_invoked": False,
                "completed_candidate_measurements_replayed": False,
            })
            base.alg.atomic(output / "batch_result.json", document)
            wave1_reused = True
            return copy.deepcopy(source_wave1_rows), kwargs["state"]

        if (
            output.name == "attempt_01"
            and output.parent.name == "wave_002"
            and output.parent.parent.name == "round_002"
            and not wave2_reset_reused
        ):
            original_settle = single_measurement.async_xp_settle
            single_measurement.async_xp_settle = type(
                "RecoveredSettle", (), {"reset_xp": staticmethod(reuse_recovered_reset)}
            )
            try:
                return physical_measure["callable"](**kwargs)
            finally:
                single_measurement.async_xp_settle = original_settle
        return physical_measure["callable"](**kwargs)

    def resumed_atomic(path: Path, value: Mapping[str, Any]) -> None:
        document = copy.deepcopy(dict(value))
        if path.name == "algorithm_a_state.json" and document.get("formal_started") is True:
            state_path_holder["path"] = path
            document["canonical_baseline"] = copy.deepcopy(predecessor["canonical_baseline"])
            document["baseline_by_worker"] = copy.deepcopy(predecessor["baseline_by_worker"])
            document["round2_wave2_recovery_resume"] = {
                "contract_path": str(CONTRACT),
                "contract_sha256": sha(CONTRACT),
                "round1_committed_state_retained": True,
                "round2_wave1_physical_evidence_reused": wave1_reused,
                "round2_wave2_recovered_reset_reused": wave2_reset_reused,
                "completed_candidate_measurements_replayed": False,
                "accepted_or_rejected_credit_for_recovery": 0,
                "new_physical_work_starts_after_rank": 8,
            }
        original_atomic(path, document)

    # The XP8 entrypoint assigns ``base.measure = no_lease_measure`` inside
    # its own exec-backed implementation immediately before ``base.run``.
    # Patch that exact dispatch binding, not only the public module attribute.
    def patch_measure_dispatch(value: Any, seen: set[int] | None = None) -> None:
        seen = seen or set()
        if id(value) in seen or not isinstance(value, dict):
            return
        seen.add(id(value))
        if "no_lease_measure" in value and callable(value["no_lease_measure"]):
            patched_no_lease_measure.append((value, value["no_lease_measure"]))
            value["no_lease_measure"] = resumed_measure
        for child in value.values():
            if isinstance(child, dict):
                patch_measure_dispatch(child, seen)

    patch_measure_dispatch(runtime.implementation)
    if not patched_no_lease_measure:
        raise RuntimeError("IO-A XP8 measurement dispatch binding was not found")
    unique_dispatches = {id(original): original for _, original in patched_no_lease_measure}
    if len(unique_dispatches) != 1:
        raise RuntimeError("IO-A XP8 measurement dispatch is ambiguous")
    physical_measure["callable"] = next(iter(unique_dispatches.values()))

    base.fresh_state = resumed_fresh_state
    base.aggregate_baseline = preserved_aggregate
    base.measure = resumed_measure
    base.atomic = resumed_atomic
    try:
        result = int(runtime.main())
    finally:
        for namespace, original in patched_no_lease_measure:
            namespace["no_lease_measure"] = original
        base.fresh_state = original_fresh_state
        base.aggregate_baseline = original_aggregate
        base.measure = original_measure
        base.atomic = original_atomic
    if not baseline_reused or not wave1_reused or not wave2_reset_reused:
        raise RuntimeError("IO-A Round2 Wave2 recovery hooks were not all invoked")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
