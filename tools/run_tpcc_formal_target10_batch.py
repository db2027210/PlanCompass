#!/usr/bin/env python3
"""Run one strict TPC-C formal batch toward ten accepted shrinks."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, Mapping


TARGET = 10
RESOURCES = ("cpu", "memory", "io")
LOG_TRANSFORM_PARAMS = {
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_tuple_cost",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def shrink_ranges(
    ranges: Mapping[str, Any], anchor: Mapping[str, Any]
) -> Dict[str, Any]:
    updated = json.loads(json.dumps(ranges))
    for name, bounds in updated.items():
        low = float(bounds["min_value"])
        high = float(bounds["max_value"])
        value = float(anchor[name])
        if not low <= value <= high:
            raise RuntimeError(
                "accepted anchor is outside the authoritative range: "
                f"{name}={value}, range=[{low}, {high}]"
            )
        if name in LOG_TRANSFORM_PARAMS:
            if low <= 0.0:
                raise RuntimeError(f"log range lower bound is not positive: {name}")
            midpoint = math.sqrt(low * high)
        else:
            midpoint = low + (high - low) / 2.0
        if value <= midpoint:
            bounds["max_value"] = midpoint
        else:
            bounds["min_value"] = midpoint
    return updated


def main() -> int:
    if len(sys.argv) != 9:
        raise SystemExit(
            "usage: run_tpcc_formal_target10_batch.py "
            "TOP5 TOP5_SHA GATE GATE_SHA DIAGNOSTIC_RUNNER BASELINE "
            "FORMAL_STATE BATCH_ROOT"
        )
    top5_path = Path(sys.argv[1]).resolve()
    top5_sha = sys.argv[2]
    gate_path = Path(sys.argv[3]).resolve()
    gate_sha = sys.argv[4]
    runner = Path(sys.argv[5]).resolve()
    baseline = Path(sys.argv[6]).resolve()
    formal_state_path = Path(sys.argv[7]).resolve()
    batch_root = Path(sys.argv[8]).resolve()
    batch_top5_path = batch_root / "top5.json"
    batch_state_path = batch_root / "formal_batch_state.json"
    batch_summary_path = batch_root / "formal_batch_summary.json"
    lock_path = formal_state_path.with_suffix(".lock")

    if (
        sha256(top5_path) != top5_sha
        or sha256(gate_path) != gate_sha
        or not runner.is_file()
        or not baseline.is_file()
    ):
        raise RuntimeError("formal input SHA or path mismatch")
    gate = load_json(gate_path)
    if (
        gate.get("passed") is not True
        or gate.get("formal_start_authorized") is not True
        or gate.get("selection_sha256") != top5_sha
    ):
        raise RuntimeError("TPC-C formal entry gate is not passed")
    selection = load_json(top5_path)
    promoted_validation = selection.get(
        "promoted_from_diagnostic_validation"
    ) or {}
    clean5_gate_bound_promotion = bool(
        isinstance(promoted_validation, Mapping)
        and str(promoted_validation.get("selection_sha256") or "")
        == str(gate.get("source_diagnostic_selection_sha256") or "")
        and (gate.get("quality_gate") or {}).get("passed") is True
        and (gate.get("score_span_gate") or {}).get("passed") is True
    )
    selection_contract = str(selection.get("selection_contract") or "")
    if selection_contract == "tpcc_robust_exact_plan_regime_trusted3_probe2_v1":
        replay_round = 14
    elif selection_contract == "tpcc_round13_exact_plan_regime_corrected_top5_v1":
        replay_round = 13
    else:
        raise RuntimeError(
            f"unsupported formal Top-5 selection contract: {selection_contract}"
        )
    replay_root = batch_root / f"top5_real_replay_round{replay_round}_formal"
    if [item["selection_role"] for item in selection["top5"]] != [
        "trusted",
        "trusted",
        "trusted",
        "uncertain_probe",
        "uncertain_probe",
    ]:
        raise RuntimeError("formal Top-5 role contract mismatch")
    if selection.get("qps_role") != "outer_audit_only_not_inner_score":
        raise RuntimeError("QPS outer-only contract mismatch")
    if batch_root.exists():
        raise FileExistsError(f"formal batch root already exists: {batch_root}")

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not formal_state_path.exists():
            source_config = load_json(Path(selection["source_config"]))
            baseline_summary = load_json(baseline)
            initial_state = {
                "schema_version": "tpcc_formal_target10_state_v1",
                "status": "ready",
                "accepted_target": TARGET,
                "inherited_accepted": 0,
                "accepted_count": 0,
                "formal_physical_call_count": 0,
                "formal_valid_replay_count": 0,
                "formal_technical_failure_count": 0,
                "incumbent_score": float(
                    load_json(
                        Path(selection["source_discovery"])
                        .parent
                        / "top5_real_replay_round12_probe5_v1/summary.json"
                    )["baseline_actual_score"]
                ),
                "incumbent_params": {
                    name: float(value)
                    for name, value in (
                        source_config.get("initial_params")
                        or {
                            "seq_page_cost": 1.0,
                            "random_page_cost": 4.0,
                            "cpu_tuple_cost": 0.010616461941294047,
                            "cpu_index_tuple_cost": 0.005,
                            "cpu_operator_cost": 0.0026541154853235117,
                            "parallel_setup_cost": 1000.0,
                            "parallel_tuple_cost": 0.1,
                        }
                    ).items()
                },
                "range_state": source_config["param_ranges"],
                "baseline_summary_sha256": sha256(baseline),
                "baseline_correctness_passed": (
                    baseline_summary.get("correctness_passed") is True
                ),
                "qps_used_for_inner_scoring": False,
            }
            atomic_json(formal_state_path, initial_state)
        formal_state = load_json(formal_state_path)
        if (
            formal_state.get("accepted_target") != TARGET
            or not (0 <= int(formal_state.get("accepted_count", -1)) < TARGET)
            or formal_state.get("qps_used_for_inner_scoring") is not False
        ):
            raise RuntimeError("formal target state mismatch")
        accepted_before = int(formal_state["accepted_count"])
        incumbent_before = float(formal_state["incumbent_score"])
        incumbent_params_before = json.loads(
            json.dumps(formal_state["incumbent_params"])
        )
        ranges_before = json.loads(json.dumps(formal_state["range_state"]))
        incumbent_range_violations = {
            name: {
                "incumbent": float(incumbent_params_before[name]),
                "min_value": float(bounds["min_value"]),
                "max_value": float(bounds["max_value"]),
            }
            for name, bounds in ranges_before.items()
            if not (
                float(bounds["min_value"])
                <= float(incumbent_params_before[name])
                <= float(bounds["max_value"])
            )
        }
        if incumbent_range_violations:
            raise RuntimeError(
                "formal state invariant failed before workload: incumbent "
                f"outside active range: {incumbent_range_violations}"
            )
        for item in selection["top5"][:3]:
            params = item["optimized_params"]
            if any(
                not (
                    float(bounds["min_value"])
                    < float(params[name])
                    < float(bounds["max_value"])
                )
                for name, bounds in ranges_before.items()
            ):
                raise RuntimeError(
                    "trusted formal candidate is outside active range: "
                    f"rank={item['rank']}"
                )
            maximum_relative_step = max(
                abs(
                    (
                        float(params[name])
                        - float(incumbent_params_before[name])
                    )
                    / max(abs(float(incumbent_params_before[name])), 1e-12)
                )
                for name in ranges_before
            )
            if (
                maximum_relative_step > 0.80 + 1e-12
                and not clean5_gate_bound_promotion
            ):
                raise RuntimeError(
                    "trusted formal candidate exceeds 80% trust step: "
                    f"rank={item['rank']}, step={maximum_relative_step}"
                )
        batch_root.mkdir(parents=True)
        batch_top5_path.write_bytes(top5_path.read_bytes())
        if sha256(batch_top5_path) != top5_sha:
            raise RuntimeError("formal batch Top-5 copy SHA mismatch")
        batch_state = {
            "schema_version": "tpcc_formal_target10_batch_state_v1",
            "status": "running",
            "pid": os.getpid(),
            "accepted_before": accepted_before,
            "accepted_target": TARGET,
            "physical_call_count": 0,
            "valid_replay_count": 0,
            "technical_failure_count": 0,
            "top5_sha256": top5_sha,
            "entry_gate_sha256": gate_sha,
            "qps_used_for_inner_scoring": False,
            "clean5_gate_bound_promotion": clean5_gate_bound_promotion,
            "started_at_epoch": time.time(),
        }
        atomic_json(batch_state_path, batch_state)

        env = {
            **os.environ,
            "ADAPTIVE_REAL_REPLAY_ENABLED": "0",
            "PYTHONPATH": (
                f"{runner.parent}:/home/anonymous/program/resource_adaptive"
            ),
        }
        completed = subprocess.run(
            [
                sys.executable,
                "-u",
                str(runner),
                str(batch_top5_path),
                str(replay_root),
                str(baseline),
            ],
            check=False,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        (batch_root / "diagnostic_runner.log").write_text(
            completed.stdout, encoding="utf-8"
        )
        ledger_path = (
            batch_root
            / f"strict_top5_real_replay_round{replay_round}_ledger_v1.json"
        )
        replay_summary_path = replay_root / "summary.json"
        if not ledger_path.is_file() or not replay_summary_path.is_file():
            batch_state.update(
                {
                    "status": "technical_failure",
                    "technical_failure_count": 1,
                    "error": (
                        "diagnostic substrate did not produce ledger and summary"
                    ),
                    "runner_returncode": completed.returncode,
                }
            )
            atomic_json(batch_state_path, batch_state)
            return 2
        ledger = load_json(ledger_path)
        replay_summary = load_json(replay_summary_path)
        physical = int(ledger.get("real_workload_invocation_count", 0))
        valid = len(ledger.get("successful", []))
        technical = len(ledger.get("technical_failures", []))
        baseline_restored = bool(
            replay_summary.get("baseline_params_restored") is True
            and replay_summary.get("baseline_snapshot_restored") is True
        )
        clean = bool(
            completed.returncode == 0
            and physical == 5
            and valid == 5
            and technical == 0
            and baseline_restored
        )
        successful = {int(item["rank"]): item for item in ledger["successful"]}
        trusted = [successful[rank] for rank in (1, 2, 3)] if clean else []
        accepted_result = (
            max(trusted, key=lambda item: float(item["actual_score"]))
            if trusted
            else None
        )
        accepted = bool(
            accepted_result is not None
            and float(accepted_result["actual_score"]) > incumbent_before + 1e-12
        )
        accepted_after = accepted_before + (1 if accepted else 0)
        formal_state["formal_physical_call_count"] = int(
            formal_state["formal_physical_call_count"]
        ) + physical
        formal_state["formal_valid_replay_count"] = int(
            formal_state["formal_valid_replay_count"]
        ) + valid
        formal_state["formal_technical_failure_count"] = int(
            formal_state["formal_technical_failure_count"]
        ) + technical
        if accepted:
            formal_state["accepted_count"] = accepted_after
            formal_state["incumbent_score"] = float(accepted_result["actual_score"])
            formal_state["incumbent_params"] = accepted_result["candidate_params"]
            formal_state["range_state"] = shrink_ranges(
                ranges_before, accepted_result["candidate_params"]
            )
        else:
            if (
                formal_state["accepted_count"] != accepted_before
                or formal_state["incumbent_score"] != incumbent_before
                or formal_state["incumbent_params"] != incumbent_params_before
                or formal_state["range_state"] != ranges_before
            ):
                raise RuntimeError("rejected/technical batch mutated formal state")
        formal_state["status"] = (
            "target_reached" if accepted_after >= TARGET else "ready"
        )
        formal_state["last_batch_root"] = str(batch_root)
        formal_state["updated_at_epoch"] = time.time()
        atomic_json(formal_state_path, formal_state)
        batch_summary = {
            "schema_version": "tpcc_formal_target10_batch_summary_v1",
            "status": "complete" if clean else "technical_failure",
            "accepted_before": accepted_before,
            "accepted_after": accepted_after,
            "batch_added_accepted": 1 if accepted else 0,
            "physical_call_count": physical,
            "valid_replay_count": valid,
            "technical_failure_count": technical,
            "baseline_restored": baseline_restored,
            "accepted_rank": (
                int(accepted_result["rank"]) if accepted else None
            ),
            "accepted_score": (
                float(accepted_result["actual_score"]) if accepted else None
            ),
            "incumbent_update_applied": accepted,
            "range_update_applied": accepted,
            "gradient_update_applied": False,
            "qps_used_for_inner_scoring": False,
            "formal_state_sha256": sha256(formal_state_path),
        }
        atomic_json(batch_summary_path, batch_summary)
        batch_state.update(
            {
                "status": batch_summary["status"],
                "accepted_after": accepted_after,
                "batch_added_accepted": batch_summary["batch_added_accepted"],
                "physical_call_count": physical,
                "valid_replay_count": valid,
                "technical_failure_count": technical,
                "baseline_restored": baseline_restored,
                "completed_at_epoch": time.time(),
            }
        )
        atomic_json(batch_state_path, batch_state)
        return 0 if clean else 2


if __name__ == "__main__":
    raise SystemExit(main())
