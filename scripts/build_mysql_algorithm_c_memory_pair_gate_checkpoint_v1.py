#!/usr/bin/env python3
"""Build a two-scene C checkpoint at the v95 quiet-gate boundary."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PREDECESSOR = ROOT / "scripts/build_mysql_algorithm_c_outer_checkpoint_v2.py"
_source = PREDECESSOR.read_text(encoding="utf-8")
_old = "for batch_index, expected_workers in ((1, 8), (2, 8), (3, 4)):"
_new = (
    "for batch_index, expected_workers in "
    "((1, len(SCENES) * 2), (2, len(SCENES) * 2), (3, len(SCENES))):"
)
if _source.count(_old) != 1:
    raise RuntimeError("checkpoint predecessor batch-cardinality block differs")
_source = _source.replace(_old, _new)
_impl: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._build_mysql_algorithm_c_memory_pair_gate_checkpoint_v1_impl",
    "__package__": "scripts",
}
exec(compile(_source, str(Path(__file__).resolve()), "exec"), _impl)

MEMORY_SCENES = ("job_memory", "tpch_sf4_memory")
EXPECTED_ERROR = "RuntimeError: dynamic non-interference gate failed closed"
QUIET_ERROR = (
    "RuntimeError: cross-project quiet gate exhausted its single bounded attempt"
)


def _project(document: Mapping[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(dict(document))
    state = value.get("reconstructed_state") or {}
    for key, raw in tuple(state.items()):
        if isinstance(raw, Mapping) and set(MEMORY_SCENES).issubset(raw):
            state[key] = {scene: copy.deepcopy(raw[scene]) for scene in MEMORY_SCENES}
    value["reconstructed_state"] = state
    return value


def build_checkpoint(run_root: Path) -> dict[str, Any]:
    run_root = run_root.resolve()
    state_path = run_root / "run_state.json"
    initial = _impl["alg"].load(state_path)
    completed = int(initial.get("outer_attempt") or 0)
    failed_root = run_root / f"outer_{completed + 1:03d}"
    if not (
        initial.get("schema_version")
        == "mysql_four_scene_algorithm_c_boundary_resume_state_v2"
        and initial.get("memory_pair_controller") is True
        and tuple(initial.get("active_scenes") or ()) == MEMORY_SCENES
        and initial.get("status") == "FAILED_CLOSED"
        and initial.get("stage") == "FAILED"
        and initial.get("error") == EXPECTED_ERROR
        and completed == 3
        and failed_root.is_dir()
        and not list(failed_root.glob("*.decision.json"))
        and (failed_root / "top5_batch_1_dynamic_noninterference_gate.json").is_file()
        and not (failed_root / "top5_batch_1/batch_result.json").exists()
    ):
        raise RuntimeError("v95 memory-pair gate boundary differs")

    source_checkpoint = Path(str(initial["checkpoint_path"])).resolve()
    if (
        not source_checkpoint.is_file()
        or _impl["alg"].sha(source_checkpoint) != initial["checkpoint_sha256"]
    ):
        raise RuntimeError("v95 source checkpoint binding differs")
    state_fields = (
        "incumbents", "params_by_scene", "ranges_by_scene", "active_by_scene",
        "accepted_rounds", "rejected_outer_attempts", "acceptance_tier",
        "consecutive_failed_rounds", "interval_stable_rounds",
        "stable_intervals", "converged", "full_plan_replay_history_v2",
        "replayed_param_keys_by_scene",
    )
    reconstructed: dict[str, Any] = {}
    for key in state_fields:
        raw = initial.get(key)
        if key == "active_by_scene" and raw is None:
            raw = {
                scene: list(initial["ranges_by_scene"][scene])
                for scene in MEMORY_SCENES
            }
        if key == "replayed_param_keys_by_scene" and raw is None:
            raw = {}
            for scene in MEMORY_SCENES:
                last_accepted = max(
                    (
                        outer for outer in range(1, completed + 1)
                        if _impl["alg"].load(
                            run_root / f"outer_{outer:03d}/{scene}.decision.json"
                        ).get("accepted") is True
                    ),
                    default=0,
                )
                keys: set[str] = set()
                for outer in range(last_accepted + 1, completed + 1):
                    decision = _impl["alg"].load(
                        run_root / f"outer_{outer:03d}/{scene}.decision.json"
                    )
                    keys.update(
                        _impl["alg"].optimizer_params_key(row.get("params") or {})
                        for row in decision.get("evaluations") or []
                    )
                raw[scene] = sorted(keys)
        if not isinstance(raw, Mapping) or set(raw) != set(MEMORY_SCENES):
            raise RuntimeError(f"v95 memory state field differs: {key}")
        reconstructed[key] = copy.deepcopy(dict(raw))
    reconstructed["outer_attempt"] = completed

    def bound(path: Path) -> dict[str, str]:
        path = path.resolve()
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"checkpoint evidence absent: {path}")
        return {"path": str(path), "sha256": _impl["alg"].sha(path)}

    baseline = Path(str(initial["formal_incumbent_baseline"]["path"])).resolve()
    evidence: dict[str, Any] = {
        "source_state": bound(state_path),
        "source_checkpoint": bound(source_checkpoint),
        "baseline": bound(baseline),
        "outers": {},
    }
    for outer in range(1, completed + 1):
        outer_root = run_root / f"outer_{outer:03d}"
        batches = [
            bound(outer_root / f"top5_batch_{batch}/batch_result.json")
            for batch in (1, 2, 3)
        ]
        decisions = {
            scene: bound(outer_root / f"{scene}.decision.json")
            for scene in MEMORY_SCENES
        }
        for batch_index, binding in enumerate(batches, 1):
            batch = _impl["alg"].load(Path(binding["path"]))
            expected = 4 if batch_index < 3 else 2
            if (
                batch.get("status") != "PASS"
                or batch.get("failures") not in ([], None)
                or len(batch.get("measurements") or {}) != expected
            ):
                raise RuntimeError(f"outer {outer} memory batch {batch_index} differs")
        evidence["outers"][str(outer)] = {
            "predicted_rankings": bound(outer_root / "predicted_rankings.json"),
            "batches": batches,
            "decisions": decisions,
            "companion_audit": bound(outer_root / "companion_audit.json"),
        }
    document = {
        "schema_version": "mysql_algorithm_c_outer_boundary_checkpoint_v2",
        "status": "PREPARED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "source_run_root": str(run_root),
        "completed_outer_attempts": completed,
        "workload_started": False,
        "fresh_r0_complete": False,
        "formal_outer_started": False,
        "freeze_path": initial["freeze_path"],
        "freeze_sha256": initial["freeze_sha256"],
        "resource_model_contract_path": initial["resource_model_contract_path"],
        "resource_model_contract_sha256": initial["resource_model_contract_sha256"],
        "reconstructed_state": reconstructed,
        "evidence": evidence,
    }
    document["memory_pair_projection"] = True
    document["active_scenes"] = list(MEMORY_SCENES)
    document["failed_gate_measurement_reused"] = False
    document["accepted_or_rejected_credit_for_failed_outer"] = 0
    document["builder_source_path"] = str(Path(__file__).resolve())
    document["builder_source_sha256"] = _impl["alg"].sha(Path(__file__).resolve())
    document.pop("checkpoint_sha256", None)
    document["checkpoint_sha256"] = _impl["_canonical_sha256"](document)
    return document


def verify_checkpoint(path: Path) -> dict[str, Any]:
    value = _impl["alg"].load(path)
    payload = copy.deepcopy(value)
    expected = str(payload.pop("checkpoint_sha256", ""))
    reconstructed = value.get("reconstructed_state") or {}
    if (
        value.get("schema_version")
        != "mysql_algorithm_c_outer_boundary_checkpoint_v2"
        or value.get("status") != "PREPARED"
        or value.get("memory_pair_projection") is not True
        or tuple(value.get("active_scenes") or ()) != MEMORY_SCENES
        or int(value.get("completed_outer_attempts") or 0) != 3
        or _impl["_canonical_sha256"](payload) != expected
        or any(
            not isinstance(reconstructed.get(key), Mapping)
            or set(reconstructed[key]) != set(MEMORY_SCENES)
            for key in (
                "incumbents", "params_by_scene", "ranges_by_scene",
                "active_by_scene", "accepted_rounds", "rejected_outer_attempts",
                "converged", "replayed_param_keys_by_scene",
            )
        )
    ):
        raise RuntimeError("memory-pair checkpoint verification failed")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.output_dir.name) is None:
        parser.error("unsafe output directory name")
    if args.output_dir.exists():
        raise RuntimeError(f"fresh checkpoint output exists: {args.output_dir}")
    document = build_checkpoint(args.run_root)
    args.output_dir.mkdir(parents=True, mode=0o700)
    path = args.output_dir / "prepared_binding.json"
    _impl["alg"].atomic(path, document)
    verify_checkpoint(path)
    print(json.dumps({
        "status": "PREPARED", "checkpoint": str(path),
        "completed_outer_attempts": document["completed_outer_attempts"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
