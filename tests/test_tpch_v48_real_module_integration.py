from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


PROJECT = Path(__file__).resolve().parents[1]
V32 = PROJECT / "tools/tpch_fresh10_target10_loop_v32_v8_gate_binding.py"
V48 = PROJECT / "tools/tpch_v48_prethird_feasibility_binding.py"


def import_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixture_module = import_path(
    "tpch_v46_fixture_helpers",
    PROJECT / "tests/test_tpch_v46_postclean5_gate_binding.py",
)
build_fixture = fixture_module.build_fixture
raw_boundary = fixture_module.raw_boundary


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def top5() -> list[dict]:
    roles = ["trusted", "trusted", "trusted", "probe", "probe"]
    return [
        {
            "selection_role": role,
            "three_level_fingerprint": {
                "id": f"three-level-sha256:{rank:064x}"
            },
            "diagnostic_only": True,
            "accepted_update_allowed": False,
            "incumbent_update_allowed": False,
            "range_update_allowed": False,
            "gradient_update_allowed": False,
            "qps_used_for_inner_scoring": False,
        }
        for rank, role in enumerate(roles, 1)
    ]


def test_real_v32_hook_explicitly_binds_resumed_cycle_before_gate(
    tmp_path, monkeypatch
):
    v32 = import_path("tpch_v32_v8_real", V32)
    resume = tmp_path / "formal_state.json"
    other_resume = tmp_path / "other_formal_state.json"
    write_json(resume, {"incumbent_score": 4.0})
    write_json(other_resume, {"incumbent_score": 4.1})
    selection = tmp_path / "robust.json"
    run_root = tmp_path / "robust_run"
    write_json(selection, {"top5": top5()})
    write_json(run_root / "diagnostic_summary.json", {"successful": []})

    def original_analyze(_common, cycle_root, *_args):
        output = cycle_root / "entry_gate.json"
        write_json(
            output,
            {
                "passed": False,
                "checks": {"actual_best_in_predicted_top3": False},
            },
        )
        return output

    def emulate_run(_common, args, _log, _environment):
        output = Path(args[args.index("--output") + 1])
        summary = Path(args[args.index("--output-summary") + 1])
        state = Path(args[args.index("--output-state") + 1])
        write_json(output, {"top5": top5()})
        write_json(summary, {"summary": "deterministic"})
        write_json(state, {"state": "deterministic"})

    fake_v26 = SimpleNamespace(
        build_probe=lambda *_args: selection,
        analyze_gate=original_analyze,
        promote_formal=lambda *_args: None,
        PYTHON=Path("/bin/true"),
        run=emulate_run,
        load=lambda path: json.loads(Path(path).read_text(encoding="utf-8")),
        sha256=lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest(),
    )
    fake_v27 = SimpleNamespace(atomic_json=lambda path, value: write_json(path, value))
    fake_v30 = SimpleNamespace(validate_resealed_summary=lambda *_args: None)
    common = SimpleNamespace(validate_selection=lambda *_args: None)
    monkeypatch.setattr(
        v32,
        "acceptance_feasibility",
        lambda *_args: {
            "contract": "offline_test",
            "passed": True,
            "physical_workload_calls": 0,
            "formal_state_mutated": False,
            "manual_candidate_injection": False,
            "thresholds_relaxed": False,
        },
    )
    v32.install_algorithm_hooks(fake_v26, fake_v27, fake_v30, common)
    assert callable(fake_v26.bind_cycle_resume_state)
    assert callable(fake_v26.resolve_reexploration_evidence)

    unbound_cycle = tmp_path / "cycle_004_unbound"
    unbound_cycle.mkdir()
    with pytest.raises(RuntimeError, match="cycle resume state was not bound"):
        fake_v26.analyze_gate(common, unbound_cycle, selection, run_root, {})

    bound_cycle = tmp_path / "cycle_004_bound"
    bound_cycle.mkdir()
    fake_v26.bind_cycle_resume_state(bound_cycle, resume)
    fake_v26.bind_cycle_resume_state(bound_cycle, resume)
    gate = fake_v26.analyze_gate(common, bound_cycle, selection, run_root, {})
    assert gate.is_file()
    assert json.loads(gate.read_text(encoding="utf-8"))["acceptance_feasibility"][
        "passed"
    ] is True
    effective_selection, effective_run = fake_v26.resolve_reexploration_evidence(
        selection, run_root
    )
    assert effective_selection == bound_cycle / "postrobust_reseal_v32/a/selection.json"
    assert effective_run == bound_cycle / "postrobust_reseal_v32/a"
    with pytest.raises(RuntimeError, match="binding changed"):
        fake_v26.bind_cycle_resume_state(bound_cycle, other_resume)


