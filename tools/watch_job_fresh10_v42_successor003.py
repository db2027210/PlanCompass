#!/usr/bin/env python3
"""Promote an autonomously generated JOB successor into formal batch 003."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any, Dict


B = Path("/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726")
T = B / "job_final_algorithm_fresh10_v36_20260731T2020"
P = B / "job_round8_corrected_top5_v6_20260731"
AUTO = T / "autonomous_v42"
SELECTION = AUTO / "successor003_formal.json"
STATE = T / "package/formal_state.json"
SOURCE_PACKAGE = T / "formal_batch002_promoted_validation004/package"
TARGET = AUTO / "formal_batch003"
PACKAGE = TARGET / "package"
REPLAY = TARGET / "replay"
EXPECTED_STATE_SHA = "7fde8e94b9ee76a07f2e7759404e7ed560973dd0f9ef6b23c89de7802c087276"
BUILDER_PID = 104375


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def counter(path: Path) -> int:
    value = path.read_text(encoding="utf-8").strip()
    if not value.isdigit():
        raise RuntimeError(f"invalid counter: {path}")
    return int(value)


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def validate_selection() -> None:
    selection = read_json(SELECTION)
    top5 = selection.get("top5") or []
    roles = [str(item.get("selection_role") or "") for item in top5]
    fingerprints = [
        str((item.get("three_level_fingerprint") or {}).get("id") or "")
        for item in top5
    ]
    required = (
        "feature_ratio",
        "predicted_resource",
        "utilization",
        "dScore/dU",
        "T",
        "confidence",
        "g_j",
        "eta",
        "delta_param",
        "candidate_score",
    )
    if (
        selection.get("diagnostic_only") is not False
        or roles != ["trusted", "trusted", "trusted", "probe", "probe"]
        or len(set(fingerprints)) != 5
        or "" in fingerprints
        or not all(
            item.get("boundary_corner") is False
            and all(field in item for field in required)
            for item in top5
        )
    ):
        raise RuntimeError("generated JOB formal Top-5 violates contract")


def main() -> int:
    AUTO.mkdir(parents=True, exist_ok=True)
    while process_exists(BUILDER_PID):
        time.sleep(10)
    if not SELECTION.is_file():
        atomic_json(
            AUTO / "successor003_fail_closed.json",
            {
                "status": "blocked_contract",
                "reason": "formal successor generator produced no selection",
                "new_workload_calls": 0,
                "state_mutation": False,
            },
        )
        return 2
    if sha256(STATE) != EXPECTED_STATE_SHA:
        raise RuntimeError("JOB formal state changed during EXPLAIN generation")
    validate_selection()
    PACKAGE.mkdir(parents=True, exist_ok=False)
    controller = PACKAGE / "job_formal_target10_batch_controller.py"
    runner = PACKAGE / "run_job_round8_strict_serial.py"
    contract = PACKAGE / "job_round8_strict_replay_contract.py"
    shutil.copy2(SOURCE_PACKAGE / controller.name, controller)
    shutil.copy2(SOURCE_PACKAGE / runner.name, runner)
    shutil.copy2(SOURCE_PACKAGE / contract.name, contract)

    gate = read_json(SOURCE_PACKAGE / "entry_gate.json")
    global_before = counter(B / "replay_counter.txt")
    replay_before = counter(B / "formal_search_replay_counter.txt")
    accepted_before = counter(B / "formal_search_accepted_counter.txt")
    state = read_json(STATE)
    if accepted_before != int(state.get("accepted_count", -1)):
        raise RuntimeError("JOB accepted state/counter mismatch")
    gate.update(
        {
            "formal_state_sha256_before": EXPECTED_STATE_SHA,
            "formal_state_sha256_after": EXPECTED_STATE_SHA,
            "formal_accepted_total": accepted_before,
            "formal_counter_pins_before_start": {
                "global_ordinal": global_before,
                "formal_search_replay_count": replay_before,
                "formal_search_accepted_count": accepted_before,
            },
            "formal_admission_contract": "validated_exact_plan_regime_successor_then_real_replay_v1",
            "formal_expected_improvement_required": True,
        }
    )
    gate_path = PACKAGE / "entry_gate.json"
    atomic_json(gate_path, gate)
    selection_sha = sha256(SELECTION)
    gate_sha = sha256(gate_path)

    dry_run = subprocess.run(
        [
            sys.executable,
            str(runner),
            str(SELECTION),
            str(REPLAY),
            "--authority-root",
            str(P / "authority"),
            "--observations",
            str(P / "job_round8_v6_observations.json"),
            "--exclusion",
            str(P / "round7_replayed_fingerprints.json"),
            "--state",
            str(STATE),
            "--accepted-target",
            "10",
            "--dry-run",
        ],
        cwd=str(PACKAGE),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=600,
        check=False,
    )
    (PACKAGE / "dry_run.json").write_text(
        dry_run.stdout or "", encoding="utf-8"
    )
    if dry_run.returncode != 0 or sha256(STATE) != EXPECTED_STATE_SHA:
        raise RuntimeError("JOB formal batch003 dry-run failed")

    command = [
        sys.executable,
        str(controller),
        "--selection",
        str(SELECTION),
        "--selection-sha256",
        selection_sha,
        "--gate",
        str(gate_path),
        "--gate-sha256",
        gate_sha,
        "--state",
        str(STATE),
        "--state-sha256",
        EXPECTED_STATE_SHA,
        "--authority-root",
        str(P / "authority"),
        "--observations",
        str(P / "job_round8_v6_observations.json"),
        "--exclusion",
        str(P / "round7_replayed_fingerprints.json"),
        "--replay-root",
        str(REPLAY),
        "--global-counter",
        str(B / "replay_counter.txt"),
        "--formal-replay-counter",
        str(B / "formal_search_replay_counter.txt"),
        "--accepted-counter",
        str(B / "formal_search_accepted_counter.txt"),
    ]
    completed = subprocess.run(
        command,
        cwd=str(PACKAGE),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=14400,
        check=False,
    )
    (TARGET / "controller.log").write_text(
        completed.stdout or "", encoding="utf-8"
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
