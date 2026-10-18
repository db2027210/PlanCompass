#!/usr/bin/env python3
"""Account the interrupted rank-5 call and seal round14 without a new replay."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "gradient_debug_round14_robust_regime_interval_v2_20260729T1824"
)
REPLAY = ROOT / "top5_real_replay_round14_robust_trusted3_probe2"
LEDGER = ROOT / "strict_top5_real_replay_round14_ledger_v1.json"
EVENTS = REPLAY / "events.jsonl"
LOCK = ROOT / "strict_top5_real_replay.lock"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent), text=True
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    with LOCK.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger_before = LEDGER.read_bytes()
        ledger = json.loads(ledger_before)
        events = [
            json.loads(line)
            for line in EVENTS.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        successful_ranks = [
            int(item["rank"]) for item in ledger.get("successful") or []
        ]
        reserved = [
            item
            for item in events
            if item.get("event")
            == "tpcc_gradient_real_replay_workload_invocation_reserved"
        ]
        completed_rank5 = [
            item
            for item in events
            if item.get("rank") == 5
            and item.get("event")
            in {
                "tpcc_gradient_real_replay_complete",
                "tpcc_gradient_real_replay_technical_failure",
            }
        ]
        expected = {
            "contract": "tpcc_round14_robust_exact_plan_regime_strict_serial_replay_v1",
            "real_workload_invocation_count": 5,
            "successful_ranks": [1, 2, 3, 4],
            "technical_failure_count": 0,
            "reserved_ranks": [1, 2, 3, 4, 5],
            "rank5_terminal_event_count": 0,
        }
        observed = {
            "contract": ledger.get("contract"),
            "real_workload_invocation_count": ledger.get(
                "real_workload_invocation_count"
            ),
            "successful_ranks": successful_ranks,
            "technical_failure_count": len(
                ledger.get("technical_failures") or []
            ),
            "reserved_ranks": [int(item["rank"]) for item in reserved],
            "rank5_terminal_event_count": len(completed_rank5),
        }
        if observed != expected:
            raise RuntimeError(f"fail-closed state mismatch: {observed} != {expected}")

        attempt_root = REPLAY / "rank_05/attempt_01"
        evidence: dict[str, Any] = {}
        if attempt_root.exists():
            for path in sorted(attempt_root.rglob("*")):
                if path.is_file():
                    evidence[str(path.relative_to(attempt_root))] = {
                        "path": str(path),
                        "sha256": sha256(path),
                        "size_bytes": path.stat().st_size,
                    }
        failure = {
            "rank": 5,
            "attempt": 1,
            "technical_failure": True,
            "valid_replay": False,
            "diagnostic_only": True,
            "eligible_for_incumbent_or_accepted": False,
            "consumes_physical_call_budget": True,
            "consumes_successful_replay_budget": False,
            "error": (
                "ControllerLifetimeExceeded: workload was physically reserved "
                "and its controller disappeared before a sealed summary"
            ),
            "evidence": evidence,
        }
        ledger["technical_failures"] = [failure]
        atomic_json(LEDGER, ledger)
        event = {
            "event": "tpcc_gradient_real_replay_technical_failure",
            **failure,
            "manual_fail_closed_accounting": True,
            "new_workload_calls": 0,
            "timestamp_epoch": time.time(),
        }
        with EVENTS.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        audit = {
            "event": "tpcc_round14_interrupted_rank5_fail_closed_accounting",
            "ledger_sha256_before": hashlib.sha256(ledger_before).hexdigest(),
            "ledger_sha256_after": sha256(LEDGER),
            "events_sha256_after": sha256(EVENTS),
            "physical_calls": 5,
            "valid_replays": 4,
            "technical_failures": 1,
            "new_workload_calls": 0,
            "accepted_update_applied": False,
            "incumbent_update_applied": False,
            "range_update_applied": False,
            "gradient_update_applied": False,
        }
        atomic_json(ROOT / "rank5_interruption_accounting_audit.json", audit)
        print(json.dumps(audit, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
