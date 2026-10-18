#!/usr/bin/env python3
"""Launch one fresh eight-worker XP Top4-B scene after terminal C."""

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
LOG_ROOT = HISTORY / "formal_launch_logs"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python").resolve()
CONTROLLER = (
    ROOT / "scripts/run_mysql_algorithm_b_xp_top4_cpu_a_slots_eight_worker_v1.py"
)
CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_xp_top4_algorithm_b_cpu_a_slots_eight_worker_20260828_v48"
    / "algorithm_b_top4_contract.json"
)
FREEZE = (
    ROOT / "config/formal/frozen_mysql_xp_eight_worker_cpu_a_slots_20260828_v6"
    / "xp_effective_resource_successor.json"
)
MANAGEMENT_CPUSET = "484-487"
SCENES = {
    "hybench_xp_cpu": {"suffix": "xp_cpu", "bootstrap_suffix": "cpu"},
    "hybench_xp_io": {"suffix": "xp_io", "bootstrap_suffix": "io"},
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
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def under_history(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if HISTORY.resolve() not in path.parents:
        raise argparse.ArgumentTypeError("path must be under formal history")
    return path


def validate_authority(path: Path, scene: str) -> Path:
    manifest_path = path / "expansion_manifest.json"
    manifest = load(manifest_path)
    prepared = load(path / "prepared_binding.json")
    accounts = load(path / "benchmark_accounts_binding.json")
    prefix = "xp_cpu" if scene.endswith("cpu") else "xp_io"
    expected = {f"{prefix}_w{i}" for i in range(1, 9)}
    ready = {
        str(row.get("worker_id")) for row in prepared.get("workers") or []
        if row.get("ready") is True and row.get("scene") == scene
    }
    if (
        manifest.get("status") != "PASS"
        or manifest.get("scene") != scene
        or manifest.get("physical_replay_parallelism") != 8
        or set(manifest.get("worker_ids") or []) != expected
        or manifest.get("freeze_sha256") != sha(FREEZE)
        or prepared.get("status") != "PREPARED"
        or prepared.get("freeze_sha256") != sha(FREEZE)
        or accounts.get("status") != "PASS"
        or accounts.get("prepared_binding_sha256")
        != sha(path / "prepared_binding.json")
        or ready != expected
    ):
        raise RuntimeError(f"{scene} current eight-worker authority is not ready")
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=sorted(SCENES), required=True)
    parser.add_argument("--c-state", type=under_history, required=True)
    parser.add_argument("--source-authority", type=under_history, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--pipeline-root", type=under_history, required=True)
    parser.add_argument("--authority-root", type=under_history, required=True)
    parser.add_argument("--unit", required=True)
    args = parser.parse_args()
    scene = args.scene
    suffix = SCENES[scene]["suffix"]
    c_state = load(args.c_state)
    if not (
        c_state.get("algorithm") == "C"
        and c_state.get("async_scene") == scene
        and c_state.get("status") == "COMPLETE"
        and c_state.get("stage") == "FINISHED"
        and not c_state.get("error")
        and (c_state.get("converged") or {}).get(scene) is True
    ):
        raise RuntimeError(f"{scene} Algorithm C is not converged")
    if (
        re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", args.run_id) is None
        or not args.run_id.endswith(f"_{suffix}")
    ):
        parser.error("unsafe scene-suffixed run-id")
    if not args.unit.endswith(".service") or "/" in args.unit:
        parser.error("unit must be a .service basename")
    authority_manifest = validate_authority(args.authority_root, scene)
    output = HISTORY / args.run_id
    receipt = args.pipeline_root / f"{suffix}.launch.json"
    if receipt.is_file():
        prior = load(receipt)
        state_path = Path(str(prior.get("state") or ""))
        if (
            prior.get("status") == "LAUNCHED" and state_path.is_file()
            and load(state_path).get("status") in {"RUNNING", "COMPLETE"}
        ):
            print(json.dumps(prior, sort_keys=True))
            return 0
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh B output required: {output}")
    bootstrap = HISTORY / (
        f"v161_to_b_top4_fresh_bootstrap_20260826_v40_"
        f"{SCENES[scene]['bootstrap_suffix']}"
    )
    if not (bootstrap / "bootstrap_manifest.json").is_file():
        raise RuntimeError(f"{scene} v40 fresh bootstrap absent")
    stdout = LOG_ROOT / f"{args.run_id}.stdout.log"
    stderr = LOG_ROOT / f"{args.run_id}.stderr.log"
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    command = [
        "systemd-run", "--user", f"--unit={args.unit.removesuffix('.service')}",
        f"--description=MySQL eight-worker Top4 Algorithm B {scene}",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,
        "/usr/bin/env",
        f"MYSQL_MIXED_FOUR_WORKER_AUTHORITY_ROOT={args.authority_root}",
        str(PYTHON), str(CONTROLLER), "--run-id", args.run_id,
        "--resume-from", str(bootstrap), "--scene", scene,
    ]
    launched = subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True,
        check=False, timeout=30,
    )
    if launched.returncode != 0:
        raise RuntimeError(f"{scene} Algorithm B launch failed: {launched.stderr[-3000:]}")
    state_path = output / "run_state.json"
    deadline = time.monotonic() + 900.0
    while time.monotonic() < deadline:
        shown = subprocess.run([
            "systemctl", "--user", "show", args.unit,
            "-p", "ActiveState", "-p", "MainPID",
        ], cwd=ROOT, text=True, capture_output=True, check=False, timeout=15).stdout
        if "ActiveState=failed" in shown or "ActiveState=inactive" in shown:
            raise RuntimeError(f"{scene} eight-worker B exited during startup: {shown}; {stderr}")
        if state_path.is_file():
            state = load(state_path)
            if (
                state.get("algorithm") == "B"
                and state.get("async_scene") == scene
                and state.get("status") == "RUNNING"
                and state.get("four_scene_wave_barrier") is False
                and state.get("other_scene_completion_required") is False
                and state.get("formal_candidate_batches") == [[1, 2, 3, 4, 5]]
                and int(state.get("candidate_parallelism") or 0) == 5
                and int(state.get("physical_replay_parallelism") or 0) == 8
            ):
                document = {
                    "schema_version": "mysql_algorithm_b_xp_top4_cpu_a_slots_eight_worker_scene_launch_v1",
                    "status": "LAUNCHED", "launched_at": now(), "scene": scene,
                    "formal_scope": "mysql_only",
                    "cross_engine_comparison_prohibited": True,
                    "algorithm_c_sequence_gate_path": str(args.c_state),
                    "algorithm_c_sequence_gate_sha256": sha(args.c_state),
                    "algorithm_c_state_or_result_credit": False,
                    "algorithm_a_candidate_or_qps_prior_used": False,
                    "authority_manifest_path": str(authority_manifest),
                    "authority_manifest_sha256": sha(authority_manifest),
                    "run_id": args.run_id, "unit": args.unit,
                    "output": str(output), "state": str(state_path),
                    "stdout": str(stdout), "stderr": str(stderr),
                    "contract_path": str(CONTRACT),
                    "contract_sha256": sha(CONTRACT),
                    "physical_replay_parallelism": 8,
                }
                atomic(receipt, document)
                print(json.dumps(document, ensure_ascii=False, sort_keys=True))
                return 0
        time.sleep(1.0)
    raise TimeoutError(f"{scene} eight-worker Algorithm B startup timed out")


if __name__ == "__main__":
    raise SystemExit(main())
