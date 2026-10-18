from __future__ import annotations

import importlib.util
import copy
import json
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
BUILDER = (
    ROOT
    / "tools/build_tpch_autonomous_successor_top5_v11_evidence_acquisition.py"
)
CONTROLLER = ROOT / "tools/tpch_v50_trusted_evidence_acquisition.py"
BOUNDARY_VALIDATOR = ROOT / "tools/validate_tpch_v50_liveness_fix_boundary.py"
DEPLOY = ROOT / "tools/deploy_tpch_v50_trusted_evidence_acquisition.sh"
BOUNDARY_APPLY = ROOT / "tools/apply_tpch_v50_liveness_fix_at_boundary.sh"
V51_FAILURE_FIXTURE = (
    ROOT / "tests/fixtures/tpch_v51_cycle6_preworkload_failure.json"
)


def import_path(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def candidate(index: int, score: float, evidence_count: int = 0):
    return {
        "candidate_score": score,
        "probe_acquisition_score": score + 0.01,
        "candidate_params": {"unique": float(index)},
        "boundary_corner": False,
        "boundary_plateau": False,
        "actual_parameter_noncorner_audit": {
            "reason": "actual_parameter_space_noncorner"
        },
        "partial_exact_regime_evidence_count": evidence_count,
        "three_level_fingerprint": {
            "id": f"three-level-sha256:{index:064x}",
        },
        "final_estimation_metadata": {
            "query_features": [
                {
                    "queryid": "q1",
                    "plan_fingerprint": f"exact-structure-sha256:{index:064x}",
                }
            ]
        },
    }


def test_probe_only_acquisition_keeps_credible_two_and_prefers_partial_evidence():
    builder = import_path("tpch_v50_builder_test", BUILDER)
    probes = [
        candidate(1, 4.10),
        candidate(2, 4.09),
        candidate(3, 3.90, evidence_count=1),
        candidate(4, 3.89, evidence_count=1),
        candidate(5, 3.88, evidence_count=1),
        candidate(6, 4.08),
        candidate(7, 4.07),
    ]
    credible = probes[:2]

    first = builder.trusted_evidence_acquisition_take(probes, credible)
    second = builder.trusted_evidence_acquisition_take(probes, credible)

    assert first == second
    assert len(first) == 5
    assert [item["candidate_params"]["unique"] for item in first[:2]] == [1.0, 2.0]
    assert {
        item["candidate_params"]["unique"] for item in first[2:]
    } == {3.0, 4.0, 5.0}
    assert len(
        {item["three_level_fingerprint"]["id"] for item in first}
    ) == 5


def test_probe_only_acquisition_does_not_replay_irrecoverable_regime_slots():
    builder = import_path("tpch_v50_builder_terminal_test", BUILDER)
    probes = [
        candidate(1, 4.10),
        candidate(2, 4.09),
        {
            **candidate(3, 9.00, evidence_count=2),
            "exact_regime_stability_failure_irrecoverable": True,
        },
        candidate(4, 3.90, evidence_count=1),
        candidate(5, 3.89, evidence_count=1),
        candidate(6, 3.88, evidence_count=1),
        candidate(7, 4.08),
    ]

    selected = builder.trusted_evidence_acquisition_take(probes, probes[:2])

    assert [item["candidate_params"]["unique"] for item in selected] == [
        1.0,
        2.0,
        4.0,
        5.0,
        6.0,
    ]


def test_cycle6_real_failure_uses_safe_unknown_maximin_probe2_without_gate_relaxation():
    builder = import_path("tpch_v51_safe_probe_test", BUILDER)
    fixture = json.loads(V51_FAILURE_FIXTURE.read_text(encoding="utf-8"))
    assert fixture["eligible"] == 22
    assert fixture["trusted"] == 3
    assert fixture["stable_trusted"] == 0
    assert fixture["credible_unknown_probes"] == 0
    assert fixture["target_score"] == 3.9683158761
    assert fixture["formal_state_changed"] is False
    assert fixture["accepted_counter_changed"] is False
    assert fixture["workload_calls"] == 0

    anchors = [full_candidate(builder, index, 3.96 - index * 0.001) for index in range(1, 4)]
    for item in anchors:
        item.update(
            {
                "trusted": True,
                "uncertain_probe": False,
                "confidence": {"measurement_count": 2, "unknown_regime": False},
            }
        )
    probes = []
    for index in range(4, 23):
        item = full_candidate(builder, index, 3.80 - index * 0.001)
        item.update(
            {
                "trusted": False,
                "uncertain_probe": True,
                "probe_acquisition_score": item["candidate_score"] + 0.02,
                "confidence": {"measurement_count": 0, "unknown_regime": True},
                "partial_exact_regime_evidence_count": 0,
                "exact_regime_stability_failure_irrecoverable": False,
            }
        )
        probes.append(item)

    first, audit = builder.safe_unknown_probe_maximin_take(probes, anchors, 2)
    second, second_audit = builder.safe_unknown_probe_maximin_take(probes, anchors, 2)

    assert first == second
    assert audit == second_audit
    assert len(first) == 2
    assert len({item["three_level_fingerprint"]["id"] for item in first}) == 2
    assert all(builder.safe_unknown_probe(item) for item in first)
    assert audit["formal_score_floor_satisfied"] is False
    assert audit["formal_candidate_eligible"] is False
    assert audit["optimizer_state_updated"] is False
    assert audit["manual_candidate_injection"] is False


def test_clean_probe5_retest_forms_three_trusted_after_two_stable_observations():
    builder = import_path("tpch_v50_builder_liveness_test", BUILDER)
    source = [
        full_candidate(builder, index, 4.30 - index * 0.10)
        for index in range(1, 6)
    ]
    for rank, item in enumerate(source, 1):
        item.update({"rank": rank, "selection_role": "probe"})
    selection = {
        "diagnostic_only": True,
        "top5": source,
        "validated_plan_regimes": {},
    }

    def observations(candidates, suffix):
        return [
            {
                "rank": rank,
                "candidate_params": copy.deepcopy(item["candidate_params"]),
                "actual_resource": {
                    name: float(item["predicted_resource"][name]) * (1.0 + suffix)
                    for name in builder.RESOURCES
                },
                "actual_score": float(item["candidate_score"]) + suffix,
                "_source": item,
            }
            for rank, item in enumerate(candidates, 1)
        ]

    registry_one, _ = builder.update_registry(
        selection,
        observations(source, 0.0),
        "a" * 64,
    )
    assert all(value["evidence_count"] == 1 for value in registry_one.values())
    pending = builder.first_observation_retest(
        selection,
        observations(source, 0.0),
        registry_one,
    )
    assert pending is not None
    assert [item["selection_role"] for item in pending["top5"]] == [
        "trusted",
        "trusted",
        "trusted",
        "probe",
        "probe",
    ]
    assert all(item["diagnostic_only"] is True for item in pending["top5"])
    assert all(
        item["exact_plan_regime_validated"] is False
        for item in pending["top5"]
    )

    second_selection = {
        **pending,
        "diagnostic_only": True,
    }
    registry_two, _ = builder.update_registry(
        second_selection,
        observations(pending["top5"], 0.001),
        "b" * 64,
    )
    assert all(value["evidence_count"] == 2 for value in registry_two.values())
    assert all(
        value["trusted_prediction_eligible"] is True
        for value in registry_two.values()
    )

    calibrated = [
        builder.recalibrate(item, registry_two[builder.exact_regime(item)])
        for item in pending["top5"]
    ]
    trusted = builder.stability_separated_trusted_take(
        calibrated,
        3,
        minimum_interval_gap=0.002,
        minimum_lower_score=3.0,
    )
    assert len(trusted) == 3
    assert all(item["confidence"]["measurement_count"] == 2 for item in trusted)


def full_candidate(builder, index: int, score: float):
    params = {
        name: (lower + upper) / 2.0
        for name, (lower, upper) in builder.PARAMETER_BOUNDS.items()
    }
    params["cpu_index_tuple_cost"] += index * 0.00001
    return {
        **candidate(index, score),
        "candidate_params": params,
        "feature_ratio": {"cpu": 1.0, "io": 1.0, "memory": 1.0},
        "predicted_resource": {"cpu": 1.0, "io": 1.0, "memory": 1.0},
        "utilization": {"cpu": 0.5, "io": 0.5, "memory": 0.5},
        "dScore/dU": {"cpu": 1.0, "io": 1.0, "memory": 1.0},
        "T": 1.0,
        "confidence": {"measurement_count": 0},
        "g_j": 1.0,
        "eta": 1.0,
        "delta_param": 0.01,
        "three_level_fingerprint": {
            "id": f"three-level-sha256:{index:064x}",
            "plan_level": f"ratio-plan-sha256:{index:064x}",
            "feature_ratio_level": {"cpu": 1, "io": 1, "memory": 1},
            "predicted_resource_level": {"cpu": 1, "io": 1, "memory": 1},
        },
    }


def test_builder_fallback_emits_probe5_without_relaxing_credible_floor(
    tmp_path: Path,
):
    builder = import_path("tpch_v50_builder_fallback_test", BUILDER)
    previous = {
        "top5": [full_candidate(builder, 100 + index, 3.90) for index in range(5)]
    }
    summary = {"accepted_count": 1}
    scores = [3.960, 3.950, 3.940, 3.939, 3.938]
    expanded = [
        full_candidate(builder, index + 1, score)
        for index, score in enumerate(scores)
    ]
    expanded.extend(
        full_candidate(builder, index, 3.930 - index * 0.0001)
        for index in range(6, 24)
    )
    regimes = [builder.exact_regime(item) for item in expanded]
    registry = {
        regime: {
            "evidence": [{"source": "clean_diagnostic"}],
            "trusted_prediction_eligible": False,
        }
        for regime in regimes[2:5]
    }

    builder.validate_previous = lambda selection, result: [
        {"actual_score": value}
        for value in (3.940, 3.940, 3.940, 3.940, 3.940)
    ]
    builder.update_registry = lambda selection, results, digest: (registry, [])
    builder.first_observation_retest = lambda selection, results, values: None
    builder.hierarchical_probe_prior = lambda values: {"evidence_count": 5}

    def calibrate(raw, prior):
        item = copy.deepcopy(dict(raw))
        item["probe_acquisition_score"] = item["candidate_score"]
        item["confidence"] = {"measurement_count": 0, "unknown_regime": True}
        return item

    builder.hierarchical_calibrate_probe = calibrate
    previous_path = tmp_path / "previous.json"
    summary_path = tmp_path / "summary.json"
    expansion_path = tmp_path / "expansion.json"
    output_path = tmp_path / "output.json"
    previous_path.write_text(json.dumps(previous), encoding="utf-8")
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    expansion_path.write_text(
        json.dumps({"ranked_candidates": expanded}),
        encoding="utf-8",
    )

    output = builder.build(
        previous_path,
        summary_path,
        expansion_path,
        output_path,
        minimum_score_span=0.002,
    )

    assert output["selection_contract"] == (
        "exact_structural_plan_regime_stability_probe5_diagnostic_v1"
    )
    assert [item["selection_role"] for item in output["top5"]] == ["probe"] * 5
    assert output["selection_audit"]["trusted_count"] == 0
    assert output["selection_audit"]["credible_probe_count"] == 2
    assert output["credible_probe_expected_score_must_clear_target"] is True
    assert output["evidence_acquisition_slots_are_formal_candidates"] is False
    assert output["accepted_update_allowed"] is False
    assert output["incumbent_update_allowed"] is False
    assert output["range_update_allowed"] is False
    assert output["gradient_update_allowed"] is False


def probe5_selection(controller):
    update_fields = {
        "accepted_update_allowed": False,
        "incumbent_update_allowed": False,
        "range_update_allowed": False,
        "gradient_update_allowed": False,
    }
    top5 = []
    for rank in range(1, 6):
        top5.append(
            {
                "rank": rank,
                "selection_role": "probe",
                "trusted": False,
                "uncertain_probe": True,
                "trusted_evidence_acquisition_probe": True,
                "diagnostic_only": True,
                "qps_used_for_inner_scoring": False,
                **update_fields,
            }
        )
    return {
        "selection_contract": controller.ACQUISITION_SELECTION_CONTRACT,
        "trusted_evidence_acquisition_mode": True,
        "automatic_reexploration_required_after_clean5": True,
        "diagnostic_only": True,
        "formal_continuation": False,
        "formal_admission_from_this_selection_allowed": False,
        "unknown_plan_regime_trusted_allowed": False,
        "maximum_real_replay_invocations": 5,
        "trusted_slots": 0,
        "probe_slots": 5,
        "qps_used_for_inner_scoring": False,
        "top5": top5,
        **update_fields,
    }


def test_probe5_contract_never_labels_unknown_regime_trusted():
    controller = import_path("tpch_v50_controller_contract_test", CONTROLLER)
    selection = probe5_selection(controller)

    assert controller.strict_probe5_selection(selection)
    selection["top5"][0]["selection_role"] = "trusted"
    selection["top5"][0]["trusted"] = True
    assert not controller.strict_probe5_selection(selection)


def test_clean5_wrapper_preserves_probe_roles_in_evidence(tmp_path: Path):
    controller = import_path("tpch_v50_controller_clean5_test", CONTROLLER)
    selection_path = tmp_path / "selection.json"
    run_root = tmp_path / "run"
    run_root.mkdir()
    selection = probe5_selection(controller)
    summary = {
        "successful": [
            {"rank": rank, "selection_role": "probe"}
            for rank in range(1, 6)
        ]
    }
    selection_path.write_text(json.dumps(selection), encoding="utf-8")
    (run_root / "diagnostic_summary.json").write_text(
        json.dumps(summary),
        encoding="utf-8",
    )

    module = SimpleNamespace()

    def load(path: Path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    module.load = load

    def original_clean(selection_file, diagnostic_root, accepted, state_sha):
        selected = module.load(selection_file)
        observed = module.load(diagnostic_root / "diagnostic_summary.json")
        assert [item["selection_role"] for item in selected["top5"]] == [
            "trusted",
            "trusted",
            "trusted",
            "probe",
            "probe",
        ]
        assert [item["selection_role"] for item in observed["successful"]] == [
            "trusted",
            "trusted",
            "trusted",
            "probe",
            "probe",
        ]
        return {"accepted": accepted, "resume_state_sha256": state_sha}

    module.clean5_evidence = original_clean
    controller.install_probe5_clean_evidence(module)
    evidence = module.clean5_evidence(selection_path, run_root, 1, "a" * 64)

    assert evidence["selection_roles"] == ["probe"] * 5
    assert evidence["trusted_evidence_acquisition"] is True
    assert evidence["formal_state_mutated"] is False
    assert [item["selection_role"] for item in load(selection_path)["top5"]] == [
        "probe"
    ] * 5


def test_controller_reuses_deterministic_probe5_pair_after_preworkload_crash(
    tmp_path: Path,
):
    controller = import_path("tpch_v50_controller_reuse_test", CONTROLLER)
    builder_path = BUILDER.resolve()
    controller.PATCHED_BUILDER = builder_path
    controller.EXPECTED_PATCHED_BUILDER_SHA256 = controller.sha256(builder_path)

    v48 = SimpleNamespace(
        STABILITY_ACQUISITION_BUILDER=builder_path,
        EXPECTED={builder_path: controller.sha256(builder_path)},
        DETERMINISTIC_FAILURES={"exact_repeat": ()},
        load=lambda path: json.loads(Path(path).read_text(encoding="utf-8")),
        clean5_evidence=lambda *args, **kwargs: {},
    )
    original_probe_calls = []
    v26 = SimpleNamespace()
    v26.load = v48.load

    def original_probe(*args, **kwargs):
        original_probe_calls.append((args, kwargs))
        raise AssertionError("completed deterministic pair must be reused")

    v26.build_probe = original_probe
    v26.build_exact_repeat = lambda *args, **kwargs: tmp_path / "repeat.json"
    v41 = SimpleNamespace(configure_controller=lambda *args, **kwargs: (v26, object()))
    controller.install_algorithm(v48, v41)
    installed_v26, common = v41.configure_controller()

    cycle_root = tmp_path / "cycle_005"
    cycle_root.mkdir()
    expansion = cycle_root / "dynamic_expansion_v32" / "expanded_candidates.a.json"
    expansion.parent.mkdir()
    expansion.write_text("{}\n", encoding="utf-8")
    selection = probe5_selection(controller)
    selection.update(
        {
            "accepted_count_start": 1,
            "inherited_accepted_count": 1,
            "source_evidence": {
                "expansion": {
                    "path": str(expansion),
                    "sha256": controller.sha256(expansion),
                }
            },
        }
    )
    first = cycle_root / "probe_top5.a.json"
    second = cycle_root / "probe_top5.b.json"
    payload = json.dumps(selection, sort_keys=True) + "\n"
    first.write_text(payload, encoding="utf-8")
    second.write_text(payload, encoding="utf-8")

    reused = installed_v26.build_probe(
        common,
        cycle_root,
        tmp_path / "previous.json",
        tmp_path / "previous_run",
        tmp_path / "formal_state.json",
        1,
        {},
    )

    assert reused == first
    assert original_probe_calls == []


def test_upgrade_boundary_validator_reuses_strict_v49_preparing_cycle_gate():
    validator = import_path("tpch_v50_boundary_validator_test", BOUNDARY_VALIDATOR)
    captured = {}
    v41 = object()
    paths = {"state": Path("/sealed/controller_state.json")}
    v48 = SimpleNamespace(
        verified_v41=lambda: v41,
        current_paths=lambda module: paths if module is v41 else None,
    )

    def validate_boundary(module49, module48, module41, observed_paths, **kwargs):
        captured.update(kwargs)
        assert module49 is v49
        assert module48 is v48
        assert module41 is v41
        assert observed_paths is paths
        return {
            "preflight": {
                "status": "ready_to_continue_clean_preparing_cycle",
                "cycle": kwargs["expected_cycle"],
            }
        }

    v49 = SimpleNamespace(
        import_v48=lambda: v48,
    )
    validator.import_v49 = lambda: v49
    validator.validate_boundary = validate_boundary
    result = validator.validate("a" * 64, "b" * 64, "c" * 64, 9)

    assert captured == {
        "expected_state_sha": "a" * 64,
        "expected_formal_sha": "b" * 64,
        "expected_counter_sha": "c" * 64,
        "expected_cycle": 9,
    }
    assert result["safe_to_replace_inactive_v50_code"] is True
    assert result["new_workload_calls"] == 0
    assert result["formal_state_mutated"] is False
    assert result["accepted_counter_mutated"] is False


def test_cycle5_exact_repeat_negative_gate_is_a_strict_upgrade_boundary(tmp_path):
    controller = import_path("tpch_v50_negative_controller_test", CONTROLLER)
    validator = import_path("tpch_v50_negative_boundary_test", BOUNDARY_VALIDATOR)
    loop = tmp_path / "loop"
    cycle5 = loop / "cycle_005"
    cycle6 = loop / "cycle_006"
    run_root = cycle5 / "diagnostic_probe"
    run_root.mkdir(parents=True)
    cycle6.mkdir(parents=True)
    selection_path = cycle5 / "probe_top5.a.json"
    selection = probe5_selection(controller)
    for rank, item in enumerate(selection["top5"], 1):
        item["three_level_fingerprint"] = {
            "id": f"three-level-sha256:{rank:064x}"
        }
    write_json = lambda path, value: path.write_text(
        json.dumps(value, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_json(selection_path, selection)
    selection_sha = validator.sha256(selection_path)
    formal = tmp_path / "formal.json"
    counter = tmp_path / "counter.txt"
    write_json(formal, {"accepted_count": 1})
    counter.write_text("1\n", encoding="utf-8")
    formal_sha = validator.sha256(formal)
    diagnostic_common = {
        "schema_version": "tpch_exact_plan_regime_diagnostic_state_v1",
        "status": "complete",
        "diagnostic_only": True,
        "accepted_count": 1,
        "physical_call_count": 5,
        "valid_replay_count": 5,
        "technical_failure_count": 0,
        "baseline_restored": True,
        "selection_sha256": selection_sha,
        "resume_state_sha256": formal_sha,
        "formal_state_mutation_allowed": False,
        "batch_added_accepted_count": 0,
        "full_selection_replay": True,
        "selection_replayed_ranks": [1, 2, 3, 4, 5],
        "selection_excluded_ranks": [],
        "maximum_real_replay_invocations": 5,
        "accepted_update_allowed": False,
        "incumbent_update_allowed": False,
        "range_update_allowed": False,
        "gradient_update_allowed": False,
    }
    write_json(run_root / "diagnostic_state.json", diagnostic_common)
    successful = []
    for rank, item in enumerate(selection["top5"], 1):
        successful.append(
            {
                "rank": rank,
                "selection_role": "probe",
                "valid_replay": True,
                "technical_failure": False,
                "diagnostic_only": True,
                "accepted_update_eligible": False,
                "accepted_update_allowed": False,
                "incumbent_update_allowed": False,
                "range_update_allowed": False,
                "gradient_update_allowed": False,
                "qps_used_for_inner_scoring": False,
                "three_level_fingerprint": copy.deepcopy(
                    item["three_level_fingerprint"]
                ),
            }
        )
    summary = {
        **diagnostic_common,
        "schema_version": "tpch_exact_plan_regime_diagnostic_batch_summary_v1",
        "successful": successful,
        "technical_failures": [],
        "accepted_update_applied": False,
        "incumbent_update_applied": False,
        "range_update_applied": False,
        "gradient_update_applied": False,
    }
    write_json(run_root / "diagnostic_summary.json", summary)
    clean = validator._clean_probe5(selection_path, run_root, formal_sha)
    failure_log = cycle5 / "exact_builder_a.log"
    failure_log.write_text(validator.REEXPLORE_PHRASE + "\n", encoding="utf-8")
    audit_path = cycle5 / "v44_deterministic_negative_gate.json"
    audit = {
        "contract": validator.NEGATIVE_GATE_CONTRACT,
        "cycle": 5,
        "status": "reexplore_after_exact_repeat",
        "deterministic_negative_gate": True,
        "failure_stage": "exact_repeat",
        "failure_reason": validator.REEXPLORE_PHRASE,
        "failure_evidence_path": str(failure_log.resolve()),
        "failure_evidence_sha256": validator.sha256(failure_log),
        "accepted_before": 1,
        "accepted_after": 1,
        "diagnostics": [clean],
        "physical_workload_calls": 5,
        "valid_replays": 5,
        "technical_failures": 0,
        "negative_gate_new_workload_calls": 0,
        "formal_started": False,
        "formal_state_mutated": False,
        "formal_state": str(formal.resolve()),
        "formal_state_sha256": formal_sha,
        "accepted_counter_mutated": False,
        "manual_candidate_injection": False,
        "thresholds_relaxed": False,
        "next_cycle": 6,
        "next_action": "automatic_bounded_explain_reexploration",
        "novelty_gate_unchanged": "five_new_noncorner_three_level_fingerprints",
        "role_gate_unchanged": "trusted3_probe2",
        "per_diagnostic_physical_cap": 5,
        "qps_role": "outer_audit_only_not_inner_score",
    }
    write_json(audit_path, audit)
    last_cycle = {
        **audit,
        "negative_gate_path": str(audit_path.resolve()),
        "negative_gate_sha256": validator.sha256(audit_path),
    }
    state_path = loop / "controller_state.json"
    state = {
        "schema_version": "tpch_fresh10_target10_loop_v26",
        "status": "preparing_cycle",
        "accepted_count": 1,
        "accepted_target": 10,
        "next_cycle": 6,
        "resume_state": str(formal.resolve()),
        "cycles": [last_cycle],
    }
    write_json(state_path, state)
    for name in ("config.a.json", "config.b.json"):
        write_json(cycle6 / name, {"cycle": 6})
    expansion = cycle6 / "dynamic_expansion_v32"
    expansion.mkdir()
    for name in ("expanded_candidates.a.json", "expanded_candidates.b.json"):
        write_json(expansion / name, {"ranked_candidates": []})
    write_json(expansion / "audit.json", {"complete": True})
    (cycle6 / "probe_builder_a.log").write_text(
        fixture_failure := json.loads(
            V51_FAILURE_FIXTURE.read_text(encoding="utf-8")
        )["failure"] + "\n",
        encoding="utf-8",
    )

    allowed = {
        "config.a.json",
        "config.b.json",
        "dynamic_expansion_v32/expanded_candidates.a.json",
        "dynamic_expansion_v32/expanded_candidates.b.json",
        "dynamic_expansion_v32/audit.json",
        "probe_builder_a.log",
    }
    v49 = SimpleNamespace(_allowed_preworkload_file=lambda path: path in allowed)
    v48 = SimpleNamespace(
        ensure_no_formal_started=lambda root: None,
        validate_counter=lambda path, digest: (
            None
            if validator.sha256(path) == digest
            and path.read_text(encoding="utf-8").strip() == "1"
            else (_ for _ in ()).throw(RuntimeError("counter mismatch"))
        ),
        validate_formal_authority=lambda path, digest: (
            None
            if validator.sha256(path) == digest
            else (_ for _ in ()).throw(RuntimeError("formal mismatch"))
        ),
    )
    boundary = validator._negative_gate_boundary(
        v49,
        v48,
        SimpleNamespace(V41_LOOP=loop),
        {"state": state_path, "counter": counter},
        expected_state_sha=validator.sha256(state_path),
        expected_formal_sha=formal_sha,
        expected_counter_sha=validator.sha256(counter),
        expected_cycle=6,
    )
    assert boundary["preflight"]["boundary_kind"] == (
        "cycle5_exact_repeat_negative_gate"
    )
    assert boundary["evidence_selection"] == selection_path
    assert boundary["preflight"]["completed_expansion_reused"] is True
    assert boundary["protected_logs"] == [cycle6 / "probe_builder_a.log"]
    assert "credible_unknown_probes=0" in fixture_failure


def test_deploy_requires_locked_validated_boundary_before_replacement():
    source = DEPLOY.read_text(encoding="utf-8")
    boundary_call = source.index('"$python_bin" -u "$stage/$validator_name"')
    replacement_loop = source.index(
        'for name in "$controller_name" "$builder_name" "$launcher_name" "$validator_name"'
    )
    assert "flock -n 9" in source
    assert boundary_call < replacement_loop
    assert "safe_to_replace_inactive_v50_code == true" in source
    assert "new_workload_calls == 0" in source
    assert (
        "dddb9edd6cc6d27a01352bc0d9255f3f87c3f3555ec8ef69f0f2b4e9c93a6036"
        in source
    )
    assert (
        "210430646ceadfbe2eec4d86927ce8888561736f9d94b3650e521914d510193a"
        in source
    )
    assert (
        "8662a1defd9ad44eb266c70ac6ff33dd1fe1a4c4d74034e4b675114c6aea220a"
        in source
    )
    assert (
        "2dade67f736eabe116ee76246836c175dd928648b4f8f1cabbf7255c50baea20"
        in source
    )

    apply_source = BOUNDARY_APPLY.read_text(encoding="utf-8")
    assert apply_source.index(
        'bash "$stage/deploy_tpch_v50_trusted_evidence_acquisition.sh"'
    ) < apply_source.index(
        "/tmp/launch_tpch_v50_trusted_evidence_acquisition.sh"
    )
    assert "  preflight \\" in apply_source
    assert "workload_started: false" in apply_source
    assert "  resume \\" not in apply_source

    controller_source = CONTROLLER.read_text(encoding="utf-8")
    assert "boundary_validator.validate_boundary(" in controller_source
    assert "return v49.resume_boundary(v48, v41, paths, boundary)" in controller_source
