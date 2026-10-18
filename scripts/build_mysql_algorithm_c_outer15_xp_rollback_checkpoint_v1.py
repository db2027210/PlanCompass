#!/usr/bin/env python3
"""Rollback only Outer-15 XP acceptances after repeated incumbent hangs."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_outer_checkpoint_v6 as checkpointmod
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg
from scripts.recover_mysql_algorithm_c_outer15_technical_timeout_v3 import (
    XP_IDS, recorded_client_pids, sha, unit_fields,
)


XP_SCENES = {"hybench_xp_cpu", "hybench_xp_io"}
RETAINED_SCENES = {"job_memory", "tpch_sf4_memory"}


def load(path: Path) -> dict[str, Any]:
    return alg.load(path.resolve())


def accepted_choice(decision: Mapping[str, Any]) -> dict[str, Any]:
    rows = [
        dict(row) for row in (decision.get("evaluations") or [])
        if isinstance(row, Mapping)
        and (row.get("outer_decision") or {}).get("accepted") is True
    ]
    rows.sort(key=lambda row: int(row.get("rank") or 0))
    if not rows:
        raise RuntimeError("accepted XP candidate evidence required")
    return rows[0]


def resource_window(path: Path) -> dict[str, dict[str, float]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    by_worker: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_worker.setdefault(str(row["worker_id"]), []).append(row)
    result = {}
    for worker, values in by_worker.items():
        values.sort(key=lambda row: float(row["captured_monotonic"]))
        end = float(values[-1]["captured_monotonic"])
        window = [row for row in values
                  if float(row["captured_monotonic"]) >= end - 30.0]
        first, last = window[0], window[-1]
        seconds = float(last["captured_monotonic"]) - float(first["captured_monotonic"])
        if seconds <= 0:
            raise RuntimeError("positive resource observation window required")
        result[worker] = {
            "seconds": seconds,
            "cpu_cores": (int(last["cpu_ns"]) - int(first["cpu_ns"])) / 1e9 / seconds,
            "read_mib": (int(last["read_bytes"]) - int(first["read_bytes"])) / 1048576,
            "write_mib": (int(last["write_bytes"]) - int(first["write_bytes"])) / 1048576,
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outer14-checkpoint", type=Path, required=True)
    parser.add_argument("--outer15-checkpoint", type=Path, required=True)
    parser.add_argument("--full-timeout-root", type=Path, required=True)
    parser.add_argument("--early-hang-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.output_dir.name) is None:
        parser.error("safe output directory required")
    output = args.output_dir.resolve()
    if output.exists():
        raise RuntimeError(f"fresh rollback output exists: {output}")

    outer14_path = args.outer14_checkpoint.resolve()
    outer15_path = args.outer15_checkpoint.resolve()
    outer14 = checkpointmod.verify_checkpoint(outer14_path)
    outer15 = checkpointmod.verify_checkpoint(outer15_path)
    state14 = outer14.get("reconstructed_state") or {}
    state15 = outer15.get("reconstructed_state") or {}
    if (
        int(outer14.get("completed_outer_attempts") or 0) != 14
        or int(outer15.get("completed_outer_attempts") or 0) != 15
        or outer14.get("freeze_sha256") != outer15.get("freeze_sha256")
        or state14.get("accepted_rounds") != {
            "job_memory": 9, "tpch_sf4_memory": 9,
            "hybench_xp_cpu": 9, "hybench_xp_io": 8,
        }
        or state15.get("accepted_rounds") != {
            "job_memory": 10, "tpch_sf4_memory": 10,
            "hybench_xp_cpu": 10, "hybench_xp_io": 9,
        }
    ):
        raise RuntimeError("Outer-14/15 rollback checkpoint authority differs")

    full_root = args.full_timeout_root.resolve()
    early_root = args.early_hang_root.resolve()
    full_state_path, early_state_path = full_root / "run_state.json", early_root / "run_state.json"
    full_batch_path = full_root / "formal_incumbent_baseline/batch_result.json"
    early_samples_path = early_root / "formal_incumbent_baseline/resource_samples.jsonl"
    full_state, early_state, full_batch = (
        load(full_state_path), load(early_state_path), load(full_batch_path)
    )
    early_receipt_path = early_root / "launch_receipt.json"
    early_receipt = load(early_receipt_path)
    timeouts = {
        str(row.get("task")) for row in (full_batch.get("failures") or [])
        if str(row.get("error") or "").startswith("TimeoutExpired:")
        and "timed out after 900 seconds" in str(row.get("error") or "")
    }
    window = resource_window(early_samples_path)
    unit = unit_fields(str(early_receipt.get("systemd_unit") or ""))
    early_clients = recorded_client_pids(early_root / "formal_incumbent_baseline")
    live_clients = sorted(pid for pid in early_clients if Path(f"/proc/{pid}").exists())
    if (
        full_state.get("status") != "FAILED_CLOSED"
        or full_state.get("error") != "RuntimeError: formal incumbent baseline failed closed"
        or int(full_state.get("outer_attempt") or 0) != 15
        or full_batch.get("status") != "FAILED_CLOSED"
        or not (900 <= float(full_batch.get("elapsed_seconds") or 0) <= 920)
        or timeouts != {"pair_1", "pair_2", *XP_IDS}
        or any(row.get("coverage_complete") is not False
               for row in (full_batch.get("measurements") or {}).values())
        or early_state.get("status") != "RUNNING"
        or early_state.get("stage") != "FORMAL_INCUMBENT_BASELINE_MEASURING"
        or int(early_state.get("outer_attempt") or 0) != 15
        or unit.get("ActiveState") != "inactive"
        or int(unit.get("MainPID") or 0) != 0
        or live_clients
        or set(window) != {
            "job_w1", "job_w2", "tpch_w1", "tpch_w2", *XP_IDS,
        }
        or any(window[worker]["seconds"] < 25 for worker in window)
        or any(window[worker]["cpu_cores"] >= 0.01 for worker in XP_IDS)
        or any(window[worker]["cpu_cores"] <= 1.0
               for worker in {"job_w1", "job_w2", "tpch_w1", "tpch_w2"})
    ):
        raise RuntimeError("repeated baseline incident evidence differs")

    source_run = Path(str(outer15.get("source_run_root") or "")).resolve()
    choices = {}
    for scene in XP_SCENES:
        decision_path = source_run / f"outer_015/{scene}.decision.json"
        decision = load(decision_path)
        choice = accepted_choice(decision)
        plan = str(choice.get("candidate_physical_plan_sha256") or "")
        if len(plan) != 64 or int(choice.get("rank") or 0) != 4:
            raise RuntimeError(f"{scene} incumbent plan identity differs")
        choices[scene] = {
            "decision_path": str(decision_path), "decision_sha256": sha(decision_path),
            "rank": 4, "params": copy.deepcopy(choice["params"]),
            "full_plan_sha256": plan,
        }

    document = copy.deepcopy(outer15)
    reconstructed = document["reconstructed_state"]
    rollback_fields = (
        "incumbents", "params_by_scene", "ranges_by_scene",
        "accepted_rounds", "rejected_outer_attempts", "acceptance_tier",
        "consecutive_failed_rounds", "interval_stable_rounds",
        "stable_intervals", "converged", "full_plan_replay_history_v2",
    )
    for field in rollback_fields:
        for scene in XP_SCENES:
            reconstructed[field][scene] = copy.deepcopy(state14[field][scene])
    for scene in XP_SCENES:
        replayed = set(state14["replayed_param_keys_by_scene"][scene])
        replayed.add(alg.optimizer_params_key(choices[scene]["params"]))
        reconstructed["replayed_param_keys_by_scene"][scene] = sorted(replayed)
        for incident_id, evidence_path in (
            ("outer16_baseline_full_timeout_attempt033", full_batch_path),
            ("outer16_baseline_early_hang_attempt034", early_samples_path),
        ):
            reconstructed["full_plan_replay_history_v2"][scene].append({
                "contract": "mysql_algorithm_c_full_plan_replay_pruning_v3",
                "scene": scene, "outer_round": 16,
                "full_plan_sha256": choices[scene]["full_plan_sha256"],
                "parameter_point_key": alg.optimizer_params_key(choices[scene]["params"]),
                "accepted": False, "learnable_qps_decline": False,
                "qps_nondecline_counterexample": False,
                "technical_instability_proven": True,
                "technical_incident_id": incident_id,
                "technical_evidence_path": str(evidence_path),
                "technical_evidence_sha256": sha(evidence_path),
                "formal_state_credit_increment": 0,
                "qps_role": "not_observed_not_used",
            })

    document["builder_source_path"] = str(Path(__file__).resolve())
    document["builder_source_sha256"] = sha(Path(__file__).resolve())
    document["post_acceptance_technical_invalidation"] = {
        "schema_version": "mysql_algorithm_c_outer15_xp_post_acceptance_invalidation_v1",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "rollback_scope": sorted(XP_SCENES),
        "retained_outer15_scenes": sorted(RETAINED_SCENES),
        "outer15_xp_accepted_credit_removed": True,
        "outer15_xp_incumbent_and_interval_removed": True,
        "job_tpch_outer15_state_unchanged": True,
        "repeated_formal_incumbent_incidents": 2,
        "full_timeout_state": {"path": str(full_state_path), "sha256": sha(full_state_path)},
        "full_timeout_batch": {"path": str(full_batch_path), "sha256": sha(full_batch_path)},
        "early_hang_state": {"path": str(early_state_path), "sha256": sha(early_state_path)},
        "early_hang_receipt": {"path": str(early_receipt_path), "sha256": sha(early_receipt_path)},
        "early_hang_resource_samples": {
            "path": str(early_samples_path), "sha256": sha(early_samples_path),
            "last_30_second_window": window,
        },
        "early_hang_terminal_unit_readback": unit,
        "early_hang_recorded_client_pids": sorted(early_clients),
        "early_hang_live_client_pids": [],
        "invalidated_incumbent_plans": choices,
        "qps_pruning_contract_changed": False,
        "technical_safety_pruning_contract":
            "mysql_algorithm_c_full_plan_replay_pruning_v3",
        "workload_resource_envelope_candidate_score_and_top5_changed": False,
    }
    payload = copy.deepcopy(document)
    payload.pop("checkpoint_sha256", None)
    document["checkpoint_sha256"] = checkpointmod.base._canonical_sha256(payload)
    output.mkdir(parents=True, mode=0o700)
    path = output / "prepared_binding.json"
    alg.atomic(path, document)
    checkpointmod.verify_checkpoint(path)
    print(json.dumps({"status": "PREPARED", "checkpoint": str(path),
                      "completed_outer_attempts": 15,
                      "accepted_rounds": reconstructed["accepted_rounds"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
