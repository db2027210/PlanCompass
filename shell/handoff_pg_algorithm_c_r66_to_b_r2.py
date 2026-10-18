#!/usr/bin/env python3
"""Launch Algorithm B only after all four Algorithm C authorities pass."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time
from typing import Any, Dict


PROJECT = Path("/home/anonymous/program/resource_adaptive")
C_ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260815_algorithm_c_io_cost_champion_clean_r66"
)
SCENES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
STATUS = C_ROOT / "algorithm_c_then_b_handoff_status.json"
PREPARE_B = PROJECT / "shell/prepare_pg_algorithm_b_io_cost_champion_r2.py"
LAUNCH_B = PROJECT / "shell/launch_pg_algorithm_b_io_cost_champion_r2.sh"


def write_status(payload: Dict[str, Any]) -> None:
    temporary = STATUS.with_name(f".{STATUS.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(STATUS)


def c_complete() -> bool:
    for scene in SCENES:
        state_path = C_ROOT / scene / "algorithm_c_state.json"
        authority_path = C_ROOT / scene / "authority_manifest.json"
        if not state_path.is_file() or not authority_path.is_file():
            return False
        state = json.loads(state_path.read_text(encoding="utf-8"))
        authority = json.loads(authority_path.read_text(encoding="utf-8"))
        if not (
            state.get("phase") == "complete"
            and int(state.get("accepted") or 0) == 7
            and int(state.get("consecutive_stable_rounds") or 0) == 2
            and isinstance(state.get("final_champion"), dict)
            and (state.get("repeatability") or {}).get("passed") is True
            and isinstance(authority.get("final_champion"), dict)
        ):
            return False
    return True


def main() -> int:
    started = time.time()
    while not c_complete():
        write_status(
            {
                "phase": "waiting_for_algorithm_c_authorities",
                "algorithm_c_root": str(C_ROOT),
                "algorithm_b_started": False,
                "updated_at_epoch_s": time.time(),
            }
        )
        time.sleep(60.0)
    write_status(
        {
            "phase": "preparing_algorithm_b",
            "algorithm_c_root": str(C_ROOT),
            "algorithm_b_started": False,
            "algorithm_c_wait_seconds": time.time() - started,
            "updated_at_epoch_s": time.time(),
        }
    )
    subprocess.run([str(PREPARE_B)], cwd=PROJECT, check=True)
    subprocess.run([str(LAUNCH_B)], cwd=PROJECT, check=True)
    write_status(
        {
            "phase": "algorithm_b_launched",
            "algorithm_c_root": str(C_ROOT),
            "algorithm_b_started": True,
            "algorithm_c_wait_seconds": time.time() - started,
            "updated_at_epoch_s": time.time(),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
