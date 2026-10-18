from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
EXPANDER = ROOT / "tools/tpch_exact_regime_neighbor_expand_v6_checkpointed.py"
RECOVERY = ROOT / "tools/tpch_v42_cycle004_checkpoint_restart.py"
MANIFEST = ROOT / "tools/tpch_v42_cycle004_checkpoint_restart_manifest.json"
RACE_REPAIR = ROOT / "tools/tpch_v43_checkpoint_race_zero_call_repair.py"
POSTLAUNCH = ROOT / "tools/tpch_v43_postlaunch_readonly_audit.py"
STABILITY_BUILDER = (
    ROOT / "tools/build_tpch_autonomous_successor_top5_v10_stability_acquisition.py"
)
CHECKPOINTED_CONTROLLER = (
    ROOT / "tools/tpch_fresh10_target10_loop_v32_v7_checkpointed.py"
)
STABILITY_DIAGNOSTIC_RUNNER = (
    ROOT / "tools/run_tpch_round29_diagnostic_exact_plan_regime_batch_v43_stability.py"
)


def import_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_v43_acquires_three_exact_regime_stability_points_without_probe5() -> None:
    builder = import_path("tpch_v43_stability_builder_test", STABILITY_BUILDER)

    def candidate(index: int, score: float, measurement_count: int):
        return {
            "candidate_score": score,
            "candidate_params": {"unique": float(index)},
            "boundary_corner": False,
            "boundary_plateau": False,
            "actual_parameter_noncorner_audit": {
                "reason": "actual_parameter_space_noncorner"
            },
            "confidence": {"measurement_count": measurement_count},
            "prediction_interval": {},
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

    trusted = [candidate(index, 4.1 - index * 0.01, 1) for index in range(1, 4)]
    probes = [candidate(index, 4.0 - index * 0.01, 0) for index in range(4, 29)]
    stable = builder.stability_separated_trusted_take(
        trusted, 3, 0.002, 3.95
    )
    acquisition_a = builder.stability_probe_take(trusted, 3)
    acquisition_b = builder.stability_probe_take(trusted, 3)
    selected_probes, _ = builder.score_ladder_probe_take(probes, 2, 3.95)
    assert stable == []
    assert acquisition_a == acquisition_b
    assert len(acquisition_a) == 3
    assert len(selected_probes) == 2
    top5 = [*acquisition_a, *selected_probes]
    assert len(top5) == 5
    assert len(
        {
            item["three_level_fingerprint"]["id"]
            for item in top5
        }
    ) == 5


def test_v43_reuses_completed_expansion_without_running_expander(
    tmp_path: Path,
) -> None:
    controller = import_path(
        "tpch_v43_completed_expansion_reuse_test", CHECKPOINTED_CONTROLLER
    )
    cycle = tmp_path / "cycle_004"
    dynamic = cycle / "dynamic_expansion_v32"
    dynamic.mkdir(parents=True)
    evidence = tmp_path / "selection.json"
    resume = tmp_path / "formal_state.json"
    write_json(evidence, {"top5": []})
    write_json(resume, {"accepted_count": 1})

    resume_sha = file_sha256(resume)
    payload = {
        "diagnostic_only": True,
        "explain_only": True,
        "new_workload_calls": 0,
        "accepted_update_allowed": False,
        "incumbent_update_allowed": False,
        "range_update_allowed": False,
        "gradient_update_allowed": False,
        "qps_used_for_inner_scoring": False,
        "incumbent_coordinate_neighbor_count": 5,
        "ranked_candidates": [{"candidate_score": 4.0}] * 5,
        "formal_incumbent_calibration": {
            "resume_state_sha256": resume_sha,
            "historical_candidate_initial_resource_used": False,
        },
        "bounded_expansion_audit": {
            "contract": "tpch_explain_expansion_hard_budget_v1",
            "candidate_estimations": 128,
            "maximum_candidate_estimations": 128,
            "explain_invocations": 100,
            "logical_explain_requests": 100,
            "physical_explain_invocations_this_process": 100,
            "checkpoint_cache_hits_this_process": 0,
            "singleflight_cache_hits_this_process": 0,
            "physical_explain_budget_consumed_cumulative": 100,
            "maximum_explain_invocations": 3000,
            "cache_replay_consumes_new_physical_budget": False,
            "budget_exhausted": True,
            "checkpoint_schema_version": "tpch_explain_expansion_checkpoint_v1",
            "checkpoint_input_binding": {"resume_state_sha256": resume_sha},
            "elapsed_wall_seconds": 1.0,
        },
    }
    replayed = copy.deepcopy(payload)
    replayed["bounded_expansion_audit"][
        "physical_explain_invocations_this_process"
    ] = 0
    replayed["bounded_expansion_audit"][
        "checkpoint_cache_hits_this_process"
    ] = 100
    replayed["bounded_expansion_audit"]["elapsed_wall_seconds"] = 0.1
    first = dynamic / "expanded_candidates.a.json"
    second = dynamic / "expanded_candidates.b.json"
    write_json(first, payload)
    write_json(second, replayed)
    write_json(
        dynamic / "audit.json",
        {
            "contract": "tpch_incumbent_bound_dynamic_expansion_v32",
            "status": "complete",
            "source_evidence_sha256": file_sha256(evidence),
            "resume_state_sha256": resume_sha,
            "expansion_sha256": file_sha256(first),
            "replayed_expansion_sha256": file_sha256(second),
            "deterministic_double_generation": True,
            "checkpoint_replay_used": True,
            "generation_b_zero_physical_explain": True,
            "ranked_candidate_count": 5,
            "incumbent_coordinate_neighbor_count": 5,
            "physical_workload_calls": 0,
            "formal_state_mutated": False,
            "manual_candidate_injection": False,
            "thresholds_relaxed": False,
        },
    )

    def forbidden_run(*args, **kwargs):
        raise AssertionError("completed expansion reuse must not run expander")

    fake_v26 = SimpleNamespace(
        B=tmp_path,
        load=lambda path: json.loads(path.read_text(encoding="utf-8")),
        sha256=file_sha256,
        verify=lambda path, expected: None,
        run=forbidden_run,
    )
    reused = controller.dynamic_expansion(
        fake_v26,
        SimpleNamespace(),
        SimpleNamespace(),
        cycle,
        evidence,
        resume,
        {},
    )
    assert reused == first


def test_v43_readonly_audit_reseals_exact_legacy_completion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    audit_module = import_path(
        "tpch_v43_legacy_reseal_test", POSTLAUNCH
    )
    formal_sha = "f" * 64
    first = tmp_path / "expanded_candidates.a.json"
    second = tmp_path / "expanded_candidates.b.json"
    audit_path = tmp_path / "audit.json"
    checkpoint = tmp_path / "explain_checkpoint.json"
    bounded = {
        "contract": "tpch_explain_expansion_hard_budget_v1",
        "budget_exhausted": True,
        "budget_exhaustion_reason": "candidate-estimation budget exhausted",
        "candidate_estimations": 128,
        "maximum_candidate_estimations": 128,
        "maximum_explain_invocations": 3000,
        "checkpoint_live_explain_invocations": 2817,
        "checkpoint_prior_delivery_failure_invocations": 5,
        "checkpoint_delivery_failure_outputs_recovered": True,
        "explain_invocations": 2839,
        "elapsed_wall_seconds": 330.0,
        "checkpoint_input_binding": {
            "resume_state_sha256": formal_sha,
            "evidence_top5_sha256": audit_module.LEGACY_EVIDENCE_SHA256,
        },
    }
    payload = {
        "diagnostic_only": True,
        "explain_only": True,
        "new_workload_calls": 0,
        "accepted_update_allowed": False,
        "incumbent_update_allowed": False,
        "range_update_allowed": False,
        "gradient_update_allowed": False,
        "qps_used_for_inner_scoring": False,
        "ranked_candidates": [{}] * 95,
        "incumbent_coordinate_neighbor_count": 23,
        "formal_incumbent_calibration": {"resume_state_sha256": formal_sha},
        "bounded_expansion_audit": bounded,
    }
    replayed = copy.deepcopy(payload)
    replayed["bounded_expansion_audit"]["elapsed_wall_seconds"] = 8.0
    write_json(first, payload)
    write_json(second, replayed)
    write_json(checkpoint, {"sealed": True})
    write_json(
        audit_path,
        {
            "status": "complete",
            "deterministic_double_generation": True,
            "checkpoint_replay_used": True,
            "deterministic_comparison_ignored_fields": [
                "bounded_expansion_audit.elapsed_wall_seconds"
            ],
            "expansion_sha256": audit_module.LEGACY_COMPLETE_A_SHA256,
            "replayed_expansion_sha256": audit_module.LEGACY_COMPLETE_B_SHA256,
            "checkpoint_sha256": audit_module.LEGACY_COMPLETE_CHECKPOINT_SHA256,
            "resume_state_sha256": formal_sha,
            "source_evidence_sha256": audit_module.LEGACY_EVIDENCE_SHA256,
            "ranked_candidate_count": 95,
            "incumbent_coordinate_neighbor_count": 23,
            "incumbent_coordinate_above_incumbent_count": 5,
            "physical_workload_calls": 0,
            "formal_state_mutated": False,
            "manual_candidate_injection": False,
            "thresholds_relaxed": False,
        },
    )
    real_sha256 = audit_module.sha256
    sealed = {
        first: audit_module.LEGACY_COMPLETE_A_SHA256,
        second: audit_module.LEGACY_COMPLETE_B_SHA256,
        audit_path: audit_module.LEGACY_COMPLETE_AUDIT_SHA256,
        checkpoint: audit_module.LEGACY_COMPLETE_CHECKPOINT_SHA256,
    }
    monkeypatch.setattr(
        audit_module,
        "sha256",
        lambda path: sealed.get(path, real_sha256(path)),
    )
    result = audit_module.validate_expansion(
        first,
        second,
        audit_path,
        formal_sha,
        checkpoint,
    )
    assert result["legacy_complete_semantic_reseal"] is True
    assert result["new_explain_invocations_for_reseal"] == 0


def test_v43_diagnostic_runner_accepts_only_state_neutral_stability_contract() -> None:
    runner = import_path("tpch_v43_stability_runner_test", STABILITY_DIAGNOSTIC_RUNNER)
    params = {
        "cpu_index_tuple_cost": 0.005,
        "cpu_operator_cost": 0.0025,
        "cpu_tuple_cost": 0.01,
        "parallel_setup_cost": 1000.0,
        "parallel_tuple_cost": 0.1,
        "random_page_cost": 4.0,
        "seq_page_cost": 1.0,
    }
    top5 = []
    for rank in range(1, 6):
        trusted = rank <= 3
        top5.append(
            {
                "rank": rank,
                "selection_role": "trusted" if trusted else "probe",
                "three_level_fingerprint": {"id": f"fp-{rank}"},
                "candidate_params": params,
                "feature_ratio": {},
                "predicted_resource": {},
                "utilization": {},
                "dScore/dU": 0.0,
                "T": 1.0,
                "confidence": {},
                "g_j": {},
                "eta": 0.1,
                "delta_param": {},
                "candidate_score": 4.0,
                "diagnostic_only": True,
                "qps_used_for_inner_scoring": False,
                "incumbent_update_eligible": False,
                "eligible_for_best_candidate": False,
                "accepted_update_allowed": False,
                "incumbent_update_allowed": False,
                "range_update_allowed": False,
                "gradient_update_allowed": False,
                "trusted_stability_gate_passed": False,
                "formal_trust_requires_independent_diagnostic": trusted,
            }
        )
    selection = {
        "selection_contract": runner.STABILITY_ACQUISITION_CONTRACT,
        "top5": top5,
        "stability_acquisition_mode": True,
        "stable_trusted_ready_before_diagnostic": False,
        "formal_admission_from_this_selection_allowed": False,
        "formal_admission_requires_exact_and_robust_repeat": True,
        "automatic_recovery_mode": True,
        "automatic_recovery_reason": "exact_regime_trusted_stability_acquisition",
        "diagnostic_only": True,
        "formal_continuation": False,
        "serial_real_replay_required": True,
        "maximum_real_replay_invocations": 5,
        "qps_used_for_inner_scoring": False,
        "technical_failures_count_toward_physical_cap": True,
        "technical_failures_count_as_valid_or_accepted": False,
        "unknown_plan_regime_trusted_allowed": False,
        "accepted_update_allowed": False,
        "incumbent_update_allowed": False,
        "range_update_allowed": False,
        "gradient_update_allowed": False,
    }
    assert len(runner.validate_selection(selection)) == 5
    contaminated = copy.deepcopy(selection)
    contaminated["formal_admission_from_this_selection_allowed"] = True
    try:
        runner.validate_selection(contaminated)
    except ValueError as exc:
        assert "stability-acquisition selection contract mismatch" in str(exc)
    else:
        raise AssertionError("stability acquisition must never directly enter formal")


def test_v43_clean5_pipeline_reaches_formal_compatible_reseal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_path = Path("/tmp/tpch_cycle004_v43_evidence/probe_top5.a.json")
    if not source_path.is_file():
        return
    exact_builder = import_path(
        "tpch_v43_exact_repeat_pipeline_test",
        ROOT / "tools/build_tpch_exact_plan_repeat_validation.py",
    )
    robust_builder = import_path(
        "tpch_v43_robust_repeat_pipeline_test",
        Path("/tmp/build_tpch_two_observation_robust_repeat_v3.py"),
    )
    resealer = import_path(
        "tpch_v43_reseal_pipeline_test",
        Path("/tmp/build_tpch_three_observation_postrobust_reseal_v2.py"),
    )
    analyzer = import_path(
        "tpch_v43_analyzer_pipeline_test",
        ROOT / "tools/analyze_top5_validation_gate.py",
    )
    diagnostic_runner = import_path(
        "tpch_v43_diagnostic_pipeline_test", STABILITY_DIAGNOSTIC_RUNNER
    )
    formal_runner = import_path(
        "tpch_v43_formal_pipeline_test",
        ROOT / "tools/run_tpch_formal_exact_plan_regime_batch_v41_robust4.py",
    )

    source = json.loads(source_path.read_text(encoding="utf-8"))
    selection = tmp_path / "source.json"
    write_json(selection, source)

    def clean5(payload, selection_path: Path):
        successful = []
        for candidate in payload["top5"]:
            successful.append(
                {
                    "rank": candidate["rank"],
                    "selection_role": candidate["selection_role"],
                    "actual_resource": copy.deepcopy(candidate["predicted_resource"]),
                    "actual_utilization": copy.deepcopy(candidate["utilization"]),
                    "actual_score": candidate["candidate_score"],
                    "three_level_fingerprint": copy.deepcopy(
                        candidate["three_level_fingerprint"]
                    ),
                    "technical_failure": False,
                    "valid_replay": True,
                }
            )
        return {
            "schema_version": "offline_clean5_v1",
            "selection_sha256": file_sha256(selection_path),
            "diagnostic_only": True,
            "physical_call_count": 5,
            "valid_replay_count": 5,
            "technical_failure_count": 0,
            "baseline_restored": True,
            "accepted_count": 1,
            "accepted_update_applied": False,
            "incumbent_update_applied": False,
            "range_update_applied": False,
            "gradient_update_applied": False,
            "successful": successful,
        }

    source_summary = tmp_path / "source_summary.json"
    write_json(source_summary, clean5(source, selection))
    repeat = tmp_path / "repeat.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(exact_builder.__file__),
            "--selection", str(selection),
            "--summary", str(source_summary),
            "--minimum-score-span", "0.002",
            "--output", str(repeat),
        ],
    )
    assert exact_builder.main() == 0
    repeat_payload = json.loads(repeat.read_text(encoding="utf-8"))
    assert len(diagnostic_runner.validate_selection(repeat_payload)) == 5

    repeat_run = tmp_path / "repeat_run"
    repeat_run.mkdir()
    repeat_summary = repeat_run / "diagnostic_summary.json"
    write_json(repeat_summary, clean5(repeat_payload, repeat))
    robust = tmp_path / "robust.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(robust_builder.__file__),
            "--source-selection", str(selection),
            "--source-summary", str(source_summary),
            "--repeat-selection", str(repeat),
            "--repeat-summary", str(repeat_summary),
            "--maximum-trusted-score-span", "0.01",
            "--maximum-trusted-resource-factor", "1.5",
            "--output", str(robust),
        ],
    )
    assert robust_builder.main() == 0
    robust_payload = json.loads(robust.read_text(encoding="utf-8"))
    assert len(diagnostic_runner.validate_selection(robust_payload)) == 5
    assert robust_payload["formal_admission_blocked_until_three_observation_reseal"] is True

    third_run = tmp_path / "third_run"
    third_run.mkdir()
    third_summary = third_run / "diagnostic_summary.json"
    third_state = third_run / "diagnostic_state.json"
    write_json(third_summary, clean5(robust_payload, robust))
    write_json(
        third_state,
        {
            "selection_sha256": file_sha256(robust),
            "status": "complete",
            "diagnostic_only": True,
            "physical_call_count": 5,
            "valid_replay_count": 5,
            "technical_failure_count": 0,
            "baseline_restored": True,
            "accepted_count": 1,
            "accepted_update_allowed": False,
            "incumbent_update_allowed": False,
            "range_update_allowed": False,
            "gradient_update_allowed": False,
        },
    )
    resealed = tmp_path / "resealed.json"
    resealed_summary = tmp_path / "resealed_summary.json"
    resealed_state = tmp_path / "resealed_state.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(resealer.__file__),
            "--selection", str(robust),
            "--third-observation-summary", str(third_summary),
            "--maximum-trusted-score-span", "0.01",
            "--adaptive-score-span-cap", "0.05",
            "--maximum-trusted-resource-factor", "1.5",
            "--output", str(resealed),
            "--output-summary", str(resealed_summary),
            "--output-state", str(resealed_state),
        ],
    )
    assert resealer.main() == 0
    resealed_payload = json.loads(resealed.read_text(encoding="utf-8"))
    assert len(diagnostic_runner.validate_selection(resealed_payload)) == 5
    formal_audits, formal_points = formal_runner.install_robust_measurement(
        resealed_payload
    )
    assert formal_audits == {}
    assert len(formal_points) == 5

    gate = tmp_path / "gate.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(analyzer.__file__),
            "--selection", str(resealed),
            "--observations", str(resealed_summary),
            "--output", str(gate),
        ],
    )
    assert analyzer.main() == 0
    gate_payload = json.loads(gate.read_text(encoding="utf-8"))
    assert gate_payload["checks"]["trusted_only_spearman_positive"] is True
    assert gate_payload["checks"]["actual_best_in_predicted_top3"] is True
    assert gate_payload["checks"][
        "trusted_resource_predictions_no_order_of_magnitude_error"
    ] is True

    resume_state = tmp_path / "formal_state.json"
    write_json(resume_state, {"incumbent_score": 3.9442345874528946})
    checkpointed = import_path(
        "tpch_v43_acceptance_pipeline_test", CHECKPOINTED_CONTROLLER
    )
    fake_v26 = SimpleNamespace(
        load=lambda path: json.loads(path.read_text(encoding="utf-8")),
        sha256=file_sha256,
    )
    feasibility = checkpointed.acceptance_feasibility(
        fake_v26,
        resealed,
        resume_state,
    )
    assert feasibility["passed"] is True
    assert feasibility["physical_workload_calls"] == 0
    assert feasibility["formal_state_mutated"] is False
    assert feasibility["feasible_trusted_ranks"]

    common = import_path(
        "tpch_v43_formal_promotion_pipeline_test",
        ROOT / "tools/tpch_fresh10_robust_validation_v22.py",
    )
    formal_source = tmp_path / "formal_source"
    formal_source.mkdir()
    write_json(
        formal_source / "diagnostic_summary.json",
        json.loads(resealed_summary.read_text(encoding="utf-8")),
    )
    write_json(
        formal_source / "diagnostic_state.json",
        json.loads(resealed_state.read_text(encoding="utf-8")),
    )
    v30 = import_path(
        "tpch_v43_v30_pipeline_test",
        ROOT / "tools/tpch_fresh10_target10_loop_v30.py",
    )
    v30.validate_resealed_summary(
        SimpleNamespace(
            load=lambda path: json.loads(path.read_text(encoding="utf-8")),
            sha256=file_sha256,
        ),
        resealed,
        formal_source,
    )
    common.ROBUST_SELECTION_A = resealed
    common.VALIDATION_RUN = formal_source
    common.FORMAL_PACKAGE = tmp_path / "formal_package"
    common.FORMAL_RUN = tmp_path / "formal_run"
    common.EXPECTED["config"] = "c" * 64
    formal_selection_path, formal_gate_path = common.promote_formal(
        resealed_payload,
        gate_payload,
    )
    formal_selection = json.loads(
        formal_selection_path.read_text(encoding="utf-8")
    )
    assert formal_selection["diagnostic_only"] is False
    assert formal_selection["formal_continuation"] is True
    assert formal_selection["qps_used_for_inner_scoring"] is False
    assert formal_selection["accepted_update_allowed"] is True
    assert json.loads(formal_gate_path.read_text(encoding="utf-8"))["passed"] is True
    for trusted in formal_selection["top5"][:3]:
        assert trusted["accepted_update_allowed"] is True
        assert trusted["incumbent_update_allowed"] is True
        assert trusted["range_update_allowed"] is True
    for probe in formal_selection["top5"][3:]:
        assert probe["accepted_update_allowed"] is False
        assert probe["incumbent_update_allowed"] is False
        assert probe["range_update_allowed"] is False
        assert probe["gradient_update_allowed"] is False
    _, promoted_points = formal_runner.install_robust_measurement(formal_selection)
    assert len(promoted_points) == 5

    for diagnostic_payload in (source, repeat_payload, robust_payload, resealed_payload):
        assert diagnostic_payload["maximum_real_replay_invocations"] == 5
        assert diagnostic_payload["accepted_update_allowed"] is False
        assert diagnostic_payload["incumbent_update_allowed"] is False
        assert diagnostic_payload["range_update_allowed"] is False
        assert diagnostic_payload["gradient_update_allowed"] is False
        assert diagnostic_payload["qps_used_for_inner_scoring"] is False
        assert [item["selection_role"] for item in diagnostic_payload["top5"]] == [
            "trusted", "trusted", "trusted", "probe", "probe"
        ]
        for probe in diagnostic_payload["top5"][3:]:
            assert probe["accepted_update_allowed"] is False
            assert probe["incumbent_update_allowed"] is False
            assert probe["range_update_allowed"] is False
            assert probe["gradient_update_allowed"] is False


