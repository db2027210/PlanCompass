#!/usr/bin/env python3
"""Seal an independent C boundary while retiring the legacy 10-round cap."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_outer_checkpoint_v4 as standard


SCHEMA = "mysql_algorithm_c_actual_width_boundary_checkpoint_v1"
SCENES = ("job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io")
FIELDS = (
    "incumbents", "params_by_scene", "ranges_by_scene", "active_by_scene",
    "accepted_rounds", "rejected_outer_attempts", "acceptance_tier",
    "consecutive_failed_rounds", "interval_stable_rounds", "stable_intervals",
    "converged", "full_plan_replay_history_v2", "valid_replay_archive_by_scene",
    "previously_accepted_parameter_signatures",
)


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def bound(path: Path) -> dict[str, str]:
    path = path.resolve()
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular evidence required: {path}")
    return {"path": str(path), "sha256": sha(path)}


def build(run_root: Path) -> dict[str, Any]:
    run_root = run_root.resolve()
    state_path = run_root / "run_state.json"
    state = load(state_path)
    active = list(state.get("active_scenes") or [])
    completed = int(state.get("outer_attempt") or 0)
    if (
        state.get("algorithm") != "C"
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("formal_evidence_eligible") is not True
        or state.get("accepted_state_credit_prohibited") is not False
        or len(active) != 1 or active[0] not in SCENES or completed < 1
    ):
        raise RuntimeError("actual-width source boundary differs")
    scene = active[0]
    incomplete = run_root / f"outer_{completed + 1:03d}"
    if not incomplete.is_dir() or list(incomplete.glob("*.decision.json")):
        raise RuntimeError("incomplete successor boundary differs")

    base_path = Path(str(state.get("checkpoint_path") or "")).resolve()
    base = standard.verify_checkpoint(base_path)
    prior_completed = int(base.get("completed_outer_attempts") or 0)
    if prior_completed >= completed:
        raise RuntimeError("source has no newly completed outer")
    reconstructed = copy.deepcopy(base["reconstructed_state"])
    for field in FIELDS:
        source = state.get(field) or {}
        if field == "active_by_scene" and not source:
            reconstructed[field][scene] = list(state["ranges_by_scene"][scene])
            continue
        if field == "previously_accepted_parameter_signatures" and not source:
            continue
        if not isinstance(source, Mapping) or scene not in source:
            raise RuntimeError(f"source state field differs: {field}")
        reconstructed.setdefault(field, {})[scene] = copy.deepcopy(source[scene])

    legacy_counts = dict(
        state.get("post_target_stability_confirmation_counts") or {}
    )
    promoted = int(legacy_counts.get(scene, 0))
    reconstructed["accepted_rounds"][scene] = (
        int(reconstructed["accepted_rounds"][scene]) + promoted
    )
    decision_acceptances = sum(
        int(load(run_root / f"outer_{outer:03d}" / f"{scene}.decision.json")["accepted"])
        for outer in range(prior_completed + 1, completed + 1)
    )
    expected_acceptances = (
        int(base["reconstructed_state"]["accepted_rounds"][scene])
        + decision_acceptances
    )
    if int(reconstructed["accepted_rounds"][scene]) != expected_acceptances:
        raise RuntimeError("promoted accepted count differs from completed decisions")
    reconstructed["post_target_stability_confirmation_counts"] = {}
    reconstructed["outer_attempt"] = completed
    if int((state.get("candidate_replay_dedup") or {}).get(scene, {}).get(
        "retained_for_next_outer", 0
    )) != 0:
        raise RuntimeError("accepted boundary must clear replay de-dup state")
    reconstructed.setdefault("replayed_param_keys_by_scene", {})[scene] = []

    decisions = []
    for outer in range(prior_completed + 1, completed + 1):
        decision_path = run_root / f"outer_{outer:03d}" / f"{scene}.decision.json"
        decision = load(decision_path)
        if (
            decision.get("scene") != scene
            or int(decision.get("outer_attempt") or 0) != outer
            or decision.get("formal_evidence_eligible") is not True
            or not isinstance(decision.get("accepted"), bool)
        ):
            raise RuntimeError(f"completed decision differs: {decision_path}")
        decisions.append({"outer_attempt": outer, **bound(decision_path)})

    document: dict[str, Any] = {
        "schema_version": SCHEMA,
        "status": "PREPARED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "source_run_root": str(run_root),
        "scene": scene,
        "completed_outer_attempts": completed,
        "workload_started": False,
        "fresh_r0_complete": False,
        "formal_outer_started": False,
        "freeze_path": state["freeze_path"],
        "freeze_sha256": state["freeze_sha256"],
        "resource_model_contract_path": state["resource_model_contract_path"],
        "resource_model_contract_sha256": state["resource_model_contract_sha256"],
        "reconstructed_state": reconstructed,
        "evidence": {
            "source_checkpoint": bound(base_path),
            "baseline": copy.deepcopy(base["evidence"]["baseline"]),
            "source_state": bound(state_path),
            "completed_decisions": decisions,
        },
        "independent_scene_boundary": {
            "scene": scene,
            "completed_outer_attempts": completed,
            "discarded_incomplete_outer": completed + 1,
            "completed_rounds_preserved": True,
        },
        "actual_width_credit_migration": {
            "schema_version": "mysql_algorithm_c_actual_width_credit_migration_v1",
            "legacy_formal_target": 10,
            "legacy_zero_credit_acceptances_promoted": promoted,
            "accepted_rounds_after": int(reconstructed["accepted_rounds"][scene]),
            "normal_stop_uses_actual_interval_width": True,
            "accepted_round_target": None,
            "completed_measurements_replayed": False,
            "candidate_or_workload_changed": False,
        },
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": sha(Path(__file__).resolve()),
    }
    document["checkpoint_sha256"] = canonical(document)
    return document


def verify_checkpoint(path: Path) -> dict[str, Any]:
    value = load(path)
    payload = copy.deepcopy(value)
    expected = str(payload.pop("checkpoint_sha256", ""))
    if (
        value.get("schema_version") != SCHEMA
        or value.get("status") != "PREPARED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("workload_started") is not False
        or value.get("formal_outer_started") is not False
        or value.get("scene") not in SCENES
        or canonical(payload) != expected
    ):
        raise RuntimeError("actual-width checkpoint contract differs")
    builder = Path(str(value.get("builder_source_path") or "")).resolve()
    if builder != Path(__file__).resolve() or sha(builder) != value.get(
        "builder_source_sha256"
    ):
        raise RuntimeError("actual-width checkpoint builder differs")
    bindings = [
        value["evidence"]["source_checkpoint"], value["evidence"]["baseline"],
        value["evidence"]["source_state"],
        *value["evidence"]["completed_decisions"],
    ]
    for binding in bindings:
        source = Path(str(binding.get("path") or "")).resolve()
        if not source.is_file() or source.is_symlink() or sha(source) != binding.get("sha256"):
            raise RuntimeError(f"actual-width checkpoint evidence differs: {source}")
    if build(Path(value["source_run_root"])) != value:
        raise RuntimeError("actual-width checkpoint reconstruction differs")
    return value


def verify_or_standard(path: Path) -> dict[str, Any]:
    value = load(path)
    return (
        verify_checkpoint(path)
        if value.get("schema_version") == SCHEMA
        else standard.verify_checkpoint(path)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.output_dir.name) is None:
        parser.error("unsafe output directory")
    if args.output_dir.exists() or args.output_dir.is_symlink():
        raise RuntimeError("fresh checkpoint output required")
    document = build(args.run_root)
    args.output_dir.mkdir(parents=True, mode=0o700)
    output = args.output_dir / "prepared_binding.json"
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    output.with_name(output.name + ".sha256").write_text(
        f"{sha(output)}  {output.name}\n", encoding="ascii"
    )
    verify_checkpoint(output)
    print(json.dumps({
        "status": "PREPARED", "checkpoint": str(output),
        "completed_outer_attempts": document["completed_outer_attempts"],
        "scene": document["scene"],
        **document["actual_width_credit_migration"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
