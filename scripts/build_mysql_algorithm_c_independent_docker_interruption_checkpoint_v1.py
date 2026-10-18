#!/usr/bin/env python3
"""Seal one independent Algorithm C scene after a Docker daemon interruption.

The source must have completed its last outer decision and then be stopped in
the reset of the following undecided outer.  Completed decisions are replayed
from their SHA-bound measurements; the interrupted outer receives no credit.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_independent_boundary_checkpoint_v2 as prior


base = prior.base
verifier = prior.verifier
SCENES = tuple(base.alg.SCENES)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def _command(command: list[str], timeout: int = 30) -> str:
    return subprocess.run(
        command, text=True, capture_output=True, check=True, timeout=timeout,
    ).stdout.strip()


def _docker_interruption_proof(
    run_root: Path, state: Mapping[str, Any], scene: str,
) -> dict[str, Any]:
    unit = str(state.get("formal_systemd_unit") or "")
    if not unit:
        # The original v55 launcher predates persistence of the unit name.
        unit = (
            "mysql-algc-async-"
            "v55_to_c_xp_top4_20260825_attempt021_dynamic_db_gate_cpu_"
            "xp_cpu.service"
        )
    user_state = subprocess.run(
        ["systemctl", "--user", "is-active", unit], text=True,
        capture_output=True, check=False, timeout=30,
    ).stdout.strip()
    docker_state = _command(["systemctl", "is-active", "docker.service"])
    active_enter = _command([
        "systemctl", "show", "docker.service", "--property=ActiveEnterTimestamp",
        "--value",
    ])
    try:
        daemon_started = datetime.strptime(
            active_enter, "%a %Y-%m-%d %H:%M:%S %Z",
        ).astimezone()
        state_updated = datetime.fromisoformat(str(state["stage_updated_at"]))
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("Docker interruption timestamp proof differs") from error
    processes = _command(["ps", "-eo", "pid=,args="])
    run_id = str(state.get("run_id") or run_root.name)
    live_source_processes = [
        line.strip() for line in processes.splitlines()
        if "run_mysql_algorithm_c_async_scene_v55.py" in line
        and f"--run-id {run_id}" in line
    ]
    if (
        user_state not in {"inactive", "failed", "unknown"}
        or docker_state != "active"
        or daemon_started <= state_updated
        or live_source_processes
    ):
        raise RuntimeError("Docker daemon interruption liveness proof differs")
    return {
        "contract": "mysql_algorithm_c_docker_daemon_interruption_v1",
        "scene": scene,
        "source_unit": unit,
        "source_unit_state": user_state,
        "docker_service_state": docker_state,
        "docker_active_enter_timestamp": active_enter,
        "source_stage_updated_at": state["stage_updated_at"],
        "docker_restart_after_source_stage": True,
        "live_source_processes": [],
        "interrupted_outer_credit_increment": 0,
    }


def _verify_batch(path: Path, scene: str, ranks: list[int]) -> dict[str, Any]:
    value = base.alg.load(path)
    measurements = _mapping(value.get("measurements"), "batch measurements")
    actual = sorted(int(row.get("rank") or 0) for row in measurements.values())
    if (
        value.get("status") != "PASS"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("failures") not in ([], None)
        or actual != sorted(ranks)
        or any(
            row.get("scene") != scene
            or row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            for row in measurements.values()
        )
    ):
        raise RuntimeError(f"completed independent batch differs: {path}")
    return value


def build(run_root: Path) -> dict[str, Any]:
    run_root = run_root.resolve()
    state_path = run_root / "run_state.json"
    state = base.alg.load(state_path)
    active = list(state.get("active_scenes") or [])
    completed = int(state.get("outer_attempt") or 0)
    if (
        state.get("algorithm") != "C"
        or state.get("status") != "RUNNING"
        or state.get("stage")
        != f"OUTER_{completed + 1:03d}_BATCH_3_RESETTING_XP"
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("formal_evidence_eligible") is not True
        or state.get("accepted_state_credit_prohibited") is not False
        or len(active) != 1
        or active[0] not in SCENES
        or completed < 1
    ):
        raise RuntimeError("independent Docker-interrupted source differs")
    scene = active[0]
    source_checkpoint_path = Path(str(state.get("checkpoint_path") or "")).resolve()
    source_checkpoint = verifier.verify_checkpoint(source_checkpoint_path)
    prior_completed = int(source_checkpoint.get("completed_outer_attempts") or 0)
    if prior_completed >= completed:
        raise RuntimeError("source has no newly completed outer boundary")
    reconstructed = copy.deepcopy(_mapping(
        source_checkpoint.get("reconstructed_state"),
        "source checkpoint reconstructed state",
    ))

    baseline_binding = _mapping(
        state.get("formal_incumbent_baseline"), "formal incumbent baseline",
    )
    baseline_path = Path(str(baseline_binding.get("path") or "")).resolve()
    if (
        baseline_binding.get("status") != "PASS"
        or not baseline_path.is_file()
        or base.alg.sha(baseline_path) != baseline_binding.get("sha256")
    ):
        raise RuntimeError("formal incumbent baseline binding differs")
    reconstructed["incumbents"][scene] = copy.deepcopy(
        _mapping(baseline_binding.get("incumbents"), "baseline incumbents")[scene]
    )
    evidence: dict[str, Any] = {
        "source_state": prior.bound(state_path, "source state"),
        "source_checkpoint": prior.bound(source_checkpoint_path, "source checkpoint"),
        "baseline": prior.bound(baseline_path, "formal baseline"),
        "outers": {},
    }

    for outer in range(prior_completed + 1, completed + 1):
        root = run_root / f"outer_{outer:03d}"
        ranking_path = root / "predicted_rankings.json"
        companion_path = root / "companion_audit.json"
        decision_path = root / f"{scene}.decision.json"
        _verify_batch(root / "top5_batch_1/batch_result.json", scene, [1, 2])
        _verify_batch(root / "top5_batch_2/batch_result.json", scene, [3, 4])
        # v55 physically padded the absent fifth candidate with a rank-0
        # incumbent.  It is bound as execution evidence but excluded from the
        # four real candidate evaluations and receives no replay credit.
        _verify_batch(root / "top5_batch_3/batch_result.json", scene, [0])
        companion = base.alg.load(companion_path)
        decision = base.alg.load(decision_path)
        rows = [
            _mapping(row, "decision evaluation")
            for row in (decision.get("evaluations") or [])
        ]
        if (
            companion.get("status") != "PASS"
            or companion.get("full_workload_contract_preserved") is not True
            or decision.get("scene") != scene
            or int(decision.get("outer_attempt") or 0) != outer
            or sorted(int(row.get("rank") or 0) for row in rows) != [1, 2, 3, 4]
            or any(
                row.get("hard_gate_passed") is not True
                or row.get("coverage_complete") is not True
                for row in rows
            )
        ):
            raise RuntimeError(f"outer {outer} completed decision differs")
        reconstructed["full_plan_replay_history_v2"][scene] = prior.record_outcomes(
            reconstructed["full_plan_replay_history_v2"][scene],
            scene=scene,
            outer_round=outer,
            incumbent_qps=float(reconstructed["incumbents"][scene]["db_qps"]),
            evaluations=rows,
        )
        moved = prior.moved_accepted_rows(
            rows,
            incumbent_params=reconstructed["params_by_scene"][scene],
            active_params=list(reconstructed["active_by_scene"][scene]),
        )
        accepted = decision.get("accepted") is True
        if accepted != bool(moved):
            raise RuntimeError(f"outer {outer} accepted movement differs")
        if accepted:
            chosen, _row_decision, subset = moved[0]
            reconstructed["params_by_scene"][scene] = copy.deepcopy(chosen["params"])
            reconstructed["incumbents"][scene] = {
                key: copy.deepcopy(chosen[key])
                for key in ("db_qps", "p95_ms", "p99_ms", "resource_means")
            }
            reconstructed["ranges_by_scene"][scene] = base.alg.shrink_ranges(
                reconstructed["ranges_by_scene"][scene],
                reconstructed["params_by_scene"][scene], subset,
            )
            reconstructed["accepted_rounds"][scene] += 1
            reconstructed["replayed_param_keys_by_scene"][scene] = []
        else:
            reconstructed["rejected_outer_attempts"][scene] += 1
        tier = _mapping(decision.get("tier_progress"), "tier progress")
        convergence = _mapping(
            decision.get("interval_convergence"), "interval convergence",
        )
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
        if (
            reconstructed["accepted_rounds"][scene]
            != int(decision["accepted_rounds"])
            or reconstructed["rejected_outer_attempts"][scene]
            != int(decision["rejected_outer_attempts"])
        ):
            raise RuntimeError(f"outer {outer} counters differ")
        evidence["outers"][str(outer)] = {
            "predicted_rankings": prior.bound(ranking_path, "predicted rankings"),
            "companion_audit": prior.bound(companion_path, "companion audit"),
            "batches": [
                prior.bound(root / f"top5_batch_{index}/batch_result.json", "batch")
                for index in (1, 2, 3)
            ],
            "decisions": {scene: prior.bound(decision_path, "outer decision")},
        }

    for field in (
        "accepted_rounds", "rejected_outer_attempts", "acceptance_tier",
        "consecutive_failed_rounds", "interval_stable_rounds",
        "stable_intervals", "converged", "full_plan_replay_history_v2",
        "post_target_stability_confirmation_counts",
    ):
        if reconstructed.get(field, {}).get(scene) != state.get(field, {}).get(scene):
            raise RuntimeError(f"source {field} differs after reconstruction")
    reconstructed["outer_attempt"] = completed

    interrupted = run_root / f"outer_{completed + 1:03d}"
    ranking = interrupted / "predicted_rankings.json"
    if (
        not ranking.is_file()
        or list(interrupted.glob("*.decision.json"))
        or not (interrupted / "top5_batch_1/batch_result.json").is_file()
        or not (interrupted / "top5_batch_2/batch_result.json").is_file()
        or (interrupted / "top5_batch_3/batch_result.json").exists()
    ):
        raise RuntimeError("Docker-interrupted undecided outer differs")
    _verify_batch(interrupted / "top5_batch_1/batch_result.json", scene, [1, 2])
    _verify_batch(interrupted / "top5_batch_2/batch_result.json", scene, [3, 4])
    interruption_proof = _docker_interruption_proof(run_root, state, scene)
    interruption_proof.update({
        "discarded_outer": completed + 1,
        "predicted_rankings": prior.bound(ranking, "interrupted rankings"),
        "discarded_completed_batches": [
            prior.bound(
                interrupted / f"top5_batch_{index}/batch_result.json",
                "discarded interrupted batch",
            )
            for index in (1, 2)
        ],
        "batch_3_result_absent": True,
        "decision_absent": True,
    })
    document = {
        "schema_version": base.CONTRACT,
        "status": "PREPARED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "source_run_root": str(run_root),
        "completed_outer_attempts": completed,
        "workload_started": False,
        "fresh_r0_complete": False,
        "formal_outer_started": False,
        "freeze_path": state["freeze_path"],
        "freeze_sha256": state["freeze_sha256"],
        "resource_model_contract_path": state["resource_model_contract_path"],
        "resource_model_contract_sha256": state["resource_model_contract_sha256"],
        "reconstructed_state": reconstructed,
        "evidence": evidence,
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": base.alg.sha(Path(__file__).resolve()),
        "independent_scene_docker_interruption_boundary": interruption_proof,
    }
    payload = copy.deepcopy(document)
    document["checkpoint_sha256"] = base._canonical_sha256(payload)
    return document


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
    path = args.output_dir / "prepared_binding.json"
    base.alg.atomic(path, document)
    verifier.verify_checkpoint(path)
    print(json.dumps({
        "status": "PREPARED",
        "checkpoint": str(path),
        "completed_outer_attempts": document["completed_outer_attempts"],
        "scene": document[
            "independent_scene_docker_interruption_boundary"
        ]["scene"],
        "accepted_rounds": document["reconstructed_state"]["accepted_rounds"],
        "rejected_outer_attempts": document[
            "reconstructed_state"
        ]["rejected_outer_attempts"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
