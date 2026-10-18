#!/usr/bin/env python3
"""Independently hand off each formal scene from C to Top4-B to Top4-A."""

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
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python").resolve()
B_LAUNCHER = ROOT / "scripts/launch_mysql_four_scene_top4_algorithm_b_two_worker_scene_v4.py"
A_LAUNCHER = ROOT / "scripts/launch_mysql_four_scene_top4_algorithm_a_two_worker_scene_v1.py"
SOURCE_AUTHORITY = HISTORY / "v301_to_four_scene_two_worker_live_authority_20260826"
SCENES = {
    "job_memory": "job", "tpch_sf4_memory": "tpch",
    "hybench_xp_cpu": "xp_cpu", "hybench_xp_io": "xp_io",
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
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(f"{sha(path)}  {path.name}\n", encoding="ascii")


def terminal(path: Path, algorithm: str, scene: str) -> tuple[bool, dict[str, Any] | None]:
    if not path.is_file():
        return False, None
    state = load(path)
    return (
        state.get("algorithm") == algorithm
        and state.get("async_scene") == scene
        and state.get("status") == "COMPLETE"
        and state.get("stage") == "FINISHED"
        and not state.get("error")
        and bool((state.get("converged") or {}).get(scene)),
        state,
    )


def unit_state(unit: str) -> dict[str, str]:
    completed = subprocess.run([
        "systemctl", "--user", "show", unit, "-p", "ActiveState",
        "-p", "SubState", "-p", "Result", "-p", "ExecMainStatus",
    ], cwd=ROOT, text=True, capture_output=True, check=False, timeout=15)
    return dict(line.split("=", 1) for line in completed.stdout.splitlines() if "=" in line)


def start_handoff(unit: str, description: str, command: list[str], stdout: Path, stderr: Path) -> None:
    shown = unit_state(unit)
    if shown.get("ActiveState") in {"active", "activating"}:
        return
    stdout.parent.mkdir(parents=True, exist_ok=True)
    launched = subprocess.run([
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        f"--description={description}", f"--property=WorkingDirectory={ROOT}",
        "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}", *command,
    ], cwd=ROOT, text=True, capture_output=True, check=False, timeout=30)
    if launched.returncode != 0:
        raise RuntimeError(f"handoff unit launch failed: {unit}: {launched.stderr[-2000:]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pipeline-id", default="v306_to_four_scene_top4_abc_chain_20260826")
    parser.add_argument("--c-campaign", default="v302_to_c_qps_rejection_four_scene_20260826_attempt001")
    parser.add_argument("--c-job-campaign")
    parser.add_argument("--c-tpch-campaign")
    parser.add_argument("--c-xp-cpu-campaign")
    parser.add_argument("--c-xp-io-campaign")
    parser.add_argument("--b-campaign", default="v304_to_b_qps_rejection_four_scene_top4_20260826_attempt001")
    parser.add_argument("--b-job-campaign")
    parser.add_argument("--b-tpch-campaign")
    parser.add_argument("--b-xp-cpu-campaign")
    parser.add_argument("--b-xp-io-campaign")
    parser.add_argument("--a-campaign", default="v305_to_a_four_scene_top4_20260826_attempt001")
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    args = parser.parse_args()
    c_campaigns = {
        "job_memory": args.c_job_campaign or args.c_campaign,
        "tpch_sf4_memory": args.c_tpch_campaign or args.c_campaign,
        "hybench_xp_cpu": args.c_xp_cpu_campaign or args.c_campaign,
        "hybench_xp_io": args.c_xp_io_campaign or args.c_campaign,
    }
    b_campaigns = {
        "job_memory": args.b_job_campaign or args.b_campaign,
        "tpch_sf4_memory": args.b_tpch_campaign or args.b_campaign,
        "hybench_xp_cpu": args.b_xp_cpu_campaign or args.b_campaign,
        "hybench_xp_io": args.b_xp_io_campaign or args.b_campaign,
    }
    for value in (
        args.pipeline_id, args.a_campaign,
        *c_campaigns.values(), *b_campaigns.values(),
    ):
        if re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", value) is None:
            parser.error("unsafe campaign id")
    pipeline = HISTORY / args.pipeline_id
    state_path = pipeline / "pipeline_state.json"
    b_receipts = pipeline / "b_launches"
    a_receipts = pipeline / "a_launches"
    a_authority = HISTORY / f"{args.a_campaign}_authority"
    log_root = HISTORY / "formal_launch_logs"
    while True:
        rows: dict[str, dict[str, Any]] = {}
        for scene, suffix in SCENES.items():
            c_campaign = c_campaigns[scene]
            b_campaign = b_campaigns[scene]
            c_state_path = HISTORY / f"{c_campaign}_{suffix}" / "run_state.json"
            b_run = f"{b_campaign}_{suffix}"
            b_state_path = HISTORY / b_run / "run_state.json"
            a_run = f"{args.a_campaign}_{suffix}"
            a_state_path = HISTORY / a_run / "algorithm_a_state.json"
            c_done, c_state = terminal(c_state_path, "C", scene)
            b_done, b_state = terminal(b_state_path, "B", scene)
            a_done, a_state = terminal(a_state_path, "A", scene)
            row: dict[str, Any] = {
                "scene": scene, "c_campaign": c_campaign, "c_state": str(c_state_path),
                "c_status": (c_state or {}).get("status", "NOT_STARTED"),
                "c_stage": (c_state or {}).get("stage"), "c_complete": c_done,
                "b_campaign": b_campaign, "b_state": str(b_state_path),
                "b_status": (b_state or {}).get("status", "NOT_STARTED"),
                "b_stage": (b_state or {}).get("stage"), "b_complete": b_done,
                "a_state": str(a_state_path),
                "a_status": (a_state or {}).get("status", "NOT_STARTED"),
                "a_stage": (a_state or {}).get("stage"), "a_complete": a_done,
            }
            b_receipt = b_receipts / f"{suffix}.launch.json"
            if c_done and not b_state_path.exists() and not b_receipt.exists():
                unit = f"mysql-handoff-b-top4-{suffix}-v319.service"
                start_handoff(
                    unit, f"MySQL {scene} C to B handoff",
                    [str(PYTHON), str(B_LAUNCHER), "--scene", scene,
                     "--c-state", str(c_state_path), "--source-authority", str(SOURCE_AUTHORITY),
                     "--run-id", b_run, "--pipeline-root", str(b_receipts),
                     "--authority-root", str(HISTORY / f"{b_campaign}_authority"),
                     "--unit", f"mysql-algb-top4-{suffix}-v320.service"],
                    log_root / f"handoff_b_{suffix}_v319.stdout.log",
                    log_root / f"handoff_b_{suffix}_v319.stderr.log",
                )
                row["b_handoff_unit"] = unit
            a_receipt = a_receipts / f"{suffix}.launch.json"
            if b_done and not a_state_path.exists() and not a_receipt.exists():
                unit = f"mysql-handoff-a-top4-{suffix}-v319.service"
                start_handoff(
                    unit, f"MySQL {scene} B to A handoff",
                    [str(PYTHON), str(A_LAUNCHER), "--scene", scene,
                     "--b-state", str(b_state_path), "--source-authority", str(SOURCE_AUTHORITY),
                     "--run-id", a_run, "--pipeline-root", str(a_receipts),
                     "--authority-root", str(a_authority),
                     "--unit", f"mysql-alga-top4-{suffix}-v321.service"],
                    log_root / f"handoff_a_{suffix}_v319.stdout.log",
                    log_root / f"handoff_a_{suffix}_v319.stderr.log",
                )
                row["a_handoff_unit"] = unit
            rows[scene] = row
        complete = all(row["a_complete"] for row in rows.values())
        atomic(state_path, {
            "schema_version": "mysql_four_scene_top4_abc_independent_chain_v1",
            "status": "COMPLETE" if complete else "RUNNING",
            "updated_at": now(), "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "four_scene_barrier": False, "sibling_scene_wait": False,
            "external_process_control_or_stop_performed": False,
            "postgresql_process_control_or_stop_performed": False,
            "source_authority": str(SOURCE_AUTHORITY),
            "c_campaigns": c_campaigns, "b_campaigns": b_campaigns,
            "scenes": rows,
        })
        if complete:
            return 0
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
