#!/usr/bin/env python3
"""Run one zero-credit native-XP stability wave on both v16 XP/I/O lanes."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mysql_workload_adapter import MySQLEndpoint, run_hybench
from scripts import run_mysql_four_scene_r0_v5 as r0


FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v16"
    / "four_scene_effective_resource_successor.json"
)
RESET = (
    ROOT / "history/mysql_four_scene_20260809"
    / "xp_reset_v16_after_512k_stability_failure_20260813T1252p0800"
    / "reset_binding.json"
)
BLOCKED_PREFLIGHT = (
    ROOT / "preflight/mysql_four_scene_20260809"
    / "runtime_preflight_v16_attempt2_20260813T1300p0800.json"
)
XP_IDS = ("xp_io_w1", "xp_io_w2")
FREEZE_SCHEMA = "mysql_disk2_four_scene_two_worker_effective_resource_successor_v16"
XP_IO_WRITE_BPS = 1048576
RESULT_SCHEMA = "mysql_xp_io_v16_stability_pilot_v1"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def inspect(worker: dict) -> dict:
    values = json.loads(subprocess.run(
        ["docker", "inspect", worker["container"]], capture_output=True,
        text=True, check=True, timeout=60,
    ).stdout)
    return values[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run-id")
    freeze, reset, preflight = load(FREEZE), load(RESET), load(BLOCKED_PREFLIGHT)
    frozen = {row["worker_id"]: row for row in freeze["workers"]}
    reset_rows = {row["worker_id"]: row for row in reset.get("workers") or []}
    if (
        freeze.get("schema_version")
        != FREEZE_SCHEMA
        or reset.get("status") != "PASS"
        or reset.get("freeze_sha256") != sha(FREEZE)
        or preflight.get("status") != "BLOCKED_FAIL_CLOSED"
        or preflight.get("formal_launch_authorized") is not False
        or preflight.get("freeze_sha256") != sha(FREEZE)
        or any(int(frozen[key]["io_write_bps"]) != XP_IO_WRITE_BPS for key in XP_IDS)
    ):
        raise RuntimeError("v16 stability pilot authority differs")
    workers = {
        key: {**frozen[key], **reset_rows[key]} for key in XP_IDS
    }
    before = {key: inspect(worker) for key, worker in workers.items()}
    for key, value in before.items():
        if (
            (value.get("State") or {}).get("Running") is not True
            or value.get("Id") != reset_rows[key].get("container_id")
            or int((reset_rows[key].get("blkio_authority") or {}).get("write_bps") or 0)
            != XP_IO_WRITE_BPS
        ):
            raise RuntimeError(f"{key} live identity/readback differs")
    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    runners = {
        key: r0.PinnedRunner(worker, output / key / "client_processes.jsonl")
        for key, worker in workers.items()
    }
    sampler = r0.CgroupSampler(list(workers.values()), output / "resource_samples.jsonl")
    started = time.monotonic()
    windows: dict[str, tuple[float, float]] = {}
    results: dict[str, dict] = {}
    failures: list[dict] = []

    def run_one(key: str) -> dict:
        worker = workers[key]
        user, password = r0._private_credential(worker)
        begin = time.monotonic()
        try:
            return run_hybench(
                "xp",
                MySQLEndpoint("127.0.0.1", int(worker["host_port"]),
                              "hybench_sf10x", user, password),
                output / key,
                hybench_home=r0.HYBENCH_HOME,
                duration_s=600,
                clients=8,
                extra_properties={"sf": "10x"},
                runtime_jar=r0.NATIVE_XP_JAR,
                dry_run=False,
                timeout_s=900,
                runner=runners[key],
            )
        finally:
            windows[key] = (begin, time.monotonic())

    sampler.start()
    try:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="xp-io-v16") as pool:
            futures = {pool.submit(run_one, key): key for key in XP_IDS}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    results[key] = future.result()
                    print(json.dumps({"worker_id": key, "workload": "PASS"}), flush=True)
                except BaseException as error:
                    failures.append({"worker_id": key, "error": f"{type(error).__name__}: {error}"})
                    print(json.dumps(failures[-1]), flush=True)
    finally:
        resources = sampler.finish(windows)
    after = {key: inspect(worker) for key, worker in workers.items()}
    gates = {}
    for key in XP_IDS:
        coverage = (results.get(key) or {}).get("workload_coverage") or {}
        gates[key] = {
            "coverage_complete": coverage.get("coverage_complete") is True,
            "qps": float(((results.get(key) or {}).get("metrics") or {}).get("qps") or 0),
            "io_fraction": max(
                float(resources[key]["read_fraction_of_cap"]),
                float(resources[key]["write_fraction_of_cap"]),
            ),
            "container_id_unchanged": before[key].get("Id") == after[key].get("Id"),
            "container_pid_unchanged": (before[key].get("State") or {}).get("Pid")
            == (after[key].get("State") or {}).get("Pid"),
            "running_after": (after[key].get("State") or {}).get("Running") is True,
        }
    elapsed = time.monotonic() - started
    passed = not failures and elapsed <= 900 and all(
        row["coverage_complete"] and row["qps"] > 0 and row["io_fraction"] >= 0.8
        and row["container_id_unchanged"] and row["container_pid_unchanged"]
        and row["running_after"] for row in gates.values()
    )
    document = {
        "schema_version": RESULT_SCHEMA,
        "status": "PASS" if passed else "PILOT_FAILED_CLOSED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "formal_evidence_eligible": False,
        "accepted_state_credit_prohibited": True,
        "accepted_rounds_credit": 0,
        "freeze_path": str(FREEZE),
        "freeze_sha256": sha(FREEZE),
        "reset_binding_path": str(RESET),
        "reset_binding_sha256": sha(RESET),
        "blocked_preflight_path": str(BLOCKED_PREFLIGHT),
        "blocked_preflight_sha256": sha(BLOCKED_PREFLIGHT),
        "native_xp_runtime_sha256": sha(r0.NATIVE_XP_JAR),
        "duration_seconds": 600,
        "elapsed_seconds": elapsed,
        "results": results,
        "resources": resources,
        "gates": gates,
        "failures": failures,
    }
    result = output / "pilot_result.json"
    result.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    result.with_name(result.name + ".sha256").write_text(
        f"{sha(result)}  {result.name}\n", encoding="ascii"
    )
    print(json.dumps({"status": document["status"], "result": str(result), "elapsed_seconds": elapsed}))
    return 0 if passed else 4


if __name__ == "__main__":
    raise SystemExit(main())