def test_real_v46_absorbs_only_rank4_probe_quality_miss(tmp_path):
    v46 = import_path("tpch_v48_absorb_real", V48)
    selection = tmp_path / "selection.json"
    run_root = tmp_path / "diagnostic"
    formal = tmp_path / "formal_state.json"
    gate = tmp_path / "entry_gate.json"
    write_json(formal, {"accepted_count": 1})
    selected = top5()
    write_json(selection, {"top5": selected})
    selection_sha = v46.sha256(selection)
    formal_sha = v46.sha256(formal)
    write_json(
        run_root / "diagnostic_state.json",
        {
            "schema_version": "tpch_exact_plan_regime_diagnostic_state_v1",
            "status": "complete",
            "diagnostic_only": True,
            "formal_state_mutation_allowed": False,
            "accepted_count": 1,
            "batch_added_accepted_count": 0,
            "physical_call_count": 5,
            "valid_replay_count": 5,
            "technical_failure_count": 0,
            "baseline_restored": True,
            "accepted_update_allowed": False,
            "incumbent_update_allowed": False,
            "range_update_allowed": False,
            "gradient_update_allowed": False,
            "selection_sha256": selection_sha,
            "resume_state_sha256": formal_sha,
            "full_selection_replay": True,
            "selection_replayed_ranks": [1, 2, 3, 4, 5],
            "selection_excluded_ranks": [],
            "maximum_real_replay_invocations": 5,
        },
    )
    scores = [4.4, 4.3, 4.2, 4.8, 4.0]
    successful = []
    for rank, candidate in enumerate(selected, 1):
        successful.append(
            {
                "rank": rank,
                "selection_role": candidate["selection_role"],
                "three_level_fingerprint": candidate["three_level_fingerprint"],
                "actual_score": scores[rank - 1],
                "valid_replay": True,
                "technical_failure": False,
                "diagnostic_only": True,
                "accepted_update_eligible": False,
                "accepted_update_allowed": False,
                "incumbent_update_allowed": False,
                "range_update_allowed": False,
                "gradient_update_allowed": False,
                "qps_used_for_inner_scoring": False,
            }
        )
    write_json(
        run_root / "diagnostic_summary.json",
        {
            "schema_version": "tpch_exact_plan_regime_diagnostic_batch_summary_v1",
            "diagnostic_only": True,
            "formal_state_mutation_allowed": False,
            "accepted_count": 1,
            "batch_added_accepted_count": 0,
            "physical_call_count": 5,
            "valid_replay_count": 5,
            "technical_failure_count": 0,
            "baseline_restored": True,
            "selection_sha256": selection_sha,
            "full_selection_replay": True,
            "selection_replayed_ranks": [1, 2, 3, 4, 5],
            "selection_excluded_ranks": [],
            "maximum_real_replay_invocations": 5,
            "technical_failures": [],
            "successful": successful,
            "accepted_update_applied": False,
            "incumbent_update_applied": False,
            "range_update_applied": False,
            "gradient_update_applied": False,
        },
    )
    checks = {
        "actual_best_in_predicted_top3": False,
        "candidate_audit_fields_complete": True,
        "five_unique_three_level_fingerprints": True,
        "top5_no_technical_failure": True,
        "trusted3_probe2": True,
        "trusted_only_spearman_positive": True,
        "trusted_resource_predictions_no_order_of_magnitude_error": True,
        "trusted_acceptance_feasible": True,
    }
    write_json(
        gate,
        {
            "passed": False,
            "checks": checks,
            "actual_best_predicted_rank": 4,
            "trusted_only_spearman": 0.5,
            "maximum_trusted_resource_factor_error": 1.099,
        },
    )
    fake_v26 = SimpleNamespace(
        sha256=v46.sha256,
        load=v46.load,
        atomic_json=v46.atomic_json,
        accepted_counter=lambda: 1,
        resolve_reexploration_evidence=lambda source, run: (source, run),
    )
    cycle = tmp_path / "cycle_004"
    cycle.mkdir()
    payload, effective_selection, effective_run = v46.absorb_actual_best_probe_rank4(
        fake_v26,
        cycle_root=cycle,
        selection=selection,
        validation_run=run_root,
        analysis_path=gate,
        accepted=1,
        resume_state=formal,
        expected_formal_sha256=formal_sha,
    )
    assert payload["actual_best_predicted_rank"] == 4
    assert payload["actual_best_probe_fingerprint"] == selected[3][
        "three_level_fingerprint"
    ]["id"]
    assert payload["probe_absorbed_as_trusted"] is False
    assert payload["probe_used_as_observed_reexploration_evidence"] is True
    assert payload["top3_threshold_relaxed"] is False
    assert payload["manual_candidate_injection"] is False
    assert payload["new_workload_calls"] == 0
    assert effective_selection == selection
    assert effective_run == run_root

    bad_gate = json.loads(gate.read_text(encoding="utf-8"))
    bad_gate["checks"]["trusted_only_spearman_positive"] = False
    write_json(gate, bad_gate)
    with pytest.raises(RuntimeError, match="rank4-only quality miss"):
        v46.absorb_actual_best_probe_rank4(
            fake_v26,
            cycle_root=cycle,
            selection=selection,
            validation_run=run_root,
            analysis_path=gate,
            accepted=1,
            resume_state=formal,
            expected_formal_sha256=formal_sha,
        )


