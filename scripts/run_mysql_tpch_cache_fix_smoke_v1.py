#!/usr/bin/env python3
"""Run one non-scoring cache-preconditioned TPC-H default replay."""

from __future__ import annotations

import argparse
import copy
from pathlib import Path

from scripts import mysql_tpch_cache_preconditioned_measurement_v1 as cache_measurement
from scripts import run_mysql_algorithm_a_async_scene_v3 as algorithm_a


SCENE = "tpch_sf4_memory"


def regular_file(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.is_symlink():
        raise argparse.ArgumentTypeError(f"regular non-symlink file required: {path}")
    return path


def run(args: argparse.Namespace) -> int:
    output = args.output_dir.expanduser().resolve()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"fresh TPC-H cache-fix smoke output required: {output}")
    active, ranges = algorithm_a.scene_parameter_space(SCENE)
    algorithm_a.validate_a_contract(SCENE, active)
    output.mkdir(parents=True, mode=0o700)
    state_path = output / "smoke_state.json"
    state = algorithm_a.fresh_state(
        run_id=args.run_id,
        scene=SCENE,
        active=active,
        ranges=ranges,
        authority={},
    )
    state.update({
        "schema_version": "mysql_tpch_cache_fix_smoke_state_v1",
        "formal_evidence_eligible": False,
        "accepted_state_credit_prohibited": True,
        "purpose": "validate_cache_precondition_before_scored_replay",
    })
    algorithm_a.atomic(state_path, state)
    try:
        freeze, workers, authority = algorithm_a.validate_fresh_authority(
            runtime_preflight=args.runtime_preflight,
            prepared_binding=args.prepared_binding,
            benchmark_accounts=args.benchmark_accounts,
            fresh_r0=args.fresh_r0,
            scene=SCENE,
        )
        algorithm_a.runtime.alg.measure_batch = cache_measurement.measure_batch
        state = algorithm_a.alg.load(state_path)
        state["authority"] = authority
        state["stage"] = "CACHE_FIX_SMOKE_MEASURING"
        algorithm_a.atomic(state_path, state)
        assignments = {
            worker_id: {
                "rank": index + 1,
                "params": copy.deepcopy(algorithm_a.alg.DEFAULTS),
            }
            for index, worker_id in enumerate(sorted(workers))
        }
        measured, _ = algorithm_a.measure(
            freeze=freeze,
            workers=workers,
            assignments=assignments,
            output=output / "default_scored_replay",
            reset_token=f"{args.run_id}_default_scored_replay",
            state_path=state_path,
            state=state,
            stage_prefix="CACHE_FIX_SMOKE",
            run_id=args.run_id,
            contract=authority["coordination_contract"],
        )
        aggregate = algorithm_a.aggregate_baseline(measured)
        restore = algorithm_a.clean_restore(workers)
        result = {
            "schema_version": "mysql_tpch_cache_fix_smoke_result_v1",
            "status": "PASS",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "formal_evidence_eligible": False,
            "accepted_state_credit": 0,
            "scene": SCENE,
            "cache_contract_path": str(cache_measurement.CONTRACT_PATH),
            "cache_contract_sha256": cache_measurement._sha(
                cache_measurement.CONTRACT_PATH
            ),
            "aggregate_default_replay": aggregate,
            "worker_measurements": measured,
            "clean_restore": restore,
        }
        algorithm_a.atomic(output / "smoke_result.json", result)
        state = algorithm_a.alg.load(state_path)
        state.update({
            "status": "COMPLETE",
            "stage": "COMPLETE",
            "converged": False,
            "accepted": 0,
            "smoke_result_path": str(output / "smoke_result.json"),
            "smoke_result_sha256": algorithm_a.alg.sha(
                output / "smoke_result.json"
            ),
        })
        algorithm_a.atomic(state_path, state)
        return 0
    except BaseException as error:
        state = algorithm_a.alg.load(state_path)
        state.update({
            "status": "FAILED_CLOSED",
            "stage": "FAILED",
            "accepted": 0,
            "error": f"{type(error).__name__}: {error}",
        })
        algorithm_a.atomic(state_path, state)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--runtime-preflight", type=regular_file, required=True)
    parser.add_argument("--prepared-binding", type=regular_file, required=True)
    parser.add_argument("--benchmark-accounts", type=regular_file, required=True)
    parser.add_argument("--fresh-r0", type=regular_file, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not args.run_id.endswith("_tpch"):
        raise RuntimeError("TPC-H cache-fix smoke run id must end with _tpch")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
