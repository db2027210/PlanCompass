#!/usr/bin/env python3
"""Recover the zero-call TPC-C formal35 launch after sandbox EROFS."""

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
PREPARE = CONTROL / "prepare_35/sealed_run"
LAUNCHER_LOG = PREPARE / "launcher.log"
RUN_ROOT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "formal10_final_algorithm_fresh10_v29_20260731T2005/"
    "autonomous_runs_v32/autonomous_formal_35"
)
EXPECTED_STATE_SHA256 = (
    "38c2ace97a26ec689b03be1ed70f451bbad4bc6452c9b6df0f72ccdaa0a34a3d"
)
EXPECTED_FORMAL_STATE_SHA256 = (
    "9944cbe9cb17dbdfedb78de12ccce39a35f08b1096b39fc8ecb22258bedbce89"
)
EXPECTED_LAUNCHER_LOG_SHA256 = (
    "36298f2b1b705649a63ed01ba1bc040a1160ccfef1d4526fcf0d00e09a044054"
)
EXPECTED_LAUNCHER_SHA256 = (
    "804d8b4f8de58f3cec80d686da8ef7d75be68d42a844947eae7dc4f4c7ad592a"
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
    if sha256(STATE) != EXPECTED_STATE_SHA256:
        raise RuntimeError("controller state SHA mismatch")
    if sha256(FORMAL_STATE) != EXPECTED_FORMAL_STATE_SHA256:
        raise RuntimeError("formal state SHA mismatch")
    if sha256(LAUNCHER_LOG) != EXPECTED_LAUNCHER_LOG_SHA256:
        raise RuntimeError("failed launcher log SHA mismatch")
    launcher_log = LAUNCHER_LOG.read_text(encoding="utf-8")
    if (
        "OSError: [Errno 30] Read-only file system" not in launcher_log
        or str(RUN_ROOT) not in launcher_log
    ):
        raise RuntimeError("launcher log does not prove pre-call EROFS")
    if RUN_ROOT.exists():
        raise RuntimeError("formal35 run root exists; zero-call recovery forbidden")
    if controller_running():
        raise RuntimeError("TPC-C v35 controller or formal35 runner is still active")

    state = load(STATE)
    current = state.get("current_run")
    if not isinstance(current, dict):
        raise RuntimeError("current_run is absent")
    if not (
        state.get("status") == "waiting_current_batch"
        and int(state.get("next_ordinal", -1)) == 36
        and int(state.get("last_launcher_returncode", -1)) == 1
        and state.get("formal_state_sha256") == EXPECTED_FORMAL_STATE_SHA256
        and current.get("mode") == "formal"
        and Path(str(current.get("root"))) == RUN_ROOT
        and current.get("launcher_sha256") == EXPECTED_LAUNCHER_SHA256
        and current.get("formal_state_sha256_before")
        == EXPECTED_FORMAL_STATE_SHA256
        and "launch_if_absent" not in current
        and "launcher_invoked" not in current
    ):
        raise RuntimeError("observed controller state does not match zero-call failure")

    timestamp = time.strftime("%Y%m%dT%H%M%S")
    backup = STATE.with_name(
        f"controller_state.before_formal35_zero_call_recovery_{timestamp}.json"
    )
    shutil.copy2(STATE, backup)
    if sha256(backup) != EXPECTED_STATE_SHA256:
        raise RuntimeError("controller state backup verification failed")

    current["launch_if_absent"] = True
    current["launcher_invoked"] = False
    state["status"] = "recovery_ready_zero_call_launcher"
    state.setdefault("recovery_audits", []).append(
        {
            "event": "formal35_pre_call_ero_fs_retry",
            "formal_state_sha256": EXPECTED_FORMAL_STATE_SHA256,
            "launcher_log_sha256": EXPECTED_LAUNCHER_LOG_SHA256,
            "physical_workload_calls_before_failure": 0,
            "run_root_absent_verified": True,
            "state_backup": str(backup),
            "state_sha256_before": EXPECTED_STATE_SHA256,
        }
    )
    atomic_json(STATE, state)
    if sha256(FORMAL_STATE) != EXPECTED_FORMAL_STATE_SHA256:
        raise RuntimeError("formal state changed during zero-call recovery")
    print(
        json.dumps(
            {
                "backup": str(backup),
                "formal_state_sha256": EXPECTED_FORMAL_STATE_SHA256,
                "physical_workload_calls_added": 0,
                "state_sha256_after": sha256(STATE),
                "status": state["status"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
