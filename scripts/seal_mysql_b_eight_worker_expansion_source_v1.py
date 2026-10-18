#!/usr/bin/env python3
"""Seal a stopped four-worker B state as an explicit eight-worker continuation boundary."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ERROR = "EightWorkerBoundaryExpansion: incomplete physical wave discarded"
EXPECTED = {
    "hybench_xp_cpu": (8, 4, 4),
    "hybench_xp_io": (5, 2, 3),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError("run state must be an object")
    return dict(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", choices=sorted(EXPECTED), required=True)
    parser.add_argument("--state", required=True)
    args = parser.parse_args()
    path = Path(args.state).resolve()
    sidecar = path.with_name(path.name + ".sha256")
    if not path.is_file() or path.is_symlink() or sidecar.read_text(encoding="ascii").split()[0] != sha(path):
        raise RuntimeError("source state binding differs")
    state = load(path)
    outer, accepted, rejected = EXPECTED[args.scene]
    if (
        state.get("status") != "RUNNING"
        or state.get("async_scene") != args.scene
        or int(state.get("outer_attempt") or 0) != outer
        or int((state.get("accepted_rounds") or {}).get(args.scene) or 0) != accepted
        or int((state.get("rejected_outer_attempts") or {}).get(args.scene) or 0) != rejected
        or state.get("stage") in {"COMPLETE", "FAILED"}
    ):
        raise RuntimeError("source is not the expected stopped incomplete wave")
    state.update({
        "status": "FAILED_CLOSED", "stage": "FAILED", "error": ERROR,
        "stage_updated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "technical_continuation": True,
        "incomplete_physical_wave_formal_credit": 0,
        "completed_candidate_measurements_replayed": False,
        "eight_worker_boundary_expansion_authorized": True,
    })
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)
    sidecar.write_text(f"{sha(path)}  {path.name}\n", encoding="ascii")
    print(json.dumps({"status": "SEALED", "scene": args.scene, "sha256": sha(path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
