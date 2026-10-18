#!/usr/bin/env python3
"""Launch the sealed Algorithm B 2+2+1 continuation as user services."""

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
    / "scripts/run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v9.py"
)
OBSERVER = ROOT / "scripts/observe_frozen_external_pg_client_affinity_v1.py"
CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_b_parallel_221_20260818_v9"
    / "algorithm_b_parallel_221_contract.json"
)
RESUME_SOURCE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v6_formal_independent_outer_plan_resume_v63_20260818_attempt001"
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
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def unit_fields(unit: str) -> dict[str, str]:
    shown = subprocess.run(
        [
            "systemctl", "--user", "show", unit,
            "-p", "ActiveState", "-p", "SubState", "-p", "MainPID",
            "-p", "Result", "-p", "ExecMainStatus",
        ],
        text=True, capture_output=True, check=True, timeout=10,
    ).stdout
    return dict(line.split("=", 1) for line in shown.splitlines() if "=" in line)


def validate_contract() -> dict[str, Any]:
    if not CONTRACT.is_file() or CONTRACT.is_symlink():
        raise RuntimeError(f"sealed parallel scheduler contract absent: {CONTRACT}")
    contract = load(CONTRACT)
    if (
        contract.get("schema_version")
        != "mysql_four_scene_algorithm_b_parallel_221_contract_v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("active_scenes") != SCENES
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or int(contract.get("candidate_parallelism") or 0) != 2
    ):
        raise RuntimeError("parallel scheduler contract differs")
    for name, row in (contract.get("source_bindings") or {}).items():
        path = Path(str((row or {}).get("path") or ""))
        if not path.is_absolute():
            path = ROOT / path
        if (
            not path.is_file()
            or path.is_symlink()
            or (row or {}).get("sha256") != sha(path)
        ):
            raise RuntimeError(f"parallel scheduler source binding differs: {name}")
    return contract


def stop_unit(unit: str) -> None:
    subprocess.run(
        ["systemctl", "--user", "stop", unit],
        check=False, timeout=30, text=True, capture_output=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--resume-from", type=Path, default=RESUME_SOURCE)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run id")
    if args.resume_from.expanduser().resolve() != RESUME_SOURCE.resolve():
        raise RuntimeError("parallel continuation resume authority differs")
    output = HISTORY / args.run_id
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh Algorithm B output required: {output}")
    if not PYTHON.is_file() or PYTHON.is_symlink():
        raise RuntimeError(f"sealed Python interpreter absent: {PYTHON}")
    contract = validate_contract()
    LOG_ROOT.mkdir(parents=True, exist_ok=True)

    unit = f"mysql-four-scene-algb-{args.run_id}.service"
    observer_unit = f"mysql-four-scene-algb-affinity-{args.run_id}.service"
    stdout_path = LOG_ROOT / f"{args.run_id}.stdout.log"
    stderr_path = LOG_ROOT / f"{args.run_id}.stderr.log"
    observer_log = LOG_ROOT / f"{args.run_id}.external_pg_affinity.jsonl"
    observer_stdout = LOG_ROOT / f"{args.run_id}.affinity.stdout.log"
    observer_stderr = LOG_ROOT / f"{args.run_id}.affinity.stderr.log"

    observer_command = [
        "systemd-run", "--user",
        f"--unit={observer_unit.removesuffix('.service')}",
        "--description=MySQL Algorithm B parallel 221 affinity observer",
        f"--property=WorkingDirectory={ROOT}",
        "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{observer_stdout}",
        f"--property=StandardError=append:{observer_stderr}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET, str(PYTHON),
        str(OBSERVER), "--formal-unit", unit,
        "--log", str(observer_log), "--interval-seconds", "0.25",
        "--startup-grace-seconds", "120",
    ]
    started_observer = subprocess.run(
        observer_command, cwd=ROOT, text=True, capture_output=True,
        check=False, timeout=30,
    )
    if started_observer.returncode != 0:
        raise RuntimeError(
            "parallel affinity observer launch failed: "
            + started_observer.stderr[-1000:]
        )

    controller_command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        "--description=MySQL four-scene Algorithm B parallel 2+2+1",
        f"--property=WorkingDirectory={ROOT}",
        "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout_path}",
        f"--property=StandardError=append:{stderr_path}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET, str(PYTHON),
        str(CONTROLLER), "--run-id", args.run_id,
        "--resume-from", str(RESUME_SOURCE),
    ]
    started = subprocess.run(
        controller_command, cwd=ROOT, text=True, capture_output=True,
        check=False, timeout=30,
    )
    if started.returncode != 0:
        stop_unit(observer_unit)
        raise RuntimeError(
            "parallel Algorithm B launch failed: " + started.stderr[-1000:]
        )

    state_path = output / "run_state.json"
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    state: dict[str, Any] | None = None
    fields: dict[str, str] = {}
    while time.monotonic() < deadline:
        fields = unit_fields(unit)
        if fields.get("ActiveState") in {"inactive", "failed"}:
            stop_unit(observer_unit)
            raise RuntimeError(
                f"parallel Algorithm B exited during startup: {fields}; "
                f"stderr={stderr_path}"
            )
        if state_path.is_file():
            state = load(state_path)
            if (
                state.get("status") == "RUNNING"
                and state.get("algorithm") == "B"
                and state.get("formal_scope") == "mysql_only"
                and state.get("formal_candidate_batches")
                == [[1, 2], [3, 4], [5]]
                and int(state.get("candidate_parallelism") or 0) == 2
            ):
                break
        time.sleep(0.25)
    else:
        stop_unit(unit)
        stop_unit(observer_unit)
        raise RuntimeError("parallel Algorithm B startup timed out")

    observer_fields = unit_fields(observer_unit)
    if observer_fields.get("ActiveState") != "active":
        stop_unit(unit)
        stop_unit(observer_unit)
        raise RuntimeError("parallel affinity observer is not active")

    manifest_path = LOG_ROOT / f"{args.run_id}.launch_manifest.json"
    atomic(manifest_path, {
        "schema_version": "mysql_algorithm_b_parallel_221_launch_manifest_v1",
        "status": "LAUNCHED",
        "launched_at": datetime.now(timezone.utc).isoformat(),
        "run_id": args.run_id,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "active_scenes": SCENES,
        "candidate_batches": [[1, 2], [3, 4], [5]],
        "candidate_parallelism": 2,
        "controller_unit": unit,
        "controller_main_pid": int(fields.get("MainPID") or 0),
        "observer_unit": observer_unit,
        "observer_main_pid": int(observer_fields.get("MainPID") or 0),
        "controller_path": str(CONTROLLER),
        "controller_sha256": sha(CONTROLLER),
        "contract_path": str(CONTRACT),
        "contract_sha256": sha(CONTRACT),
        "resume_source": str(RESUME_SOURCE),
        "output": str(output),
        "run_state": str(state_path),
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "affinity_log": str(observer_log),
        "contract_release": contract.get("release_version"),
    })
    print(json.dumps({
        "status": "LAUNCHED",
        "run_id": args.run_id,
        "unit": unit,
        "pid": int(fields.get("MainPID") or 0),
        "stage": None if state is None else state.get("stage"),
        "candidate_batches": [[1, 2], [3, 4], [5]],
        "manifest": str(manifest_path),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