def test_deployment_manifest_and_launcher_are_sealed() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for remote, expected in manifest["artifacts"].items():
        local = ROOT / "tools" / Path(remote).name
        assert local.is_file()
        assert file_sha256(local) == expected
    launcher = (
        ROOT / "tools/launch_tpch_v42_cycle004_checkpoint_restart.sh"
    ).read_text(encoding="utf-8")
    assert "/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11" in launcher
    assert "/tmp/tpch_v42_cycle004_checkpoint_restart.py" in launcher
    assert "preflight) mode_flag=--preflight-only" in launcher
    assert "apply) mode_flag=--apply-recovery-only" in launcher
    assert "resume) mode_flag=--resume" in launcher
    assert manifest["source_controller_state"]["accepted_count"] == 1
    assert manifest["source_controller_state"]["sha256"] == (
        "216e65012e1d80905fa1773c14d4d2de5ff8beb0d2bed92723bb759ba8bc62e2"
    )


def test_budget_boundary_is_partial_and_checkpoint_replay_is_zero_live_calls(
    tmp_path: Path,
    monkeypatch,
) -> None:
    expander = import_path("tpch_v42_expander_test", EXPANDER)
    code_root = tmp_path / "code"
    code_root.mkdir()
    (code_root / "ratio_scale_resource_estimator.py").write_text(
        """
from concurrent.futures import ThreadPoolExecutor
import json
import math

class RatioScaleResourceEstimator:
    def __init__(self, *, psql_query_func, **kwargs):
        self.psql = psql_query_func

    def calibrate(self, queries, params, baseline_real):
        with ThreadPoolExecutor(max_workers=22) as pool:
            outputs = list(
                pool.map(self.psql, [f"CALIBRATE-{i // 2}" for i in range(22)])
            )
        if len(outputs) != 22 or any(value != "cached-plan\\n" for value in outputs):
            raise RuntimeError("complete coverage failed")
        return {"baseline_features": {"query_features": [
            {"queryid": "q1", "plan_fingerprint": "plan0"}
        ]}}

    def estimate_with_metadata(self, queries, params):
        self.psql("EXPLAIN " + json.dumps(params, sort_keys=True))
        ratio = float(params["cpu_index_tuple_cost"]) / 0.005
        shift = math.log(max(ratio, 1e-12))
        resource = {"cpu": 5.0 + shift, "io": 5.0 + shift, "memory": 8.5 + shift}
        metadata = {
            "workload_plan_fingerprint": "plan0",
            "feature_ratios": {
                "cpu": 1.0 + shift,
                "io": 1.0 + shift,
                "memory": 1.0 + shift,
            },
            "query_features": [{"queryid": "q1", "plan_fingerprint": "plan0"}],
        }
        return resource, {}, metadata
""",
        encoding="utf-8",
    )
    selector = tmp_path / "selector.py"
    selector.write_text(
        """
import json

def load_analysis(path):
    return json.loads(path.read_text())

def structural_plan_regime_id(candidate):
    rows = candidate["final_estimation_metadata"]["query_features"]
    return "|".join(row["plan_fingerprint"] for row in rows)

def plan_level(candidate):
    return candidate.get("workload_plan_fingerprint") or candidate[
        "final_estimation_metadata"
    ]["workload_plan_fingerprint"]

def is_non_corner(candidate, ranges):
    return True
""",
        encoding="utf-8",
    )
    initial = {
        "cpu_index_tuple_cost": 0.005,
        "cpu_operator_cost": 0.0025,
        "cpu_tuple_cost": 0.01,
        "parallel_setup_cost": 1000.0,
        "parallel_tuple_cost": 0.1,
        "random_page_cost": 4.0,
        "seq_page_cost": 1.0,
    }
    ranges = {
        name: {"min_value": value * 0.1, "max_value": value * 10.0}
        for name, value in initial.items()
    }
    ranges["parallel_setup_cost"]["min_value"] = 0.0
    capability = {"cpu": 10.0, "io": 10.0, "memory": 10.0}
    incumbent_resource = {"cpu": 5.0, "io": 5.0, "memory": 8.5}
    incumbent_score, _ = expander.score_resource(incumbent_resource, capability)
    baseline_features = {
        "query_features": [
            {"queryid": "q1", "plan_fingerprint": "plan0", "replay_count": 1}
        ]
    }
    reference = {
        "rank": 1,
        "candidate_params": initial,
        "selection_role": "probe",
        "candidate_trust_confidence": 0.0,
        "workload_plan_fingerprint": "plan0",
        "three_level_fingerprint": {"id": "seed-reference"},
        "final_estimation_metadata": {
            "baseline_features": baseline_features,
            "query_features": baseline_features["query_features"],
            "workload_plan_fingerprint": "plan0",
        },
    }
    history = tmp_path / "history.json"
    evidence = tmp_path / "evidence.json"
    config = tmp_path / "config.json"
    capture = tmp_path / "capture.json"
    resume = tmp_path / "formal_state.json"
    checkpoint = tmp_path / "checkpoint.json"
    output_a = tmp_path / "a.json"
    output_b = tmp_path / "b.json"
    write_json(history, {"ranked_candidates": [reference]})
    anchors = []
    for rank in range(1, 6):
        anchor = copy.deepcopy(reference)
        anchor["rank"] = rank
        anchor["three_level_fingerprint"] = {"id": f"anchor-{rank}"}
        anchors.append(anchor)
    write_json(evidence, {"top5": anchors})
    write_json(
        config,
        {
            "initial_params": initial,
            "param_ranges": ranges,
            "capability": capability,
            "postgres": {"host": "unused", "port": 5432, "user": "u", "dbname": "d"},
            "metrics": {"plan_estimator": {}},
        },
    )
    write_json(capture, {"items": [{"queryid": "q1", "query": "select 1"}]})
    write_json(
        resume,
        {
            "schema_version": "tpch_exact_plan_regime_formal_state_v1",
            "status": "complete",
            "baseline_restored": True,
            "incumbent_params": initial,
            "incumbent_resource": incumbent_resource,
            "incumbent_score": incumbent_score,
            "range_state": ranges,
        },
    )

    live_calls = []

    def fake_run(argv, **kwargs):
        time.sleep(0.002)
        live_calls.append(argv[-1])
        return SimpleNamespace(returncode=0, stdout="cached-plan\n", stderr="")

    monkeypatch.setattr(expander.subprocess, "run", fake_run)

    def invoke(output: Path) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                str(EXPANDER),
                "--history", str(history),
                "--evidence-top5", str(evidence),
                "--config", str(config),
                "--capture", str(capture),
                "--selector", str(selector),
                "--code-root", str(code_root),
                "--resume-state", str(resume),
                "--checkpoint", str(checkpoint),
                "--output", str(output),
                "--maximum-wall-seconds", "60",
                "--maximum-explain-invocations", "3000",
                "--maximum-candidate-estimations", "128",
                "--explain-timeout-seconds", "1",
            ],
        )
        assert expander.main() == 0

    invoke(output_a)
    calls_after_first = len(live_calls)
    invoke(output_b)
    assert len(live_calls) == calls_after_first
    first = json.loads(output_a.read_text())
    second = json.loads(output_b.read_text())
    assert first["incumbent_coordinate_neighbor_count"] >= 5
    audit = first["bounded_expansion_audit"]
    assert audit["budget_exhausted"] is True
    assert audit["candidate_estimations"] == 128
    assert audit["budget_exhaustion_reason"] == "candidate-estimation budget exhausted"
    assert audit["physical_explain_invocations_this_process"] == calls_after_first
    assert audit["physical_explain_budget_consumed_cumulative"] == calls_after_first
    assert audit["cache_replay_consumes_new_physical_budget"] is False
    assert second["bounded_expansion_audit"][
        "physical_explain_invocations_this_process"
    ] == 0
    assert second["bounded_expansion_audit"][
        "checkpoint_cache_hits_this_process"
    ] == second["bounded_expansion_audit"]["logical_explain_requests"]
    for payload in (first, second):
        for field in (
            "elapsed_wall_seconds",
            "physical_explain_invocations_this_process",
            "checkpoint_cache_hits_this_process",
            "singleflight_cache_hits_this_process",
        ):
            del payload["bounded_expansion_audit"][field]
    assert first == second
    sealed = json.loads(checkpoint.read_text())
    assert sealed["live_explain_invocations"] == calls_after_first
    assert sealed["lost_checkpoint_results"] == 0
    assert len(sealed["psql_results"]) == calls_after_first

    postlaunch = import_path("tpch_v43_postlaunch_test", POSTLAUNCH)
    expansion_audit = tmp_path / "audit.json"
    write_json(
        expansion_audit,
        {
            "status": "complete",
            "deterministic_double_generation": True,
            "checkpoint_replay_used": True,
            "generation_b_zero_physical_explain": True,
            "expansion_sha256": file_sha256(output_a),
            "replayed_expansion_sha256": file_sha256(output_b),
            "resume_state_sha256": file_sha256(resume),
            "physical_workload_calls": 0,
            "formal_state_mutated": False,
        },
    )
    expansion_result = postlaunch.validate_expansion(
        output_a,
        output_b,
        expansion_audit,
        file_sha256(resume),
    )
    assert expansion_result["candidate_estimations"] == 128
    assert expansion_result["generation_b_physical_explain"] == 0

    candidates = json.loads(output_a.read_text())["ranked_candidates"][:5]
    for rank, candidate in enumerate(candidates, 1):
        candidate["rank"] = rank
        candidate["selection_role"] = "probe"
        candidate["non_corner_verified"] = True
        candidate["boundary_corner"] = False
        candidate["diagnostic_only"] = True
        candidate["qps_used_for_inner_scoring"] = False
        candidate.setdefault("predicted_resource", candidate["final_resource"])
        candidate.setdefault("utilization", {"cpu": 0.1, "io": 0.1, "memory": 0.8})
        for required in postlaunch.REQUIRED_CANDIDATE_FIELDS:
            candidate.setdefault(required, {} if required in {"confidence", "g_j", "delta_param"} else 0.0)
    selection = tmp_path / "probe_top5.a.json"
    write_json(
        selection,
        {
            "top5": candidates,
            "novel_unique_fingerprint_count": 5,
            "accepted_count_start": 1,
            "inherited_accepted_count": 1,
            "maximum_real_replay_invocations": 5,
            "serial_real_replay_required": True,
            "accepted_update_allowed": False,
            "incumbent_update_allowed": False,
            "range_update_allowed": False,
            "gradient_update_allowed": False,
            "qps_used_for_inner_scoring": False,
            "source_evidence": {
                "expansion": {"sha256": file_sha256(output_a)}
            },
        },
    )
    top5_result = postlaunch.validate_top5(selection, file_sha256(output_a))
    assert len(top5_result["fingerprints"]) == 5


