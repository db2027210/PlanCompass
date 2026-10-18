#!/usr/bin/env python3
"""Resume IO-A from its valid baseline and recovered Round1 Wave1 reset."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
import types
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_single_scene_measurement_v1 as single_measurement
from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_scene_v6 as reset8
from scripts import run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v12 as runtime


HISTORY = ROOT / "history/mysql_four_scene_20260809"
SOURCE_RUN = HISTORY / "v850_to_a_io_baseline_w5_retry_disk2_settle_20260827_xp_io"
SOURCE_STATE = SOURCE_RUN / "algorithm_a_state.json"
SOURCE_BASELINE = SOURCE_RUN / "formal_baseline/batch_result.json"
SOURCE_CANDIDATES = SOURCE_RUN / "round_001/candidates.json"
RECOVERY_ROOT = HISTORY / "v851_to_a_io_round1_w5_single_reset_recovery_20260827"
RECOVERY_TOKEN = "v851_to_a_io_round1_w5_single_reset_recovery_20260827"
RECOVERED_BINDING = RECOVERY_ROOT / "xp_resets" / RECOVERY_TOKEN / "reset_binding.json"
RECOVERED_SETTLE = RECOVERY_ROOT / "xp_resets" / RECOVERY_TOKEN / "post_reset_storage_settle.json"
AUTHORITY = HISTORY / "v852_to_a_io_round1_w5_recovery_live_authority_20260827/live_authority.json"
CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_xp_io_round1_w5_resume_20260827_v1"
    / "transition_contract.json"
)
SCENE = "hybench_xp_io"
EXPECTED = {f"xp_io_w{i}" for i in range(1, 9)}


def load(path: Path) -> dict[str, Any]:
    value = runtime.base.alg.load(path)
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def validate() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    contract = load(CONTRACT)
    failed = load(SOURCE_STATE)
    baseline = load(SOURCE_BASELINE)
    candidates = load(SOURCE_CANDIDATES)
    recovered = load(RECOVERED_BINDING)
    settle = load(RECOVERED_SETTLE)
    authority = load(AUTHORITY)
    rows = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in recovered.get("workers") or []
    }
    baseline_rows = dict(baseline.get("measurements") or {})
    required_true = (
        "baseline_physical_measurements_reused",
        "recovered_round1_reset_reused",
        "candidate_measurements_reused_false",
        "accepted_or_rejected_round_credit_zero",
        "candidate_set_unchanged",
        "resource_envelope_unchanged",
        "native_xp_workload_unchanged",
        "physical_replay_parallelism_eight",
        "external_process_control_or_stop_prohibited",
    )
    if (
        contract.get("schema_version")
        != "mysql_algorithm_a_xp_io_round1_w5_resume_contract_v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("scene") != SCENE
        or any(contract.get(key) is not True for key in required_true)
        or failed.get("status") != "FAILED_CLOSED"
        or failed.get("stage") != "FAILED"
        or int(failed.get("round_index") or 0) != 1
        or int(failed.get("accepted") or 0) != 0
        or int(failed.get("rejected_rounds") or 0) != 0
        or baseline.get("status") != "PASS"
        or set(baseline_rows) != EXPECTED
        or any(
            not runtime.base.pg_outer.physical_valid(row)
            for row in baseline_rows.values()
        )
        or int(candidates.get("candidate_count") or 0) != 16
        or recovered.get("status") != "PASS"
        or set(rows) != EXPECTED
        or recovered.get("candidate_measurements_reused") != 0
        or recovered.get("accepted_or_rejected_round_credit") != 0
        or recovered.get("physical_worker_reset_in_this_recovery") != ["xp_io_w5"]
        or settle.get("status") != "PASS"
        or settle.get("selected_worker_ids") != ["xp_io_w5"]
        or authority.get("status") != "PASS"
        or authority.get("scene") != SCENE
        or Path(str(authority.get("source_reset_binding_path"))).resolve()
        != RECOVERED_BINDING.resolve()
        or authority.get("source_reset_binding_sha256")
        != runtime.base.alg.sha(RECOVERED_BINDING)
    ):
        raise RuntimeError("IO-A recovered Round1 resume boundary differs")
    bindings = {
        "controller": Path(__file__).resolve(),
        "runtime_v12": Path(runtime.__file__).resolve(),
        "source_state": SOURCE_STATE,
        "source_baseline": SOURCE_BASELINE,
        "source_candidates": SOURCE_CANDIDATES,
        "recovered_binding": RECOVERED_BINDING,
        "recovered_settle": RECOVERED_SETTLE,
        "live_authority": AUTHORITY,
    }
    for label, path in bindings.items():
        frozen = (contract.get("source_bindings") or {}).get(label) or {}
        if (
            frozen.get("path") != str(path.relative_to(ROOT))
            or frozen.get("sha256") != runtime.base.alg.sha(path)
        ):
            raise RuntimeError(f"IO-A recovered resume binding differs: {label}")
    return contract, rows


def main() -> int:
    _, recovered_rows = validate()
    base = runtime.base
    physical_measure: dict[str, Any] = {}
    baseline_reused = False
    reset_reused = False

    def reuse_reset(
        freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
        output: Path, token: str, *, worker_ids: set[str] | None = None,
    ) -> None:
        nonlocal reset_reused
        selected = set(worker_ids or workers)
        if reset_reused or selected != EXPECTED or set(workers) != EXPECTED:
            raise RuntimeError("IO-A recovered reset reuse scope differs")
        reset_reused = True
        impl = reset8.predecessor.predecessor.predecessor.base._impl
        for worker_id, row in sorted(recovered_rows.items()):
            inspected = impl["_docker_json"]("inspect", str(row["container"]))
            if (
                inspected.get("Id") != row.get("container_id")
                or (inspected.get("State") or {}).get("Running") is not True
                or row.get("ready") is not True
            ):
                raise RuntimeError(f"{worker_id} recovered reset identity differs")
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
            "schema_version": "mysql_algorithm_a_recovered_reset_drain_v1",
            "created_at": base.alg.now(),
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            **drain,
        })
        source = load(RECOVERED_BINDING)
        document = copy.deepcopy(source)
        document.update({
            "schema_version": "mysql_algorithm_a_xp_io_round1_recovered_reset_reuse_v1",
            "created_at": base.alg.now(),
            "source_recovered_binding": str(RECOVERED_BINDING),
            "source_recovered_binding_sha256": base.alg.sha(RECOVERED_BINDING),
            "source_post_reset_settle": str(RECOVERED_SETTLE),
            "source_post_reset_settle_sha256": base.alg.sha(RECOVERED_SETTLE),
            "physical_reset_performed_in_this_resume": False,
            "candidate_measurements_reused": 0,
            "accepted_or_rejected_round_credit": 0,
        })
        base.alg.atomic(stage / "reset_binding.json", document)
        base.alg.atomic(stage / "recovered_reset_reuse_manifest.json", {
            "schema_version": "mysql_algorithm_a_xp_io_round1_recovered_reset_reuse_manifest_v1",
            "status": "PASS",
            "created_at": base.alg.now(),
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "source_recovered_binding": str(RECOVERED_BINDING),
            "source_recovered_binding_sha256": base.alg.sha(RECOVERED_BINDING),
            "source_post_reset_settle": str(RECOVERED_SETTLE),
            "source_post_reset_settle_sha256": base.alg.sha(RECOVERED_SETTLE),
            "workers": sorted(EXPECTED),
            "workload_started_since_recovery": False,
            "candidate_measurements_reused": 0,
            "accepted_or_rejected_round_credit": 0,
        })

    def measure_with_recovery(**kwargs: Any):
        nonlocal baseline_reused
        output = Path(kwargs["output"])
        if output.name == "formal_baseline" and not baseline_reused:
            baseline_reused = True
            assignments = dict(kwargs["assignments"])
            source = load(SOURCE_BASELINE)
            source_rows = copy.deepcopy(dict(source.get("measurements") or {}))
            if set(assignments) != EXPECTED or set(source_rows) != EXPECTED:
                raise RuntimeError("IO-A recovered baseline reuse scope differs")
            output.mkdir(parents=True, mode=0o700)
            document = copy.deepcopy(source)
            document.update({
                "schema_version": "mysql_algorithm_a_xp_io_recovered_baseline_reuse_v1",
                "status": "PASS",
                "technical_baseline_reuse": True,
                "source_batch_path": str(SOURCE_BASELINE),
                "source_batch_sha256": base.alg.sha(SOURCE_BASELINE),
                "physical_workload_invocations": 0,
                "candidate_measurements_reused": 0,
                "accepted_or_rejected_round_credit": 0,
            })
            base.alg.atomic(output / "batch_result.json", document)
            return source_rows, kwargs["state"]

        if (
            output.name == "attempt_01"
            and output.parent.name == "wave_001"
            and output.parent.parent.name == "round_001"
            and not reset_reused
        ):
            current_candidates = load(output.parent.parent / "candidates.json")
            source_candidates = load(SOURCE_CANDIDATES)
            current_vectors = [
                {"rank": int(row["rank"]), "optimized_params": row["optimized_params"]}
                for row in current_candidates.get("candidates") or []
            ]
            source_vectors = [
                {"rank": int(row["rank"]), "optimized_params": row["optimized_params"]}
                for row in source_candidates.get("candidates") or []
            ]
            if current_vectors != source_vectors:
                raise RuntimeError("IO-A recovered Round1 candidate vectors differ")
            original_settle = single_measurement.async_xp_settle
            single_measurement.async_xp_settle = types.SimpleNamespace(
                reset_xp=reuse_reset,
            )
            try:
                return physical_measure["callable"](**kwargs)
            finally:
                single_measurement.async_xp_settle = original_settle
        return physical_measure["callable"](**kwargs)

    original_run = base.run

    def run_with_recovery(args: Any) -> int:
        original_measure = base.measure
        physical_measure["callable"] = original_measure
        base.measure = measure_with_recovery
        try:
            return int(original_run(args))
        finally:
            base.measure = original_measure

    base.run = run_with_recovery
    try:
        result = int(runtime.main())
    finally:
        base.run = original_run
    if not baseline_reused or not reset_reused:
        raise RuntimeError("IO-A recovered baseline/reset hook was not invoked")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
