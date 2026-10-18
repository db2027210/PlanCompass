#!/usr/bin/env python3
"""Move each live XP-C scene to v68 at its own completed-round boundary."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
BUILDER = ROOT / "scripts/build_mysql_algorithm_c_independent_round_boundary_checkpoint_v3.py"
QUARANTINE = ROOT / "scripts/build_mysql_algorithm_c_empty_quarantine_for_checkpoint_v1.py"
LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_c_async_v68.py"
CHAIN = ROOT / "scripts/monitor_mysql_four_scene_top4_abc_chain_v1.py"
AUTHORITY = HISTORY / "v301_to_four_scene_two_worker_live_authority_20260826"
LOG_ROOT = HISTORY / "formal_launch_logs"
TRANSITION_ROOT = HISTORY / "v368_to_xp_c_empty_wave_v68_transition_20260826"
STATE_PATH = TRANSITION_ROOT / "transition_state.json"

SCENES = {
    "hybench_xp_cpu": {
        "suffix": "xp_cpu",
        "source_campaign": "v351_to_c_qps_rejection_xp_cpu_top4_boundary_recovery_20260826_attempt001",
        "source_run": "v351_to_c_qps_rejection_xp_cpu_top4_boundary_recovery_20260826_attempt001_xp_cpu",
        "source_unit": "mysql-algc-async-v351_to_c_qps_rejection_xp_cpu_top4_boundary_recovery_20260826_attempt001_xp_cpu.service",
        "minimum_completed_outer": 6,
        "checkpoint_root": "v369_to_c_xp_cpu_outer6_v68_boundary_20260826",
        "target_campaign": "v370_to_c_xp_cpu_empty_wave_skip_20260826_attempt001",
    },
    "hybench_xp_io": {
        "suffix": "xp_io",
        "source_campaign": "v359_to_c_qps_rejection_xp_io_partial_topk_settlement_20260826_attempt001",
        "source_run": "v359_to_c_qps_rejection_xp_io_partial_topk_settlement_20260826_attempt001_xp_io",
        "source_unit": "mysql-algc-async-v359_to_c_qps_rejection_xp_io_partial_topk_settlement_20260826_attempt001_xp_io.service",
        "minimum_completed_outer": 5,
        "checkpoint_root": "v371_to_c_xp_io_outer5_v68_boundary_20260826",
        "target_campaign": "v372_to_c_xp_io_empty_wave_skip_20260826_attempt001",
    },
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


def run(command: list[str], timeout: float = 900.0) -> str:
    completed = subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True,
        check=False, timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout={completed.stdout[-2000:]}\nstderr={completed.stderr[-4000:]}"
        )
    return completed.stdout.strip()


def systemd_active(unit: str) -> bool:
    completed = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", unit],
        cwd=ROOT, check=False, timeout=15,
    )
    return completed.returncode == 0


def transition(scene: str, config: Mapping[str, Any]) -> dict[str, Any] | None:
    source_root = HISTORY / str(config["source_run"])
    source_state_path = source_root / "run_state.json"
    state = load(source_state_path)
    if (
        state.get("algorithm") != "C"
        or state.get("async_scene") != scene
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError(f"{scene} source state differs")
    if (
        state.get("status") == "COMPLETE"
        and state.get("stage") == "FINISHED"
        and bool((state.get("converged") or {}).get(scene))
    ):
        return {
            "status": "SOURCE_COMPLETED_BEFORE_TRANSITION",
            "campaign": config["source_campaign"],
            "source_state": str(source_state_path),
        }
    if state.get("status") != "RUNNING":
        raise RuntimeError(f"{scene} source became terminal: {state.get('status')}")
    completed_outer = int(state.get("outer_attempt") or 0)
    stage = str(state.get("stage") or "")
    if (
        completed_outer < int(config["minimum_completed_outer"])
        or "MEASURING" in stage
    ):
        return None

    unit = str(config["source_unit"])
    if systemd_active(unit):
        run(["systemctl", "--user", "stop", unit], timeout=60)
    if systemd_active(unit):
        raise RuntimeError(f"{scene} source unit did not stop")

    checkpoint_root = HISTORY / str(config["checkpoint_root"])
    run([
        str(PYTHON), str(BUILDER), "--run-root", str(source_root),
        "--output-dir", str(checkpoint_root),
    ])
    checkpoint = checkpoint_root / "prepared_binding.json"
    quarantine_root = checkpoint_root / "replay_quarantine"
    run([
        str(PYTHON), str(QUARANTINE), "--checkpoint", str(checkpoint),
        "--output-dir", str(quarantine_root),
    ])
    target_campaign = str(config["target_campaign"])
    launch_output = run([
        str(PYTHON), str(LAUNCHER), "--run-id", target_campaign,
        "--checkpoint", str(checkpoint),
        "--runtime-preflight", str(AUTHORITY / "transition_preflight.json"),
        "--prepared-binding", str(AUTHORITY / "prepared_binding.json"),
        "--benchmark-accounts", str(AUTHORITY / "benchmark_accounts_binding.json"),
        "--fresh-r0", str(AUTHORITY / "fresh_r0_binding.json"),
        "--replay-quarantine", str(quarantine_root / "replay_quarantine.json"),
        "--resume-search", "--scene", scene,
        "--startup-timeout-seconds", "600",
    ], timeout=660)
    return {
        "status": "V68_LAUNCHED",
        "campaign": target_campaign,
        "completed_outer_preserved": completed_outer,
        "source_stage_at_stop": stage,
        "source_unit_stopped": unit,
        "checkpoint": str(checkpoint),
        "checkpoint_sha256": sha(checkpoint),
        "replay_quarantine": str(quarantine_root / "replay_quarantine.json"),
        "launcher_output": launch_output,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    }


def replace_chain(results: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    run(["systemctl", "--user", "stop", "mysql-four-scene-top4-abc-chain-v367.service"], timeout=60)
    unit = "mysql-four-scene-top4-abc-chain-v373.service"
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        "--description=MySQL four-scene Top4 C-B-A independent chain v373",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{LOG_ROOT / 'v373_to_four_scene_top4_abc_chain_20260826.stdout.log'}",
        f"--property=StandardError=append:{LOG_ROOT / 'v373_to_four_scene_top4_abc_chain_20260826.stderr.log'}",
        "/usr/bin/taskset", "-c", "240-241,496-497", str(PYTHON), str(CHAIN),
        "--pipeline-id", "v373_to_four_scene_top4_abc_chain_20260826",
        "--c-job-campaign", "v365_to_c_job_outer8_gate_recovery_20260826_attempt001",
        "--c-tpch-campaign", "v366_to_c_tpch_outer8_gate_recovery_20260826_attempt001",
        "--c-xp-cpu-campaign", str(results["hybench_xp_cpu"]["campaign"]),
        "--c-xp-io-campaign", str(results["hybench_xp_io"]["campaign"]),
        "--b-campaign", "v320_to_b_qps_rejection_four_scene_top4_20260826_attempt001",
        "--a-campaign", "v321_to_a_four_scene_top4_20260826_attempt001",
        "--poll-seconds", "15",
    ]
    output = run(command, timeout=60)
    return {"unit": unit, "launch_output": output}


def main() -> int:
    results: dict[str, dict[str, Any]] = {}
    while len(results) != len(SCENES):
        for scene, config in SCENES.items():
            if scene in results:
                continue
            result = transition(scene, config)
            if result is not None:
                results[scene] = result
                atomic(STATE_PATH, {
                    "status": "TRANSITIONING",
                    "updated_at": now(),
                    "formal_scope": "mysql_only",
                    "cross_engine_comparison_prohibited": True,
                    "results": results,
                })
        time.sleep(0.5)
    chain = replace_chain(results)
    atomic(STATE_PATH, {
        "status": "COMPLETE",
        "completed_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "results": results,
        "chain_monitor": chain,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