def test_zero_call_recovery_preserves_formal_and_counter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    recovery = import_path("tpch_v42_recovery_test", RECOVERY)
    root = tmp_path / "v41"
    loop = root / "target10_loop_v41"
    cycle = loop / "cycle_004"
    expansion = cycle / "dynamic_expansion_v32"
    formal_run = root / "adopted_v40_formal/formal_run"
    formal_run.mkdir(parents=True)
    expansion.mkdir(parents=True)
    state_path = loop / "controller_state.json"
    counter = root / "formal_search_accepted_counter.txt"
    handoff = root / "preformal_handoff_state.json"
    reuse = root / "v40_diagnostic_reuse_audit.json"
    formal_state = formal_run / "formal_state.json"
    write_json(
        formal_state,
        {
            "schema_version": "tpch_exact_plan_regime_formal_state_v1",
            "status": "complete",
            "baseline_restored": True,
            "accepted_count": 1,
            "physical_call_count": 5,
            "valid_replay_count": 5,
            "technical_failure_count": 0,
        },
    )
    write_json(
        state_path,
        {
            "schema_version": "tpch_fresh10_target10_loop_v26",
            "status": "preparing_cycle",
            "accepted_count": 1,
            "accepted_target": 10,
            "next_cycle": 4,
            "resume_state": str(formal_state),
            "cycles": [],
        },
    )
    counter.write_text("1\n", encoding="utf-8")
    write_json(
        handoff,
        {
            "adopted_formal_started": True,
            "adopted_formal_clean": True,
            "adopted_formal_run": str(formal_run),
            "accepted_count": 1,
        },
    )
    write_json(reuse, {"v40_executed_selections_added_to_history": []})
    write_json(cycle / "config.a.json", {"accepted_count": 1})
    write_json(cycle / "config.b.json", {"accepted_count": 1})
    (expansion / "expand_a.log").write_text(
        "ExpansionBudgetExhausted: candidate-estimation budget exhausted\n",
        encoding="utf-8",
    )
    fake_v41 = SimpleNamespace(
        V41_ROOT=root,
        V41_LOOP=loop,
        V41_COUNTER=counter,
    )
    paths = recovery.current_paths(fake_v41)
    formal_sha = recovery.sha256(formal_state)
    counter_sha = recovery.sha256(counter)
    boundary = recovery.validate_failed_boundary(
        fake_v41,
        paths,
        recovery.sha256(state_path),
        formal_sha,
        counter_sha,
    )
    result = recovery.apply_recovery(fake_v41, paths, boundary)
    assert result["status"] == "recovery_applied_zero_call"
    assert result["new_physical_workload_calls"] == 0
    assert recovery.sha256(formal_state) == formal_sha
    assert recovery.sha256(counter) == counter_sha
    assert counter.read_text(encoding="utf-8") == "1\n"
    assert not cycle.exists()
    recovered = json.loads(state_path.read_text())
    assert recovered["status"] == "restart_ready_v42"
    assert recovered["accepted_count"] == 1
    assert recovered["next_cycle"] == 4

    resume_shapes = []

    def fake_run_loop(v41, paths_arg, state, *, resume_existing_cycle004=False):
        resume_shapes.append(resume_existing_cycle004)
        return 17

    monkeypatch.setattr(recovery, "run_loop", fake_run_loop)
    assert recovery.resume(
        fake_v41,
        paths,
        recovery.sha256(state_path),
        formal_sha,
        counter_sha,
    ) == 17
    assert resume_shapes == [False]

    cycle.mkdir()
    dynamic = cycle / "dynamic_expansion_v32"
    dynamic.mkdir()
    write_json(cycle / "config.a.json", {"accepted_count": 1})
    write_json(cycle / "config.b.json", {"accepted_count": 1})
    write_json(dynamic / "explain_checkpoint.json", {"sealed": True})
    recovered["status"] = "preparing_cycle"
    write_json(state_path, recovered)
    assert recovery.resume(
        fake_v41,
        paths,
        recovery.sha256(state_path),
        formal_sha,
        counter_sha,
    ) == 17
    assert resume_shapes == [False, True]

    (cycle / "diagnostic_probe").mkdir()
    try:
        recovery.resume(
            fake_v41,
            paths,
            recovery.sha256(state_path),
            formal_sha,
            counter_sha,
        )
    except RuntimeError as exc:
        assert "zero-workload preworkload boundary" in str(exc)
    else:
        raise AssertionError("resume must fail closed after a workload directory exists")


