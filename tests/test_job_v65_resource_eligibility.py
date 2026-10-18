from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
from types import ModuleType

import pytest


ROOT = next(
    candidate.resolve()
    for candidate in (Path(__file__).resolve().parents[1], Path.cwd())
    if (candidate / "tools").is_dir()
    and (candidate / "history/job_final_algorithm_fresh10_v36_20260731T2020").is_dir()
)
TOOLS = ROOT / "tools"
os.environ.setdefault(
    "JOB_ROUND8_BASE_RUNNER",
    str(TOOLS / "run_job_round8_strict_serial_base_v11_explicit_selection.py"),
)
os.environ.setdefault(
    "JOB_ROUND8_FROZEN_BASE_RUNNER",
    str(
        ROOT
        / "history/job_final_algorithm_fresh10_v36_20260731T2020/package"
        / "run_job_round8_strict_serial.py"
    ),
)
sys.path.insert(0, str(TOOLS))

import job_fresh10_target10_autonomous_v65 as v65  # noqa: E402
import run_job_round8_strict_serial_robust_v13_resource_eligibility as runner  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_resource_median_upper_bound_matches_dense_box_search() -> None:
    priors = [
        {"cpu": 0.40, "io": 0.62, "memory": 0.80},
        {"cpu": 0.70, "io": 0.45, "memory": 0.92},
    ]
    upper, audit = v65.resource_median_score_upper_bound(priors)
    brute = 0.0
    for low, high, weight, target in (
        (0.40, 0.70, 1.0, False),
        (0.45, 0.62, 3.0, False),
        (0.80, 0.92, 1.0, True),
    ):
        brute += max(
            v65.directional_band_score(low + (high - low) * index / 10000, weight, target)
            for index in range(10001)
        )
    assert upper == pytest.approx(brute, abs=1e-8)
    assert audit["cpu"]["maximizer"] == 0.5
    assert audit["io"]["maximizer"] == 0.5
    assert audit["memory"]["maximizer"] == 0.85


def test_unproven_bound_keeps_trusted_and_never_creates_false_skip(monkeypatch) -> None:
    candidates = []
    for rank, role in enumerate(["trusted", "trusted", "trusted", "probe", "probe"], 1):
        candidates.append(
            {
                "selection_role": role,
                "candidate_params": {"p": rank},
                "algorithmic_gate": {
                    "accepted": True,
                    "gradient_rank_update_allowed": True,
                },
            }
        )
    monkeypatch.setattr(v65, "verified_prior_utilizations", lambda _candidate: None)
    monkeypatch.setattr(v65.implementation.v49, "candidate_list", lambda value: value["top5"])
    selection, audit = v65.build_resource_eligibility_selection(
        {
            "gradient_update_allowed": True,
            "formal_update_flags": {"gradient": True},
            "top5": candidates,
        },
        incumbent_score=4.5,
        formal_state_sha256="a" * 64,
    )
    assert selection["formal_replay_eligible_ranks"] == [1, 2, 3]
    assert selection["formal_zero_call_skip_ranks"] == [4, 5]
    assert all(row["reason"] == "unproven_bound_keep_trusted" for row in audit["rows"][:3])
    assert all(item["used_for_gradient"] is True for item in selection["top5"][:3])


def test_top_level_gradient_gate_is_preserved_without_candidate_gate(monkeypatch) -> None:
    candidates = [
        {
            "selection_role": role,
            "candidate_params": {"p": rank},
            "used_for_gradient": role == "trusted",
        }
        for rank, role in enumerate(["trusted", "trusted", "trusted", "probe", "probe"], 1)
    ]
    monkeypatch.setattr(v65, "verified_prior_utilizations", lambda _candidate: None)
    monkeypatch.setattr(v65.implementation.v49, "candidate_list", lambda value: value["top5"])
    selection, _audit = v65.build_resource_eligibility_selection(
        {
            "gradient_update_allowed": True,
            "formal_update_flags": {"gradient": True},
            "top5": candidates,
        },
        incumbent_score=4.5,
        formal_state_sha256="b" * 64,
    )
    assert [item["used_for_gradient"] for item in selection["top5"]] == [
        True, True, True, False, False
    ]


