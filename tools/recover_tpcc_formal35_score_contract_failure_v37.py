#!/usr/bin/env python3
"""Retire TPC-C formal35 after its pre-workload score-contract failure."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any, Dict


CAMPAIGN = Path(
    "/home/anonymous/program/resource_adaptive/"
    "history/tpcc_final_algorithm_fresh10_v29_20260731T2005"
)
CONTROL = CAMPAIGN / "autonomous_controller_v32"
STATE = CONTROL / "controller_state.json"
FORMAL_STATE = CAMPAIGN / "formal_state.json"
RUN_ROOT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "formal10_final_algorithm_fresh10_v29_20260731T2005/"
    "autonomous_runs_v32/autonomous_formal_35"
)
EXPECTED_STATE_SHA256 = (
    "3397939ada3f50c0e55d4d8af48d681c2dd6c98219dbfdf39607d13ce1760612"
)
EXPECTED_FORMAL_STATE_SHA256 = (
    "9944cbe9cb17dbdfedb78de12ccce39a35f08b1096b39fc8ecb22258bedbce89"
)
EXPECTED_BATCH_STATE_SHA256 = (
    "ac64957f081acb3f59cbd33d03370ed8b520f9b65bb2c40ec7f88341b969e0fd"
)
EXPECTED_RUNNER_LOG_SHA256 = (
    "b4e7cef2f46fc885a959e562040c05ce7cdea1dfd29e8951ec0792cd26f12e45"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def controller_running() -> bool:
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if (
            b"tpcc_final_fresh0_autonomous_v35_20260801" in command
            or b"autonomous_formal_35" in command
        ):
            return True
    return False


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> int:
    if controller_running():
        raise RuntimeError("TPC-C v35 controller or formal35 runner is active")
    if sha256(STATE) != EXPECTED_STATE_SHA256:
        raise RuntimeError("controller state SHA mismatch")
    if sha256(FORMAL_STATE) != EXPECTED_FORMAL_STATE_SHA256:
        raise RuntimeError("formal state SHA mismatch")
    batch_state_path = RUN_ROOT / "formal_batch_state.json"
    runner_log_path = RUN_ROOT / "diagnostic_runner.log"
    if sha256(batch_state_path) != EXPECTED_BATCH_STATE_SHA256:
        raise RuntimeError("formal35 batch state SHA mismatch")
    if sha256(runner_log_path) != EXPECTED_RUNNER_LOG_SHA256:
        raise RuntimeError("formal35 runner log SHA mismatch")
    batch = load(batch_state_path)
    if not (
        batch.get("status") == "technical_failure"
        and int(batch.get("physical_call_count", -1)) == 0
        and int(batch.get("valid_replay_count", -1)) == 0
        and int(batch.get("technical_failure_count", -1)) == 1
        and batch.get("error")
        == "diagnostic substrate did not produce ledger and summary"
    ):
        raise RuntimeError("formal35 batch state is not the pinned pre-call failure")
    runner_log = runner_log_path.read_text(encoding="utf-8")
    if "rank 1 score does not match predicted resource" not in runner_log:
        raise RuntimeError("formal35 failure reason mismatch")
    if (RUN_ROOT / "formal_batch_summary.json").exists() or (
        RUN_ROOT / "strict_top5_real_replay_round14_ledger_v1.json"
    ).exists():
        raise RuntimeError("formal35 unexpectedly has summary or ledger")
    replay_root = RUN_ROOT / "top5_real_replay_round14_formal"
    if not replay_root.is_dir() or any(path.is_file() for path in replay_root.rglob("*")):
        raise RuntimeError("formal35 contains physical replay evidence")

    state = load(STATE)
    current = state.get("current_run")
    if not isinstance(current, dict) or not (
        state.get("status") == "waiting_current_batch"
        and int(state.get("next_ordinal", -1)) == 36
        and int(state.get("last_launcher_returncode", -1)) == 2
        and Path(str(current.get("root"))) == RUN_ROOT
        and current.get("mode") == "formal"
        and current.get("launcher_invoked") is True
    ):
        raise RuntimeError("controller state does not bind the failed formal35")
    selection_path = Path(str(current["selection_path"]))
    selection = load(selection_path)
    fingerprint_ids = sorted(
        str(item["three_level_fingerprint"]["id"])
        for item in selection.get("top5") or []
    )
    if len(fingerprint_ids) != 5 or len(set(fingerprint_ids)) != 5:
        raise RuntimeError("formal35 selection fingerprint audit failed")

    timestamp = time.strftime("%Y%m%dT%H%M%S")
    state_backup = STATE.with_name(
        f"controller_state.before_formal35_score_contract_retire_{timestamp}.json"
    )
    shutil.copy2(STATE, state_backup)
    if sha256(state_backup) != EXPECTED_STATE_SHA256:
        raise RuntimeError("controller state backup verification failed")
    quarantine = RUN_ROOT.with_name(
        f"quarantine_autonomous_formal_35_score_contract_{timestamp}"
    )
    if quarantine.exists():
        raise RuntimeError(f"quarantine target already exists: {quarantine}")
    os.replace(RUN_ROOT, quarantine)

    state["current_run"] = None
    state["status"] = "ready_after_formal35_technical_failure"
    state["quarantined_fingerprints"] = sorted(
        set(state.get("quarantined_fingerprints") or []) | set(fingerprint_ids)
    )
    state.setdefault("technical_failure_audits", []).append(
        {
            "actual_workload_calls": 0,
            "contract_physical_budget_consumed": 1,
            "event": "formal35_score_resource_inconsistency_pre_call",
            "fingerprint_ids": fingerprint_ids,
            "formal_state_sha256": EXPECTED_FORMAL_STATE_SHA256,
            "quarantine_root": str(quarantine),
            "runner_log_sha256": EXPECTED_RUNNER_LOG_SHA256,
            "state_backup": str(state_backup),
            "state_sha256_before": EXPECTED_STATE_SHA256,
            "valid_replays": 0,
        }
    )
    atomic_json(STATE, state)
    if sha256(FORMAL_STATE) != EXPECTED_FORMAL_STATE_SHA256:
        raise RuntimeError("formal state changed while retiring formal35")
    print(
        json.dumps(
            {
                "accepted_count": load(FORMAL_STATE)["accepted_count"],
                "contract_physical_budget_consumed": 1,
                "next_ordinal": state["next_ordinal"],
                "quarantine": str(quarantine),
                "state_sha256_after": sha256(STATE),
                "valid_replays": 0,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
