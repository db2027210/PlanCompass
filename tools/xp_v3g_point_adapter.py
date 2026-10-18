#!/usr/bin/env python3
"""Execute exactly one local XP diagnostic point and restore its baseline."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import time
from pathlib import Path
from typing import Any


def load_module(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("xp_legacy_replay", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load replay support: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    config = load(Path(os.environ["XP_REPLAY_CONFIG"]))
    driver = load_module(Path(os.environ["XP_REPLAY_SUPPORT"]))
    candidate = load(args.candidate)
    if candidate.get("diagnostic_only") is not True:
        raise ValueError("candidate is not diagnostic-only")
    if any(
        candidate.get(field) is not False
        for field in (
            "accepted_update_applied",
            "gradient_update_applied",
            "incumbent_update_applied",
            "range_update_applied",
        )
    ):
        raise ValueError("diagnostic candidate has a state-update flag")

    baseline_params = {
        name: driver.finite(value, field=f"initial_params.{name}", minimum=0.0)
        for name, value in (config.get("initial_params") or {}).items()
    }
    if set(baseline_params) != driver.ALLOWED_PARAMS:
        raise ValueError("config initial_params is incomplete")
    capability = {
        name: driver.finite(
            (config.get("capability") or {}).get(name),
            field=f"capability.{name}",
            minimum=0.0,
        )
        for name in ("cpu", "memory", "io")
    }
    recovery_command = config["pre_benchmark_recovery"]["snapshot_restore"]["command"]
    warmup_command = config["benchmark"]["validation_warmup_cmd"]
    workload_command = config["benchmark"]["workload_cmd"]
    error: Exception | None = None
    physical_call_started = False
    try:
        recovery = driver.run_command(recovery_command, 900, args.output_dir / "recovery.log")
        if recovery.returncode != 0:
            raise RuntimeError(f"snapshot recovery failed: rc={recovery.returncode}")
        driver.apply_params(candidate["candidate_params"])
        warmup = driver.run_command(warmup_command, 240, args.output_dir / "warmup.log")
        if warmup.returncode != 0:
            raise RuntimeError(f"warmup failed: rc={warmup.returncode}")
        marker = {
            "physical_workload_call_started": True,
            "rank": int(candidate["rank"]),
            "timestamp_epoch": time.time(),
            "fingerprint_id": (candidate["three_level_fingerprint"] or {})["id"],
        }
        write(args.output_dir / "physical_call_started.json", marker)
        physical_call_started = True
        measured = driver.run_command(workload_command, 900, args.output_dir / "workload.log")
        payload = driver.parse_payload(measured.stdout)
        utilization, protocol, measurement_summary = driver.validate_payload(payload)
        evidence = {
            "rank": int(candidate["rank"]),
            "selection_role": candidate["selection_role"],
            "candidate_params": candidate["candidate_params"],
            "three_level_fingerprint": candidate["three_level_fingerprint"],
            "workload_plan_fingerprint": candidate.get("workload_plan_fingerprint"),
            "predicted_score": candidate["candidate_score"],
            "feature_ratio": candidate["feature_ratio"],
            "predicted_resource": candidate["predicted_resource"],
            "utilization_prediction": candidate["utilization"],
            "dScore/dU": candidate["dScore/dU"],
            "T": candidate["T"],
            "confidence": candidate["confidence"],
            "g_j": candidate["g_j"],
            "eta": candidate["eta"],
            "delta_param": candidate["delta_param"],
            "actual_score": driver.actual_score(utilization, capability),
            "actual_resource": driver.actual_resource(utilization, capability),
            "actual_utilization": utilization,
            "algorithmic_gate_rejection": measured.returncode != 0,
            "measurement_summary_status": measurement_summary.get("status"),
            "workload_returncode": measured.returncode,
            "benchmark": (payload or {}).get("benchmark") or {},
            "measurement_protocol": protocol,
            "technical_failure": False,
            "valid_replay": True,
            "diagnostic_only": True,
            "accepted_update_applied": False,
            "gradient_update_applied": False,
            "incumbent_update_applied": False,
            "range_update_applied": False,
            "qps_role": "outer_audit_only",
        }
        write(args.output_dir / "evidence.json", evidence)
    except Exception as exc:
        error = exc
        write(args.output_dir / "technical_failure.json", {
            "technical_failure": True,
            "valid_replay": False,
            "physical_workload_call_started": physical_call_started,
            "error": f"{type(exc).__name__}: {exc}",
            "diagnostic_only": True,
            "accepted_update_applied": False,
            "gradient_update_applied": False,
            "incumbent_update_applied": False,
            "range_update_applied": False,
        })
    finally:
        try:
            final_recovery = driver.run_command(
                recovery_command,
                900,
                args.output_dir / "final_recovery.log",
            )
            if final_recovery.returncode != 0:
                raise RuntimeError(f"final snapshot recovery failed: rc={final_recovery.returncode}")
            readback = driver.apply_params(baseline_params)
            if not driver.params_match(readback, baseline_params):
                raise RuntimeError("baseline parameter readback mismatch")
            write(args.output_dir / "baseline_restored.json", {
                "baseline_restored": True,
                "params": baseline_params,
            })
        except Exception as restore_exc:
            error = restore_exc if error is None else RuntimeError(
                f"{error}; restore failed: {restore_exc}"
            )
    if error is not None:
        print(f"{type(error).__name__}: {error}", flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
