#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List


ATTEMPT = Path(
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
    "hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726"
)
SOURCE_RUN = ATTEMPT / "formal10_after_v3g_entry_gate_reuse_20260729T1425"
SUMMARY = ATTEMPT / "candidate_measurements/20260729T143213+0800_2725057/summary.json"
HISTORY = SOURCE_RUN / "optimizer_history_2026-07-29_142547.jsonl"
BASELINE = SOURCE_RUN / "sealed_startup_baseline_sql.json"
DEST = ATTEMPT / "formal10_canonical_after_unreserved_call_20260729T1448"

EXPECTED_SHA = {
    SUMMARY: "a7865a46bbc8d2c4ad6d31c9226ad58890a62e0b07289f2adecca1b92307f027",
    HISTORY: "1fa71aa812a3b631be1bea3404470b024ee8c896c66c7592f4facd347fb08cdd",
    BASELINE: "ec986fed69e87439b5f84e52ec23960693be04863a76ce1c0be2bb9b50c518a9",
}
EXPECTED_PARAMS = {
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "cpu_tuple_cost": 0.01,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
    "random_page_cost": 4.0,
    "seq_page_cost": 0.2,
}
EXPECTED_FINGERPRINT = (
    "three-level-sha256:fc40eb419f48160230f0932a2d94cf3411a5fdb5c49ef72fd9653139f747dfec"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def same_number(left: Any, right: float) -> bool:
    return math.isclose(float(left), right, rel_tol=0.0, abs_tol=1e-12)


def load_history() -> tuple[Dict[str, Any], Dict[str, Any]]:
    ranking = None
    final = None
    with HISTORY.open("r", encoding="utf-8") as handle:
        for line in handle:
            event = json.loads(line)
            if event.get("event") == "experience_table_all_subset_ranking":
                if ranking is not None:
                    raise RuntimeError("expected exactly one ranking event")
                ranking = event
            elif event.get("event") == "final_summary":
                final = event
    if ranking is None or final is None:
        raise RuntimeError("ranking or final_summary event missing")
    return ranking, final


def pg_readback() -> Dict[str, float]:
    names = sorted(EXPECTED_PARAMS)
    sql_names = ",".join(f"'{name}'" for name in names)
    command = [
        "psql",
        "-h",
        "127.0.0.1",
        "-p",
        "59630",
        "-U",
        "postgres",
        "-d",
        "hybench_sf10x",
        "-Atc",
        f"select name||'='||setting from pg_settings where name in ({sql_names}) order by name",
    ]
    completed = subprocess.run(command, check=True, text=True, capture_output=True, timeout=20)
    result: Dict[str, float] = {}
    for line in completed.stdout.splitlines():
        name, value = line.split("=", 1)
        result[name] = float(value)
    return result


def main() -> None:
    if DEST.exists():
        raise RuntimeError(f"destination already exists: {DEST}")

    for path, expected in EXPECTED_SHA.items():
        observed = sha256(path)
        if observed != expected:
            raise RuntimeError(f"source SHA mismatch: {path}: {observed} != {expected}")

    for name in (
        "formal_search_accepted_counter.txt",
        "formal_search_replay_counter.txt",
        "formal_startup_baseline_counter.txt",
        "global_replay_counter.txt",
    ):
        path = SOURCE_RUN / "state" / name
        if path.read_text(encoding="utf-8").strip() != "0":
            raise RuntimeError(f"source counter is not the observed zero state: {path}")

    pid = int((SOURCE_RUN / "formal_launcher.pid").read_text(encoding="utf-8").strip())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pass
    else:
        raise RuntimeError(f"source optimizer is still alive: pid={pid}")

    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    if summary.get("status") != "ok" or summary.get("technical_failure") is not False:
        raise RuntimeError("measurement is not a valid non-technical result")
    if summary.get("correctness_passed") is not True or summary.get("stable") is not True:
        raise RuntimeError("measurement correctness/stability gate failed")
    if float(summary.get("effective_measurement_seconds", 0.0)) < 180.0:
        raise RuntimeError("measurement duration below contract minimum")
    aggregate = summary.get("formal_pg_resource", {}).get("aggregate", {})
    for key in ("cpu_utilization", "memory_utilization", "io_utilization"):
        value = float(aggregate.get(key, -1.0))
        if not 0.0 <= value <= 0.95:
            raise RuntimeError(f"resource gate failed: {key}={value}")

    effective = summary.get("effective_parameters", {})
    for name, expected in EXPECTED_PARAMS.items():
        if name not in effective or not same_number(effective[name], expected):
            raise RuntimeError(f"summary parameter mismatch: {name}")

    ranking, final = load_history()
    candidates: List[Dict[str, Any]] = ranking.get("candidate_rankings", [])
    if len(candidates) != 7:
        raise RuntimeError(f"unexpected unique candidate count: {len(candidates)}")
    candidate = candidates[0]
    candidate_fp = candidate.get("three_level_fingerprint", {}).get("id")
    if candidate_fp != EXPECTED_FINGERPRINT:
        raise RuntimeError(f"candidate fingerprint mismatch: {candidate_fp}")
    for name, expected in EXPECTED_PARAMS.items():
        if not same_number(candidate["optimized_params"][name], expected):
            raise RuntimeError(f"candidate parameter mismatch: {name}")

    initial_score = float(final["initial_score"])
    actual_score = float(final["final_score"])
    if not actual_score > initial_score:
        raise RuntimeError(f"candidate did not improve: {initial_score} -> {actual_score}")
    if final.get("optimized_params") != candidate.get("optimized_params"):
        raise RuntimeError("final optimizer parameters do not bind to ranked candidate")

    readback = pg_readback()
    if set(readback) != set(EXPECTED_PARAMS):
        raise RuntimeError(f"incomplete PG readback: {sorted(readback)}")
    for name, expected in EXPECTED_PARAMS.items():
        if not same_number(readback[name], expected):
            raise RuntimeError(f"live PG parameter mismatch: {name}={readback[name]} != {expected}")

    candidate_contract = {
        "rank": 1,
        "params": candidate["optimized_params"],
        "feature_ratio": candidate["final_estimation_metadata"]["feature_ratios"],
        "predicted_resource": candidate["final_resource"],
        "utilization": {
            "cpu": float(candidate["final_resource"]["cpu"]) / 10.5,
            "memory": float(candidate["final_resource"]["memory"]) / 28.31593155860901,
            "io": float(candidate["final_resource"]["io"]) / 0.8940696716308594,
        },
        "dScore/dU": candidate["dScore/dU"],
        "T": candidate["T"],
        "confidence": candidate["confidence"],
        "g_j": candidate["g_j"],
        "eta": candidate["eta"],
        "delta_param": candidate["delta_param"],
        "candidate_score": candidate["final_score"],
        "three_level_fingerprint": candidate["three_level_fingerprint"],
        "plan_fingerprint": candidate["three_level_fingerprint"]["plan_level"],
        "feature_ratio_level": candidate["three_level_fingerprint"]["feature_ratio_level"],
        "predicted_resource_level": candidate["three_level_fingerprint"]["predicted_resource_level"],
        "actual_resource_utilization": {
            "cpu": aggregate["cpu_utilization"],
            "memory": aggregate["memory_utilization"],
            "io": aggregate["io_utilization"],
        },
        "actual_score": actual_score,
    }
    now = datetime.now(timezone(timedelta(hours=8))).isoformat()
    audit = {
        "schema_version": 1,
        "event": "xp_unreserved_formal_call_zero_call_reclassification",
        "created_at": now,
        "zero_new_workload_calls": True,
        "source_controller_quarantined": True,
        "source_controller_reason": "valid physical call completed without replay/accepted/global counter persistence",
        "source_run": str(SOURCE_RUN),
        "source_artifacts": {str(path): digest for path, digest in EXPECTED_SHA.items()},
        "source_observed_counters": {
            "formal_search_accepted": 0,
            "formal_search_replay": 0,
            "formal_startup_baseline": 0,
            "global_replay": 0,
        },
        "accepted_decision": {
            "accepted": True,
            "reason": "valid_stable_correct_improving_actual_score_no_resource_overshoot",
            "initial_score": initial_score,
            "actual_score": actual_score,
        },
        "candidate": candidate_contract,
        "live_pg_readback": readback,
        "formal_accounting": {
            "inherited_accepted": 3,
            "new_accepted_this_batch": 1,
            "countable_formal_total": 4,
            "accepted_shrink_target": 10,
            "physical_calls_this_batch": 1,
            "valid_replays_this_batch": 1,
            "technical_failures_this_batch": 0,
        },
        "qps_role": "outer_audit_only_not_inner_score",
        "cpu_affinity_contract": "32-51,288-307",
        "mysql_exclusion_contract": "do_not_touch_mysql_cpu_184-199,440-455_or_mysql_state",
    }

    DEST.mkdir(parents=True, mode=0o700)
    atomic_json(DEST / "zero_call_reclassification_audit.json", audit)
    atomic_json(
        DEST / "canonical_state.json",
        {
            "schema_version": 1,
            "scenario": "XP",
            "status": "formal_started",
            "incumbent_score": actual_score,
            "incumbent_params": candidate["optimized_params"],
            "inherited_accepted": 3,
            "new_accepted": 1,
            "accepted_total": 4,
            "accepted_target": 10,
            "formal_replay_count": 1,
            "global_replay_ordinal_delta": 1,
            "source_audit": str(DEST / "zero_call_reclassification_audit.json"),
        },
    )
    state_dir = DEST / "state"
    atomic_text(state_dir / "formal_search_accepted_counter.txt", "4\n")
    atomic_text(state_dir / "formal_search_replay_counter.txt", "1\n")
    atomic_text(state_dir / "global_replay_counter_delta.txt", "1\n")
    atomic_text(state_dir / "inherited_accepted_counter.txt", "3\n")

    manifest = {
        "created_at": now,
        "status": "sealed",
        "files": {},
    }
    for path in sorted(DEST.rglob("*")):
        if path.is_file():
            manifest["files"][str(path.relative_to(DEST))] = sha256(path)
    atomic_json(DEST / "manifest.json", manifest)
    print(json.dumps({"status": "ok", "destination": str(DEST), "accepted_total": 4}, ensure_ascii=False))


if __name__ == "__main__":
    main()