def test_real_nested_frozen_loop_invokes_only_filtered_candidates(tmp_path, monkeypatch) -> None:
    source = (
        ROOT
        / "history/job_final_algorithm_fresh10_v36_20260731T2020/package"
        / "top5_promoted_formal.json"
    )
    selection = json.loads(source.read_text(encoding="utf-8"))
    for rank, candidate in enumerate(selection["top5"], 1):
        candidate["formal_replay_eligible"] = rank in (1, 3)
    selection_path = tmp_path / "selection.json"
    write_json(selection_path, selection)
    state_path = tmp_path / "state.json"
    write_json(state_path, runner.frozen.initial_state(selection["top5"][0]))

    ranking_sha = selection["top5"][0]["candidate_pool_binding"]["source_sha256"]
    monkeypatch.setattr(
        runner.frozen,
        "validate_authority_bundle",
        lambda *_args: {"fingerprints": set(), "hashes": {"ranking_analysis.json": ranking_sha}},
    )
    monkeypatch.setattr(runner.frozen, "validate_exclusion", lambda *_args: set())
    monkeypatch.setattr(runner.frozen, "apply_params", lambda params, _psql: {"params": dict(params)})
    calls = []

    def fake_invoke(_command, point_root, _timeout, *, diagnostic_only):
        calls.append(point_root.name)
        return 0, {
            "candidate_measurement_protocol": {
                "correctness_passed": True,
                "stable": True,
                "oom_killed": False,
                "database_restart_count": 0,
            },
            "formal_pg_resource": {
                "aggregate": {
                    "cpu_utilization": 0.5,
                    "io_utilization": 0.5,
                    "memory_utilization": 0.85,
                }
            },
        }, ""

    monkeypatch.setattr(runner.frozen, "invoke_workload", fake_invoke)
    monkeypatch.setattr(
        runner.frozen,
        "decide_state_update",
        lambda candidate, actual, state, **_kwargs: {
            "actual_score": runner.frozen.actual_score(actual),
            "accepted": False,
            "gradient_update_applied": False,
            "incumbent_update_applied": False,
            "range_update_applied": False,
            "incumbent_state_before": runner.frozen._state_subset(state),
            "incumbent_state_after": copy.deepcopy(runner.frozen._state_subset(state)),
        },
    )

    targets = runner._filter_targets()
    original_contract = targets[-1][2]

    def filtered(value):
        return [item for item in original_contract(value) if item.get("formal_replay_eligible") is True]

    for target, name, _original in targets:
        runner._set_target(target, name, filtered)
    try:
        result = runner.frozen.run(
            argparse.Namespace(
                selection=selection_path,
                replay_root=tmp_path / "replay",
                authority_root=tmp_path,
                observations=tmp_path / "observations.json",
                exclusion=tmp_path / "exclusion.json",
                state=state_path,
                accepted_target=10,
                diagnostic_only=False,
            )
        )
    finally:
        for target, name, original in reversed(targets):
            runner._set_target(target, name, original)
    assert calls == ["rank_01", "rank_02"]
    assert result["physical_invocation_count"] == 2
    summary = json.loads((tmp_path / "replay/summary.json").read_text(encoding="utf-8"))
    assert summary["invocations"][1]["candidate_binding"]["three_level_fingerprint"] == (
        selection["top5"][2]["three_level_fingerprint"]
    )


def test_dynamic_count_formal_controller_commits_transaction(tmp_path, monkeypatch) -> None:
    fake_strict = ModuleType("run_job_round8_strict_serial")
    fake_strict.run = lambda _args: None
    monkeypatch.setitem(sys.modules, "run_job_round8_strict_serial", fake_strict)
    import job_formal_target10_batch_controller as controller

    params = {
        "cpu_index_tuple_cost": 0.0005,
        "cpu_operator_cost": 0.0025,
        "cpu_tuple_cost": 0.01,
        "parallel_setup_cost": 7000.0,
        "parallel_tuple_cost": 0.001,
        "random_page_cost": 4.0,
        "seq_page_cost": 1.0,
    }
    state = {
        "accepted_count": 1,
        "incumbent_score": 4.54,
        "incumbent_params": params,
        "range_by_parameter": {name: {"min": value, "max": value} for name, value in params.items()},
    }
    selection = tmp_path / "selection.json"
    gate = tmp_path / "gate.json"
    state_path = tmp_path / "state.json"
    global_counter = tmp_path / "global.txt"
    formal_counter = tmp_path / "formal.txt"
    accepted_counter = tmp_path / "accepted.txt"
    write_json(selection, {"selection": True})
    write_json(state_path, state)
    write_json(
        gate,
        {
            "passed": True,
            "checks": {"entry": True},
            "formal_accepted_total": 1,
            "formal_counter_pins_before_start": {
                "global_ordinal": 10,
                "formal_search_replay_count": 4,
                "formal_search_accepted_count": 1,
            },
        },
    )
    global_counter.write_text("10\n", encoding="utf-8")
    formal_counter.write_text("4\n", encoding="utf-8")
    accepted_counter.write_text("1\n", encoding="utf-8")

    def fake_run(_args):
        (tmp_path / "replay").mkdir()
        global_counter.write_text("12\n", encoding="utf-8")
        formal_counter.write_text("6\n", encoding="utf-8")
        return {
            "baseline_params_restored": True,
            "physical_invocation_count": 2,
            "successful_real_replay_count": 2,
            "technical_failure_count": 0,
            "batch_budget_attempt_count": 2,
            "pre_invocation_technical_failure_count": 0,
            "successful": [],
            "final_state": copy.deepcopy(state),
        }

    monkeypatch.setattr(controller.strict_runner, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "controller",
            "--selection", str(selection), "--selection-sha256", hashlib.sha256(selection.read_bytes()).hexdigest(),
            "--gate", str(gate), "--gate-sha256", hashlib.sha256(gate.read_bytes()).hexdigest(),
            "--state", str(state_path), "--state-sha256", hashlib.sha256(state_path.read_bytes()).hexdigest(),
            "--authority-root", str(tmp_path), "--observations", str(tmp_path / "obs"),
            "--exclusion", str(tmp_path / "exclusion"), "--replay-root", str(tmp_path / "replay"),
            "--global-counter", str(global_counter), "--formal-replay-counter", str(formal_counter),
            "--accepted-counter", str(accepted_counter),
        ],
    )
    for name in controller.FORMAL_REPLAY_CAP_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    assert controller.main() == 0
    transaction = json.loads(
        (tmp_path / "replay/formal_state_commit_transaction.json").read_text(encoding="utf-8")
    )
    assert transaction["status"] == "committed"
    committed = json.loads(state_path.read_text(encoding="utf-8"))
    assert committed["last_formal_batch"]["batch_budget_attempts"] == 2
    assert committed["formal_replay_count"] == 6


