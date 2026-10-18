from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


PROJECT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT / "tools/tpch_v44_deterministic_reexploration.py"


def import_v44():
    specification = importlib.util.spec_from_file_location("tpch_v44_test", MODULE_PATH)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def clean5(v44, root: Path, name: str, accepted: int = 1):
    selection = root / f"{name}.json"
    run_root = root / f"diagnostic_{name}"
    write_json(selection, {"selection": name})
    selection_sha = v44.sha256(selection)
    write_json(
        run_root / "diagnostic_state.json",
        {
            "status": "complete",
            "diagnostic_only": True,
            "accepted_count": accepted,
            "physical_call_count": 5,
            "valid_replay_count": 5,
            "technical_failure_count": 0,
            "baseline_restored": True,
            "accepted_update_allowed": False,
            "incumbent_update_allowed": False,
            "range_update_allowed": False,
            "gradient_update_allowed": False,
            "selection_sha256": selection_sha,
        },
    )
    write_json(
        run_root / "diagnostic_summary.json",
        {
            "accepted_count": accepted,
            "physical_call_count": 5,
            "valid_replay_count": 5,
            "technical_failure_count": 0,
            "baseline_restored": True,
            "selection_sha256": selection_sha,
            "accepted_update_applied": False,
            "incumbent_update_applied": False,
            "range_update_applied": False,
            "gradient_update_applied": False,
        },
    )
    return selection, run_root


@pytest.mark.parametrize(
    ("stage", "relative_log", "phrase"),
    [
        (
            "exact_repeat",
            "exact_builder_a.log",
            "source score span is not distinguishable from replay noise",
        ),
        (
            "exact_repeat",
            "exact_builder_a.log",
            "repeat fingerprints are not distinct",
        ),
        (
            "robust_repeat",
            "robust_builder_a.log",
            "fewer than three stable exact-plan regimes",
        ),
        (
            "robust_repeat",
            "robust_builder_a.log",
            "three stable regimes require excessive replay-noise span",
        ),
        (
            "robust_repeat",
            "robust_builder_a.log",
            "robust repeat is not unique trusted3+probe2",
        ),
        (
            "postrobust_reseal",
            "postrobust_reseal_v32/reseal_a.log",
            "fewer than three resource-stable three-observation regimes",
        ),
        (
            "postrobust_reseal",
            "postrobust_reseal_v32/reseal_a.log",
            "fewer than three stable three-observation regimes",
        ),
        (
            "postrobust_reseal",
            "postrobust_reseal_v32/reseal_a.log",
            "post-robust reseal is not unique trusted3+probe2",
        ),
    ],
)
def test_deterministic_failure_allowlist(tmp_path, stage, relative_log, phrase):
    v44 = import_v44()
    log = tmp_path / relative_log
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(f"ValueError: {phrase}\n", encoding="utf-8")
    result = v44.deterministic_failure(stage, tmp_path, "subprocess failed")
    assert result is not None
    assert result["reason"] == phrase
    assert result["evidence_sha256"] == v44.sha256(log)


def test_unknown_or_ambiguous_failure_remains_fail_closed(tmp_path):
    v44 = import_v44()
    log = tmp_path / "robust_builder_a.log"
    log.write_text("connection refused\n", encoding="utf-8")
    assert v44.deterministic_failure("robust_repeat", tmp_path) is None
    log.write_text(
        "fewer than three stable exact-plan regimes\n"
        "robust repeat is not unique trusted3+probe2\n",
        encoding="utf-8",
    )
    assert v44.deterministic_failure("robust_repeat", tmp_path) is None


def test_prethird_requires_sealed_zero_call_negative_gate(tmp_path):
    v44 = import_v44()
    gate = tmp_path / "prethird_acceptance_feasibility.json"
    payload = {
        "contract": "tpch_bounded_four_observation_median_prethird_v1",
        "passed": False,
        "physical_workload_calls": 0,
        "formal_state_mutated": False,
        "manual_candidate_injection": False,
        "thresholds_relaxed": False,
    }
    write_json(gate, payload)
    result = v44.deterministic_failure(
        "prethird_feasibility",
        tmp_path,
        "bounded four-observation median cannot beat incumbent",
    )
    assert result is not None
    payload["thresholds_relaxed"] = True
    write_json(gate, payload)
    assert (
        v44.deterministic_failure("prethird_feasibility", tmp_path) is None
    )


