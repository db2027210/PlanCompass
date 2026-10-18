#!/usr/bin/env python3
"""Seal CPU B after committed Outer-10 for Docker-control v2 transition."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path


ERROR = "DockerControlV2Boundary: completed Outer-10 preserved"
STATE = Path(__file__).resolve().parents[1] / "history/mysql_four_scene_20260809/v538_to_b_cpu_mixed_eight_worker_20260827_xp_cpu/run_state.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    sidecar = STATE.with_name(STATE.name + ".sha256")
    state = json.loads(STATE.read_text(encoding="utf-8"))
    scene = "hybench_xp_cpu"
    if (
        sidecar.read_text(encoding="ascii").split()[0] != sha(STATE)
        or state.get("status") != "RUNNING"
        or state.get("async_scene") != scene
        or int(state.get("outer_attempt") or 0) != 10
        or int((state.get("accepted_rounds") or {}).get(scene) or 0) != 4
        or int((state.get("rejected_outer_attempts") or {}).get(scene) or 0) != 6
        or "OUTER_011" not in str(state.get("stage") or "")
    ):
        raise RuntimeError("CPU Docker-control v2 boundary differs")
    state.update({
        "status": "FAILED_CLOSED", "stage": "FAILED", "error": ERROR,
        "stage_updated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "technical_continuation": True,
        "docker_control_v2_transition": True,
        "completed_outer_history_preserved_through": 10,
        "incomplete_physical_wave_formal_credit": 0,
    })
    temporary = STATE.with_name(f".{STATE.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, STATE)
    sidecar.write_text(f"{sha(STATE)}  {STATE.name}\n", encoding="ascii")
    print(json.dumps({"status": "SEALED", "sha256": sha(STATE)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
