#!/usr/bin/env python3
"""Prepare JOB formal continuation 14 after the valid rejected point 216."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict


SOURCE_CONFIG = Path("/tmp/job_cont13_config.json")
POINT216 = Path("/tmp/job_point0216_result.json")
PACKAGE = Path("/tmp/job_cont14_package")
ATTEMPT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/"
    "attempt_7_cap5_contract_20260726"
)
FORMAL = ATTEMPT / "formal10_after_round10_gate_cont14_exclude_p216_v1_20260729T2035"
FINGERPRINT = (
    "three-level-sha256:"
    "274d0f6c5006e174f496b448d3811f7d67941305d9c95de4259c432af0d43656"
)
PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 3.9654064389466446,
    "cpu_tuple_cost": 0.010875008756367419,
    "cpu_index_tuple_cost": 0.0005,
    "cpu_operator_cost": 0.0027500000000000003,
    "parallel_setup_cost": 7504.630881041151,
    "parallel_tuple_cost": 0.001,
}


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dump_json(path: Path, value: Dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    config = load_json(SOURCE_CONFIG)
    point = load_json(POINT216)
    if (
        sha256(SOURCE_CONFIG)
        != "44a21426c0ca413b7d2dc47893f5e8f9d4d6dc3f21550c05b7397d6fac9993b3"
        or sha256(POINT216)
        != "4036a84c1f92c7c3d4b9287c53af26d15b8281f9c0a68bc04d38eb07692f4342"
        or point.get("status") != "complete"
        or point.get("point_id") != "job_experience_table_outer_attempt1_0216"
    ):
        raise RuntimeError("cont13 or point216 evidence changed")

    observed = point.get("cost_parameters") or {}
    for name, expected in PARAMS.items():
        if abs(float(observed[name]) - expected) > max(1e-5, abs(expected) * 1e-5):
            raise RuntimeError(f"point216 parameter mismatch: {name}")

    formal = config["formal_outer_protocol"]
    if (
        formal["formal_search_accepted_count_start"] != 0
        or formal["formal_search_replay_count_start"] != 156
        or formal["global_replay_ordinal_at_binding"] != 215
        or formal["formal_search_maximum_replay_count"] != 161
        or formal["qps_used_for_inner_scoring"] is not False
    ):
        raise RuntimeError("cont13 formal state binding mismatch")

    prior_params = formal["excluded_prior_parameter_signatures"]
    if PARAMS not in prior_params:
        prior_params.append(PARAMS)
    prior_fingerprints = formal["excluded_prior_three_level_fingerprints"]
    if FINGERPRINT not in prior_fingerprints:
        prior_fingerprints.append(FINGERPRINT)

    formal.update(
        {
            "formal_search_replay_count_start": 157,
            "initial_real_replay_count": 157,
            "formal_search_maximum_replay_count": 162,
            "formal_search_attempt_safety_cap": 162,
            "maximum_cumulative_real_replay_count": 162,
            "global_replay_ordinal_at_binding": 216,
            "formal_startup_baseline_counter_path": str(
                FORMAL / "state/formal_startup_baseline_counter.txt"
            ),
        }
    )
    continuation = config["_job_formal_continuation"]
    continuation.update(
        {
            "schema_version": "job_formal10_cont14_exclude_p216_v1",
            "previous_preparation_dir": str(
                ATTEMPT
                / "formal10_after_round10_gate_cont13_ratio_anchor_reuse_v1_20260729T2005"
            ),
            "accepted_count_start": 0,
            "formal_replay_count_start": 157,
            "global_replay_ordinal_at_binding": 216,
            "absolute_replay_cap": 162,
            "excluded_point": 216,
            "excluded_point_result_sha256": sha256(POINT216),
            "excluded_three_level_fingerprint": FINGERPRINT,
        }
    )
    config["output"]["directory"] = str(FORMAL)
    config["logging"]["history_path"] = str(FORMAL / "optimizer_history.jsonl")
    config["optimizer"]["gradient"]["subset_log_dir"] = str(
        FORMAL / "subset_logs"
    )

    PACKAGE.mkdir(parents=True, exist_ok=True)
    config_path = PACKAGE / "config.json"
    dump_json(config_path, config)
    manifest = {
        "schema_version": "job_formal10_cont14_exclude_p216_manifest_v1",
        "preparation_only": True,
        "workload_started": False,
        "remote_formal_dir": str(FORMAL),
        "accepted_count_start": 0,
        "formal_replay_count_start": 157,
        "global_replay_ordinal_at_binding": 216,
        "batch_physical_cap": 5,
        "absolute_replay_cap": 162,
        "qps_used_for_inner_scoring": False,
        "model_retraining_enabled": False,
        "point216_result_sha256": sha256(POINT216),
        "excluded_three_level_fingerprint": FINGERPRINT,
        "output_config_sha256": sha256(config_path),
    }
    dump_json(PACKAGE / "continuation_manifest.json", manifest)
    (PACKAGE / "formal_startup_baseline_counter.txt").write_text(
        "1\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
