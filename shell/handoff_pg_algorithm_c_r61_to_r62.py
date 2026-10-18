#!/usr/bin/env python3
"""One-shot round-boundary handoff from r61 to the fresh r62 campaign."""

from __future__ import annotations

import json
import hashlib
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Dict


R61 = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260813_algorithm_c_cached_tier_redecision_clean_r61"
)
R62 = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260813_algorithm_c_same_plan_numeric_clean_r62"
)
LANES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
TARGETS = {
    "job_memory": "memory",
    "tpch_memory": "memory",
    "xp_cpu": "cpu",
    "xp_io": "io",
}
PIDS = (4084421, 4084424, 4084427, 4084430)
SUPERVISOR_PID = 4084433
ROUND_TERMINALS = {
    "algorithm_c_round_accepted",
    "algorithm_c_round_rejected",
    "algorithm_c_round_rejected_after_full_range_basin_escape",
    "algorithm_c_round_rejected_by_reused_outer_evidence",
    "algorithm_c_candidate_space_exhausted_clean_stop",
    "algorithm_c_outer_pruning_candidate_space_exhausted",
}
RECEIPT = R62 / "r61_to_r62_handoff_receipt.json"
LAUNCHER = Path(
    "/home/anonymous/program/resource_adaptive/shell/"
    "launch_pg_algorithm_c_nomutex_cohort_r62.sh"
)
PYTHON = Path("/home/anonymous/miniconda3/envs/resource_adaptive/bin/python")
SEED_EXPORTER = Path(
    "/home/anonymous/program/resource_adaptive/shell/"
    "export_algorithm_c_historical_valid_replay_seed.py"
)


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def round_complete(lane: str, round_index: int = 8) -> bool:
    path = R61 / lane / "algorithm_c.jsonl"
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if (
                int(row.get("round_index") or 0) == round_index
                and row.get("event") in ROUND_TERMINALS
            ):
                return True
    return False


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def write_receipt(payload: Dict[str, Any]) -> None:
    temporary = RECEIPT.with_name(f".{RECEIPT.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, RECEIPT)


def write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_historical_seeds() -> Dict[str, Any]:
    """Export audited valid outcomes and configure fresh r62 before launch."""
    prepared: Dict[str, Any] = {}
    for lane in LANES:
        fresh_lane = R62 / lane
        for forbidden in ("algorithm_c_state.json", "algorithm_c.jsonl"):
            if (fresh_lane / forbidden).exists():
                raise RuntimeError(f"r62 is not fresh: {fresh_lane / forbidden}")
        source_config_path = R61 / lane / "optimizer_config.json"
        fresh_config_path = fresh_lane / "optimizer_config.json"
        source_config = read_json(source_config_path)
        fresh_config = read_json(fresh_config_path)
        source_algorithm = source_config.get("algorithm_c")
        fresh_algorithm = fresh_config.get("algorithm_c")
        if not isinstance(source_algorithm, dict) or not isinstance(
            fresh_algorithm,
            dict,
        ):
            raise RuntimeError(f"algorithm_c config missing for {lane}")
        experience_path = Path(
            str(source_algorithm.get("experience_table_path") or "")
        )
        seed_path = fresh_lane / "historical_valid_replay_seed.json"
        export = subprocess.run(
            [
                str(PYTHON),
                str(SEED_EXPORTER),
                "--state",
                str(R61 / lane / "algorithm_c_state.json"),
                "--log",
                str(R61 / lane / "algorithm_c.jsonl"),
                "--config",
                str(source_config_path),
                "--experience-table",
                str(experience_path),
                "--source-campaign",
                str(R61),
                "--target",
                TARGETS[lane],
                "--output",
                str(seed_path),
            ],
            cwd="/home/anonymous/program/resource_adaptive",
            text=True,
            capture_output=True,
            timeout=120.0,
            check=False,
        )
        if export.returncode != 0:
            raise RuntimeError(
                f"historical seed export failed for {lane}: "
                f"{export.stderr.strip()}"
            )
        fresh_algorithm["historical_valid_replay_seed_path"] = str(seed_path)
        write_json_atomic(fresh_config_path, fresh_config)
        seed = read_json(seed_path)
        prepared[lane] = {
            "seed_path": str(seed_path),
            "seed_sha256": sha256_file(seed_path),
            "seed_payload_sha256": seed.get("payload_sha256"),
            "seed_row_count": int(seed.get("row_count") or 0),
            "fresh_config_sha256": sha256_file(fresh_config_path),
            "accepted_state_inherited": False,
            "physical_workload_invocations_inherited": 0,
            "valid_replays_inherited": 0,
        }

    manifest_path = R62 / "fresh_campaign_manifest.json"
    manifest = read_json(manifest_path)
    manifest["contract"] = "algorithm_c_fresh_config_plus_outer_replay_seed_v2"
    manifest["historical_valid_replay_seeds"] = prepared
    manifest["accepted_state_inherited"] = False
    manifest["ranges_inherited"] = False
    manifest["gradient_state_inherited"] = False
    manifest["technical_failures_inherited"] = False
    manifest["qps_outer_only"] = True
    scenes = manifest.get("scenes")
    if isinstance(scenes, dict):
        for lane, evidence in prepared.items():
            if isinstance(scenes.get(lane), dict):
                scenes[lane]["config_semantically_identical"] = False
                scenes[lane]["config_change_scope"] = (
                    "historical_valid_replay_seed_path_only"
                )
                scenes[lane]["config_sha256"] = evidence[
                    "fresh_config_sha256"
                ]
    write_json_atomic(manifest_path, manifest)
    return prepared


def main() -> int:
    if RECEIPT.exists():
        raise RuntimeError(f"handoff receipt already exists: {RECEIPT}")
    started = time.time()
    deadline = started + 3600.0
    while time.time() < deadline:
        if all(round_complete(lane) for lane in LANES):
            break
        if not all(process_alive(pid) for pid in PIDS):
            raise RuntimeError("r61 controller exited before the round-8 boundary")
        time.sleep(5.0)
    else:
        raise TimeoutError("r61 round-8 boundary was not reached within one hour")

    for pid in PIDS:
        os.kill(pid, signal.SIGTERM)
    stop_deadline = time.time() + 1800.0
    while time.time() < stop_deadline and any(process_alive(pid) for pid in PIDS):
        time.sleep(5.0)
    if any(process_alive(pid) for pid in PIDS):
        raise TimeoutError("r61 clean stop did not complete within 30 minutes")

    clean_states: Dict[str, Any] = {}
    for lane in LANES:
        state = read_json(R61 / lane / "algorithm_c_state.json")
        if state.get("phase") != "stopped_by_signal_clean":
            raise RuntimeError(f"{lane} lacks stopped_by_signal_clean state")
        receipt = state.get("signal_stop_clean_receipt")
        if not isinstance(receipt, dict) or receipt.get("passed") is not True:
            raise RuntimeError(f"{lane} lacks a passing clean-stop receipt")
        clean_states[lane] = {
            "accepted": int(state.get("accepted") or 0),
            "round_index": int(state.get("round_index") or 0),
            "technical_failures": int(state.get("technical_failures") or 0),
            "phase": state.get("phase"),
            "signal_stop_clean_receipt": receipt,
        }

    if process_alive(SUPERVISOR_PID):
        os.kill(SUPERVISOR_PID, signal.SIGTERM)
    historical_seeds = prepare_historical_seeds()
    launch = subprocess.run(
        [str(LAUNCHER)],
        cwd="/home/anonymous/program/resource_adaptive",
        text=True,
        capture_output=True,
        timeout=60.0,
        check=False,
    )
    if launch.returncode != 0:
        raise RuntimeError(
            f"r62 launcher failed rc={launch.returncode}: {launch.stderr.strip()}"
        )
    payload = {
        "contract": "algorithm_c_round_boundary_clean_handoff_v1",
        "started_at_epoch_s": started,
        "completed_at_epoch_s": time.time(),
        "r61_round_boundary": 8,
        "r61_clean_states": clean_states,
        "r62_campaign": str(R62),
        "r62_fresh_state": True,
        "r62_launcher_stdout": launch.stdout.splitlines(),
        "r62_launcher_stderr": launch.stderr.splitlines(),
        "technical_failures_inherited": False,
        "accepted_state_inherited": False,
        "predictor_artifacts_changed": False,
        "historical_valid_replay_seeds": historical_seeds,
        "historical_results_redecided_after_fresh_baseline": True,
    }
    write_receipt(payload)
    print(json.dumps(payload, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
