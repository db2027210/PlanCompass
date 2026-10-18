from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts import build_mysql_algorithm_b_from_algorithm_c_release_v1 as handoff


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> tuple[Path, dict]:
    source_state_path = tmp_path / "run_state.json"
    source_state = {
        "status": "COMPLETE",
        "stage": "FINISHED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "formal_evidence_eligible": True,
        "accepted_state_credit_prohibited": False,
        "active_scenes": list(handoff.SCENES),
    }
    _write(source_state_path, source_state)
    watchdog_path = tmp_path / "runtime_external_interference_watchdog.jsonl"
    watchdog_path.write_text(
        "\n".join([
            json.dumps({"event": "watchdog_started"}),
            json.dumps({
                "event": "formal_unit_terminal",
                "interference_detected": False,
                "unit_fields": {"Result": "success"},
            }),
        ]) + "\n",
        encoding="utf-8",
    )
    freeze_path = tmp_path / "four_scene_freeze.json"
    freeze = {
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "formal_launch_authorized": True,
        "active_scenes": list(handoff.SCENES),
        "workers": [
            {"worker_id": f"{scene}_w{lane}", "scene": scene}
            for scene in handoff.SCENES for lane in (1, 2)
        ],
    }
    _write(freeze_path, freeze)
    checkpoint_path = tmp_path / "prepared_binding.json"
    _write(checkpoint_path, {"placeholder": True})
    reconstructed = {
        "accepted_rounds": {scene: 10 for scene in handoff.SCENES},
        "interval_stable_rounds": {scene: 2 for scene in handoff.SCENES},
        "converged": {scene: True for scene in handoff.SCENES},
        "active_by_scene": {
            scene: ["io_block_read_cost"] for scene in handoff.SCENES
        },
        "ranges_by_scene": {
            scene: {"io_block_read_cost": {"lower": 1.0, "upper": 1.009}}
            for scene in handoff.SCENES
        },
        "stable_intervals": {
            scene: {"io_block_read_cost": {"lower": 1.0, "upper": 1.009}}
            for scene in handoff.SCENES
        },
        "params_by_scene": {
            scene: {"io_block_read_cost": 1.004} for scene in handoff.SCENES
        },
        "incumbents": {
            scene: {
                "db_qps": 101.0,
                "p95_ms": 10.0,
                "p99_ms": 20.0,
                "resource_means": {"cpu": 0.6, "memory": 0.7, "io": 0.5},
            }
            for scene in handoff.SCENES
        },
    }
    checkpoint = {
        "source_run_root": str(tmp_path),
        "completed_outer_attempts": 16,
        "freeze_path": str(freeze_path),
        "freeze_sha256": _sha(freeze_path),
        "evidence": {
            "source_state": {
                "path": str(source_state_path),
                "sha256": _sha(source_state_path),
            }
        },
        "reconstructed_state": reconstructed,
    }
    return checkpoint_path, checkpoint


