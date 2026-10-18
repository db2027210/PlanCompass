#!/usr/bin/env python3
"""Launch Algorithm B after isolating a formal Top-5 candidate timeout."""

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
CONTROLLER = (
    ROOT
    / "scripts/run_mysql_four_scene_algorithm_b_independent_resume_outer_rank3_v6.py"
)
AFFINITY_OBSERVER = (
    ROOT / "scripts/observe_frozen_external_pg_client_affinity_v1.py"
)
CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_b_independent_resume_outer_rank3_20260818_v7"
    / "algorithm_b_contract.json"
)
HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
MANAGEMENT_CPUSET = "240-241,496-497"
SCENES = ["job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io"]
STARTUP_TIMEOUT_SECONDS = 120.0


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


def unit_fields(unit: str) -> dict[str, str]:
    shown = subprocess.run(
        [
            "systemctl", "--user", "show", unit,
            "-p", "ActiveState", "-p", "SubState",
            "-p", "MainPID", "-p", "Result",
        ],
        text=True, capture_output=True, check=True, timeout=10,
    ).stdout
    return dict(line.split("=", 1) for line in shown.splitlines() if "=" in line)


def validate_contract() -> dict[str, Any]:
    if not CONTRACT.is_file() or CONTRACT.is_symlink():
        raise RuntimeError(f"sealed Algorithm B contract absent: {CONTRACT}")
    contract = load(CONTRACT)
    bindings = contract.get("source_bindings") or {}
    required = {
        "controller": CONTROLLER,
        "launcher": Path(__file__).resolve(),
        "external_pg_affinity_observer": AFFINITY_OBSERVER,
    }
    if (
        contract.get("schema_version")
        != "mysql_four_scene_algorithm_b_independent_resume_contract_v7"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("active_scenes") != SCENES
        or (contract.get("independence") or {}).get(
            "algorithm_c_used_only_as_launch_sequence_gate"
        ) is not True
        or (contract.get("independence") or {}).get(
            "algorithm_c_state_or_result_inputs"
        ) != []
        or (contract.get("technical_continuation") or {}).get(
            "completed_calibration_waves_reused"
        ) != 15
        or (contract.get("technical_continuation") or {}).get(
            "partial_failed_wave_results_reused"
        ) is not False
        or (contract.get("technical_continuation") or {}).get(
            "calibration_design_amended"
        ) is not True
        or (contract.get("technical_continuation") or {}).get(
            "all_calibration_wave_results_reused"
        ) is not True
        or (contract.get("technical_continuation") or {}).get(
            "failed_model_gate_output_reused"
        ) is not False
        or (contract.get("technical_continuation") or {}).get(
            "accepted_or_rejected_outer_state_reused"
        ) is not False
        or (contract.get("technical_continuation") or {}).get(
            "failure_phase"
        ) != "outer_001_rank_2_formal_timeout"
        or (contract.get("technical_continuation") or {}).get(
            "failed_outer_real_replay_started"
        ) is not True
        or (contract.get("technical_continuation") or {}).get(
            "valid_outer_batches_reused"
        ) != [1]
        or (contract.get("technical_continuation") or {}).get(
            "invalid_outer_batches_reused_as_measurement"
        ) is not False
    ):
        raise RuntimeError("independent Algorithm B frozen contract differs")
    for name, path in required.items():
        row = bindings.get(name) or {}
        if (
            not path.is_file()
            or path.is_symlink()
            or Path(str(row.get("path") or "")).resolve() != path.resolve()
            or row.get("sha256") != sha(path)
        ):
            raise RuntimeError(f"Algorithm B source binding differs: {name}")
    for name, row in bindings.items():
        path = Path(str((row or {}).get("path") or ""))
        if not path.is_absolute():
            path = ROOT / path
        if not path.is_file() or path.is_symlink() or (row or {}).get("sha256") != sha(path):
            raise RuntimeError(f"Algorithm B complete source binding differs: {name}")
    return contract


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--resume-from", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run id")
    output = HISTORY / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh Algorithm B output exists: {output}")
    if not PYTHON.is_file() or PYTHON.is_symlink():
        raise RuntimeError(f"resolved sealed Python interpreter absent: {PYTHON}")
    contract = validate_contract()
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    stdout_path = LOG_ROOT / f"{args.run_id}.stdout.log"
    stderr_path = LOG_ROOT / f"{args.run_id}.stderr.log"
    unit = f"mysql-four-scene-algb-{args.run_id}.service"
    observer_unit = f"mysql-four-scene-algb-affinity-{args.run_id}.service"
    observer_log = LOG_ROOT / f"{args.run_id}.external_pg_affinity.jsonl"
    observer_stdout = LOG_ROOT / f"{args.run_id}.affinity.stdout.log"
    observer_stderr = LOG_ROOT / f"{args.run_id}.affinity.stderr.log"

    observer_command = [
        "systemd-run", "--user",
        f"--unit={observer_unit.removesuffix('.service')}",
        "--description=MySQL independent Algorithm B external PG affinity observer",
        f"--property=WorkingDirectory={ROOT}",
        "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{observer_stdout}",
        f"--property=StandardError=append:{observer_stderr}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET, str(PYTHON),
        str(AFFINITY_OBSERVER), "--formal-unit", unit,
        "--log", str(observer_log), "--interval-seconds", "0.25",
        "--startup-grace-seconds", "120",
    ]
    started_observer = subprocess.run(
        observer_command, cwd=ROOT, text=True, capture_output=True,
        check=False, timeout=30,
    )
    if started_observer.returncode != 0:
        raise RuntimeError(
            "Algorithm B affinity observer launch failed: "
            + started_observer.stderr[-1000:]
        )
    observer_pid = 0
    observer_deadline = time.monotonic() + 10.0
    while time.monotonic() < observer_deadline:
        fields = unit_fields(observer_unit)
        observer_pid = int(fields.get("MainPID") or 0)
        if fields.get("ActiveState") == "active" and observer_pid > 0:
            break
        if fields.get("ActiveState") in {"inactive", "failed"}:
            raise RuntimeError(f"Algorithm B affinity observer exited: {fields}")
        time.sleep(0.25)
    else:
        subprocess.run(
            ["systemctl", "--user", "stop", observer_unit],
            check=False, timeout=30,
        )
        raise RuntimeError("Algorithm B affinity observer startup timed out")

    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        "--description=MySQL four-scene independent Algorithm B continuation",
        f"--property=WorkingDirectory={ROOT}",
        "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout_path}",
        f"--property=StandardError=append:{stderr_path}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET, str(PYTHON),
        str(CONTROLLER), "--run-id", args.run_id,
        "--resume-from", str(args.resume_from.resolve()),
    ]
    started = subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True,
        check=False, timeout=30,
    )
    if started.returncode != 0:
        subprocess.run(
            ["systemctl", "--user", "stop", observer_unit],
            check=False, timeout=30,
        )
        raise RuntimeError(
            "independent Algorithm B systemd launch failed: "
            + started.stderr[-1000:]
        )

    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    state_path = output / "run_state.json"
    state: dict[str, Any] | None = None
    main_pid = 0
    fields: dict[str, str] = {}
    while time.monotonic() < deadline:
        fields = unit_fields(unit)
        main_pid = int(fields.get("MainPID") or 0)
        if fields.get("ActiveState") in {"inactive", "failed"} or main_pid <= 0:
            subprocess.run(
                ["systemctl", "--user", "stop", observer_unit],
                check=False, timeout=30,
            )
            raise RuntimeError(
                f"independent Algorithm B controller exited during startup: {fields}; "
                f"stderr={stderr_path}"
            )
        if state_path.is_file():
            state = load(state_path)
            independence = state.get("independence_contract") or {}
            if (
                state.get("status") == "RUNNING"
                and state.get("algorithm") == "B"
                and state.get("formal_scope") == "mysql_only"
                and state.get("cross_engine_comparison_prohibited") is True
                and state.get("formal_evidence_eligible") is True
                and state.get("active_scenes") == SCENES
                and all(int(value) == 0 for value in (state.get("accepted_rounds") or {}).values())
                and independence.get("algorithm_c_checkpoint_read") is False
                and independence.get("algorithm_c_release_read") is False
                and independence.get("algorithm_c_incumbent_read") is False
                and independence.get("algorithm_c_interval_read") is False
                and independence.get("algorithm_c_measurement_read") is False
                and state.get("controller_source_sha256") == sha(CONTROLLER)
                and (state.get("technical_continuation") or {}).get(
                    "source_run_root"
                ) == str(args.resume_from.resolve())
                and (state.get("technical_continuation") or {}).get("kind")
                == "post_outer_candidate_timeout_isolation_resume_v6"
                and (state.get("technical_continuation") or {}).get(
                    "completed_calibration_waves_reused"
                ) == 15
                and (state.get("technical_continuation") or {}).get(
                    "partial_failed_wave_results_reused"
                ) is False
                and (state.get("technical_continuation") or {}).get(
                    "calibration_design_amended"
                ) is True
                and (state.get("technical_continuation") or {}).get(
                    "all_calibration_wave_results_reused"
                ) is True
                and (state.get("technical_continuation") or {}).get(
                    "failed_model_gate_output_reused"
                ) is False
                and (state.get("technical_continuation") or {}).get(
                    "accepted_rounds_inherited"
                ) is False
            ):
                break
        time.sleep(0.25)
    else:
        subprocess.run(["systemctl", "--user", "stop", unit], check=False, timeout=30)
        subprocess.run(
            ["systemctl", "--user", "stop", observer_unit],
            check=False, timeout=30,
        )
        raise RuntimeError("independent Algorithm B did not publish valid state in 120s")
    assert state is not None
    receipt = {
        "schema_version": "mysql_four_scene_algorithm_b_independent_launch_receipt_v1",
        "status": "STARTED",
        "verified_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "algorithm": "B",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "formal_evidence_eligible": True,
        "active_scenes": SCENES,
        "systemd_unit": unit,
        "pid": main_pid,
        "pid_alive_at_verification": True,
        "management_cpuset": MANAGEMENT_CPUSET,
        "controller_source_path": str(CONTROLLER),
        "controller_source_sha256": sha(CONTROLLER),
        "contract_path": str(CONTRACT),
        "contract_sha256": sha(CONTRACT),
        "affinity_observer_unit": observer_unit,
        "affinity_observer_pid": observer_pid,
        "affinity_observer_source_path": str(AFFINITY_OBSERVER),
        "affinity_observer_source_sha256": sha(AFFINITY_OBSERVER),
        "affinity_observer_log": str(observer_log),
        "rolling_window_dynamic_interference_gate": (
            contract["non_interference"]["rolling_window_gate"]
        ),
        "algorithm_c_state_or_result_inputs": [],
        "algorithm_b_technical_resume_source": str(args.resume_from.resolve()),
        "algorithm_b_accepted_or_rejected_outer_state_reused": False,
        "run_state_path": str(state_path),
        "run_state_sha256_at_verification": sha(state_path),
        "stage_at_verification": state.get("stage"),
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
    }
    atomic(output / "launch_receipt.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