def test_postclean5_gate_boundary_resumes_zero_call_into_next_cycle(
    tmp_path, monkeypatch
):
    v46 = import_path("tpch_v48_boundary_real", V48)
    fixture = build_fixture(v46, tmp_path)
    v41, paths, formal_sha, _counter_sha, config, probe, probe_run = fixture
    v46.seal_clean5_boundary(paths, raw_boundary(v46, fixture))
    cycle = v41.V41_LOOP / "cycle_004"
    selected = json.loads(probe.read_text(encoding="utf-8"))

    def create_diagnostic(selection_path: Path, run_root: Path, schema: str) -> None:
        selection_payload = json.loads(selection_path.read_text(encoding="utf-8"))
        is_resealed = schema == "tpch_postrobust_resealed_diagnostic_summary_v1"
        selection_sha = v46.sha256(selection_path)
        write_json(
            run_root / "diagnostic_state.json",
            {
                "schema_version": (
                    "tpch_postrobust_resealed_diagnostic_state_v1"
                    if is_resealed
                    else "tpch_exact_plan_regime_diagnostic_state_v1"
                ),
                "status": "complete",
                "diagnostic_only": True,
                "formal_state_mutation_allowed": False,
                "accepted_count": 1,
                "batch_added_accepted_count": 0,
                "physical_call_count": 5,
                "valid_replay_count": 5,
                "technical_failure_count": 0,
                "baseline_restored": True,
                "accepted_update_allowed": False,
                "incumbent_update_allowed": False,
                "range_update_allowed": False,
                "gradient_update_allowed": False,
                "selection_sha256": selection_sha,
                "resume_state_sha256": formal_sha,
                "full_selection_replay": True,
                "selection_replayed_ranks": [1, 2, 3, 4, 5],
                "selection_excluded_ranks": [],
                "maximum_real_replay_invocations": 5,
            },
        )
        scores = [4.4, 4.3, 4.2, 4.8, 4.0]
        rows = []
        for rank, candidate in enumerate(selection_payload["top5"], 1):
            row = {
                "rank": rank,
                "selection_role": candidate["selection_role"],
                "three_level_fingerprint": candidate[
                    "three_level_fingerprint"
                ],
                "actual_score": scores[rank - 1],
                "valid_replay": True,
                "technical_failure": False,
                "diagnostic_only": True,
                "accepted_update_eligible": False,
                "accepted_update_allowed": False,
                "incumbent_update_allowed": False,
                "range_update_allowed": False,
                "gradient_update_allowed": False,
                "qps_used_for_inner_scoring": False,
            }
            if "candidate_params" in candidate:
                row["candidate_params"] = candidate["candidate_params"]
            rows.append(row)
        write_json(
            run_root / "diagnostic_summary.json",
            {
                "schema_version": schema,
                "diagnostic_only": True,
                "formal_state_mutation_allowed": False,
                "accepted_count": 1,
                "batch_added_accepted_count": 0,
                "physical_call_count": 5,
                "valid_replay_count": 5,
                "technical_failure_count": 0,
                "baseline_restored": True,
                "selection_sha256": selection_sha,
                "full_selection_replay": True,
                "selection_replayed_ranks": [1, 2, 3, 4, 5],
                "selection_excluded_ranks": [],
                "maximum_real_replay_invocations": 5,
                "technical_failures": [],
                "successful": rows,
                "accepted_update_applied": False,
                "incumbent_update_applied": False,
                "range_update_applied": False,
                "gradient_update_applied": False,
            },
        )

    # Add actual scores to the already completed probe summary.
    create_diagnostic(
        probe,
        probe_run,
        "tpch_exact_plan_regime_diagnostic_batch_summary_v1",
    )
    repeat = cycle / "exact_repeat_top5.a.json"
    robust = cycle / "robust_top5.a.json"
    for first, second in (
        (repeat, cycle / "exact_repeat_top5.b.json"),
        (robust, cycle / "robust_top5.b.json"),
    ):
        write_json(first, selected)
        write_json(second, selected)
    repeat_run = cycle / "diagnostic_exact_repeat"
    robust_run = cycle / "diagnostic_robust_repeat"
    create_diagnostic(
        repeat,
        repeat_run,
        "tpch_exact_plan_regime_diagnostic_batch_summary_v1",
    )
    create_diagnostic(
        robust,
        robust_run,
        "tpch_exact_plan_regime_diagnostic_batch_summary_v1",
    )
    resealed_a = cycle / "postrobust_reseal_v32/a/selection.json"
    resealed_b = cycle / "postrobust_reseal_v32/b/selection.json"
    resealed_selected = json.loads(json.dumps(selected))
    resealed_selected["schema_version"] = (
        "tpch_three_observation_postrobust_reseal_top5_v1"
    )
    for rank, candidate in enumerate(resealed_selected["top5"], 1):
        candidate["rank"] = rank
        candidate["candidate_params"] = {"seq_page_cost": float(rank)}
        candidate["three_level_fingerprint"] = {
            "contract": "plan_feature_resource_three_level_v1",
            "valid": True,
            "validation_errors": [],
            "id": f"three-level-sha256:{rank:064x}",
            "plan_level": f"ratio-plan-sha256:{rank:064x}",
            "feature_ratio_level": {"rank": rank},
            "predicted_resource_level": {"rank": rank},
        }
    write_json(resealed_a, resealed_selected)
    write_json(resealed_b, resealed_selected)
    create_diagnostic(
        resealed_a,
        resealed_a.parent,
        "tpch_postrobust_resealed_diagnostic_summary_v1",
    )
    create_diagnostic(
        resealed_b,
        resealed_b.parent,
        "tpch_postrobust_resealed_diagnostic_summary_v1",
    )
    analysis = cycle / "entry_gate.json"
    write_json(
        analysis,
        {
            "passed": False,
            "checks": {
                "actual_best_in_predicted_top3": False,
                "candidate_audit_fields_complete": True,
                "five_unique_three_level_fingerprints": True,
                "top5_no_technical_failure": True,
                "trusted3_probe2": True,
                "trusted_only_spearman_positive": True,
                "trusted_resource_predictions_no_order_of_magnitude_error": True,
                "trusted_acceptance_feasible": True,
            },
            "actual_best_predicted_rank": 4,
            "trusted_only_spearman": 0.5,
            "maximum_trusted_resource_factor_error": 1.099,
        },
    )
    feasibility_scores = [4.4, 4.3, 4.2, 4.8, 4.0]
    write_json(
        cycle / "prethird_acceptance_feasibility.json",
        {
            "contract": "tpch_bounded_four_observation_median_prethird_v1",
            "passed": True,
            "physical_workload_calls": 0,
            "formal_state_mutated": False,
            "manual_candidate_injection": False,
            "thresholds_relaxed": False,
            "complete_two_observation_binding": True,
            "incumbent_score": 3.9,
            "maximum_utility_score": 5.0,
            "feasible_ranks": [1, 2, 3, 4, 5],
            "selection_sha256": v46.sha256(repeat),
            "repeat_summary_sha256": v46.sha256(
                repeat_run / "diagnostic_summary.json"
            ),
            "resume_state_sha256": formal_sha,
            "candidate_bounds": [
                {
                    "rank": rank,
                    "selection_role": role,
                    "first_score": score,
                    "second_score": score,
                    "maximum_four_observation_median": (score + 5.0) / 2.0,
                    "can_still_beat_incumbent": True,
                }
                for rank, (role, score) in enumerate(
                    zip(
                        ["trusted", "trusted", "trusted", "probe", "probe"],
                        feasibility_scores,
                    ),
                    1,
                )
            ],
        },
    )
    v46.REMOTE_PRETHIRD_FEASIBILITY_SHA256 = v46.sha256(
        cycle / "prethird_acceptance_feasibility.json"
    )
    state = json.loads(paths["state"].read_text(encoding="utf-8"))
    state["status"] = "preparing_cycle"
    write_json(paths["state"], state)
    boundary = v46.validate_postclean5_gate_boundary(
        v41,
        paths,
        expected_state_sha=v46.sha256(paths["state"]),
        expected_formal_sha=formal_sha,
        expected_counter_sha=v46.sha256(paths["counter"]),
        expected_cycle=4,
    )
    assert boundary["preflight"]["existing_physical_workload_calls"] == 15
    assert boundary["preflight"]["new_physical_workload_calls"] == 0
    assert boundary["preflight"]["failed_checks"] == [
        "actual_best_in_predicted_top3"
    ]
    assert boundary["preflight"]["acceptance_feasibility"]["source"] == (
        "live_prethird"
    )

    calls = {"bind": 0, "reseal_bind": 0, "new_cycle": 0, "workload": 0}

    def bind(*_args):
        calls["bind"] += 1

    def bind_resealed(*_args):
        calls["reseal_bind"] += 1

    def dynamic_config(*_args):
        calls["new_cycle"] += 1
        raise RuntimeError("automatic reexploration reached")

    def forbidden(*_args):
        calls["workload"] += 1
        raise AssertionError("no workload may run while consuming completed gate")

    fake_v26 = SimpleNamespace(
        TARGET=10,
        MAX_CYCLES=2,
        LOOP_ROOT=v41.V41_LOOP,
        atomic_json=v46.atomic_json,
        sha256=v46.sha256,
        load=v46.load,
        accepted_counter=lambda: 1,
        bind_cycle_resume_state=bind,
        bind_resealed_evidence=bind_resealed,
        resolve_reexploration_evidence=lambda _source, _run: (
            resealed_a,
            resealed_a.parent,
        ),
        dynamic_config=dynamic_config,
        build_probe=forbidden,
        run_clean_diagnostic=forbidden,
    )
    common = SimpleNamespace(runtime_environment=lambda: {})
    adoption = tmp_path / "adoption/a"
    adoption.mkdir(parents=True)
    write_json(adoption / "selection.json", {"initial": True})
    v41.ADOPTION_ROOT = tmp_path / "adoption"
    v41.configure_controller = lambda *_args: (fake_v26, common)
    monkeypatch.setattr(v46, "executed_history", lambda *_args: [])
    with pytest.raises(RuntimeError, match="automatic reexploration reached"):
        v46.run_loop(
            v41,
            paths,
            dict(boundary["state"]),
            start_cycle=4,
            resume_completed_gate=boundary["completed_gate"],
        )
    assert calls == {"bind": 1, "reseal_bind": 1, "new_cycle": 1, "workload": 0}
    persisted = json.loads(paths["state"].read_text(encoding="utf-8"))
    assert persisted["accepted_count"] == 1
    assert persisted["cycles"][-1]["new_workload_calls_during_resume"] == 0
    assert persisted["cycles"][-1]["v47_rank4_probe_absorption"][
        "probe_absorbed_as_trusted"
    ] is False
