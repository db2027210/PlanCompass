#!/usr/bin/env python3
"""Launch IO-C Outer-6, reusing ranks 1-4 and replaying only wave 5."""

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
SOURCE = HISTORY / "v225_to_c_top4_io_xp4_20260826_attempt020_xp_io"
CHECKPOINT = HISTORY / "v250_to_c_top4_io_outer5_checkpoint_20260826/prepared_binding.json"
QUARANTINE = (
    HISTORY / "v251_to_c_top4_io_outer5_quarantine_20260826/replay_quarantine.json"
)
LIVE = HISTORY / "v246_to_xp_top4_live_authority_20260826/hybench_xp_io"
RECOVERY = (
    HISTORY / "v245_to_xp_top4_unhealthy_recovery_20260826_attempt002"
    / "recovered_xp_binding.json"
)
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_top4_io_xp4_recovery_20260826_v23"
)
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v16.py"
RUN_ID = "v252_to_c_top4_io_outer6_partial_20260826_attempt021_xp_io"
OUTPUT = HISTORY / RUN_ID
RECEIPT = HISTORY / "v252_to_c_top4_io_outer6_partial_20260826/launch.json"
UNIT = "mysql-algc-top4-io-v252.service"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
MANAGEMENT_CPUSET = "240-241,496-497"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular JSON required: {path}")
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
    live = load(LIVE / "live_authority.json")
    recovery = load(RECOVERY)
    quarantine = load(QUARANTINE)
    source = load(SOURCE / "run_state.json")
    completed = load(SOURCE / "outer_006/top5_batch_1/batch_result.json")
    artifacts = live.get("artifacts") or {}
    if (
        OUTPUT.exists() or OUTPUT.is_symlink() or RECEIPT.exists()
        or int(checkpoint.get("completed_outer_attempts") or 0) != 5
        or live.get("status") != "PASS"
        or live.get("scene") != "hybench_xp_io"
        or live.get("physical_replay_parallelism") != 4
        or recovery.get("status") != "PASS"
        or quarantine.get("status") != "PASS"
        or quarantine.get("resume_checkpoint_outer_attempt") != 5
        or source.get("status") != "FAILED_CLOSED"
        or int(source.get("outer_attempt") or 0) != 5
        or completed.get("status") != "PASS"
        or sorted(int(row.get("rank") or 0) for row in completed["measurements"].values())
        != [1, 2, 3, 4]
        or any(name not in artifacts for name in ("preflight", "prepared", "accounts", "r0"))
    ):
        raise RuntimeError("IO-C Outer-6 partial launch authority differs")
    stdout = HISTORY / "formal_launch_logs" / f"{RUN_ID}.stdout.log"
    stderr = HISTORY / "formal_launch_logs" / f"{RUN_ID}.stderr.log"
    barrier = Path(f"/tmp/mysql_c_xp4_start/{RUN_ID}")
    command = [
        "systemd-run", "--user", f"--unit={UNIT.removesuffix('.service')}",
        "--description=MySQL Top4 IO C Outer6 physical partial continuation",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,
        str(PYTHON), str(CONTROLLER), "--run-id", RUN_ID,
        "--checkpoint", str(CHECKPOINT),
        "--runtime-preflight", str(artifacts["preflight"]),
        "--prepared-binding", str(artifacts["prepared"]),
        "--benchmark-accounts", str(artifacts["accounts"]),
        "--fresh-r0", str(artifacts["r0"]),
        "--replay-quarantine", str(QUARANTINE),
        "--scene", "hybench_xp_io", "--scene-start-barrier", str(barrier),
        "--formal-partial-root", str(SOURCE), "--formal-start-rank", "5",
    ]
    launched = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=30,
    )
    if launched.returncode != 0:
        raise RuntimeError("IO-C partial launch failed: " + launched.stderr[-2000:])
    state_path = OUTPUT / "run_state.json"
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        if state_path.is_file():
            state = load(state_path)
            if state.get("status") == "FAILED_CLOSED":
                raise RuntimeError(f"IO-C partial startup failed: {state.get('error')}")
            partial = state.get("formal_partial_continuation") or {}
            if (
                state.get("status") == "RUNNING"
                and state.get("async_scene") == "hybench_xp_io"
                and state.get("four_scene_wave_barrier") is False
                and state.get("other_scene_completion_required") is False
                and partial.get("completed_candidate_ranks_reused") == [1, 2, 3, 4]
                and partial.get("failed_companion_wave_replayed") == [5]
            ):
                receipt = {
                    "schema_version": "mysql_algorithm_c_top4_io_physical_partial_launch_v1",
                    "status": "LAUNCHED", "launched_at": now(),
                    "scene": "hybench_xp_io", "formal_scope": "mysql_only",
                    "cross_engine_comparison_prohibited": True,
                    "run_id": RUN_ID, "unit": UNIT, "output": str(OUTPUT),
                    "state": str(state_path), "stdout": str(stdout), "stderr": str(stderr),
                    "checkpoint": {"path": str(CHECKPOINT), "sha256": sha(CHECKPOINT)},
                    "source_failed_run": {
                        "path": str(SOURCE / "run_state.json"),
                        "sha256": sha(SOURCE / "run_state.json"),
                    },
                    "completed_candidate_ranks_reused": [1, 2, 3, 4],
                    "candidate_measurements_replayed": [],
                    "failed_companion_wave_replayed": [5],
                    "accepted_or_rejected_state_credit_increment": 0,
                    "live_authority": {
                        "path": str(LIVE / "live_authority.json"),
                        "sha256": sha(LIVE / "live_authority.json"),
                    },
                    "recovery_binding": {"path": str(RECOVERY), "sha256": sha(RECOVERY)},
                    "contract_root": str(CONTRACT),
                }
                atomic(RECEIPT, receipt)
                print(json.dumps(receipt, sort_keys=True))
                return 0
        time.sleep(1)
    raise TimeoutError("IO-C partial startup evidence timed out")


if __name__ == "__main__":
    raise SystemExit(main())
