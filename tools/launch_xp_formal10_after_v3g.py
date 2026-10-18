#!/usr/bin/env python3
"""Fail-closed launcher for the isolated XP formal-10 optimizer."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any


XP_CPUS = set(range(32, 52)) | set(range(288, 308))
MYSQL_EXCLUDED_CPUS = set(range(184, 200)) | set(range(440, 456))


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_text(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def process_matches(root: Path) -> list[str]:
    completed = subprocess.run(
        ["pgrep", "-af", str(root)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    matches = []
    for line in completed.stdout.splitlines():
        fields = line.split(maxsplit=1)
        if not fields or not fields[0].isdigit() or int(fields[0]) == os.getpid():
            continue
        command = fields[1] if len(fields) == 2 else ""
        if "launch_xp_formal10_after_v3g.py" in command:
            continue
        if any(token in command for token in (
            "/repair/parameter_optimizer.py",
            "run_xp_repair_optimizer_with_project_modules.py",
            "candidate_stable_measurement_formal_v5.py",
            "com.hybench.HyBench",
        )):
            matches.append(line)
    return matches


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formal-dir", required=True, type=Path)
    parser.add_argument("--attempt-root", required=True, type=Path)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--python-bin", required=True, type=Path)
    parser.add_argument("--optimizer", required=True, type=Path)
    parser.add_argument("--repair-root", required=True, type=Path)
    parser.add_argument("--lock-path", required=True, type=Path)
    args = parser.parse_args()

    manifest_path = args.formal_dir / "formal_manifest.json"
    config_path = args.formal_dir / "config.json"
    manifest = load_json(manifest_path)
    config = load_json(config_path)
    if manifest.get("contract") not in {
        "xp_formal10_after_v3g_entry_gate_v1",
        "xp_formal10_accepted4_continuation_v1",
    }:
        raise RuntimeError("formal manifest contract mismatch")
    if manifest.get("output_config_sha256") != sha256(config_path):
        raise RuntimeError("formal config SHA mismatch")
    if XP_CPUS & MYSQL_EXCLUDED_CPUS:
        raise RuntimeError("XP CPU set overlaps the protected MySQL CPU set")

    protocol = config.get("formal_outer_protocol") or {}
    accepted_start = int(protocol.get("formal_search_accepted_count_start", -1))
    replay_start = int(protocol.get("formal_search_replay_count_start", -1))
    global_start = int(protocol.get("global_replay_ordinal_at_binding", -1))
    replay_cap = int(protocol.get("formal_search_maximum_replay_count", -1))
    resource_replay_cap = int(
        protocol.get("maximum_resource_qualified_real_replays_per_batch", -1)
    )
    if not 1 <= resource_replay_cap <= 5:
        raise RuntimeError(
            "maximum_resource_qualified_real_replays_per_batch is outside [1, 5]"
        )
    if replay_cap != replay_start + resource_replay_cap:
        raise RuntimeError(
            "formal replay cap does not equal start plus this batch candidate cap"
        )
    expected = {
        "formal_search_accepted_target": 10,
        "global_real_replay_top_k_hard_cap": 5,
        "maximum_resource_qualified_real_replays_per_batch": resource_replay_cap,
        "formal_search_attempt_safety_cap": replay_cap,
        "formal_search_maximum_replay_count": replay_cap,
    }
    for name, wanted in expected.items():
        if int(protocol.get(name, -1)) != wanted:
            raise RuntimeError(f"formal protocol mismatch: {name}")
    if config.get("_xp_next_validation_round", {}).get(
        "qps_used_for_inner_scoring"
    ) is not False:
        raise RuntimeError("QPS inner-scoring exclusion is not enabled")
    if config.get("_xp_next_validation_round", {}).get(
        "rejected_candidates_update_incumbent_range_or_accepted"
    ) is not False:
        raise RuntimeError("rejected-state mutation guard is not enabled")

    expected_counters = {
        "formal_search_accepted_counter_path": accepted_start,
        "formal_search_replay_counter_path": replay_start,
        "formal_startup_baseline_counter_path": int(
            protocol.get("formal_startup_baseline_count_start", 0)
        ),
        "global_replay_counter_path": global_start,
    }
    for name, expected_value in expected_counters.items():
        path = Path(protocol[name])
        actual_value = int(path.read_text(encoding="utf-8").strip())
        if actual_value != expected_value:
            raise RuntimeError(
                f"formal counter mismatch for {path}: "
                f"{actual_value} != {expected_value}"
            )

    matches = process_matches(args.attempt_root)
    if matches:
        raise RuntimeError(f"XP optimizer/workload already active: {matches}")

    args.lock_path.parent.mkdir(parents=True, exist_ok=True)
    with args.lock_path.open("a+", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("XP optimizer lock is already held") from exc
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    log_path = args.formal_dir / "formal_launcher.log"
    log_handle = log_path.open("ab", buffering=0)
    env = os.environ.copy()
    env.update({
        "BONECT_OPTIMIZER_GLOBAL_LOCK": "1",
        "BONECT_OPTIMIZER_LOCK_PATH": str(args.lock_path),
        "OPTIMIZER_CONFIG": str(config_path),
        "PYTHONPATH": f"{args.project_root}:{args.repair_root}",
        "PYTHONUNBUFFERED": "1",
        "RA_PG_FORMAL_SEARCH_MAX_REPLAYS": str(replay_cap),
        "RA_PG_FORMAL_SEARCH_ATTEMPT_SAFETY_CAP": str(replay_cap),
    })

    def child_setup() -> None:
        os.setsid()
        os.sched_setaffinity(0, XP_CPUS)

    process = subprocess.Popen(
        [str(args.python_bin), "-u", str(args.optimizer)],
        cwd=args.project_root,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        preexec_fn=child_setup,
        start_new_session=False,
    )
    log_handle.close()
    atomic_text(args.formal_dir / "formal_launcher.pid", f"{process.pid}\n")
    time.sleep(3)
    if process.poll() is not None:
        raise RuntimeError(
            f"XP formal optimizer exited during launch: rc={process.returncode}"
        )
    affinity = os.sched_getaffinity(process.pid)
    if affinity != XP_CPUS:
        os.killpg(process.pid, signal.SIGTERM)
        raise RuntimeError(f"XP affinity mismatch: {sorted(affinity)}")
    print(json.dumps({
        "formal_started": True,
        "pid": process.pid,
        "affinity": "32-51,288-307",
        "accepted_start": accepted_start,
        "accepted_target": 10,
        "formal_replay_start": replay_start,
        "formal_replay_batch_cap": replay_cap,
        "log": str(log_path),
    }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
