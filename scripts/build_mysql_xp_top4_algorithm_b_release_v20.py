#!/usr/bin/env python3
"""Freeze CPU-B v30 with the 300-second Java drain runner."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v29"
OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v30"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v21.py"
LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_b_cpu_rank13_continuation_v8.py"
RUNNER = (
    ROOT / "config/formal/frozen_mysql_async_outer_20260820_v38/runtime"
    / "hybench-adaptive-runner-v4.jar"
)
RUNNER_BUILDER = ROOT / "scripts/build_mysql_hybench_adaptive_runner_v4.py"
RUNNER_SOURCE = ROOT / "scripts/java/com/hybench/HyBenchAdaptiveMain.java"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def binding(path: Path) -> dict[str, str]:
    return {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError("fresh Algorithm-B v30 release directory required")
    if sha(RUNNER) != "c8f8e23fe7c195e21b5cd3e59a37ce7bb78742761edbea66f011d98c583febe3":
        raise RuntimeError("HyBench v4 runner binding differs")
    OUTPUT.mkdir(parents=True, mode=0o700)
    coordination = load(SOURCE / "coordination_contract.json")
    atomic(OUTPUT / "coordination_contract.json", coordination)
    contract = load(SOURCE / "algorithm_b_top4_contract.json")
    contract["created_at"] = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ).astimezone().isoformat()
    bindings = contract["source_bindings"]
    bindings["controller"] = binding(CONTROLLER)
    bindings["scene_launcher"] = binding(LAUNCHER)
    bindings["coordination_contract"] = binding(OUTPUT / "coordination_contract.json")
    bindings["hybench_adaptive_runner_300s"] = binding(RUNNER)
    bindings["hybench_adaptive_runner_builder"] = binding(RUNNER_BUILDER)
    bindings["hybench_adaptive_runner_predecessor_source"] = binding(RUNNER_SOURCE)
    contract["graceful_quiescence_seconds"] = 300
    contract["technical_continuation"]["java_runner_release"] = "v4_300_second_cap"
    contract["technical_continuation"]["failed_premeasurement_attempts_excluded"] = [
        "v247_to_b_top4_fresh_20260826_attempt026_xp_cpu",
        "v254_to_b_top4_fresh_20260826_attempt027_xp_cpu",
    ]
    atomic(OUTPUT / "algorithm_b_top4_contract.json", contract)
    print(json.dumps({
        "status": "FROZEN",
        "contract": str(OUTPUT / "algorithm_b_top4_contract.json"),
        "sha256": sha(OUTPUT / "algorithm_b_top4_contract.json"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
