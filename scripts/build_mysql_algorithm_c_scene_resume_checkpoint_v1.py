#!/usr/bin/env python3
"""Build a content-bound single-scene C checkpoint at the last clean outer.

The source run must have failed before writing the next outer decision.  Every
completed decision and the failed source state are SHA-bound.  Completed
accepted/rejected credit, incumbent, interval, replay archive, and accepted
signatures are retained; artifacts from the incomplete outer grant no credit.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_outer_checkpoint_v4 as standard
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


HISTORY = ROOT / "history/mysql_four_scene_20260809"
_STANDARD_VERIFY = standard.verify_checkpoint
SCHEMA = "mysql_algorithm_c_single_scene_outer_resume_checkpoint_v1"
SCENES = tuple(alg.SCENES)
RECOVERABLE_ERRORS = {
    "RuntimeError: cross-project quiet gate exhausted its single bounded attempt",
    "RuntimeError: outer 2 current_interval_plan_coverage_002 wave 1 failed closed",
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _bound(path: Path) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular evidence required: {path}")
    return {"path": str(path), "sha256": _sha(path)}


def _optimizer_key(params: Mapping[str, Any]) -> str:
    return alg.optimizer_params_key(dict(params))


def _validate_source(run_root: Path, scene: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    state_path = run_root / "run_state.json"
    state = _load(state_path)
    completed = int(state.get("outer_attempt") or 0)
    if (
        scene not in SCENES
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("formal_evidence_eligible") is not True
        or state.get("accepted_state_credit_prohibited") is not False
        or state.get("active_scenes") != [scene]
        or completed < 1
        or str(state.get("error") or "") not in RECOVERABLE_ERRORS
    ):
        raise RuntimeError("source failed-scene boundary differs")
    pid = int(state.get("pid") or 0)
    if pid > 0 and Path(f"/proc/{pid}").exists():
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ")
        if str(run_root.name).encode() in cmdline:
            raise RuntimeError("source controller is still live")

    decisions: list[dict[str, Any]] = []
    accepted = rejected = 0
    last_valid_keys: set[str] = set()
    for outer in range(1, completed + 1):
        path = run_root / f"outer_{outer:03d}" / f"{scene}.decision.json"
        row = _load(path)
        if (
            row.get("scene") != scene
            or int(row.get("outer_attempt") or 0) != outer
            or row.get("formal_evidence_eligible") is not True
            or not isinstance(row.get("accepted"), bool)
        ):
            raise RuntimeError(f"completed outer decision differs: {path}")
        accepted += int(row["accepted"])
        rejected += int(not row["accepted"])
        if outer == completed and not row["accepted"]:
            for evaluation in row.get("evaluations") or []:
                if (
                    isinstance(evaluation, Mapping)
                    and evaluation.get("hard_gate_passed") is True
                    and evaluation.get("coverage_complete") is True
                    and isinstance(evaluation.get("params"), Mapping)
                ):
                    last_valid_keys.add(_optimizer_key(evaluation["params"]))
        decisions.append({"outer_attempt": outer, **_bound(path)})
    if (
        accepted != int((state.get("accepted_rounds") or {}).get(scene, -1))
        or rejected != int((state.get("rejected_outer_attempts") or {}).get(scene, -1))
    ):
        raise RuntimeError("source completed decision counts differ")
    incomplete = run_root / f"outer_{completed + 1:03d}"
    if not incomplete.is_dir() or any(incomplete.glob("*.decision.json")):
        raise RuntimeError("source is not an incomplete next-outer boundary")
    if scene == "tpch_sf4_memory":
        failed_batch = (
            incomplete / "current_interval_plan_coverage_002/wave_1/batch_result.json"
        )
        batch = _load(failed_batch)
        failures = batch.get("failures") or []
        measurements = batch.get("measurements") or {}
        if (
            batch.get("status") != "FAILED_CLOSED"
            or not any(
                str(row.get("task") or "").startswith("tpch_w")
                and str(row.get("error") or "").startswith("TimeoutExpired:")
                for row in failures if isinstance(row, Mapping)
            )
            or not any(
                isinstance(row, Mapping)
                and row.get("hard_gate_passed") is True
                and row.get("coverage_complete") is True
                for row in measurements.values()
            )
        ):
            raise RuntimeError("TPC-H candidate-local timeout boundary differs")
    state["_resume_last_valid_param_keys"] = sorted(last_valid_keys)
    return state, decisions


def build_checkpoint(run_root: Path, base_checkpoint: Path, scene: str) -> dict[str, Any]:
    run_root = run_root.resolve()
    base_checkpoint = base_checkpoint.resolve()
    if HISTORY.resolve() not in run_root.parents or run_root.is_symlink():
        raise RuntimeError("source run must be a regular history directory")
    # Capture the verifier before legacy launch wrappers install their global
    # chain-audit decorator.  Rebuilding this new checkpoint must verify the
    # bound base checkpoint itself, not recursively re-enter that decorator.
    base = _STANDARD_VERIFY(base_checkpoint)
    state, decisions = _validate_source(run_root, scene)
    reconstructed = copy.deepcopy(base["reconstructed_state"])
    scene_fields = (
        "incumbents", "params_by_scene", "ranges_by_scene", "accepted_rounds",
        "rejected_outer_attempts", "acceptance_tier",
        "consecutive_failed_rounds", "interval_stable_rounds",
        "stable_intervals", "converged", "full_plan_replay_history_v2",
    )
    for field in scene_fields:
        source = state.get(field)
        if not isinstance(source, Mapping) or scene not in source:
            raise RuntimeError(f"source state field differs: {field}")
        reconstructed[field][scene] = copy.deepcopy(source[scene])
    reconstructed["active_by_scene"][scene] = list(
        state["ranges_by_scene"][scene]
    )
    reconstructed["replayed_param_keys_by_scene"][scene] = copy.deepcopy(
        state.pop("_resume_last_valid_param_keys")
    )
    reconstructed["outer_attempt"] = int(state["outer_attempt"])
    reconstructed.setdefault("post_target_stability_confirmation_counts", {})
    reconstructed["post_target_stability_confirmation_counts"][scene] = int(
        (state.get("post_target_stability_confirmation_counts") or {}).get(scene, 0)
    )
    for field in (
        "valid_replay_archive_by_scene",
        "previously_accepted_parameter_signatures",
    ):
        reconstructed.setdefault(field, {})
        reconstructed[field][scene] = copy.deepcopy((state.get(field) or {})[scene])

    source_state_path = run_root / "run_state.json"
    document: dict[str, Any] = {
        "schema_version": SCHEMA,
        "status": "PREPARED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "source_run_root": str(run_root),
        "scene": scene,
        "completed_outer_attempts": int(state["outer_attempt"]),
        "workload_started": False,
        "fresh_r0_complete": False,
        "formal_outer_started": False,
        "freeze_path": state["freeze_path"],
        "freeze_sha256": state["freeze_sha256"],
        "resource_model_contract_path": state["resource_model_contract_path"],
        "resource_model_contract_sha256": state[
            "resource_model_contract_sha256"
        ],
        "reconstructed_state": reconstructed,
        "evidence": {
            "source_checkpoint": _bound(base_checkpoint),
            "baseline": copy.deepcopy(base["evidence"]["baseline"]),
            "source_state": _bound(source_state_path),
            "outers": {},
        },
        "scene_resume_audit": {
            "completed_decisions": decisions,
            "incomplete_outer_attempt": int(state["outer_attempt"]) + 1,
            "incomplete_outer_credit": 0,
            "completed_rounds_preserved": True,
            "accepted_history_preserved": True,
            "replay_archive_preserved": True,
            "source_error": state["error"],
        },
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": _sha(Path(__file__).resolve()),
    }
    document["checkpoint_sha256"] = _canonical(document)
    return document


def verify_checkpoint(path: Path) -> dict[str, Any]:
    value = _load(path)
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
        or int(value.get("completed_outer_attempts") or 0) < 1
        or _canonical(payload) != expected
    ):
        raise RuntimeError("scene resume checkpoint contract or SHA differs")
    builder = Path(str(value.get("builder_source_path") or "")).resolve()
    if builder != Path(__file__).resolve() or _sha(builder) != value.get(
        "builder_source_sha256"
    ):
        raise RuntimeError("scene resume checkpoint builder differs")
    audit = value.get("scene_resume_audit") or {}
    bindings = [
        value["evidence"]["source_checkpoint"],
        value["evidence"]["baseline"],
        value["evidence"]["source_state"],
        *(audit.get("completed_decisions") or []),
    ]
    for binding in bindings:
        bound = Path(str(binding.get("path") or "")).resolve()
        if not bound.is_file() or bound.is_symlink() or _sha(bound) != binding.get("sha256"):
            raise RuntimeError(f"scene resume evidence differs: {bound}")
    for path_key, sha_key in (
        ("freeze_path", "freeze_sha256"),
        ("resource_model_contract_path", "resource_model_contract_sha256"),
    ):
        bound = Path(str(value.get(path_key) or "")).resolve()
        if not bound.is_file() or _sha(bound) != value.get(sha_key):
            raise RuntimeError(f"scene resume source binding differs: {path_key}")
    rebuilt = build_checkpoint(
        Path(value["source_run_root"]),
        Path(value["evidence"]["source_checkpoint"]["path"]),
        str(value["scene"]),
    )
    if rebuilt != value:
        raise RuntimeError("scene resume checkpoint reconstruction differs")
    return value


def verify_or_standard(path: Path) -> dict[str, Any]:
    value = _load(path)
    if value.get("schema_version") == SCHEMA:
        return verify_checkpoint(path)
    return _STANDARD_VERIFY(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--base-checkpoint", type=Path, required=True)
    parser.add_argument("--scene", choices=SCENES, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,180}", args.output_dir.name) is None:
        parser.error("unsafe output directory name")
    if args.output_dir.exists() or args.output_dir.is_symlink():
        raise RuntimeError(f"fresh checkpoint output required: {args.output_dir}")
    document = build_checkpoint(args.run_root, args.base_checkpoint, args.scene)
    args.output_dir.mkdir(parents=True, mode=0o700)
    output = args.output_dir / "prepared_binding.json"
    alg.atomic(output, document)
    print(json.dumps({"status": "PREPARED", "checkpoint": str(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