def test_v43_repairs_exact_concurrent_checkpoint_failure_zero_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repair = import_path("tpch_v43_race_repair_test", RACE_REPAIR)
    root = tmp_path / "campaign"
    loop = root / "target10_loop_v41"
    cycle = loop / "cycle_004"
    dynamic = cycle / "dynamic_expansion_v32"
    dynamic.mkdir(parents=True)
    formal = root / "adopted_v40_formal/formal_run/formal_state.json"
    formal.parent.mkdir(parents=True)
    counter = root / "formal_search_accepted_counter.txt"
    state_path = loop / "controller_state.json"
    write_json(
        formal,
        {
            "schema_version": "tpch_exact_plan_regime_formal_state_v1",
            "status": "complete",
            "baseline_restored": True,
            "accepted_count": 1,
            "physical_call_count": 5,
            "valid_replay_count": 5,
            "technical_failure_count": 0,
        },
    )
    counter.write_text("1\n", encoding="utf-8")
    formal_sha = file_sha256(formal)
    counter_sha = file_sha256(counter)
    source = Path("/tmp/tpch_v42_rc1_sync")
    shutil.copy2(source / "config.a.json", cycle / "config.a.json")
    shutil.copy2(source / "config.b.json", cycle / "config.b.json")
    checkpoint = dynamic / "explain_checkpoint.json"
    failure_log = dynamic / "expand_a.log"
    shutil.copy2(source / "dynamic_expansion_v32/explain_checkpoint.json", checkpoint)
    shutil.copy2(source / "dynamic_expansion_v32/expand_a.log", failure_log)
    write_json(
        state_path,
        {
            "schema_version": "tpch_fresh10_target10_loop_v26",
            "status": "preparing_cycle",
            "accepted_count": 1,
            "accepted_target": 10,
            "next_cycle": 4,
            "resume_state": str(formal),
            "cycles": [],
            "v42_recovery": {
                "contract": "tpch_v42_cycle004_zero_workload_recovery_v1",
                "formal_state_sha256": formal_sha,
                "accepted_counter_sha256": counter_sha,
            },
        },
    )
    monkeypatch.setattr(repair, "ROOT", root)
    monkeypatch.setattr(repair, "LOOP", loop)
    monkeypatch.setattr(repair, "STATE", state_path)
    monkeypatch.setattr(repair, "COUNTER", counter)
    monkeypatch.setattr(repair, "CYCLE", cycle)
    monkeypatch.setattr(repair, "FORMAL", formal)
    monkeypatch.setattr(repair, "DYNAMIC", dynamic)
    monkeypatch.setattr(repair, "CHECKPOINT", checkpoint)
    monkeypatch.setattr(repair, "FAILURE_LOG", failure_log)
    monkeypatch.setattr(
        repair,
        "AUDIT",
        dynamic / "v43_checkpoint_race_repair_audit.json",
    )
    state_sha = file_sha256(state_path)
    checkpoint_sha = file_sha256(checkpoint)
    log_sha = file_sha256(failure_log)
    authority = repair.validate(
        state_sha,
        formal_sha,
        counter_sha,
        checkpoint_sha,
        log_sha,
    )
    result = repair.apply(authority)
    assert result["status"] == "checkpoint_repair_applied_zero_call"
    assert result["new_explain_invocations"] == 0
    assert result["new_workload_calls"] == 0
    assert result["lost_result_physical_explain_invocations"] == 5
    assert result["cached_explain_result_count"] == 22
    assert file_sha256(state_path) == state_sha
    assert file_sha256(formal) == formal_sha
    assert file_sha256(counter) == counter_sha
    repaired = json.loads(checkpoint.read_text())
    assert repaired["live_explain_invocations"] == 22
    assert len(repaired["psql_results"]) == 22
    assert repaired["lost_checkpoint_results"] == 0
    assert repaired["lost_result_physical_explain_invocations"] == 5
    assert repaired["lost_result_outputs_present_in_cache"] is True
