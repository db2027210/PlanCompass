#!/usr/bin/env python3
"""Fail-closed preparation for JOB formal-10 after the round10 entry gate."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict


BASE = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "job_node02/history/job/outer/attempt_7_cap5_contract_20260726"
)
ROUND10 = BASE / "gradient_debug_round10_exact_query_transition_regime_v1_20260729T1025"
SOURCE = BASE / "gradient_debug_round7_fresh_exclusion_v1"
FORMAL = BASE / "formal10_after_round10_gate_20260729T1055"
EXPECTED_PARAMS = {
    "cpu_index_tuple_cost": 0.0005,
    "cpu_operator_cost": 0.0025,
    "cpu_tuple_cost": 0.01,
    "parallel_setup_cost": 7000,
    "parallel_tuple_cost": 0.001,
    "random_page_cost": 4,
    "seq_page_cost": 1,
}


def fail(message: str) -> None:
    raise RuntimeError(f"FAIL_CLOSED: {message}")


def read_int(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as exc:
        fail(f"invalid counter {path}: {exc}")
    raise AssertionError


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def main() -> int:
    summary_path = ROUND10 / "replay" / "summary.json"
    ranking_path = ROUND10 / "job_round10_top5.json"
    config_path = SOURCE / "config.json"
    for path in (summary_path, ranking_path, config_path):
        if not path.is_file():
            fail(f"missing authority file: {path}")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    ranking = json.loads(ranking_path.read_text(encoding="utf-8"))
    checks = {
        "diagnostic_only": summary.get("diagnostic_only") is True,
        "no_formal_mutation": summary.get("diagnostic_updates_formal_state") is False,
        "five_physical": summary.get("physical_invocation_count") == 5,
        "five_valid": summary.get("successful_real_replay_count") == 5,
        "zero_technical": summary.get("technical_failure_count") == 0,
        "baseline_restored": summary.get("baseline_params_restored") is True,
        "qps_outer_only": summary.get("qps_used_for_inner_scoring") is False,
        "no_rejected_mutation": (
            summary.get("rejected_candidate_updates_incumbent_range_or_accepted") is False
        ),
    }
    if not all(checks.values()):
        fail(f"round10 contract mismatch: {checks}")

    successful = summary.get("successful", [])
    if len(successful) != 5:
        fail("round10 successful list does not contain exactly five records")
    roles = [item.get("selection_role") for item in successful]
    if roles != ["trusted", "trusted", "trusted", "probe", "probe"]:
        fail(f"unexpected trusted/probe roles: {roles}")
    if any(item.get("accepted") is not False for item in successful):
        fail("diagnostic replay contains accepted=true")
    for item in successful:
        if any(
            item.get(flag) is not False
            for flag in (
                "gradient_update_applied",
                "incumbent_update_applied",
                "range_update_applied",
            )
        ):
            fail(f"diagnostic mutation flag set at rank {item.get('rank')}")
        state = item.get("incumbent_state_after", {})
        if state.get("accepted_count") != 0:
            fail(f"accepted_count mismatch at rank {item.get('rank')}")
        if state.get("incumbent_params") != EXPECTED_PARAMS:
            fail(f"incumbent params mismatch at rank {item.get('rank')}")

    trusted = successful[:3]
    predicted = [float(item["calibrated_candidate_score"]) for item in trusted]
    actual = [float(item["actual_score"]) for item in trusted]
    predicted_order = sorted(range(3), key=lambda idx: predicted[idx], reverse=True)
    actual_order = sorted(range(3), key=lambda idx: actual[idx], reverse=True)
    rank_map = {idx: rank for rank, idx in enumerate(predicted_order, start=1)}
    spearman = 1.0 - 6.0 * sum(
        (rank_map[idx] - (actual_order.index(idx) + 1)) ** 2 for idx in range(3)
    ) / 24.0
    if spearman <= 0:
        fail(f"trusted-only Spearman is not positive: {spearman}")
    if rank_map[actual_order[0]] > 3:
        fail("actual best is outside predicted Top-3")
    if len({
        item["candidate_binding"]["fingerprint_id"] for item in successful
    }) != 5:
        fail("Top-5 fingerprints are not distinct")

    if read_int(BASE / "formal_search_accepted_counter.txt") != 0:
        fail("base accepted counter is not zero")
    if read_int(BASE / "formal_search_replay_counter.txt") != 130:
        fail("formal replay counter is not 130")
    if read_int(BASE / "replay_counter.txt") != 189:
        fail("global replay ordinal is not 189")

    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("initial_params") != EXPECTED_PARAMS:
        fail("source initial_params do not match recovered incumbent")
    protocol = config["formal_outer_protocol"]
    if protocol.get("global_real_replay_top_k_hard_cap") != 5:
        fail("source per-batch hard cap is not five")
    if protocol.get("qps_role") != "outer_real_measurement_only":
        fail("source QPS role is not outer-only")

    if FORMAL.exists():
        fail(f"formal directory already exists: {FORMAL}")
    (FORMAL / "state").mkdir(parents=True)
    for dirname in ("auto_explain", "plan_captures", "resource_reports", "subset_logs"):
        (FORMAL / dirname).mkdir()

    protocol.update(
        {
            "accepted_shrink_target": 10,
            "formal_search_accepted_count_start": 0,
            "formal_search_accepted_target": 10,
            "formal_search_accepted_counter_path": str(
                FORMAL / "state" / "formal_search_accepted_counter.txt"
            ),
            "formal_search_replay_count_start": 130,
            "formal_search_replay_counter_path": str(
                BASE / "formal_search_replay_counter.txt"
            ),
            "formal_search_attempt_safety_cap": 260,
            "formal_search_maximum_replay_count": 260,
            "maximum_cumulative_real_replay_count": 260,
            "maximum_non_default_real_candidates": 260,
            "formal_startup_baseline_count_start": 0,
            "formal_startup_baseline_counter_path": str(
                FORMAL / "state" / "formal_startup_baseline_counter.txt"
            ),
            "formal_startup_baseline_maximum_count": 1,
            "global_replay_counter_path": str(BASE / "replay_counter.txt"),
            "global_replay_ordinal_at_binding": 189,
            "initial_real_replay_count": 130,
            "maximum_resource_qualified_real_replays_per_batch": 5,
            "candidate_failure_consumes_budget": True,
        }
    )
    config["_job_round10_entry_gate"] = {
        "passed": True,
        "trusted_only_spearman": spearman,
        "actual_best_predicted_rank": rank_map[actual_order[0]],
        "round10_summary_path": str(summary_path),
        "round10_summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
        "round10_ranking_path": str(ranking_path),
        "round10_ranking_sha256": hashlib.sha256(ranking_path.read_bytes()).hexdigest(),
        "formal_replay_count_at_entry": 130,
        "global_ordinal_at_entry": 189,
    }
    config["_gradient_contract_round"]["remote_round_root"] = str(FORMAL)
    config["auto_explain"]["output_dir"] = str(FORMAL / "auto_explain")
    config["logging"]["history_path"] = str(FORMAL / "optimizer_history.jsonl")
    config["optimizer"]["gradient"]["subset_log_dir"] = str(FORMAL / "subset_logs")
    config["output"]["directory"] = str(FORMAL)
    config["plan_capture"]["output_dir"] = str(FORMAL / "plan_captures")
    config["resource_iteration_reports"]["output_dir"] = str(FORMAL / "resource_reports")

    (FORMAL / "state" / "formal_search_accepted_counter.txt").write_text(
        "0\n", encoding="utf-8"
    )
    (FORMAL / "state" / "formal_startup_baseline_counter.txt").write_text(
        "0\n", encoding="utf-8"
    )
    shutil.copy2(summary_path, FORMAL / "round10_entry_summary.json")
    shutil.copy2(ranking_path, FORMAL / "round10_entry_ranking_analysis.json")
    atomic_json(FORMAL / "config.json", config)

    manifest = {
        "status": "prepared",
        "formal_root": str(FORMAL),
        "source_config": str(config_path),
        "config_sha256": hashlib.sha256((FORMAL / "config.json").read_bytes()).hexdigest(),
        "entry_checks": checks,
        "trusted_only_spearman": spearman,
        "actual_best_predicted_rank": rank_map[actual_order[0]],
        "accepted_start": 0,
        "formal_replay_start_preserved": 130,
        "global_ordinal_start_preserved": 189,
        "per_batch_physical_cap": 5,
        "accepted_target": 10,
    }
    atomic_json(FORMAL / "entry_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr, flush=True)
        sys.exit(2)
