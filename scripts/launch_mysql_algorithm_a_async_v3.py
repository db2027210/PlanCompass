#!/usr/bin/env python3
"""Launch four independent MySQL Algorithm A scenes after C completes."""

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
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python").resolve()
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_async_scene_v3.py"
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_async_20260822_v4"
    / "algorithm_a_contract.json"
)
HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
MANAGEMENT_CPUSET = "240-241,496-497"
SCENES = {
    "job_memory": "job",
    "tpch_sf4_memory": "tpch",
    "hybench_xp_cpu": "xp_cpu",
    "hybench_xp_io": "xp_io",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def regular_file(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.is_symlink():
        raise argparse.ArgumentTypeError(f"regular non-symlink file required: {path}")
    return path


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
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def unit_fields(unit: str) -> dict[str, str]:
    shown = subprocess.run(
        [
            "systemctl", "--user", "show", unit,
            "-p", "ActiveState", "-p", "SubState", "-p", "MainPID",
            "-p", "Result", "-p", "ExecMainStatus",
        ],
        cwd=ROOT, text=True, capture_output=True, check=True, timeout=15,
    ).stdout
    return dict(line.split("=", 1) for line in shown.splitlines() if "=" in line)


def stop_units(units: list[str]) -> None:
    for unit in units:
        subprocess.run(
            ["systemctl", "--user", "stop", unit], cwd=ROOT,
            text=True, capture_output=True, check=False, timeout=30,
        )


def validate_contract() -> dict[str, Any]:
    if not CONTRACT.is_file() or CONTRACT.is_symlink():
        raise RuntimeError(f"frozen Algorithm A contract absent: {CONTRACT}")
    contract = load(CONTRACT)
    handoff = contract.get("algorithm_c_handoff") or {}
    terminal_compatibility = handoff.get("per_scene_terminal_compatibility") or {}
    if (
        contract.get("release_version") != "v4"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("algorithm") != "A"
        or contract.get("method") != "exhaustive_binary_factorial_algorithm_a_v2"
        or contract.get("four_scene_wave_barrier") is not False
        or contract.get("per_scene_outer_state_machine") is not True
        or contract.get("accepted_target") != 10
        or contract.get("physical_replay_parallelism_per_scene") != 2
        or contract.get("post_convergence_stability_required") is not False
        or contract.get("post_target_physical_replays") != 0
        or (contract.get("postgresql_algorithm_a_flow") or {}).get(
            "qps_reference_mode"
        ) != "last_accepted"
        or (contract.get("postgresql_algorithm_a_flow") or {}).get(
            "incumbent_reference_replayed_every_round"
        ) is not False
        or (contract.get("postgresql_algorithm_a_flow") or {}).get(
            "incumbent_reference_refresh_after_consecutive_rejections"
        ) != 2
        or (contract.get("postgresql_algorithm_a_flow") or {}).get(
            "partially_failed_wave_valid_sibling_retained"
        ) is not True
        or terminal_compatibility.get("enabled") is not True
        or terminal_compatibility.get("legacy_aggregate_status")
        != "NON_CONVERGED"
        or terminal_compatibility.get("legacy_aggregate_completion_reason")
        != "candidate_space_or_max_rounds_exhausted"
        or terminal_compatibility.get("required_scene_decision_contract")
        != "pg_accepted_count_terminal_v1"
        or terminal_compatibility.get("original_c_state_mutation") is not False
    ):
        raise RuntimeError("MySQL Algorithm A launch contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding["path"])
        if (
            not path.is_file() or path.is_symlink()
            or sha(path) != str(binding["sha256"])
        ):
            raise RuntimeError(f"MySQL Algorithm A source binding differs: {path}")
    return contract


def _validate_c_scene_terminal(
    *, scene: str, path: Path, state: Mapping[str, Any]
) -> dict[str, Any]:
    """Normalize the legacy aggregate terminal label at a per-scene boundary.

    The independent C controller inherits PostgreSQL's four-scene final status
    aggregation.  Its selected scene can therefore be terminal while the three
    deliberately absent sibling entries keep the aggregate status at
    NON_CONVERGED.  Preserve the original state and require its final decision
    artifact to prove the selected scene reached the accepted-count terminal.
    """
    common = (
        state.get("algorithm") == "C"
        and state.get("async_scene") == scene
        and state.get("formal_scope") == "mysql_only"
        and state.get("cross_engine_comparison_prohibited") is True
        and state.get("stage") == "FINISHED"
        and int((state.get("accepted_rounds") or {}).get(scene) or 0) == 10
        and (state.get("converged") or {}).get(scene) is True
        and state.get("formal_evidence_eligible") is True
        and state.get("post_search_stability_confirmation") is False
        and state.get("other_scene_completion_required") is False
    )
    if not common:
        raise RuntimeError(f"Algorithm C is not terminal for A handoff: {scene}")

    if (
        state.get("status") == "COMPLETE"
        and state.get("completion_reason") == "accepted_count_reached_10"
    ):
        return {"terminal_mode": "canonical_complete"}

    if not (
        state.get("status") == "NON_CONVERGED"
        and state.get("completion_reason")
        == "candidate_space_or_max_rounds_exhausted"
    ):
        raise RuntimeError(f"Algorithm C terminal label is invalid: {scene}")

    outer_attempt = int(state.get("outer_attempt") or 0)
    decision_path = (
        path.parent / f"outer_{outer_attempt:03d}" / f"{scene}.decision.json"
    )
    if not decision_path.is_file() or decision_path.is_symlink():
        raise RuntimeError(f"Algorithm C terminal decision absent: {scene}")
    decision = load(decision_path)
    convergence = decision.get("interval_convergence") or {}
    if (
        decision.get("scene") != scene
        or int(decision.get("outer_attempt") or 0) != outer_attempt
        or decision.get("accepted") is not True
        or int(decision.get("accepted_rounds") or 0) != 10
        or decision.get("formal_evidence_eligible") is not True
        or convergence.get("contract") != "pg_accepted_count_terminal_v1"
        or convergence.get("converged") is not True
        or convergence.get("post_search_stability_confirmation") is not False
    ):
        raise RuntimeError(f"Algorithm C terminal decision differs: {scene}")
    return {
        "terminal_mode": "legacy_aggregate_label_normalized",
        "terminal_decision_path": str(decision_path),
        "terminal_decision_sha256": sha(decision_path),
    }


def validate_c_completion(paths: Mapping[str, Path]) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    for scene, path in paths.items():
        state = load(path)
        terminal = _validate_c_scene_terminal(scene=scene, path=path, state=state)
        evidence[scene] = {
            "state_path": str(path),
            "state_sha256": sha(path),
            "accepted_rounds": 10,
            "converged": True,
            "completed_at": state.get("completed_at"),
            **terminal,
        }
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--runtime-preflight", type=regular_file, required=True)
    parser.add_argument("--prepared-job", type=regular_file, required=True)
    parser.add_argument("--accounts-job", type=regular_file, required=True)
    parser.add_argument("--prepared-tpch", type=regular_file, required=True)
    parser.add_argument("--accounts-tpch", type=regular_file, required=True)
    parser.add_argument("--prepared-xp-cpu", type=regular_file, required=True)
    parser.add_argument("--accounts-xp-cpu", type=regular_file, required=True)
    parser.add_argument("--prepared-xp-io", type=regular_file, required=True)
    parser.add_argument("--accounts-xp-io", type=regular_file, required=True)
    parser.add_argument("--r0-job", type=regular_file, required=True)
    parser.add_argument("--r0-tpch", type=regular_file, required=True)
    parser.add_argument("--r0-xp-cpu", type=regular_file, required=True)
    parser.add_argument("--r0-xp-io", type=regular_file, required=True)
    parser.add_argument("--c-job-state", type=regular_file, required=True)
    parser.add_argument("--c-tpch-state", type=regular_file, required=True)
    parser.add_argument("--c-xp-cpu-state", type=regular_file, required=True)
    parser.add_argument("--c-xp-io-state", type=regular_file, required=True)
    parser.add_argument("--startup-timeout-seconds", type=float, default=300.0)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,100}", args.run_id) is None:
        parser.error("unsafe Algorithm A run id")
    if args.startup_timeout_seconds < 120.0:
        parser.error("startup timeout must be at least 120 seconds")
    contract = validate_contract()
    c_evidence = validate_c_completion({
        "job_memory": args.c_job_state,
        "tpch_sf4_memory": args.c_tpch_state,
        "hybench_xp_cpu": args.c_xp_cpu_state,
        "hybench_xp_io": args.c_xp_io_state,
    })
    r0 = {
        "job_memory": args.r0_job,
        "tpch_sf4_memory": args.r0_tpch,
        "hybench_xp_cpu": args.r0_xp_cpu,
        "hybench_xp_io": args.r0_xp_io,
    }
    prepared = {
        "job_memory": args.prepared_job,
        "tpch_sf4_memory": args.prepared_tpch,
        "hybench_xp_cpu": args.prepared_xp_cpu,
        "hybench_xp_io": args.prepared_xp_io,
    }
    accounts = {
        "job_memory": args.accounts_job,
        "tpch_sf4_memory": args.accounts_tpch,
        "hybench_xp_cpu": args.accounts_xp_cpu,
        "hybench_xp_io": args.accounts_xp_io,
    }
    outputs = {
        scene: HISTORY / f"{args.run_id}_{suffix}"
        for scene, suffix in SCENES.items()
    }
    if any(path.exists() or path.is_symlink() for path in outputs.values()):
        raise RuntimeError("fresh MySQL Algorithm A outputs are required")
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    units: dict[str, str] = {}
    logs: dict[str, dict[str, str]] = {}
    started_units: list[str] = []
    try:
        for scene, suffix in SCENES.items():
            lane_run_id = f"{args.run_id}_{suffix}"
            unit = f"mysql-alga-async-{lane_run_id}.service"
            stdout = LOG_ROOT / f"{lane_run_id}.stdout.log"
            stderr = LOG_ROOT / f"{lane_run_id}.stderr.log"
            command = [
                "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
                f"--description=MySQL Algorithm A independent scene {scene}",
                f"--property=WorkingDirectory={ROOT}",
                "--property=KillMode=control-group",
                f"--property=StandardOutput=append:{stdout}",
                f"--property=StandardError=append:{stderr}",
                "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,
                str(PYTHON), str(CONTROLLER),
                "--run-id", lane_run_id,
                "--scene", scene,
                "--runtime-preflight", str(args.runtime_preflight),
                "--prepared-binding", str(prepared[scene]),
                "--benchmark-accounts", str(accounts[scene]),
                "--fresh-r0", str(r0[scene]),
                "--output-dir", str(outputs[scene]),
            ]
            started = subprocess.run(
                command, cwd=ROOT, text=True, capture_output=True,
                check=False, timeout=30,
            )
            if started.returncode != 0:
                raise RuntimeError(
                    f"{scene} Algorithm A launch failed: {started.stderr[-1000:]}"
                )
            units[scene] = unit
            logs[scene] = {"stdout": str(stdout), "stderr": str(stderr)}
            started_units.append(unit)

        deadline = time.monotonic() + args.startup_timeout_seconds
        states: dict[str, dict[str, Any]] = {}
        fields: dict[str, dict[str, str]] = {}
        while time.monotonic() < deadline:
            ready = True
            for scene, unit in units.items():
                fields[scene] = unit_fields(unit)
                if fields[scene].get("ActiveState") in {"inactive", "failed"}:
                    raise RuntimeError(
                        f"{scene} Algorithm A exited during startup: "
                        f"{fields[scene]}; stderr={logs[scene]['stderr']}"
                    )
                path = outputs[scene] / "algorithm_a_state.json"
                if not path.is_file():
                    ready = False
                    continue
                states[scene] = load(path)
                if not (
                    states[scene].get("status") == "RUNNING"
                    and states[scene].get("algorithm") == "A"
                    and states[scene].get("async_scene") == scene
                    and states[scene].get("four_scene_wave_barrier") is False
                    and states[scene].get("other_scene_completion_required") is False
                ):
                    ready = False
            if ready and len(states) == len(SCENES):
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("MySQL Algorithm A asynchronous startup timed out")
    except BaseException:
        stop_units(started_units)
        raise

    manifest = LOG_ROOT / f"{args.run_id}.async_a.launch_manifest.json"
    atomic(manifest, {
        "schema_version": "mysql_algorithm_a_async_launch_manifest_v1",
        "status": "LAUNCHED",
        "launched_at": datetime.now(timezone.utc).isoformat(),
        "campaign_id": args.run_id,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm": "A",
        "method": contract["method"],
        "contract_path": str(CONTRACT),
        "contract_sha256": sha(CONTRACT),
        "algorithm_c_terminal_handoff": c_evidence,
        "four_scene_wave_barrier": False,
        "per_scene_outer_state_machine": True,
        "post_convergence_stability_required": False,
        "post_target_physical_replays": 0,
        "scenes": {
            scene: {
                "run_id": f"{args.run_id}_{SCENES[scene]}",
                "unit": units[scene],
                "main_pid": int(fields[scene].get("MainPID") or 0),
                "output": str(outputs[scene]),
                "state": str(outputs[scene] / "algorithm_a_state.json"),
                "stage": states[scene].get("stage"),
                "prepared_binding": str(prepared[scene]),
                "prepared_binding_sha256": sha(prepared[scene]),
                "benchmark_accounts": str(accounts[scene]),
                "benchmark_accounts_sha256": sha(accounts[scene]),
                "fresh_r0": str(r0[scene]),
                "fresh_r0_sha256": sha(r0[scene]),
                **logs[scene],
            }
            for scene in SCENES
        },
    })
    print(json.dumps({
        "status": "LAUNCHED",
        "campaign_id": args.run_id,
        "manifest": str(manifest),
        "units": units,
        "stages": {scene: states[scene].get("stage") for scene in SCENES},
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
