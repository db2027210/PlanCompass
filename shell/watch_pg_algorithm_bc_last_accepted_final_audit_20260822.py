#!/usr/bin/env python3
"""Wait for fresh Algorithm C/B campaigns and emit the final authority audit."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Tuple


PROJECT = Path("/home/anonymous/program/resource_adaptive")
C_ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260822_algorithm_c_last_accepted_qps_clean_r67"
)
B_ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/"
    "20260822_algorithm_b_last_accepted_qps_clean_r3"
)
SCENES: Tuple[str, ...] = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
STATUS_PATH = C_ROOT / "algorithm_bc_final_audit_watch_status.json"
AUDIT_PATH = C_ROOT / "algorithm_bc_last_accepted_final_audit.json"
AUDITOR = PROJECT / "shell/audit_pg_algorithm_bc_last_accepted_20260822.py"


def read_json(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return payload


def write_status(payload: Dict[str, Any]) -> None:
    temporary = STATUS_PATH.with_name(f".{STATUS_PATH.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(STATUS_PATH)


def campaign_complete(root: Path, state_name: str) -> bool:
    for scene in SCENES:
        state_path = root / scene / state_name
        if not state_path.is_file():
            return False
        state = read_json(state_path)
        if not (
            state.get("phase") == "complete"
            and int(state.get("accepted") or 0) == 7
            and isinstance(state.get("final_champion"), dict)
        ):
            return False
    return True


def main() -> int:
    started = time.time()
    while True:
        c_complete = campaign_complete(C_ROOT, "algorithm_c_state.json")
        b_complete = campaign_complete(B_ROOT, "algorithm_b_state.json")
        write_status(
            {
                "phase": "waiting_for_fresh_campaigns",
                "algorithm_c_complete": c_complete,
                "algorithm_b_complete": b_complete,
                "algorithm_c_root": str(C_ROOT),
                "algorithm_b_root": str(B_ROOT),
                "elapsed_s": time.time() - started,
                "updated_at_epoch_s": time.time(),
            }
        )
        if c_complete and b_complete:
            break
        time.sleep(60.0)

    audit = subprocess.run(
        [
            sys.executable,
            str(AUDITOR),
            "--output",
            str(AUDIT_PATH),
            str(C_ROOT),
            str(B_ROOT),
        ],
        cwd=PROJECT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=600.0,
        check=False,
    )
    write_status(
        {
            "phase": "final_audit_passed" if audit.returncode == 0 else "final_audit_failed",
            "algorithm_c_complete": True,
            "algorithm_b_complete": True,
            "audit_path": str(AUDIT_PATH),
            "audit_returncode": audit.returncode,
            "audit_stdout_tail": audit.stdout.splitlines()[-40:],
            "elapsed_s": time.time() - started,
            "updated_at_epoch_s": time.time(),
        }
    )
    return audit.returncode


if __name__ == "__main__":
    raise SystemExit(main())
