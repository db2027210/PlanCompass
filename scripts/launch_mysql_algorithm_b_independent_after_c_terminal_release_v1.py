#!/usr/bin/env python3
"""Verify Algorithm C terminal release, then start independent Algorithm B."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_float_boundary_terminal_release_v1 as c_release


HISTORY = ROOT / "history/mysql_four_scene_20260809"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
B_LAUNCHER = ROOT / "scripts/launch_mysql_four_scene_algorithm_b_independent_v1.py"
SCENES = {
    "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io",
}


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
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--c-terminal-release", type=Path, required=True)
    parser.add_argument("--b-run-id", required=True)
    parser.add_argument("--audit-output", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.b_run_id) is None:
        parser.error("unsafe Algorithm B run id")
    release_path = args.c_terminal_release.expanduser().resolve()
    audit_path = args.audit_output.expanduser().resolve()
    b_root = (HISTORY / args.b_run_id).resolve()
    if (
        b_root.parent != HISTORY.resolve()
        or b_root.exists()
        or audit_path.exists()
        or not release_path.is_file()
        or release_path.is_symlink()
    ):
        raise RuntimeError("fresh bounded C-release-to-B handoff paths required")

    release = c_release.verify_release(release_path)
    summary = release.get("terminal_summary") or {}
    if (
        release.get("status") != "COMPLETE"
        or release.get("formal_scope") != "mysql_only"
        or release.get("cross_engine_comparison_prohibited") is not True
        or release.get("workload_replayed_for_recovery") is not False
        or release.get("accepted_point_changed_by_recovery") is not False
        or set((summary.get("converged") or {})) != SCENES
        or not all((summary.get("converged") or {}).values())
        or (summary.get("accepted_rounds") or {})
        != {scene: 10 for scene in SCENES}
        or (summary.get("interval_stable_rounds") or {})
        != {scene: 2 for scene in SCENES}
    ):
        raise RuntimeError("Algorithm C terminal release is not auditable completion")

    audit: dict[str, Any] = {
        "schema_version": "mysql_algorithm_c_terminal_release_to_independent_b_handoff_v1",
        "status": "ALGORITHM_C_RELEASE_VERIFIED",
        "started_at": now(),
        "algorithm_c_role": "launch_sequence_gate_and_terminal_audit_only",
        "algorithm_c_terminal_release": {
            "path": str(release_path),
            "sha256": sha(release_path),
            "canonical_release_sha256": release["release_sha256"],
            "passed_to_algorithm_b": False,
        },
        "algorithm_c_terminal_summary": summary,
        "algorithm_c_state_or_result_inputs_to_algorithm_b": [],
        "algorithm_b_run_id": args.b_run_id,
        "algorithm_b_launcher": {
            "path": str(B_LAUNCHER), "sha256": sha(B_LAUNCHER),
        },
        "handoff_source": {
            "path": str(Path(__file__).resolve()),
            "sha256": sha(Path(__file__).resolve()),
        },
    }
    atomic(audit_path, audit)

    audit["status"] = "LAUNCHING_INDEPENDENT_ALGORITHM_B"
    audit["launch_started_at"] = now()
    atomic(audit_path, audit)
    launched = subprocess.run(
        [str(PYTHON), str(B_LAUNCHER), "--run-id", args.b_run_id],
        cwd=ROOT, text=True, capture_output=True, check=False, timeout=240,
    )
    if launched.returncode != 0:
        audit["status"] = "ALGORITHM_B_LAUNCH_FAILED_CLOSED"
        audit["failed_at"] = now()
        audit["launcher_stdout_tail"] = launched.stdout[-4000:]
        audit["launcher_stderr_tail"] = launched.stderr[-4000:]
        atomic(audit_path, audit)
        return 3

    receipt_path = b_root / "launch_receipt.json"
    state_path = b_root / "run_state.json"
    receipt = load(receipt_path)
    state = load(state_path)
    independence = state.get("independence_contract") or {}
    if (
        receipt.get("status") != "STARTED"
        or receipt.get("algorithm") != "B"
        or receipt.get("algorithm_c_state_or_result_inputs") != []
        or state.get("status") != "RUNNING"
        or state.get("algorithm") != "B"
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or set(state.get("active_scenes") or []) != SCENES
        or independence.get("algorithm_c_checkpoint_read") is not False
        or independence.get("algorithm_c_release_read") is not False
        or independence.get("algorithm_c_incumbent_read") is not False
        or independence.get("algorithm_c_interval_read") is not False
        or independence.get("algorithm_c_measurement_read") is not False
        or independence.get("algorithm_c_candidate_or_pruning_history_read") is not False
    ):
        raise RuntimeError("independent Algorithm B startup evidence differs")

    audit["status"] = "INDEPENDENT_ALGORITHM_B_STARTED"
    audit["completed_at"] = now()
    audit["algorithm_b_launch_receipt"] = {
        "path": str(receipt_path), "sha256": sha(receipt_path),
    }
    audit["algorithm_b_initial_state"] = {
        "path": str(state_path), "sha256": sha(state_path),
        "stage": state.get("stage"),
        "accepted_rounds": state.get("accepted_rounds"),
    }
    audit["algorithm_c_state_or_result_inputs_to_algorithm_b"] = []
    atomic(audit_path, audit)
    print(json.dumps({
        "status": audit["status"],
        "algorithm_b_run_id": args.b_run_id,
        "algorithm_b_stage": state.get("stage"),
        "audit": str(audit_path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
