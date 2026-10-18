#!/usr/bin/env python3
"""Read-only progress and acceptance-chain watcher for C r75 -> B r12."""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional


C_ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260823_algorithm_c_training_boundary_clean_r75"
)
B_ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/"
    "20260823_algorithm_b_training_boundary_clean_r12"
)
SCENES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
TERMINAL_PHASES = {"complete", "converged_on_incumbent_clean"}
VALID_TIERS = {"strict", "secondary", "fallback"}


def read_json_retry(path: Path) -> Dict[str, Any]:
    last_error: Optional[Exception] = None
    for _ in range(5):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise RuntimeError(f"expected JSON object: {path}")
            return value
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(0.05)
    raise RuntimeError(f"cannot read state: {path}: {last_error}")


def accepted_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"invalid JSONL {path}:{line_number}: {exc}"
                ) from exc
            if row.get("event") == "algorithm_c_round_accepted":
                rows.append(row)
    return rows


def audit_chain(
    state: Mapping[str, Any],
    rows: List[Mapping[str, Any]],
) -> List[str]:
    errors: List[str] = []
    state_accepted = int(state.get("accepted") or 0)
    if len(rows) != state_accepted:
        errors.append(
            f"accepted_event_count={len(rows)}!=state_accepted={state_accepted}"
        )
    previous_qps: Optional[float] = None
    for index, row in enumerate(rows, start=1):
        decision = row.get("decision") or {}
        qps = float(decision.get("db_qps") or 0.0)
        reference = float(decision.get("qps_gate_reference_db_qps") or 0.0)
        tier = str(decision.get("acceptance_tier") or "")
        role = str(decision.get("qps_gate_reference_role") or "")
        if not (
            decision.get("accepted") is True
            and decision.get("qps_up") is True
            and qps > reference
            and decision.get("non_target_safe") is True
            and tier in VALID_TIERS
        ):
            errors.append(f"accepted[{index}]:base_acceptance_contract_failed")
        if tier == "strict" and not (
            decision.get("target_down") is True
            and decision.get("other_resource_preference_met") is True
        ):
            errors.append(f"accepted[{index}]:strict_components_missing")
        if tier == "secondary" and decision.get("target_down") is not True:
            errors.append(f"accepted[{index}]:secondary_target_down_missing")
        if index == 1 and role != "canonical_baseline_first_round":
            errors.append(f"accepted[{index}]:wrong_first_reference_role")
        if index > 1:
            if role != "previous_accepted_round":
                errors.append(f"accepted[{index}]:wrong_previous_reference_role")
            if previous_qps is None or not math.isclose(
                reference,
                previous_qps,
                rel_tol=1e-12,
                abs_tol=1e-12,
            ):
                errors.append(f"accepted[{index}]:reference_chain_mismatch")
            if previous_qps is not None and not qps > previous_qps:
                errors.append(f"accepted[{index}]:qps_chain_not_strict")
        previous_qps = qps
    return errors


def scene_snapshot(root: Path, algorithm: str, scene: str) -> Dict[str, Any]:
    prefix = f"algorithm_{algorithm.lower()}"
    state_path = root / scene / f"{prefix}_state.json"
    history_path = root / scene / f"{prefix}.jsonl"
    if not state_path.is_file():
        return {"scene": scene, "state": "missing", "errors": []}
    state = read_json_retry(state_path)
    rows = accepted_rows(history_path)
    errors = audit_chain(state, rows)
    accepted = []
    for row in rows:
        decision = row.get("decision") or {}
        accepted.append(
            {
                "index": int(row.get("accepted") or 0),
                "round": int(row.get("round_index") or 0),
                "tier": decision.get("acceptance_tier"),
                "qps": decision.get("db_qps"),
                "reference_qps": decision.get("qps_gate_reference_db_qps"),
                "reference_role": decision.get("qps_gate_reference_role"),
            }
        )
    return {
        "scene": scene,
        "round": int(state.get("round_index") or 0),
        "accepted": int(state.get("accepted") or 0),
        "accepted_target": int(state.get("accepted_target") or 0),
        "valid_replays": int(state.get("valid_replays") or 0),
        "technical_failures": int(state.get("technical_failures") or 0),
        "physical_workload_invocations": int(
            state.get("physical_workload_invocations") or 0
        ),
        "no_improvement_streak": int(
            state.get("consecutive_no_improvement_rounds") or 0
        ),
        "phase": state.get("phase"),
        "accepted_chain": accepted,
        "errors": errors,
    }


def snapshot() -> Dict[str, Any]:
    b_started = any(
        (B_ROOT / scene / "algorithm_b_state.json").is_file()
        for scene in SCENES
    )
    algorithm = "B" if b_started else "C"
    root = B_ROOT if b_started else C_ROOT
    scenes = [scene_snapshot(root, algorithm, scene) for scene in SCENES]
    return {
        "time": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "algorithm": algorithm,
        "root": str(root),
        "algorithm_b_started": b_started,
        "scenes": scenes,
        "errors": [
            f"{row['scene']}:{error}"
            for row in scenes
            for error in row.get("errors") or []
        ],
        "all_terminal": all(
            row.get("phase") in TERMINAL_PHASES for row in scenes
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-s", type=float, default=30.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    previous_signature = ""
    heartbeat_deadline = 0.0
    while True:
        current = snapshot()
        signature_value = dict(current)
        signature_value.pop("time", None)
        signature = json.dumps(signature_value, sort_keys=True)
        now = time.time()
        if signature != previous_signature or now >= heartbeat_deadline:
            print(json.dumps(current, ensure_ascii=False), flush=True)
            previous_signature = signature
            heartbeat_deadline = now + 120.0
        if args.once:
            return 1 if current["errors"] else 0
        if (
            current["algorithm"] == "B"
            and current["all_terminal"] is True
            and not current["errors"]
        ):
            return 0
        time.sleep(max(5.0, args.interval_s))


if __name__ == "__main__":
    raise SystemExit(main())
