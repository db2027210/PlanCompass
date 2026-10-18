#!/usr/bin/env python3
"""Repair the final status of a proven-converged independent C scene."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
SCENES = {
    "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io",
}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def repair_if_scoped_complete(state_path: Path, scene: str) -> bool:
    """Return true only when a contradictory terminal state was repaired."""
    state_path = state_path.expanduser().resolve()
    if HISTORY.resolve() not in state_path.parents or scene not in SCENES:
        raise RuntimeError("formal history state and supported scene required")
    state = load(state_path)
    if state.get("status") == "COMPLETE":
        return False
    if state.get("status") not in {"NON_CONVERGED", "RUNNING"}:
        return False
    # RUNNING must never be changed, even if an intermediate converged bit is
    # already present.  Only the core's contradictory terminal tuple is valid.
    if state.get("status") == "RUNNING":
        return False
    outer = int(state.get("outer_attempt") or 0)
    decision_path = state_path.parent / f"outer_{outer:03d}" / f"{scene}.decision.json"
    decision = load(decision_path)
    convergence = decision.get("interval_convergence") or {}
    active = state.get("active_scenes") or []
    if (
        state.get("algorithm") != "C"
        or state.get("async_scene") != scene
        or active != [scene]
        or state.get("stage") != "FINISHED"
        or state.get("error") not in (None, "")
        or (state.get("converged") or {}).get(scene) is not True
        or convergence.get("converged") is not True
        or float(convergence.get("normalized_width") or 1.0) > 0.01 + 1e-12
        or decision.get("scene") != scene
        or int(decision.get("outer_attempt") or 0) != outer
        or int(decision.get("accepted_rounds") or -1)
        != int((state.get("accepted_rounds") or {}).get(scene) or 0)
    ):
        raise RuntimeError("scoped C terminal convergence evidence differs")
    predecessor_sha = sha(state_path)
    audit_path = state_path.parent / "scoped_terminal_status_repair.json"
    audit = {
        "schema_version": "mysql_algorithm_c_scoped_terminal_status_repair_v1",
        "status": "PASS",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": scene,
        "outer_attempt": outer,
        "predecessor_state_path": str(state_path),
        "predecessor_state_sha256": predecessor_sha,
        "decision_path": str(decision_path),
        "decision_sha256": sha(decision_path),
        "interval_normalized_width": float(convergence["normalized_width"]),
        "active_scene_converged": True,
        "inactive_sibling_flags_excluded_from_terminal_aggregation": True,
        "candidate_measurements_mutated": False,
        "accepted_state_credit_increment": 0,
        "workload_replayed": False,
        "source_path": str(Path(__file__).resolve()),
        "source_sha256": sha(Path(__file__).resolve()),
    }
    atomic(audit_path, audit)
    state.update({
        "status": "COMPLETE",
        "completion_reason": "all_active_log_domain_widths_reached_1pct",
        "scoped_terminal_status_repair": {
            "path": str(audit_path),
            "sha256": sha(audit_path),
            "predecessor_state_sha256": predecessor_sha,
            "inactive_sibling_flags_excluded": True,
            "accepted_state_credit_increment": 0,
            "workload_replayed": False,
        },
    })
    atomic(state_path, state)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--scene", choices=sorted(SCENES), required=True)
    args = parser.parse_args()
    repaired = repair_if_scoped_complete(args.state, args.scene)
    print(json.dumps({
        "status": "REPAIRED" if repaired else "UNCHANGED",
        "state": str(args.state.expanduser().resolve()),
        "scene": args.scene,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