@pytest.mark.parametrize(
    ("stage", "count"),
    [
        ("exact_repeat", 1),
        ("robust_repeat", 2),
        ("prethird_feasibility", 2),
        ("postrobust_reseal", 3),
    ],
)
def test_negative_gate_counts_only_completed_clean5(
    tmp_path,
    stage,
    count,
):
    v44 = import_v44()
    formal = tmp_path / "formal_state.json"
    write_json(formal, {"accepted_count": 1})
    diagnostics = [clean5(v44, tmp_path, f"stage_{index}") for index in range(count)]
    failure_log = tmp_path / "failure.log"
    failure_log.write_text("deterministic negative gate\n", encoding="utf-8")
    payload = v44.negative_gate_payload(
        cycle=4,
        stage=stage,
        failure={
            "reason": "deterministic-test",
            "evidence_path": str(failure_log),
            "evidence_sha256": v44.sha256(failure_log),
        },
        accepted=1,
        resume_state=formal,
        expected_formal_sha256=v44.sha256(formal),
        completed_diagnostics=diagnostics,
    )
    assert payload["physical_workload_calls"] == 5 * count
    assert payload["valid_replays"] == 5 * count
    assert payload["technical_failures"] == 0
    assert payload["accepted_before"] == payload["accepted_after"] == 1
    assert payload["negative_gate_new_workload_calls"] == 0
    assert payload["formal_state_mutated"] is False
    assert payload["manual_candidate_injection"] is False
    assert payload["thresholds_relaxed"] is False


@pytest.mark.parametrize(
    ("target", "field", "value"),
    [
        ("state", "technical_failure_count", 1),
        ("state", "accepted_count", 2),
        ("summary", "accepted_update_applied", True),
        ("summary", "baseline_restored", False),
    ],
)
def test_clean5_mutation_or_failure_blocks_reexploration(
    tmp_path,
    target,
    field,
    value,
):
    v44 = import_v44()
    selection, run_root = clean5(v44, tmp_path, "probe")
    path = run_root / f"diagnostic_{target}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    write_json(path, payload)
    with pytest.raises(RuntimeError, match="not clean state-neutral 5/5/0"):
        v44.clean5_evidence(selection, run_root, 1)


def test_commit_is_atomic_state_neutral_and_advances_one_cycle(tmp_path):
    v44 = import_v44()
    selection, run_root = clean5(v44, tmp_path, "probe")
    formal = tmp_path / "formal_state.json"
    write_json(formal, {"accepted_count": 1})
    failure_log = tmp_path / "exact_builder_a.log"
    failure_log.write_text("repeat fingerprints are not distinct\n", encoding="utf-8")
    state_path = tmp_path / "controller_state.json"
    state = {"accepted_count": 1, "cycles": [], "next_cycle": 4}
    write_json(state_path, state)
    fake_v26 = SimpleNamespace(
        accepted_counter=lambda: 1,
        atomic_json=v44.atomic_json,
        sha256=v44.sha256,
    )
    result = v44.commit_negative_reexploration(
        fake_v26,
        {"state": state_path},
        state,
        cycle=4,
        cycle_root=tmp_path,
        stage="exact_repeat",
        failure={
            "reason": "repeat fingerprints are not distinct",
            "evidence_path": str(failure_log),
            "evidence_sha256": v44.sha256(failure_log),
        },
        accepted=1,
        resume_state=formal,
        expected_formal_sha256=v44.sha256(formal),
        completed_diagnostics=[(selection, run_root)],
    )
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["accepted_count"] == 1
    assert persisted["next_cycle"] == 5
    assert persisted["cycles"][-1]["negative_gate_sha256"] == result[
        "negative_gate_sha256"
    ]
    assert (tmp_path / "v44_deterministic_negative_gate.json").is_file()


def test_formal_artifacts_always_block_deterministic_reexploration(tmp_path):
    v44 = import_v44()
    (tmp_path / "formal_package").mkdir()
    with pytest.raises(RuntimeError, match="after formal artifacts appeared"):
        v44.ensure_no_formal_started(tmp_path)


