#!/usr/bin/env python3
"""Build a content-bound resume checkpoint from completed Algorithm C outers."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_resume_v1 as resume
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg
from mysql_algorithm_c_plan_replay_pruning import (
    record_outcomes as record_plan_replay_outcomes,
)


CONTRACT = "mysql_algorithm_c_outer_boundary_checkpoint_v2"
BOUNDARY_STATE_SCHEMA = "mysql_four_scene_algorithm_c_boundary_resume_state_v2"
SCENES = alg.SCENES
TRUSTED_PREDECESSOR_BUILDER_SHA256 = {
    # XP/I/O outer-48 recovery checkpoint was sealed by the scene-specific
    # external-gate builder before that builder advanced to cover failures
    # after completed candidate batches.  The checkpoint's canonical digest
    # and all evidence bindings are still verified below.
    "a7b30fc6edd9e6b33dff648e7c4983e73d42bc99f2f936bca5560eb1c902263a",
    # Independent-scene round-boundary builder before rejection-cycle replay
    # de-dup reconstruction learned to mirror forced incumbent shrink.  The
    # checkpoint's canonical hash and every bound measurement remain checked;
    # this permits its already-sealed output to remain verifiable after the
    # compatible builder correction.
    "9376768e4d3b9a94a51c1f18dbf3295b6d8e37c54910281ac54df69e8592e40f",
    # The already-verified Outer-2 checkpoint predates generic failed-outer
    # recovery support.  Its canonical checkpoint hash and every bound result
    # remain verified below; only this builder file has advanced compatibly.
    "6caeec5646df885b5418d1630b3274d709a61bdf699a611766a06554d12ff770",
    # Formal v18 checkpoint v5 was published by this compatible builder
    # before serialized one-rank waves and split-outer continuation support.
    "78d5e8621820a92599a1d96b5488c7b169a997c9a078159b02dc627297ce05b6",
    # Immediate predecessor of the split-outer extension below.
    "c14b45cd52873481ac8be0c2c927ecb1e5894748e73433b174ebe535da0e7644",
    # Outer-5 checkpoint v7, published by the compatible split-outer builder.
    "1e5b18d59905555b1d59b872c457624a1ae73bc9f47cad50f97ca9159d55fcb0",
    # Outer-6 checkpoint v8, published before clean quiet-gate timeout
    # boundary recovery was added below.
    "f275cec7e5ce7b4ddad537a1738490cce7c292d700329bca20ab114e30bd6191",
}


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def _scene_mapping(value: Any, label: str) -> dict[str, Any]:
    result = _mapping(value, label)
    if set(result) != set(SCENES):
        raise RuntimeError(f"{label} scene set differs")
    return result


def _bound(path: Path, label: str) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"{label} regular evidence required: {path}")
    return {"path": str(path), "sha256": alg.sha(path)}


def _accepted_evaluation(decision: Mapping[str, Any]) -> dict[str, Any] | None:
    rows = [
        _mapping(row, "outer evaluation")
        for row in (decision.get("evaluations") or [])
        if isinstance(row, Mapping)
        and isinstance(row.get("outer_decision"), Mapping)
        and row["outer_decision"].get("accepted") is True
    ]
    rows.sort(key=lambda row: int(row["rank"]))
    return rows[0] if rows else None


def _verify_batch(
    path: Path, expected_workers: int, *, expected_rank: int | None = None,
) -> dict[str, Any]:
    value = alg.load(path)
    measurements = _mapping(value.get("measurements"), "batch measurements")
    if (
        value.get("status") != "PASS"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("failures") not in ([], None)
        or len(measurements) != expected_workers
        or any(
            not isinstance(row, Mapping)
            or row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            or (
                expected_rank is not None
                and int(row.get("rank") or 0) != expected_rank
            )
            for row in measurements.values()
        )
    ):
        raise RuntimeError(f"completed batch is not a passing full measurement: {path}")
    return value


def _verify_technical_timeout_batch(path: Path, expected_rank: int) -> dict[str, Any]:
    """Bind a timed-out rank without treating it as a replay or rejection."""

    value = alg.load(path)
    measurements = _mapping(value.get("measurements"), "timeout measurements")
    failures = [
        _mapping(row, "timeout failure") for row in (value.get("failures") or [])
    ]
    timeout_tasks = {
        str(row.get("task")) for row in failures
        if str(row.get("error") or "").startswith("TimeoutExpired:")
    }
    skipped = [
        row for row in failures
        if str(row.get("error") or "") == "skipped_after_invalid_workload"
    ]
    if (
        value.get("status") != "FAILED_CLOSED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or len(measurements) != len(SCENES)
        or {str(row.get("scene")) for row in measurements.values()} != set(SCENES)
        or any(int(row.get("rank") or 0) != expected_rank for row in measurements.values())
        or not timeout_tasks
        or any("timed out after 900 seconds" not in str(row.get("error") or "")
               for row in failures if str(row.get("task")) in timeout_tasks)
        or len(skipped) != len(SCENES)
        or any(
            str(row.get("task"))
            not in {f"{worker}_plan_capture" for worker in alg.WORKER_IDS}
            for row in skipped
        )
        or len(timeout_tasks) + len(skipped) != len(failures)
        or not (900 <= float(value.get("elapsed_seconds") or 0) <= 910)
    ):
        raise RuntimeError(f"technical timeout batch differs: {path}")
    return value


def _serialized_outer_batches(
    *, state: Mapping[str, Any], outer_root: Path, outer: int,
) -> list[dict[str, Any]]:
    """Bind five one-rank waves, including a SHA-bound split continuation."""

    partial = state.get("formal_partial_continuation")
    partial_row = (
        _mapping(partial, "formal partial continuation")
        if isinstance(partial, Mapping)
        and int(partial.get("outer_attempt") or 0) == outer
        else None
    )
    source_by_rank: dict[int, dict[str, Any]] = {}
    if partial_row is not None:
        start_rank = int(partial_row.get("start_rank") or 0)
        if start_rank not in range(2, 7):
            raise RuntimeError("formal partial continuation start rank differs")
        for item in partial_row.get("completed_batches") or []:
            binding = _mapping(item, "formal partial completed batch")
            rank = int(binding.get("rank") or 0)
            if rank in source_by_rank or rank not in range(1, start_rank):
                raise RuntimeError("formal partial completed rank set differs")
            source_by_rank[rank] = binding
        if sorted(source_by_rank) != list(range(1, start_rank)):
            raise RuntimeError("formal partial completed ranks are not contiguous")

    result: list[dict[str, Any]] = []
    for rank in range(1, 6):
        if rank in source_by_rank:
            supplied = source_by_rank[rank]
            path = Path(str(supplied.get("path") or "")).resolve()
            if (
                not path.is_file()
                or path.is_symlink()
                or alg.sha(path) != str(supplied.get("sha256") or "")
            ):
                raise RuntimeError(f"formal partial rank {rank} binding differs")
            origin = "formal_partial_replay_source"
        else:
            path = outer_root / f"top5_batch_{rank}" / "batch_result.json"
            origin = "source_run_root"
        batch = alg.load(path)
        if batch.get("status") == "PASS":
            batch = _verify_batch(path, len(SCENES), expected_rank=rank)
            origin_status = "PASS"
        else:
            batch = _verify_technical_timeout_batch(path, expected_rank=rank)
            origin_status = "TECHNICAL_TIMEOUT_EXCLUDED"
        measurements = _mapping(batch.get("measurements"), "serialized measurements")
        if {str(row.get("scene")) for row in measurements.values()} != set(SCENES):
            raise RuntimeError(f"serialized rank {rank} scene coverage differs")
        result.append({
            **_bound(path, "serialized batch result"),
            "rank": rank,
            "origin": origin,
            "status": origin_status,
        })
    return result


def reconstruct_completed_state(run_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    run_root = run_root.resolve()
    state_path = run_root / "run_state.json"
    state = alg.load(state_path)
    if (
        state.get("schema_version") not in {resume.STATE_SCHEMA, BOUNDARY_STATE_SCHEMA}
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("status") not in {
            "RUNNING", "COMPLETE", "NON_CONVERGED", "FAILED_CLOSED",
        }
    ):
        raise RuntimeError("source resume state contract differs")
    completed = int(state.get("outer_attempt") or 0)
    if completed < 1:
        raise RuntimeError("no completed outer attempt is available")
    checkpoint_path = Path(str(state.get("checkpoint_path") or "")).resolve()
    if alg.sha(checkpoint_path) != str(state.get("checkpoint_sha256") or ""):
        raise RuntimeError("source checkpoint binding differs")
    if state.get("schema_version") == resume.STATE_SCHEMA:
        _checkpoint, base = resume.load_checkpoint(checkpoint_path)
        base_completed = int(_checkpoint["completed_outer_attempts"])
        baseline_path = Path(str(state["baseline_path"])).resolve()
    else:
        prior = verify_checkpoint(checkpoint_path)
        base = copy.deepcopy(
            _mapping(prior.get("reconstructed_state"), "prior reconstructed state")
        )
        base_completed = int(base["outer_attempt"])
        live_baseline = _mapping(
            state.get("formal_incumbent_baseline"),
            "source formal incumbent baseline",
        )
        baseline_path = Path(str(live_baseline.get("path") or "")).resolve()
        if (
            live_baseline.get("status") != "PASS"
            or not baseline_path.is_file()
            or baseline_path.is_symlink()
            or alg.sha(baseline_path) != live_baseline.get("sha256")
        ):
            raise RuntimeError("source formal incumbent baseline binding differs")
    if completed < base_completed:
        raise RuntimeError("source completed outer precedes its checkpoint")
    if state.get("status") == "FAILED_CLOSED":
        failed_outer = completed + 1
        failed_root = run_root / f"outer_{failed_outer:03d}"
        error = str(state.get("error") or "")
        clean_post_boundary_plan_ranking_failure = (
            error.startswith("TimeoutExpired: Command '['mysql'")
            and "information_schema.TABLES" in error
            and "timed out after 60 seconds" in error
            and not failed_root.exists()
            and all(
                (run_root / f"outer_{completed:03d}" / f"{scene}.decision.json").is_file()
                for scene in SCENES
            )
        )
        clean_failed_outer = (
            clean_post_boundary_plan_ranking_failure
            or re.search(rf"outer {failed_outer} batch [1-5] failed closed", error)
            is not None
            or error in {
                "RuntimeError: external physical workload bypassed the exclusive wave lease",
                "RuntimeError: cross-project workload lease was not acquired within the bounded pre-measurement window",
                "RuntimeError: cross-project quiet gate exhausted its single bounded attempt",
            }
        )
        if (
            state.get("stage") != "FAILED"
            or not clean_failed_outer
            or (
                not clean_post_boundary_plan_ranking_failure
                and (
                    not failed_root.is_dir()
                    or any(failed_root.glob("*.decision.json"))
                )
            )
        ):
            raise RuntimeError("failed-closed source is not at a clean outer boundary")

    reconstructed = {
        name: copy.deepcopy(base[name])
        for name in (
            "incumbents", "params_by_scene", "ranges_by_scene",
            "active_by_scene", "accepted_rounds", "rejected_outer_attempts",
            "acceptance_tier", "consecutive_failed_rounds",
            "interval_stable_rounds", "stable_intervals", "converged",
            "full_plan_replay_history_v2",
        )
    }
    if state.get("schema_version") == BOUNDARY_STATE_SCHEMA:
        reconstructed["incumbents"] = copy.deepcopy(
            _scene_mapping(
                _mapping(
                    state.get("formal_incumbent_baseline"),
                    "source formal incumbent baseline",
                ).get("incumbents"),
                "source formal baseline incumbents",
            )
        )
    replayed = {
        scene: set(base["replayed_param_keys_by_scene"][scene])
        for scene in SCENES
    }
    evidence: dict[str, Any] = {
        "source_state": _bound(state_path, "source run state"),
        "source_checkpoint": _bound(checkpoint_path, "source checkpoint"),
        "baseline": _bound(baseline_path, "formal baseline"),
        "outers": {},
    }

    for outer in range(base_completed + 1, completed + 1):
        outer_root = run_root / f"outer_{outer:03d}"
        active_scenes = [
            scene for scene in SCENES if not reconstructed["converged"][scene]
        ]
        outer_evidence: dict[str, Any] = {
            "predicted_rankings": _bound(
                outer_root / "predicted_rankings.json", "predicted rankings"
            ),
            "batches": [],
            "decisions": {},
            "companion_audit": _bound(
                outer_root / "companion_audit.json", "companion audit"
            ),
        }
        if state.get("formal_candidate_batches") == [[1], [2], [3], [4], [5]]:
            outer_evidence["batches"] = _serialized_outer_batches(
                state=state, outer_root=outer_root, outer=outer,
            )
        else:
            for batch_index, expected_workers in ((1, 8), (2, 8), (3, 4)):
                path = outer_root / f"top5_batch_{batch_index}" / "batch_result.json"
                _verify_batch(path, expected_workers)
                outer_evidence["batches"].append(_bound(path, "batch result"))
        companion = alg.load(Path(outer_evidence["companion_audit"]["path"]))
        if (
            companion.get("status") != "PASS"
            or companion.get("full_workload_contract_preserved") is not True
        ):
            raise RuntimeError(f"outer {outer} companion audit differs")

        for scene in active_scenes:
            path = outer_root / f"{scene}.decision.json"
            decision = alg.load(path)
            passed_ranks = [
                int(row["rank"]) for row in outer_evidence["batches"]
                if row.get("status", "PASS") == "PASS"
            ]
            rows = [
                _mapping(row, "decision evaluation")
                for row in (decision.get("evaluations") or [])
            ]
            if (
                decision.get("scene") != scene
                or int(decision.get("outer_attempt") or 0) != outer
                or len(rows) != len(passed_ranks)
                or sorted(int(row["rank"]) for row in rows) != passed_ranks
                or any(
                    row.get("hard_gate_passed") is not True
                    or row.get("coverage_complete") is not True
                    for row in rows
                )
            ):
                raise RuntimeError(f"outer {outer} {scene} decision evidence differs")
            accepted = decision.get("accepted") is True
            chosen = _accepted_evaluation(decision)
            if accepted != (chosen is not None):
                raise RuntimeError(f"outer {outer} {scene} accepted point differs")
            reconstructed["full_plan_replay_history_v2"][scene] = (
                record_plan_replay_outcomes(
                    reconstructed["full_plan_replay_history_v2"][scene],
                    scene=scene,
                    outer_round=outer,
                    incumbent_qps=float(
                        reconstructed["incumbents"][scene]["db_qps"]
                    ),
                    evaluations=rows,
                )
            )
            if accepted:
                reconstructed["params_by_scene"][scene] = {
                    name: float(value)
                    for name, value in _mapping(chosen["params"], "chosen params").items()
                }
                reconstructed["incumbents"][scene] = {
                    key: copy.deepcopy(chosen[key])
                    for key in ("db_qps", "p95_ms", "p99_ms", "resource_means")
                }
                reconstructed["ranges_by_scene"][scene] = alg.shrink_ranges(
                    reconstructed["ranges_by_scene"][scene],
                    reconstructed["params_by_scene"][scene],
                    reconstructed["active_by_scene"][scene],
                )
                reconstructed["accepted_rounds"][scene] += 1
                replayed[scene].clear()
            else:
                reconstructed["rejected_outer_attempts"][scene] += 1
                replayed[scene].update(
                    alg.optimizer_params_key(_mapping(row["params"], "evaluated params"))
                    for row in rows
                )
            tier = _mapping(decision.get("tier_progress"), "tier progress")
            convergence = _mapping(
                decision.get("interval_convergence"), "interval convergence"
            )
            if (
                int(decision.get("accepted_rounds", -1))
                != reconstructed["accepted_rounds"][scene]
                or int(decision.get("rejected_outer_attempts", -1))
                != reconstructed["rejected_outer_attempts"][scene]
            ):
                raise RuntimeError(f"outer {outer} {scene} attempt counters differ")
            reconstructed["acceptance_tier"][scene] = str(tier["acceptance_tier"])
            reconstructed["consecutive_failed_rounds"][scene] = int(
                tier["consecutive_failed_rounds"]
            )
            reconstructed["interval_stable_rounds"][scene] = int(
                convergence["stable_rounds"]
            )
            reconstructed["stable_intervals"][scene] = copy.deepcopy(
                convergence.get("stable_interval")
            )
            reconstructed["converged"][scene] = bool(convergence["converged"])
            outer_evidence["decisions"][scene] = _bound(path, "outer decision")
        evidence["outers"][str(outer)] = outer_evidence

    final_fields = (
        "accepted_rounds", "rejected_outer_attempts", "acceptance_tier",
        "consecutive_failed_rounds", "interval_stable_rounds",
        "stable_intervals", "converged", "full_plan_replay_history_v2",
    )
    for field in final_fields:
        if _scene_mapping(state.get(field), f"source {field}") != reconstructed[field]:
            raise RuntimeError(f"reconstructed {field} differs from source state")
    reconstructed["replayed_param_keys_by_scene"] = {
        scene: sorted(replayed[scene]) for scene in SCENES
    }
    reconstructed["outer_attempt"] = completed
    return state, {"state": reconstructed, "evidence": evidence}


def build_checkpoint(run_root: Path) -> dict[str, Any]:
    source_state, reconstruction = reconstruct_completed_state(run_root)
    document = {
        "schema_version": CONTRACT,
        "status": "PREPARED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "source_run_root": str(run_root.resolve()),
        "completed_outer_attempts": reconstruction["state"]["outer_attempt"],
        "workload_started": False,
        "fresh_r0_complete": False,
        "formal_outer_started": False,
        "freeze_path": source_state["freeze_path"],
        "freeze_sha256": source_state["freeze_sha256"],
        "resource_model_contract_path": source_state["resource_model_contract_path"],
        "resource_model_contract_sha256": source_state["resource_model_contract_sha256"],
        "reconstructed_state": reconstruction["state"],
        "evidence": reconstruction["evidence"],
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": alg.sha(Path(__file__).resolve()),
    }
    document["checkpoint_sha256"] = _canonical_sha256(document)
    return document


def verify_checkpoint(path: Path) -> dict[str, Any]:
    value = alg.load(path)
    payload = copy.deepcopy(value)
    expected = str(payload.pop("checkpoint_sha256", ""))
    reconstructed = _mapping(value.get("reconstructed_state"), "reconstructed state")
    evidence = _mapping(value.get("evidence"), "checkpoint evidence")
    if (
        value.get("schema_version") != CONTRACT
        or value.get("status") != "PREPARED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("workload_started") is not False
        or value.get("formal_outer_started") is not False
        or int(value.get("completed_outer_attempts") or 0) < 1
        or int(reconstructed.get("outer_attempt") or 0)
        != int(value.get("completed_outer_attempts") or 0)
        or _canonical_sha256(payload) != expected
    ):
        raise RuntimeError("outer-boundary checkpoint contract or SHA differs")
    for field in (
        "incumbents", "params_by_scene", "ranges_by_scene", "active_by_scene",
        "replayed_param_keys_by_scene", "accepted_rounds",
        "rejected_outer_attempts", "acceptance_tier",
        "consecutive_failed_rounds", "interval_stable_rounds",
        "stable_intervals", "converged", "full_plan_replay_history_v2",
    ):
        _scene_mapping(reconstructed.get(field), f"checkpoint {field}")
    bindings: list[dict[str, Any]] = []
    for name in ("source_checkpoint", "baseline"):
        bindings.append(_mapping(evidence.get(name), f"evidence {name}"))
    if evidence.get("source_state") is not None:
        bindings.append(_mapping(evidence["source_state"], "source state evidence"))
    for outer in _mapping(evidence.get("outers"), "outer evidence").values():
        row = _mapping(outer, "outer evidence row")
        bindings.append(_mapping(row.get("predicted_rankings"), "rankings binding"))
        bindings.append(_mapping(row.get("companion_audit"), "companion binding"))
        bindings.extend(
            _mapping(item, "batch binding") for item in (row.get("batches") or [])
        )
        bindings.extend(
            _mapping(item, "decision binding")
            for item in _mapping(row.get("decisions"), "decision bindings").values()
        )
    for binding in bindings:
        bound_path = Path(str(binding.get("path") or "")).resolve()
        if (
            not bound_path.is_file()
            or bound_path.is_symlink()
            or alg.sha(bound_path) != str(binding.get("sha256") or "")
        ):
            raise RuntimeError(f"checkpoint evidence differs: {bound_path}")
    for path_key, sha_key in (
        ("freeze_path", "freeze_sha256"),
        ("resource_model_contract_path", "resource_model_contract_sha256"),
    ):
        bound_path = Path(str(value.get(path_key) or "")).resolve()
        if not bound_path.is_file() or alg.sha(bound_path) != value.get(sha_key):
            raise RuntimeError(f"checkpoint source binding differs: {path_key}")
    builder_path = Path(str(value.get("builder_source_path") or "")).resolve()
    builder_sha = str(value.get("builder_source_sha256") or "")
    if (
        not builder_path.is_file()
        or (
            alg.sha(builder_path) != builder_sha
            and builder_sha not in TRUSTED_PREDECESSOR_BUILDER_SHA256
        )
    ):
        raise RuntimeError("checkpoint source binding differs: builder_source_path")
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
    alg.atomic(path, document)
    print(json.dumps({
        "status": "PREPARED", "checkpoint": str(path),
        "completed_outer_attempts": document["completed_outer_attempts"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
