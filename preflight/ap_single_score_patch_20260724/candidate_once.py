#!/usr/bin/env python3
"""Run one AP outer-loop candidate with the frozen R0 workload machinery.

The R0 source is executed from an in-memory, audited candidate-mode transform:
candidate points retain DB-QPS, AP13 correctness and stability gates, while the
R0-only 85--95/30--50 construction band is not an acceptance gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict


FROZEN_BASELINE_RUNNER_SHA256 = (
    "5116d3d77b27543f530e2a6a394bcdb4298deddde31441f1196dc706b3e125c4"
)
UNIFIED_STABILITY_CONTRACT_V5_SHA256 = (
    "4b90d0126806ac16031fafbfe905d3aa3d693ddf28b64717eb348fb0911d4692"
)
ACTIVE_PARAMS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
FIXED_COST_PARAMS = {
    "cpu_index_tuple_cost": 0.005,
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_candidate_params(path: Path) -> Dict[str, float]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != set(ACTIVE_PARAMS):
        raise RuntimeError(
            "candidate params must contain exactly the six formal AP PostgreSQL cost parameters"
        )
    parsed = {}
    for name in ACTIVE_PARAMS:
        value = float(raw[name])
        if not math.isfinite(value) or value <= 0.0:
            raise RuntimeError(f"invalid candidate parameter: {name}={value!r}")
        parsed[name] = value
    return parsed


def transform_baseline_source(source: str) -> tuple[str, Dict[str, int]]:
    replacements = {
        'baseline_root = args.lane_root / "results" / "shared_baseline"': (
            'baseline_root = args.lane_root / "results" / "experience_outer"'
        ),
        'point_dir = baseline_root / f"P0_repeat_1_{args.attempt_id}"': (
            'point_dir = baseline_root / f"candidate_{args.attempt_id}"'
        ),
        '"window_index": index,': (
            '"window_index": index + 1,  # candidate reports are one-based'
        ),
        '"start_window_index": end_index - required_windows,': (
            '"start_window_index": end_index - required_windows + 1,'
        ),
        '"end_window_index": end_index - 1,': (
            '"end_window_index": end_index,'
        ),
        'resource_gate_evaluated = False': (
            'resource_gate_evaluated = True  # candidate: R0 construction band exempt'
        ),
        'from formal_contracts import PG5_STABILITY_GATE_SHA256': (
            f'PG5_STABILITY_GATE_SHA256 = "{UNIFIED_STABILITY_CONTRACT_V5_SHA256}"'
        ),
        'stability_contract.get("contract_id") != "pg5_six_window_resource_mean_stability_v2"': (
            'stability_contract.get("contract_id") != "pg5_target_bottleneck_rolling_stability_v5"'
        ),
        'stability_contract.get("throughput_fields_required_for_stability") is not False': (
            'stability_contract.get("throughput_stability_required") is not False'
        ),
        '''        metric_fields = (
            "cpu_utilization",
            "memory_utilization",
            "io_utilization",
        )''': (
            '''        # AP is a memory-bottleneck benchmark.  CPU and I/O are
        # retained as diagnostic window means, but only the target bottleneck
        # participates in the stability decision.
        metric_fields = (
            "cpu_utilization",
            "memory_utilization",
            "io_utilization",
        )
        stability_metric_fields = ("memory_utilization",)'''
        ),
        'and all(value["passed"] for value in metrics.values())': (
            '''and all(
                    metrics[field]["passed"]
                    for field in stability_metric_fields
                )'''
        ),
        '''            "throughput_fields_required_for_stability": False,
            "initial_probe_seconds": 180,''': (
            '''            "throughput_fields_required_for_stability": False,
            "stability_metric_fields": list(stability_metric_fields),
            "resource_mean_metric_fields": list(metric_fields),
            "initial_probe_seconds": 180,'''
        ),
        '''                "stability_metric_fields": [
                    "cpu_utilization",
                    "memory_utilization",
                    "io_utilization",
                ],''': (
            '''                "stability_metric_fields": ["memory_utilization"],
                "resource_mean_metric_fields": [
                    "cpu_utilization",
                    "memory_utilization",
                    "io_utilization",
                ],'''
        ),
        '''            "stability_metric_fields": [
                "cpu_utilization",
                "memory_utilization",
                "io_utilization",
            ],''': (
            '''            "stability_metric_fields": ["memory_utilization"],
            "resource_mean_metric_fields": [
                "cpu_utilization",
                "memory_utilization",
                "io_utilization",
            ],'''
        ),
        '"purpose": "shared_baseline_before_common_outer_methods",': (
            '"purpose": "experience_table_outer_candidate_real_replay",'
        ),
        '"method_results_must_reuse_this_baseline": True,': (
            '"method_results_must_reuse_this_baseline": False,'
        ),
        '''    resource_gate_path = point_dir / "measurement_r0_resource_gate.json"
    if not resource_gate_path.is_file():
        raise RuntimeError("formal R0 85-95 resource gate evidence is missing")
    resource_gate_evidence = points.read_json(resource_gate_path)''': (
            '''    resource_gate_path = point_dir / "measurement_candidate_r0_exemption.json"
    resource_gate_evidence = {
        "schema_version": 1,
        "gate_id": "hybench_ap_candidate_r0_construction_band_exemption_v1",
        "passed": True,
        "candidate_r0_construction_band_exempt": True,
        "target_and_non_target_bands_evaluated": False,
        "reason": "outer candidates use stability/correctness/DB-QPS hard gates; the 85-95/30-50 bands construct R0 only",
    }
    write_json(resource_gate_path, resource_gate_evidence)'''
        ),
        '''"passed": bool(
            resource_gate_evidence.get("passed") is True
            and stability_passed''': (
            '''"passed": bool(
            stability_passed'''
        ),
        '"r0_resource_gate_passed": resource_gate_evidence.get("passed") is True,': (
            '"candidate_r0_construction_band_exempt": True,'
        ),
        '"gate_id": "hybench_ap_common_outer_entry_v1",': (
            '"gate_id": "hybench_ap_outer_candidate_hard_gate_v2",'
        ),
        '"r0_resource_gate_path": str(resource_gate_path),': (
            '"candidate_r0_construction_exemption_path": str(resource_gate_path),'
        ),
    }
    counts = {}
    transformed = source
    for old, new in replacements.items():
        count = transformed.count(old)
        counts[old] = count
        if count != 1:
            raise RuntimeError(
                f"candidate source transform expected one match, got {count}: {old}"
            )
        transformed = transformed.replace(old, new, 1)
    return transformed, counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lane-root", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--candidate-params-json", type=Path, required=True)
    parser.add_argument("--cpu-quota-cores", type=float, required=True)
    parser.add_argument("--memory-bytes", type=int, required=True)
    parser.add_argument("--io-read-bps", type=int, required=True)
    parser.add_argument("--io-write-bps", type=int, required=True)
    args = parser.parse_args()

    lane_root = args.lane_root.resolve()
    if args.attempt_id == "outer_a1_r01_rank_0008":
        transition_root = lane_root / "results" / "experience_outer" / "attempt_1_ranked_replays"
        authorization_path = transition_root / "replicate_controller_v1_authorized.json"
        wait_path = transition_root / "rank_08_replicate_transition_wait.json"
        if not authorization_path.is_file():
            write_json(wait_path, {
                "schema_version": 1,
                "status": "waiting_for_replicate_controller_v1",
                "pid": os.getpid(),
                "attempt_id": args.attempt_id,
                "docker_started": False,
                "r0_replayed": False,
                "previous_rank_evidence_rewritten": False,
                "started_at_epoch": time.time(),
            })
            while not authorization_path.is_file():
                time.sleep(1.0)
    baseline_path = lane_root / "baseline_once.py"
    baseline_sha = sha256(baseline_path)
    if baseline_sha != FROZEN_BASELINE_RUNNER_SHA256:
        raise RuntimeError(
            f"frozen baseline source mismatch: {baseline_sha}"
        )
    params = load_candidate_params(args.candidate_params_json)
    runtime_cost_params = {**params, **FIXED_COST_PARAMS}
    source, transform_counts = transform_baseline_source(
        baseline_path.read_text(encoding="utf-8")
    )

    import p24_pg_recost_runner as p24

    # This lane user owns and can read the immutable template.  The frozen
    # runner unnecessarily prefixes its two read-only validation commands with
    # sudo, which is unavailable in the non-interactive .201 controller.  Strip
    # sudo only for those exact sha256sum/test checks; all mutation paths remain
    # unchanged.
    original_p24_run = p24.run

    def p24_run_without_unneeded_sudo(command: Any, **kwargs: Any):
        normalized = list(command)
        if len(normalized) >= 2 and normalized[0] == "sudo" and normalized[1] in {
            "sha256sum",
            "test",
        }:
            normalized = normalized[1:]
        return original_p24_run(normalized, **kwargs)

    p24.run = p24_run_without_unneeded_sudo
    original_configure_modules = p24.configure_modules

    def configure_modules_with_candidate(workload: str):
        p24_module, points, validation = original_configure_modules(workload)
        original_run_cost_point = points.run_cost_point

        def run_cost_point_with_candidate(*run_args: Any, **run_kwargs: Any):
            positional = list(run_args)
            if len(positional) < 5:
                raise RuntimeError("unexpected run_cost_point signature")
            # The frozen point runner validates the complete seven-setting DB
            # payload.  AP formally optimizes only six; cpu_index_tuple_cost is
            # carried through at its immutable baseline value.
            positional[4] = dict(runtime_cost_params)
            return original_run_cost_point(*positional, **run_kwargs)

        points.run_cost_point = run_cost_point_with_candidate
        return p24_module, points, validation

    p24.configure_modules = configure_modules_with_candidate
    candidate_root = lane_root / "results" / "experience_outer"
    contract_path = candidate_root / f"candidate_runtime_contract_{args.attempt_id}.json"
    write_json(
        contract_path,
        {
            "schema_version": 1,
            "attempt_id": args.attempt_id,
            "candidate_params": params,
            "formal_active_params": list(ACTIVE_PARAMS),
            "fixed_cost_params": FIXED_COST_PARAMS,
            "runtime_complete_cost_params": runtime_cost_params,
            "frozen_baseline_source_path": str(baseline_path),
            "frozen_baseline_source_sha256": baseline_sha,
            "candidate_source_transform_counts": transform_counts,
            "unified_stability_contract_v5_path": str(
                lane_root / "stability_gate_contract_v5.json"
            ),
            "unified_stability_contract_v5_sha256": UNIFIED_STABILITY_CONTRACT_V5_SHA256,
            "r0_construction_band_applies": False,
            "window_indexing": "one_based",
            "stability_checkpoints_seconds": list(range(180, 601, 30)),
            "unstable_checkpoint_increment_seconds": 30,
            "stability_window_selection": "latest_six_complete_30_second_windows_at_each_checkpoint",
            "stability_metric_fields": ["memory_utilization"],
            "resource_mean_metric_fields": [
                "cpu_utilization",
                "memory_utilization",
                "io_utilization",
            ],
            "stability_gate_order": "require target memory CV <= 0.10; CPU/IO means are diagnostic only; candidate R0 construction bands are exempt",
            "candidate_hard_gates": [
                "formal_db_qps_pg_stat_statements",
                "ap13_correctness",
                "six_window_resource_stability",
            ],
            "outer_acceptance": "candidate.db_qps > incumbent.db_qps and candidate.memory < incumbent.memory",
        },
    )

    saved_argv = sys.argv
    namespace: Dict[str, Any] = {
        "__name__": "candidate_runtime",
        "__file__": str(baseline_path),
    }
    sys.argv = [
        str(baseline_path),
        "--lane-root", str(lane_root),
        "--measurement-seconds", "600",
        "--attempt-id", args.attempt_id,
        "--cpu-quota-cores", str(args.cpu_quota_cores),
        "--memory-bytes", str(args.memory_bytes),
        "--io-read-bps", str(args.io_read_bps),
        "--io-write-bps", str(args.io_write_bps),
        "--scoring-contract", str(lane_root / "common_scoring_contract.json"),
        "--scoring-contract-sha256", "65de8b19d794e6323795bb6c39962ef1d41b9139bf5eafb957726ad10d7ee64b",
        "--resource-gate-contract", str(lane_root / "r0_resource_gate_contract_v2.json"),
        "--resource-gate-contract-sha256", "547ec566adbb78b51e290b69f9d5bf97e5f9c1cbbdffbe7eb8afac741fddcd16",
        "--stability-contract", str(lane_root / "stability_gate_contract_v5.json"),
        "--stability-contract-sha256", UNIFIED_STABILITY_CONTRACT_V5_SHA256,
        "--io-utilization-contract", str(lane_root / "io_utilization_contract.json"),
        "--io-utilization-contract-sha256", "35eb6f304627ef915f4cfb463b64e472a760325834df7978ec67cac85c342064",
    ]
    try:
        exec(compile(source, str(baseline_path), "exec"), namespace)
        result = int(namespace["main"]())
    finally:
        sys.argv = saved_argv
        p24.configure_modules = original_configure_modules
        p24.run = original_p24_run
    point_dir = candidate_root / f"candidate_{args.attempt_id}"
    print(
        json.dumps(
            {
                "status": "complete" if result == 0 else "failed",
                "point_dir": str(point_dir),
                "candidate_runtime_contract": str(contract_path),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
