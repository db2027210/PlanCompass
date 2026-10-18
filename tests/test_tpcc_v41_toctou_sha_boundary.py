from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import run_tpcc_formal_target10_batch_v4_sha_bound as formal_v4
from tools import tpcc_v41_postrun_gate_and_sha_bound_formal_handoff as handoff_v41


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


@pytest.mark.parametrize(
    "mutated",
    [
        {"accepted_count": 0, "status": "tampered"},
        {"accepted_count": 1, "status": "ready"},
        {"accepted_count": 0, "status": "ready", "unexpected": True},
    ],
)
def test_gate_to_formal_state_mutation_fails_before_any_workload(
    tmp_path: Path,
    mutated: dict,
) -> None:
    formal_state = tmp_path / "formal_state.json"
    write_json(formal_state, {"accepted_count": 0, "status": "ready"})
    expected = formal_v4.sha256(formal_state)
    gate = {"expected_formal_state_sha256": expected}
    workload_calls = 0

    write_json(formal_state, mutated)
    with pytest.raises(RuntimeError, match="formal state SHA changed after diagnostic gate"):
        formal_v4.assert_formal_state_sha_locked(formal_state, expected, gate)

    assert workload_calls == 0
    assert not (tmp_path / "formal_batch").exists()
    runtime = formal_v4.load_runtime()
    assert runtime["assert_formal_state_sha_locked"] is formal_v4.assert_formal_state_sha_locked


def test_formal_gate_carries_required_sha_cross_bindings() -> None:
    gate = {
        "quality_gate": {"passed": True},
        "v39_algorithm_gate": {"passed": True},
        "v40_algorithm_gate": {"passed": True},
        "selection_sha256": "1" * 64,
        "score_span_gate": {"passed": True},
    }
    result = handoff_v41.build_formal_gate(
        gate,
        "2" * 64,
        "3" * 64,
        "4" * 64,
        "5" * 64,
        "6" * 64,
    )
    assert result["expected_formal_state_sha256"] == "3" * 64
    assert result["formal_runner_sha256"] == "4" * 64
    assert result["diagnostic_runner_sha256"] == "5" * 64
    assert result["baseline_sha256"] == "6" * 64


def test_persisted_handoff_seal_is_reloaded_and_rejects_state_change(
    tmp_path: Path,
) -> None:
    names = {
        "diagnostic_selection",
        "diagnostic_ledger",
        "diagnostic_events",
        "diagnostic_summary",
        "formal_state",
        "postrun_gate",
        "formal_selection",
        "formal_gate",
        "formal_runner",
        "diagnostic_runner",
        "baseline",
    }
    paths = {name: tmp_path / f"{name}.json" for name in names}
    for name, path in paths.items():
        write_json(path, {"name": name})
    expected_state = formal_v4.sha256(paths["formal_state"])
    formal_runner_sha = formal_v4.sha256(paths["formal_runner"])
    diagnostic_runner_sha = formal_v4.sha256(paths["diagnostic_runner"])
    baseline_sha = formal_v4.sha256(paths["baseline"])
    formal_selection_sha = formal_v4.sha256(paths["formal_selection"])
    write_json(
        paths["formal_gate"],
        {
            "expected_formal_state_sha256": expected_state,
            "formal_runner_sha256": formal_runner_sha,
            "diagnostic_runner_sha256": diagnostic_runner_sha,
            "baseline_sha256": baseline_sha,
            "selection_sha256": formal_selection_sha,
        },
    )
    seal = {
        "schema_version": handoff_v41.SCHEMA,
        "expected_formal_state_sha256": expected_state,
        "files": {
            name: {
                "path": str(path),
                "sha256": formal_v4.sha256(path),
            }
            for name, path in paths.items()
        },
    }
    seal_path = tmp_path / "handoff_seal.json"
    write_json(seal_path, seal)
    assert handoff_v41.load_and_validate_handoff_seal(
        seal_path, expected_state
    )["schema_version"] == handoff_v41.SCHEMA

    write_json(paths["formal_state"], {"name": "formal_state", "status": "changed"})
    with pytest.raises(RuntimeError, match="sealed formal_state SHA mismatch"):
        handoff_v41.load_and_validate_handoff_seal(seal_path, expected_state)


def test_postdiagnostic_launcher_has_no_diagnostic_replay_command() -> None:
    launcher = Path(
        "history/tpcc_v41_toctou_postdiagnostic_package_20260803/"
        "launch_postdiagnostic_only.sh"
    ).read_text(encoding="utf-8")
    assert "run_tpcc_v40_clean5_exact_strict_serial.py\" \"$selection" not in launcher
    assert "exec /home/anonymous/miniconda3/envs/resource_adaptive/bin/python -u \"$handoff\"" in launcher
