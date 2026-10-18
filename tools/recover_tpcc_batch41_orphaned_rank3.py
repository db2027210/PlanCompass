#!/usr/bin/env python3
"""Seal TPC-C batch41's orphaned third physical call and restore baseline."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any, Dict, Mapping


ROOT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "autonomous_target10_controller_v4/runs/autonomous_diagnostic_41"
)
REPLAY = ROOT / "top5_real_replay_round12_diagnostic_v1"
LEDGER = ROOT / "strict_top5_real_replay_round12_ledger_v1.json"
EVENTS = REPLAY / "events.jsonl"
FORMAL_STATE = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "formal10_tpcc_state.json"
)
RESTORE = Path(
    "/home/anonymous/program/resource_adaptive/"
    "shell/restore_pg5_tpcc_from_template.sh"
)
EXPECTED_LEDGER_SHA = (
    "89a46c9f6c0c4eb06a6cc87b26b175aaa1b1343426fdde1d410a0cadd11c9f70"
)
EXPECTED_EVENTS_SHA = (
    "a7d189fd9af91843175f2e46711fc0283896c358a0790e9e9fe18e6880b48af1"
)
EXPECTED_FORMAL_STATE_SHA = (
    "f8f16a0c8ed261e2158fa5c54f061ebb4ce3aa45e6eab816c9db40bf40ab588a"
)
EXPECTED_RESTORE_SHA = (
    "1af8eca4b8fbd229f525b627293eb58a51aa3510472c97340b5ea317be5cde59"
)
BASELINE_PARAMS = {
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0026541154853235117,
    "cpu_tuple_cost": 0.010616461941294047,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
    "random_page_cost": 4.0,
    "seq_page_cost": 1.0,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent), text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def append_event(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(value), sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def current_tpcc_workers() -> list[str]:
    completed = subprocess.run(
        ["ps", "-eo", "pid=,args="],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    markers = (
        "run_tpcc_sensitive_stable_point.py",
        "run_tpcc_round11_calibrated_top5_strict_serial.py",
        "benchbase.jar -b tpcc",
        "run_tpcc_sensitive_mix.py",
    )
    return [
        line.strip()
        for line in completed.stdout.splitlines()
        if any(marker in line for marker in markers)
    ]


def evidence() -> Dict[str, Any]:
    point = REPLAY / "rank_03/attempt_01/point_20260731_155913_2427173"
    if (point / "summary.json").exists():
        raise RuntimeError("rank3 acquired a summary; manual classification required")
    result: Dict[str, Any] = {}
    for name in (
        "run_manifest.json",
        "warmup.log",
        "measurement.log",
        "database_counter_samples.jsonl",
        "resource_samples.jsonl",
    ):
        path = point / name
        if path.is_file():
            result[name] = {
                "path": str(path),
                "sha256": sha256(path),
                "size_bytes": path.stat().st_size,
            }
    if "run_manifest.json" not in result or "measurement.log" not in result:
        raise RuntimeError("orphaned physical call evidence is incomplete")
    return result


def validate_before() -> tuple[Dict[str, Any], list[Dict[str, Any]]]:
    expected = (
        (LEDGER, EXPECTED_LEDGER_SHA),
        (EVENTS, EXPECTED_EVENTS_SHA),
        (FORMAL_STATE, EXPECTED_FORMAL_STATE_SHA),
        (RESTORE, EXPECTED_RESTORE_SHA),
    )
    for path, digest in expected:
        if not path.is_file() or sha256(path) != digest:
            raise RuntimeError(f"fail closed: unexpected SHA for {path}")
    workers = current_tpcc_workers()
    if workers:
        raise RuntimeError(f"fail closed: TPC-C workers still live: {workers}")
    ledger = load_json(LEDGER)
    if (
        ledger.get("contract")
        != "tpcc_round12_current_plan_discovery_strict_serial_replay_v1"
        or int(ledger.get("real_workload_invocation_count", -1)) != 3
        or [int(item["rank"]) for item in ledger.get("successful") or []]
        != [1, 2]
        or ledger.get("technical_failures") != []
    ):
        raise RuntimeError("fail closed: ledger is not the exact orphaned state")
    events = [
        json.loads(line)
        for line in EVENTS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    reservations = [
        item
        for item in events
        if item.get("event")
        == "tpcc_gradient_real_replay_workload_invocation_reserved"
    ]
    rank3_terminal = [
        item
        for item in events
        if int(item.get("rank") or 0) == 3
        and item.get("event")
        in (
            "tpcc_gradient_real_replay_complete",
            "tpcc_gradient_real_replay_technical_failure",
        )
    ]
    if (
        len(reservations) != 3
        or int(reservations[-1].get("rank", -1)) != 3
        or int(
            reservations[-1].get("real_workload_invocation_count", -1)
        )
        != 3
        or rank3_terminal
    ):
        raise RuntimeError("fail closed: events do not prove one orphaned rank3 call")
    return ledger, events


def apply_baseline_and_restore() -> Dict[str, Any]:
    psql = [
        "docker",
        "exec",
        "pg5_tpcc_d2_20260722",
        "psql",
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
        "-p",
        "59640",
        "-U",
        "postgres",
        "-d",
        "fullgrid_tpcc",
    ]
    for name, value in sorted(BASELINE_PARAMS.items()):
        subprocess.run(
            psql + ["-c", f"ALTER SYSTEM SET {name} = '{value:.17g}';"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    subprocess.run(
        psql + ["-c", "SELECT pg_reload_conf();"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    restored = subprocess.run(
        [str(RESTORE)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    names = ",".join(f"'{name}'" for name in sorted(BASELINE_PARAMS))
    readback = subprocess.run(
        psql
        + [
            "-Atc",
            "SELECT name || '=' || setting FROM pg_settings "
            f"WHERE name IN ({names}) ORDER BY name;",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    observed = {}
    for line in readback.stdout.splitlines():
        name, value = line.split("=", 1)
        observed[name] = float(value)
    for name, expected in BASELINE_PARAMS.items():
        if not math.isclose(
            observed.get(name, float("nan")),
            expected,
            rel_tol=5e-6,
            abs_tol=max(5e-12, abs(expected) * 5e-6),
        ):
            raise RuntimeError(f"baseline readback mismatch for {name}")
    return {
        "snapshot_restore_output": restored.stdout.strip(),
        "observed_params": observed,
    }


def main() -> int:
    ledger, _events = validate_before()
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    ledger_backup = LEDGER.with_name(f"{LEDGER.name}.orphan_backup_{timestamp}")
    events_backup = EVENTS.with_name(f"{EVENTS.name}.orphan_backup_{timestamp}")
    shutil.copy2(LEDGER, ledger_backup)
    shutil.copy2(EVENTS, events_backup)

    orphan_evidence = evidence()
    failure = {
        "rank": 3,
        "attempt": 1,
        "error": (
            "TerminationRequested: physical workload was externally "
            "terminated before a sealed summary"
        ),
        "technical_failure": True,
        "valid_replay": False,
        "consumes_physical_call_budget": True,
        "consumes_successful_replay_budget": False,
        "diagnostic_only": True,
        "eligible_for_incumbent_or_accepted": False,
        "evidence": orphan_evidence,
    }
    ledger["technical_failures"] = [failure]
    atomic_json(LEDGER, ledger)
    append_event(
        EVENTS,
        {
            "event": "tpcc_gradient_real_replay_technical_failure",
            "timestamp_epoch": time.time(),
            **failure,
        },
    )

    restore_audit = apply_baseline_and_restore()
    if sha256(FORMAL_STATE) != EXPECTED_FORMAL_STATE_SHA:
        raise RuntimeError("formal state changed during diagnostic recovery")
    audit = {
        "contract": "tpcc_batch41_orphaned_physical_call_recovery_v1",
        "status": "complete",
        "rank": 3,
        "physical_call_count_preserved": 3,
        "valid_replay_count_preserved": 2,
        "technical_failure_count_after": 1,
        "remaining_physical_budget": 2,
        "rank3_retry_allowed": False,
        "resume_selected_ranks": [4, 5],
        "formal_state_unchanged": True,
        "formal_state_sha256": EXPECTED_FORMAL_STATE_SHA,
        "baseline_params_restored": True,
        "baseline_snapshot_restored": True,
        "ledger_backup": str(ledger_backup),
        "ledger_backup_sha256": sha256(ledger_backup),
        "events_backup": str(events_backup),
        "events_backup_sha256": sha256(events_backup),
        "ledger_sha256_after": sha256(LEDGER),
        "events_sha256_after": sha256(EVENTS),
        "restore": restore_audit,
    }
    audit_path = ROOT / "orphaned_rank3_recovery_audit.json"
    atomic_json(audit_path, audit)
    print(json.dumps(audit, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