def test_builds_and_verifies_four_scene_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint_path, checkpoint = _fixture(tmp_path)
    monkeypatch.setattr(
        handoff.checkpointmod, "verify_checkpoint", lambda path: checkpoint,
    )
    release = handoff.build_release(checkpoint_path)
    assert release["status"] == "PREPARED"
    assert release["active_scenes"] == list(handoff.SCENES)
    assert release["handoff_contract"]["algorithm_b_may_expand_seed_intervals"] is False
    assert release["handoff_contract"]["algorithm_b_fresh_runtime_preflight_required"] is True
    assert release["handoff_contract"]["algorithm_b_launch_authorized_by_this_release_alone"] is False
    runtime = release["algorithm_b_runtime_contract"]
    assert runtime["method"] == "outer_binary_inner_estimated_coordinate_grid"
    assert runtime["grid_points_per_coordinate"] == 15
    assert runtime["maximum_inner_passes"] == 20
    assert runtime["combination_dimension_range"] == [2, 4]
    assert runtime["fixed_real_top_k"] == 5
    assert runtime["target_accepted_binary_shrinks"] == 10
    assert runtime["qps_resource_prediction_or_score_role"] is False
    assert runtime["model_only_hard_pruning"] is False
    generator = runtime["candidate_generator"]
    assert Path(generator["path"]).is_file()
    assert handoff._sha(Path(generator["path"])) == generator["sha256"]
    controller = runtime["runtime_controller"]
    assert Path(controller["path"]).is_file()
    assert handoff._sha(Path(controller["path"])) == controller["sha256"]
    launcher = runtime["runtime_launcher"]
    assert Path(launcher["path"]).is_file()
    assert handoff._sha(Path(launcher["path"])) == launcher["sha256"]
    waiter = runtime["auto_launch_waiter"]
    assert Path(waiter["path"]).is_file()
    assert handoff._sha(Path(waiter["path"])) == waiter["sha256"]
    for scene in handoff.SCENES:
        row = release["scenes"][scene]
        assert row["algorithm_c_accepted_rounds"] == 10
        assert row["algorithm_c_consecutive_stable_rounds"] == 2
        assert row["interval_audit"]["io_block_read_cost"]["upper_lower_ratio"] <= 1.01
    output = tmp_path / "algorithm_b_handoff.json"
    handoff._atomic(output, release)
    assert handoff.verify_release(output) == release


def test_rejects_semantic_tamper_even_with_recomputed_release_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint_path, checkpoint = _fixture(tmp_path)
    monkeypatch.setattr(
        handoff.checkpointmod, "verify_checkpoint", lambda path: checkpoint,
    )
    release = handoff.build_release(checkpoint_path)
    release["scenes"]["job_memory"]["algorithm_b_initial_binary_intervals"][
        "io_block_read_cost"
    ]["upper"] = 1.008
    payload = copy.deepcopy(release)
    payload.pop("release_sha256")
    release["release_sha256"] = handoff._canonical_sha(payload)
    output = tmp_path / "tampered_handoff.json"
    handoff._atomic(output, release)
    with pytest.raises(RuntimeError, match="semantics differ"):
        handoff.verify_release(output)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("accepted", "convergence proof differs"),
        ("wide_interval", "final <=1% stable interval proof differs"),
        ("non_converged_state", "not a completed formal four-scene run"),
        ("runtime_interference", "full-wave non-interference proof differs"),
    ],
)
def test_fails_closed_without_complete_convergence_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    mutation: str, message: str,
) -> None:
    checkpoint_path, original = _fixture(tmp_path)
    checkpoint = copy.deepcopy(original)
    if mutation == "accepted":
        checkpoint["reconstructed_state"]["accepted_rounds"]["hybench_xp_io"] = 9
    elif mutation == "wide_interval":
        checkpoint["reconstructed_state"]["ranges_by_scene"]["job_memory"][
            "io_block_read_cost"
        ]["upper"] = 1.02
        checkpoint["reconstructed_state"]["stable_intervals"]["job_memory"][
            "io_block_read_cost"
        ]["upper"] = 1.02
    elif mutation == "non_converged_state":
        source_path = Path(checkpoint["evidence"]["source_state"]["path"])
        source = json.loads(source_path.read_text(encoding="utf-8"))
        source["status"] = "NON_CONVERGED"
        _write(source_path, source)
        checkpoint["evidence"]["source_state"]["sha256"] = _sha(source_path)
    else:
        watchdog = tmp_path / "runtime_external_interference_watchdog.jsonl"
        watchdog.write_text(
            "\n".join([
                json.dumps({"event": "watchdog_started"}),
                json.dumps({
                    "event": "runtime_interference_detected_under_exclusive_wave_lease",
                }),
                json.dumps({
                    "event": "formal_unit_terminal",
                    "interference_detected": False,
                    "unit_fields": {"Result": "success"},
                }),
            ]) + "\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(
        handoff.checkpointmod, "verify_checkpoint", lambda path: checkpoint,
    )
    with pytest.raises(RuntimeError, match=message):
        handoff.build_release(checkpoint_path)
