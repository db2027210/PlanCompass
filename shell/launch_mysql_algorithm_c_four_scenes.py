#!/usr/bin/env python3
"""Launch the four frozen mysql-only Algorithm C preparation lanes."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = ROOT / "history/mysql_sgd_env_agents_20260723/four_scene_launcher"
BUNDLE_CANDIDATES = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v43",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v42",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v41",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v40",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v39",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v38",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v37",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v36",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v35",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v34",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v33",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v32",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v31",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v30",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v29",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v28",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v27",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v26",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v25",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v24",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v23",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v22",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v21",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v20",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v19",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v18",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v17",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v16",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v15",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v14",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v13",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v12",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v11",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v10",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v9",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v8",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v7",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v6",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v5",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v4",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260726_v3",
    ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260726_v2",
)
SCENARIOS = ("job_memory", "hybench_xp_io", "tpch_memory", "tpcc_cpu")
REQUIRED_CONTRACT = {
    "global_real_replay_top_k_hard_max": 5,
    "target_accepted_shrinks": 10,
    "qps_role": "outer_acceptance_only",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def frozen_manifest(path: Path | None = None) -> tuple[Path, dict[str, Any]]:
    selected = path
    if selected is None:
        selected = next((candidate for candidate in BUNDLE_CANDIDATES if (candidate / "manifest.json").is_file()), None)
    if selected is None:
        raise FileNotFoundError("no frozen four-scene manifest (v2 or v1)")
    manifest_path = selected if selected.name == "manifest.json" else selected / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"frozen manifest missing: {manifest_path}")
    return manifest_path.parent, load_json(manifest_path)


def validate_contract(manifest: dict[str, Any]) -> None:
    contract = manifest.get("method_contract")
    if not isinstance(contract, dict):
        raise ValueError("frozen manifest lacks method_contract")
    for key, expected in REQUIRED_CONTRACT.items():
        if contract.get(key) != expected:
            raise ValueError(f"Algorithm C contract mismatch: {key}={contract.get(key)!r}")
    acceptance = str(contract.get("acceptance", ""))
    if "qps_strictly_up" not in acceptance or "non_target" not in acceptance or "0.8" not in acceptance:
        raise ValueError("Algorithm C contract must require QPS outer acceptance and non-target <0.8")
    if manifest.get("formal_scope") != "mysql_only":
        raise ValueError("four-scene launcher requires formal_scope=mysql_only")
    specs = manifest.get("launch_specs")
    if not isinstance(specs, dict) or set(specs) != set(SCENARIOS):
        raise ValueError("frozen manifest launch_specs must cover exactly four scenarios")
    for scenario in SCENARIOS:
        spec = specs[scenario]
        if not isinstance(spec, dict) or not spec.get("host") or not spec.get("command"):
            raise ValueError(f"invalid frozen launch spec: {scenario}")


def launch_specs(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    validate_contract(manifest)
    return {name: dict(manifest["launch_specs"][name]) for name in SCENARIOS}


def pid_live(pid: Any) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 1:
        return False
    try:
        os.kill(value, 0)
    except OSError:
        return False
    return True


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def route_command(host: str, command: str) -> str:
    # All frozen entry points live on compute00. The entry point itself owns any
    # SSH hop to the target database lane.
    if host in {"compute00", "node01_via_compute00", "node02_via_compute00"}:
        return command
    raise ValueError(f"unsupported frozen host route: {host}")


def run_id(scenario: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"algorithm_c_four_scene_{scenario}_{stamp}"


def state_path(scenario: str, state_root: Path = STATE_ROOT) -> Path:
    return state_root / scenario / "run_state.json"


def load_state(path: Path) -> dict[str, Any]:
    try:
        return load_json(path)
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return {}


def acquire_singleton(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def launch_one(scenario: str, spec: dict[str, Any], manifest_path: Path, state_root: Path) -> dict[str, Any]:
    scenario_dir = state_root / scenario
    lock = acquire_singleton(scenario_dir / "singleton.lock")
    if lock is None:
        return {"scenario": scenario, "action": "singleton_busy", "status": "SKIPPED"}
    try:
        current_path = scenario_dir / "run_state.json"
        current = load_state(current_path)
        if pid_live(current.get("pid")) and current.get("status") in {"STARTING", "RUNNING"}:
            return {"scenario": scenario, "action": "active_skip", "status": current.get("status"), "pid": current.get("pid"), "run_id": current.get("run_id")}
        identifier = run_id(scenario)
        template = str(spec["command"])
        command = template.replace("{fresh_utc_run_id}", identifier)
        if "{fresh_utc_run_id}" in command:
            raise ValueError(f"unresolved run id placeholder: {scenario}")
        routed = route_command(str(spec["host"]), command)
        stdout_path = scenario_dir / f"{identifier}.stdout.log"
        stderr_path = scenario_dir / f"{identifier}.stderr.log"
        state = {
            "schema_version": "mysql_algorithm_c_four_scene_run_state_v1",
            "scenario": scenario,
            "machine": spec["host"],
            "run_id": identifier,
            "command": routed,
            "log_path": str(stdout_path),
            "stderr_log_path": str(stderr_path),
            "status": "STARTING",
            "next_action": f"{spec.get('stage', 'experiment')} running; waiting for fresh evidence",
            "pid": None,
            "bundle_manifest": str(manifest_path),
            "started_utc": datetime.now(timezone.utc).isoformat(),
        }
        stdout = stdout_path.open("ab", buffering=0)
        stderr = stderr_path.open("ab", buffering=0)
        try:
            process = subprocess.Popen(
                routed,
                cwd=ROOT,
                shell=True,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
                close_fds=True,
            )
        finally:
            stdout.close()
            stderr.close()
        state["pid"] = process.pid
        state["status"] = "RUNNING"
        atomic_write_json(current_path, state)
        return {"scenario": scenario, "action": "launched", "status": "RUNNING", "pid": process.pid, "run_id": identifier}
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def dry_plan(specs: dict[str, dict[str, Any]], manifest_path: Path) -> list[dict[str, Any]]:
    return [
        {
            "scenario": scenario,
            "machine": specs[scenario]["host"],
            "command": route_command(str(specs[scenario]["host"]), str(specs[scenario]["command"]).replace("{fresh_utc_run_id}", "<fresh_utc_run_id>")),
            "bundle_manifest": str(manifest_path),
            "would_start": True,
        }
        for scenario in SCENARIOS
        if scenario in specs
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--state-dir", type=Path, default=STATE_ROOT)
    parser.add_argument(
        "--scenario",
        action="append",
        choices=SCENARIOS,
        help="launch only the named scenario; repeat for more than one",
    )
    args = parser.parse_args(argv)
    bundle, manifest = frozen_manifest(args.manifest)
    specs = launch_specs(manifest)
    selected = tuple(args.scenario or SCENARIOS)
    if args.dry_run:
        selected_specs = {name: specs[name] for name in selected}
        print(json.dumps(dry_plan(selected_specs, bundle / "manifest.json"), ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    with ThreadPoolExecutor(max_workers=len(selected)) as pool:
        results = list(pool.map(lambda item: launch_one(item, specs[item], bundle / "manifest.json", args.state_dir), selected))
    for result in results:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if all(result.get("action") not in {"singleton_busy"} for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