def test_run_loop_automatically_reexplores_after_exact_negative_gate(
    tmp_path,
    monkeypatch,
):
    v44 = import_v44()
    loop_root = tmp_path / "loop"
    loop_root.mkdir()
    formal = tmp_path / "formal_state.json"
    write_json(formal, {"accepted_count": 1})
    formal_run = tmp_path / "formal_run"
    formal_run.mkdir()
    handoff = tmp_path / "handoff.json"
    write_json(handoff, {"adopted_formal_run": str(formal_run)})
    state_path = loop_root / "controller_state.json"
    state = {
        "accepted_count": 1,
        "accepted_target": 10,
        "resume_state": str(formal),
        "cycles": [],
        "next_cycle": 4,
    }
    write_json(state_path, state)
    previous_seen = []

    def dynamic_config(_common, cycle_root, _accepted):
        path = cycle_root / "config.a.json"
        write_json(path, {"cycle": cycle_root.name})
        return path

    def build_probe(
        _common,
        cycle_root,
        previous_selection,
        _previous_run,
        _resume_state,
        _accepted,
        _environment,
    ):
        previous_seen.append(previous_selection)
        path = cycle_root / "probe_top5.a.json"
        write_json(path, {"cycle": cycle_root.name})
        return path

    def run_clean_diagnostic(
        _common,
        selection,
        output,
        _config,
        _resume_state,
        accepted,
        _log,
        _environment,
    ):
        selection_sha = v44.sha256(selection)
        write_json(
            output / "diagnostic_state.json",
            {
                "status": "complete",
                "diagnostic_only": True,
                "accepted_count": accepted,
                "physical_call_count": 5,
                "valid_replay_count": 5,
                "technical_failure_count": 0,
                "baseline_restored": True,
                "accepted_update_allowed": False,
                "incumbent_update_allowed": False,
                "range_update_allowed": False,
                "gradient_update_allowed": False,
                "selection_sha256": selection_sha,
            },
        )
        write_json(
            output / "diagnostic_summary.json",
            {
                "accepted_count": accepted,
                "physical_call_count": 5,
                "valid_replay_count": 5,
                "technical_failure_count": 0,
                "baseline_restored": True,
                "selection_sha256": selection_sha,
                "accepted_update_applied": False,
                "incumbent_update_applied": False,
                "range_update_applied": False,
                "gradient_update_applied": False,
            },
        )

    def build_exact_repeat(
        _common,
        cycle_root,
        _probe,
        _probe_run,
        _accepted,
        _environment,
    ):
        (cycle_root / "exact_builder_a.log").write_text(
            "ValueError: repeat fingerprints are not distinct\n",
            encoding="utf-8",
        )
        raise RuntimeError("subprocess failed")

    fake_v26 = SimpleNamespace(
        TARGET=10,
        MAX_CYCLES=2,
        LOOP_ROOT=loop_root,
        atomic_json=v44.atomic_json,
        sha256=v44.sha256,
        accepted_counter=lambda: 1,
        dynamic_config=dynamic_config,
        build_probe=build_probe,
        run_clean_diagnostic=run_clean_diagnostic,
        build_exact_repeat=build_exact_repeat,
    )
    common = SimpleNamespace(runtime_environment=lambda: {})
    adoption = tmp_path / "adoption/a"
    adoption.mkdir(parents=True)
    initial = adoption / "selection.json"
    write_json(initial, {"initial": True})
    fake_v41 = SimpleNamespace(
        ADOPTION_ROOT=tmp_path / "adoption",
        configure_controller=lambda *_args: (fake_v26, common),
    )
    monkeypatch.setattr(v44, "executed_history", lambda *_args: [])
    paths = {"state": state_path, "handoff": handoff}
    with pytest.raises(RuntimeError, match="exhausted maximum cycles"):
        v44.run_loop(fake_v41, paths, state)
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert [item["failure_stage"] for item in persisted["cycles"]] == [
        "exact_repeat",
        "exact_repeat",
    ]
    assert persisted["accepted_count"] == 1
    assert persisted["next_cycle"] == 6
    assert previous_seen[0] == initial
    assert previous_seen[1] == loop_root / "cycle_004/probe_top5.a.json"
