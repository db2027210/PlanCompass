#!/usr/bin/env python3
"""Launch IO-C from Outer-6 with empty candidate waves skipped."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_outer_checkpoint_v4 as verifier


HISTORY = ROOT / "history/mysql_four_scene_20260809"
SOURCE = HISTORY / "v256_to_c_top4_io_outer6_partial_20260826_attempt023_xp_io"
CHECKPOINT = HISTORY / "v261_to_c_top4_io_outer6_checkpoint_20260826/prepared_binding.json"
QUARANTINE = HISTORY / "v262_to_c_top4_io_outer6_quarantine_20260826/replay_quarantine.json"
LIVE = HISTORY / "v246_to_xp_top4_live_authority_20260826/hybench_xp_io"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v19.py"
CONTRACT = ROOT / "config/formal/frozen_mysql_algorithm_c_top4_io_xp4_recovery_20260826_v26"
RUN_ID = "v263_to_c_top4_io_empty_wave_20260826_attempt024_xp_io"
OUTPUT = HISTORY / RUN_ID
RECEIPT = HISTORY / "v263_to_c_top4_io_empty_wave_20260826/launch.json"
UNIT = "mysql-algc-top4-io-v263.service"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    checkpoint = verifier.verify_checkpoint(CHECKPOINT)
    source = load(SOURCE / "run_state.json")
    quarantine = load(QUARANTINE)
    live = load(LIVE / "live_authority.json")
    artifacts = live.get("artifacts") or {}
    if (
        OUTPUT.exists() or OUTPUT.is_symlink() or RECEIPT.exists()
        or int(checkpoint.get("completed_outer_attempts") or 0) != 6
        or (checkpoint.get("reconstructed_state") or {}).get("accepted_rounds", {}).get("hybench_xp_io") != 4
        or source.get("status") != "RUNNING"
        or source.get("stage") != "OUTER_007_BATCH_2_RESETTING_XP"
        or sha(SOURCE / "run_state.json")
        != "009dc78a2fbf6371ec3e13027212840591fc8ff167c0006504e30aa4432b6c37"
        or quarantine.get("status") != "PASS"
        or quarantine.get("resume_checkpoint_outer_attempt") != 6
        or live.get("status") != "PASS"
        or live.get("scene") != "hybench_xp_io"
        or live.get("physical_replay_parallelism") != 4
        or any(name not in artifacts for name in ("preflight", "prepared", "accounts", "r0"))
    ):
        raise RuntimeError("IO-C empty-wave recovery authority differs")
    stdout = HISTORY / "formal_launch_logs" / f"{RUN_ID}.stdout.log"
    stderr = HISTORY / "formal_launch_logs" / f"{RUN_ID}.stderr.log"
    barrier = Path(f"/tmp/mysql_c_xp4_start/{RUN_ID}")
    command = [
        "systemd-run", "--user", f"--unit={UNIT.removesuffix('.service')}",
        "--description=MySQL Top4 IO C empty-candidate-wave recovery",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", "240-241,496-497",
        str(PYTHON), str(CONTROLLER), "--run-id", RUN_ID,
        "--checkpoint", str(CHECKPOINT),
        "--runtime-preflight", str(artifacts["preflight"]),
        "--prepared-binding", str(artifacts["prepared"]),
        "--benchmark-accounts", str(artifacts["accounts"]),
        "--fresh-r0", str(artifacts["r0"]),
        "--replay-quarantine", str(QUARANTINE),
        "--scene", "hybench_xp_io", "--scene-start-barrier", str(barrier),
    ]
    launched = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=30,
    )
    if launched.returncode != 0:
        raise RuntimeError("IO-C empty-wave recovery launch failed: " + launched.stderr[-2000:])
    state_path = OUTPUT / "run_state.json"
    deadline = time.monotonic() + 240.0
    while time.monotonic() < deadline:
        if state_path.is_file():
            state = load(state_path)
            if state.get("status") == "FAILED_CLOSED":
                raise RuntimeError(f"IO-C empty-wave recovery exited: {state.get('error')}")
            if (
                state.get("status") == "RUNNING"
                and state.get("async_scene") == "hybench_xp_io"
                and state.get("four_scene_wave_barrier") is False
                and state.get("other_scene_completion_required") is False
                and (state.get("accepted_rounds") or {}).get("hybench_xp_io") >= 4
                and (state.get("empty_candidate_wave_policy") or {}).get("status") == "ACTIVE"
            ):
                result = {
                    "schema_version": "mysql_algorithm_c_top4_io_empty_wave_recovery_launch_v1",
                    "status": "LAUNCHED", "launched_at": now(),
                    "scene": "hybench_xp_io", "formal_scope": "mysql_only",
                    "cross_engine_comparison_prohibited": True,
                    "run_id": RUN_ID, "unit": UNIT, "output": str(OUTPUT),
                    "state": str(state_path), "stdout": str(stdout), "stderr": str(stderr),
                    "checkpoint": {"path": str(CHECKPOINT), "sha256": sha(CHECKPOINT)},
                    "source_state": {
                        "path": str(SOURCE / "run_state.json"),
                        "sha256": sha(SOURCE / "run_state.json"),
                    },
                    "candidate_measurements_replayed": 0,
                    "diagnostic_companion_measurements_credited": 0,
                    "accepted_or_rejected_state_credit_increment": 0,
                    "empty_candidate_physical_waves_skipped": True,
                    "contract_root": str(CONTRACT),
                }
                atomic(RECEIPT, result)
                print(json.dumps(result, sort_keys=True))
                return 0
        time.sleep(1.0)
    raise TimeoutError("IO-C empty-wave recovery startup evidence timed out")


if __name__ == "__main__":
    raise SystemExit(main())
