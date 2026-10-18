#!/usr/bin/env python3
"""Audit terminal Algorithm C, then sequence-start independent Algorithm B."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
C_CHECKPOINT_BUILDER = (
    ROOT / "scripts/build_mysql_algorithm_c_outer_checkpoint_v7.py"
)
B_LAUNCHER = (
    ROOT / "scripts/launch_mysql_four_scene_algorithm_b_independent_v1.py"
)
SCENES = {"job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io"}


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


def unit_active(unit: str) -> bool:
    return subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", unit],
        check=False, timeout=10,
    ).returncode == 0


def audit_terminal_c(run_root: Path) -> dict[str, Any]:
    state_path = run_root / "run_state.json"
    receipt_path = run_root / "launch_receipt.json"
    state = load(state_path)
    receipt = load(receipt_path)
    accepted = state.get("accepted_rounds") or {}
    stable = state.get("interval_stable_rounds") or {}
    converged = state.get("converged") or {}
    outer = int(state.get("outer_attempt") or 0)
    if (
        state.get("status") != "COMPLETE"
        or state.get("stage") != "FINISHED"
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("formal_evidence_eligible") is not True
        or state.get("accepted_state_credit_prohibited") is not False
        or set(accepted) != SCENES
        or any(int(accepted[scene]) != 10 for scene in SCENES)
        or set(stable) != SCENES
        or any(int(stable[scene]) < 2 for scene in SCENES)
        or set(converged) != SCENES
        or not all(bool(converged[scene]) for scene in SCENES)
        or outer < 18
        or receipt.get("status") != "STARTED"
        or receipt.get("formal_scope") != "mysql_only"
        or receipt.get("formal_evidence_eligible") is not True
    ):
        raise RuntimeError("Algorithm C terminal state is not auditable completion")
    unit = str(receipt.get("systemd_unit") or "")
    if not unit or unit_active(unit):
        raise RuntimeError("Algorithm C controller is not terminal")
    batches: list[dict[str, Any]] = []
    for rank in range(1, 6):
        path = run_root / f"outer_{outer:03d}/top5_batch_{rank}/batch_result.json"
        batch = load(path)
        measurements = batch.get("measurements") or {}
        if (
            batch.get("status") != "PASS"
            or batch.get("failures") not in ([], None)
            or len(measurements) != 4
            or any(
                row.get("hard_gate_passed") is not True
                or row.get("coverage_complete") is not True
                for row in measurements.values()
            )
        ):
            raise RuntimeError(f"Algorithm C terminal batch differs: {path}")
        batches.append({
            "rank": rank,
            "path": str(path),
            "sha256": sha(path),
            "elapsed_seconds": batch.get("elapsed_seconds"),
        })
    decisions: dict[str, dict[str, Any]] = {}
    for scene in sorted(scene for scene in SCENES if scene != "tpch_sf4_memory"):
        path = run_root / f"outer_{outer:03d}/{scene}.decision.json"
        decision = load(path)
        convergence = decision.get("interval_convergence") or {}
        if (
            decision.get("accepted") is not True
            or int(decision.get("accepted_state_credit_increment") or 0) != 0
            or int(decision.get("accepted_rounds") or -1) != 10
            or convergence.get("converged") is not True
            or int(convergence.get("stable_rounds") or 0) < 2
            or decision.get("formal_evidence_eligible") is not True
        ):
            raise RuntimeError(f"Algorithm C terminal decision differs: {path}")
        decisions[scene] = {"path": str(path), "sha256": sha(path)}
    return {
        "state": {"path": str(state_path), "sha256": sha(state_path)},
        "launch_receipt": {
            "path": str(receipt_path), "sha256": sha(receipt_path),
        },
        "outer_attempt": outer,
        "accepted_rounds": accepted,
        "interval_stable_rounds": stable,
        "converged": converged,
        "batches": batches,
        "decisions": decisions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--c-run-id", required=True)
    parser.add_argument("--b-run-id", required=True)
    parser.add_argument("--checkpoint-output-dir", type=Path, required=True)
    parser.add_argument("--audit-output", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    parser.add_argument("--maximum-wait-seconds", type=float, default=28800.0)
    args = parser.parse_args()
    for value in (args.c_run_id, args.b_run_id):
        if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", value) is None:
            parser.error("unsafe run id")
    if args.poll_seconds <= 0 or args.maximum_wait_seconds <= 0:
        parser.error("positive wait bounds required")
    c_root = (HISTORY / args.c_run_id).resolve()
    b_root = (HISTORY / args.b_run_id).resolve()
    checkpoint_output = args.checkpoint_output_dir.expanduser().resolve()
    audit_output = args.audit_output.expanduser().resolve()
    if (
        c_root.parent != HISTORY.resolve()
        or b_root.parent != HISTORY.resolve()
        or not c_root.is_dir()
        or b_root.exists()
        or checkpoint_output.exists()
        or audit_output.exists()
    ):
        raise RuntimeError("fresh bounded C-to-independent-B handoff paths required")
    audit = {
        "schema_version": "mysql_algorithm_c_to_independent_b_handoff_v1",
        "status": "WAITING_FOR_ALGORITHM_C",
        "started_at": now(),
        "c_run_id": args.c_run_id,
        "b_run_id": args.b_run_id,
        "algorithm_c_role": "launch_sequence_gate_and_terminal_audit_only",
        "algorithm_c_state_or_result_inputs_to_algorithm_b": [],
        "checkpoint_output_dir": str(checkpoint_output),
        "checkpoint_builder_path": str(C_CHECKPOINT_BUILDER),
        "checkpoint_builder_sha256": sha(C_CHECKPOINT_BUILDER),
        "algorithm_b_launcher_path": str(B_LAUNCHER),
        "algorithm_b_launcher_sha256": sha(B_LAUNCHER),
    }
    atomic(audit_output, audit)
    deadline = time.monotonic() + args.maximum_wait_seconds
    last_signature: tuple[Any, ...] | None = None
    while time.monotonic() < deadline:
        state = load(c_root / "run_state.json")
        signature = (
            state.get("status"), state.get("stage"), state.get("stage_updated_at"),
            state.get("outer_attempt"),
        )
        if signature != last_signature:
            audit["last_c_state"] = {
                "observed_at": now(), "status": signature[0],
                "stage": signature[1], "stage_updated_at": signature[2],
                "outer_attempt": signature[3],
            }
            atomic(audit_output, audit)
            last_signature = signature
        if state.get("status") == "COMPLETE":
            break
        if state.get("status") in {"FAILED_CLOSED", "NON_CONVERGED"}:
            audit["status"] = "BLOCKED_BY_ALGORITHM_C_TERMINAL_FAILURE"
            audit["blocked_at"] = now()
            audit["algorithm_c_error"] = state.get("error")
            atomic(audit_output, audit)
            return 3
        time.sleep(args.poll_seconds)
    else:
        audit["status"] = "BLOCKED_BY_ALGORITHM_C_WAIT_TIMEOUT"
        audit["blocked_at"] = now()
        atomic(audit_output, audit)
        return 4

    # Let the C controller finish its final atomic write and leave the unit.
    terminal_deadline = time.monotonic() + 120.0
    c_unit = str(load(c_root / "launch_receipt.json").get("systemd_unit") or "")
    while c_unit and unit_active(c_unit) and time.monotonic() < terminal_deadline:
        time.sleep(1.0)
    terminal = audit_terminal_c(c_root)
    audit["algorithm_c_terminal_audit"] = terminal
    audit["status"] = "BUILDING_ALGORITHM_C_TERMINAL_CHECKPOINT"
    atomic(audit_output, audit)
    built = subprocess.run(
        [
            str(PYTHON), str(C_CHECKPOINT_BUILDER),
            "--run-root", str(c_root),
            "--output-dir", str(checkpoint_output),
        ],
        cwd=ROOT, text=True, capture_output=True, check=False, timeout=300,
    )
    if built.returncode != 0:
        audit["status"] = "BLOCKED_BY_ALGORITHM_C_CHECKPOINT_AUDIT"
        audit["blocked_at"] = now()
        audit["checkpoint_builder_stderr"] = built.stderr[-4000:]
        atomic(audit_output, audit)
        return 5
    checkpoint_path = checkpoint_output / "prepared_binding.json"
    checkpoint = load(checkpoint_path)
    if (
        checkpoint.get("status") != "PREPARED"
        or (checkpoint.get("terminal_convergence_audit") or {}).get(
            "all_four_scenes_converged"
        ) is not True
    ):
        raise RuntimeError("Algorithm C terminal checkpoint verification differs")
    audit["algorithm_c_terminal_checkpoint"] = {
        "path": str(checkpoint_path), "sha256": sha(checkpoint_path),
        "passed_to_algorithm_b": False,
    }
    audit["status"] = "LAUNCHING_INDEPENDENT_ALGORITHM_B"
    atomic(audit_output, audit)
    launched = subprocess.run(
        [str(PYTHON), str(B_LAUNCHER), "--run-id", args.b_run_id],
        cwd=ROOT, text=True, capture_output=True, check=False, timeout=240,
    )
    if launched.returncode != 0:
        audit["status"] = "BLOCKED_BY_INDEPENDENT_ALGORITHM_B_LAUNCH"
        audit["blocked_at"] = now()
        audit["algorithm_b_launcher_stderr"] = launched.stderr[-4000:]
        atomic(audit_output, audit)
        return 6
    b_receipt_path = b_root / "launch_receipt.json"
    b_receipt = load(b_receipt_path)
    if (
        b_receipt.get("status") != "STARTED"
        or b_receipt.get("algorithm") != "B"
        or b_receipt.get("algorithm_c_state_or_result_inputs") != []
    ):
        raise RuntimeError("independent Algorithm B launch receipt differs")
    audit["status"] = "INDEPENDENT_ALGORITHM_B_STARTED"
    audit["completed_at"] = now()
    audit["algorithm_b_launch_receipt"] = {
        "path": str(b_receipt_path), "sha256": sha(b_receipt_path),
    }
    audit["algorithm_c_state_or_result_inputs_to_algorithm_b"] = []
    atomic(audit_output, audit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