def test_noeligible_reexplores_and_partial_formal_fails_closed(tmp_path, monkeypatch) -> None:
    formal = tmp_path / "cycle_002/formal"
    (formal / "package").mkdir(parents=True)
    monkeypatch.setattr(v65, "_PREPARE_V63", lambda *_args, **_kwargs: formal)
    monkeypatch.setattr(v65, "seal_resource_eligibility", lambda *_args: {"eligible_ranks": []})
    monkeypatch.setattr(v65, "RUNNER_V11", TOOLS / "run_job_round8_strict_serial_robust_v11_gradient_state_guard.py")
    with pytest.raises(v65.implementation.v49.NoveltyCollision, match="automatic"):
        v65.prepare_formal_v65(tmp_path / "cycle_002", None, None, None, None, "a" * 64)

    replay = formal / "replay"
    replay.mkdir()
    with pytest.raises(v65.implementation.v49.FailClosed, match="partial formal"):
        v65._restore_inherited_runner_before_prepare(tmp_path / "cycle_002")


def test_final_allowed_cycle_reaching_target_is_complete(monkeypatch) -> None:
    def exhausted_main():
        raise v65.implementation.v49.FailClosed(
            "autonomous cycle cap reached before accepted=10"
        )

    writes = []
    monkeypatch.setattr(v65, "_MAIN_V50", exhausted_main)
    monkeypatch.setattr(
        v65.implementation.v49,
        "state_pin",
        lambda: ("f" * 64, {"accepted_count": 10}),
    )
    monkeypatch.setattr(
        v65.implementation,
        "write_controller_state",
        lambda **value: writes.append(value),
    )
    assert v65.main_v65() == 0
    assert writes == [
        {
            "status": "complete",
            "accepted_count": 10,
            "formal_state_sha256": "f" * 64,
            "recovered_after_final_allowed_cycle": True,
            "new_workload_calls": 0,
        }
    ]


@pytest.mark.parametrize(
    "source_runner",
    [
        "run_job_round8_strict_serial_robust_v12_eligibility.py",
        "run_job_round8_strict_serial_robust_v13_resource_eligibility.py",
    ],
)
def test_repeated_prepare_recognizes_v12_v13_and_restores_v11(
    tmp_path, source_runner
) -> None:
    cycle_root = tmp_path / "cycle_002"
    packaged = cycle_root / "formal/package/run_job_round8_strict_serial.py"
    packaged.parent.mkdir(parents=True)
    packaged.write_bytes((TOOLS / source_runner).read_bytes())
    v65._restore_inherited_runner_before_prepare(cycle_root)
    assert v65.implementation.v49.sha256_file(packaged) == v65.EXPECTED_V11_SHA256


def test_selection_journal_recovers_after_replace_before_staged_copy(
    tmp_path, monkeypatch
) -> None:
    formal = tmp_path / "formal"
    package = formal / "package"
    selection_path = package / "top5_promoted_formal.json"
    audit_path = formal / "resource_median_eligibility_audit_v65.json"
    staged_path = package / "top5_promoted_formal.v65.staged.json"
    state_path = tmp_path / "formal_state.json"
    write_json(state_path, {"incumbent_score": 4.5})
    state_sha = v65.implementation.v49.sha256_file(state_path)
    write_json(selection_path, {"already_replaced": True})
    expected_sha = v65.implementation.v49.sha256_file(selection_path)
    write_json(
        audit_path,
        {
            "contract": v65.ELIGIBILITY_CONTRACT,
            "formal_state_sha256": state_sha,
            "source_selection_sha256": "1" * 64,
            "eligible_selection_sha256": expected_sha,
            "eligible_ranks": [1],
        },
    )
    monkeypatch.setattr(v65.implementation.v49, "STATE", state_path)
    recovered = v65.seal_resource_eligibility(formal, state_sha)
    assert recovered["eligible_ranks"] == [1]
    assert staged_path.is_file()
    assert v65.implementation.v49.sha256_file(staged_path) == expected_sha
