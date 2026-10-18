#!/usr/bin/env python3
"""Account XP cont12's valid rejected workload that lacked a reservation."""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List


ATTEMPT = Path(
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
    "hybench_xp_compute00_59630/experience_sgd/"
    "attempt39_fresh_from_accepted3_20260726"
)
ROOT = ATTEMPT / "formal10_cont12_accepted4_sealed_ratio_samples_v1_20260729T2050"
HISTORY = ROOT / "optimizer_history_2026-07-29_204028.jsonl"
SUMMARY = (
    ATTEMPT
    / "candidate_measurements/20260729T204436+0800_3803379/summary.json"
)
EXPECTED_SHA = {
    ROOT / "config.json":
        "261521c7e12eaeb543ce962de268cfa9e4b7e5ce71ca09c373c4de1d519a8ec0",
    HISTORY:
        "8383ada7213e971757761c967a4aebe3d5807f008608fddb3f9d521f95a03538",
    SUMMARY:
        "2e8ac8ee94afcf4bed43a990042b4757bc432443590cd37e379dd1c39712c6da",
}
BASELINE = {
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "cpu_tuple_cost": 0.01,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
    "random_page_cost": 4.0,
    "seq_page_cost": 0.2,
}
REJECTED = {
    **BASELINE,
    "random_page_cost": 100.0,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=str(path.parent),
        text=True,
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def same_params(observed: Dict[str, Any], expected: Dict[str, float]) -> bool:
    return set(observed) == set(expected) and all(
        math.isclose(
            float(observed[name]),
            value,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        for name, value in expected.items()
    )


def pg_readback() -> Dict[str, float]:
    names = sorted(BASELINE)
    sql_names = ",".join(f"'{name}'" for name in names)
    completed = subprocess.run(
        [
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
            (
                "select name||'='||setting from pg_settings "
                f"where name in ({sql_names}) order by name"
            ),
        ],
        check=True,
        text=True,
        capture_output=True,
        timeout=20,
    )
    result: Dict[str, float] = {}
    for line in completed.stdout.splitlines():
        name, value = line.split("=", 1)
        result[name] = float(value)
    return result


def main() -> int:
    audit_path = ROOT / "unreserved_initial_final_call_accounting_audit.json"
    if audit_path.exists():
        raise FileExistsError(f"audit already exists: {audit_path}")
    for path, expected in EXPECTED_SHA.items():
        observed = sha256(path)
        if observed != expected:
            raise RuntimeError(
                f"immutable source SHA mismatch: {path}: {observed} != {expected}"
            )

    events: List[Dict[str, Any]] = [
        json.loads(line)
        for line in HISTORY.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    final_events = [
        event
        for event in events
        if event.get("event") == "final_top_sql"
        and (
            (
                (
                    event.get("final_validation", {}).get("resource_stats") or {}
                ).get("candidate_measurement_protocol") or {}
            ).get("summary")
            == str(SUMMARY)
        )
    ]
    rejected_events = [
        event
        for event in events
        if event.get("event") == "final_feedback_initial_validation_rejected"
    ]
    exhausted_events = [
        event
        for event in events
        if event.get("event")
        == "experience_table_gradient_candidate_space_exhausted"
    ]
    if (
        len(final_events) != 1
        or len(rejected_events) != 1
        or len(exhausted_events) != 1
    ):
        raise RuntimeError("history does not prove exactly one rejected call")
    rejected = rejected_events[0]
    if (
        not same_params(rejected.get("rejected_params") or {}, REJECTED)
        or not same_params(rejected.get("baseline_params") or {}, BASELINE)
        or exhausted_events[0].get("completion_status") != "incomplete"
        or int(exhausted_events[0].get("accepted_shrink_count", -1)) != 4
    ):
        raise RuntimeError("rejection or accepted-count history mismatch")
    forbidden = {
        "formal_search_candidate_accepted",
        "experience_table_gradient_candidate_accepted",
    }
    if any(event.get("event") in forbidden for event in events):
        raise RuntimeError("cont12 unexpectedly contains an acceptance event")

    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    protocol = (
        final_events[0]["final_validation"]["resource_stats"]
        .get("candidate_measurement_protocol")
        or {}
    )
    if (
        summary.get("status") != "ok"
        or summary.get("technical_failure") is not False
        or summary.get("correctness_passed") is not True
        or summary.get("stable") is not True
        or summary.get("stop_reason") != "stable_early_stop"
        or float(summary.get("effective_measurement_seconds", 0.0)) < 180.0
        or protocol.get("technical_failure") is not False
        or protocol.get("correctness_passed") is not True
        or protocol.get("stable") is not True
        or protocol.get("summary") != str(SUMMARY)
    ):
        raise RuntimeError("workload is not valid stable correctness evidence")
    if not same_params(summary.get("effective_parameters") or {}, REJECTED):
        raise RuntimeError("summary parameters do not bind to rejected candidate")
    formal = summary.get("formal_pg_resource") or {}
    aggregate = formal.get("aggregate") or {}
    caps = formal.get("caps") or {}
    if (
        formal.get("semantics") != "pg5_io_total_throughput_utilization_v1"
        or formal.get("io_formula")
        != "(read_bps + write_bps) / (read_cap_bps + write_cap_bps)"
        or int(formal.get("sample_count", -1)) < 178
        or float(caps.get("cpu_quota_cores", -1)) != 10.5
        or float(caps.get("read_cap_bps", -1)) != 100000.0
        or float(caps.get("write_cap_bps", -1)) != 837500.0
        or any(
            not 0.0 <= float(aggregate.get(name, -1.0)) <= 0.95
            for name in (
                "cpu_utilization",
                "memory_utilization",
                "io_utilization",
            )
        )
    ):
        raise RuntimeError("formal PostgreSQL resource contract mismatch")

    counter_paths = {
        "accepted": ROOT / "state/formal_search_accepted_counter.txt",
        "formal_replay": ROOT / "state/formal_search_replay_counter.txt",
        "global": ROOT / "state/global_replay_counter.txt",
        "startup": ROOT / "state/formal_startup_baseline_counter.txt",
    }
    before = {
        name: int(path.read_text(encoding="utf-8").strip())
        for name, path in counter_paths.items()
    }
    expected_before = {
        "accepted": 4,
        "formal_replay": 8,
        "global": 8,
        "startup": 0,
    }
    if before != expected_before:
        raise RuntimeError(f"counter mismatch: {before} != {expected_before}")
    if any(
        line.strip()
        for line in subprocess.run(
            ["ps", "-eo", "cmd="],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.splitlines()
        if "formal10_cont12_accepted4_sealed_ratio_samples" in line
        and "account_xp_cont12_unreserved_call.py" not in line
    ):
        raise RuntimeError("cont12 optimizer/launcher still appears active")
    live = pg_readback()
    if not same_params(live, BASELINE):
        raise RuntimeError(f"live PostgreSQL baseline mismatch: {live}")

    timestamp = time.strftime("%Y%m%dT%H%M%S")
    backups = ROOT / f"counter_backups_before_unreserved_accounting_{timestamp}"
    backups.mkdir(mode=0o700)
    for name, path in counter_paths.items():
        atomic_text(backups / f"{name}.txt", path.read_text(encoding="utf-8"))
    audit = {
        "schema_version": "xp_unreserved_initial_final_accounting_v1",
        "event": "xp_cont12_unreserved_initial_final_call_accounted",
        "classification": "rejected_valid_stable_unreserved_initial_final",
        "root": str(ROOT),
        "source_sha256": {
            str(path): digest for path, digest in EXPECTED_SHA.items()
        },
        "summary_path": str(SUMMARY),
        "physical_calls_accounted": 1,
        "valid_replays_accounted": 1,
        "technical_failures_accounted": 0,
        "accepted": False,
        "accepted_update_applied": False,
        "incumbent_update_applied": False,
        "range_update_applied": False,
        "gradient_update_applied": False,
        "qps_used_for_inner_scoring": False,
        "counters_before": before,
        "counters_after": {
            "accepted": 4,
            "formal_replay": 9,
            "global": 9,
            "startup": 0,
        },
        "baseline_readback": live,
        "rejected_params": REJECTED,
        "counter_backups": {
            name: {
                "path": str(backups / f"{name}.txt"),
                "sha256": sha256(backups / f"{name}.txt"),
            }
            for name in counter_paths
        },
    }
    atomic_text(
        audit_path,
        json.dumps(audit, indent=2, sort_keys=True) + "\n",
    )
    atomic_text(counter_paths["formal_replay"], "9\n")
    atomic_text(counter_paths["global"], "9\n")
    after = {
        name: int(path.read_text(encoding="utf-8").strip())
        for name, path in counter_paths.items()
    }
    if after != audit["counters_after"]:
        raise RuntimeError(f"counter write verification failed: {after}")
    print(json.dumps(audit, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
