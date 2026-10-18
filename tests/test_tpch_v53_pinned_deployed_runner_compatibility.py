from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tools/run_tpch_round29_diagnostic_exact_plan_regime_batch_v53_pinned_03be.py"
CONTROLLER = ROOT / "tools/tpch_v53_pinned_deployed_runner_compatibility.py"
RECOVERY = ROOT / "tools/recover_tpch_v53_cycle6_preworkload.py"
LAUNCHER = ROOT / "tools/launch_tpch_v53_pinned_deployed_runner_compatibility.sh"
DEPLOY = ROOT / "tools/deploy_tpch_v53_pinned_deployed_runner_compatibility.sh"
APPLY = ROOT / "tools/apply_tpch_v53_cycle6_preworkload_recovery.sh"
BASE_AUTHORITY = ROOT / "tools/tpch_v52_deployed_diagnostic_authority_03be.py"
BASE_AUTHORITY_SHA256 = (
    "03be882bd4c78ebac37f18b1c3f689305050057d3b73a9964b3d3d1f7dc14c5f"
)


def import_path(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_v53_pins_the_exact_captured_deployed_authority() -> None:
    assert hashlib.sha256(BASE_AUTHORITY.read_bytes()).hexdigest() == (
        BASE_AUTHORITY_SHA256
    )
    runner = import_path("tpch_v53_runner_authority_pin", RUNNER)
    assert runner.EXPECTED_V45_SHA256 == BASE_AUTHORITY_SHA256
    assert runner.LOCAL_V45 == BASE_AUTHORITY


def fingerprint(index: int) -> dict:
    payload = {
        "plan": f"ratio-plan-sha256:{index:064x}",
        "feature_ratio_q005": {"cpu": 200 + index, "io": 100, "memory": 80},
        "resource_utilization_q005": {"cpu": 90, "io": 70 + index, "memory": 60},
    }
    identifier = "three-level-sha256:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "contract": "plan_feature_resource_three_level_v1",
        "id": identifier,
        "plan_level": payload["plan"],
        "feature_ratio_level": payload["feature_ratio_q005"],
        "predicted_resource_level": payload["resource_utilization_q005"],
        "valid": True,
        "parameter_fallback_used": False,
    }


def candidate(rank: int) -> dict:
    trusted = rank <= 3
    params = {
        "cpu_index_tuple_cost": 0.005 + rank * 0.00001,
        "cpu_operator_cost": 0.0025,
        "cpu_tuple_cost": 0.01,
        "parallel_setup_cost": 1000.0,
        "parallel_tuple_cost": 0.1,
        "random_page_cost": 4.0,
        "seq_page_cost": 1.0,
    }
    return {
        "rank": rank,
        "selection_role": "trusted" if trusted else "probe",
        "role": "trusted" if trusted else "uncertain_probe",
        "trusted": trusted,
        "uncertain_probe": not trusted,
        "candidate_params": params,
        "feature_ratio": {"cpu": 1.0, "io": 1.0, "memory": 1.0},
        "predicted_resource": {"cpu": 9.0, "io": 2.0, "memory": 1.0},
        "utilization": {"cpu": 0.4, "io": 0.2, "memory": 0.3},
        "dScore/dU": {"cpu": 1.0, "io": 1.0, "memory": 1.0},
        "T": 1.0,
        "confidence": (
            {"measurement_count": 2, "unknown_regime": False}
            if trusted
            else {"measurement_count": 0, "unknown_regime": True}
        ),
        "g_j": 1.0,
        "eta": 0.5,
        "delta_param": 0.01,
        "candidate_score": 4.0 - rank * 0.01,
        "probe_acquisition_score": 4.0 - rank * 0.01 + (0.0 if trusted else 0.02),
        "three_level_fingerprint": fingerprint(rank),
        "diagnostic_only": True,
        "accepted_update_allowed": False,
        "incumbent_update_allowed": False,
        "range_update_allowed": False,
        "gradient_update_allowed": False,
        "incumbent_update_eligible": False,
        "eligible_for_best_candidate": False,
        "used_for_gradient": False,
        "used_for_inner_incumbent_update": False,
        "qps_used_for_inner_scoring": False,
        "boundary_corner": False,
        "boundary_plateau": False,
        "trusted_stability_gate_passed": False,
        "formal_trust_requires_independent_diagnostic": trusted,
        "safe_unknown_stability_probe": not trusted,
        "formal_score_floor_satisfied": trusted,
        "exact_plan_regime_validated": trusted,
        "exact_regime_stability_failure_irrecoverable": False,
        "regime_calibration": (
            {
                "trusted_prediction_eligible": True,
                "evidence_count": 2,
                "global_scalar_forbidden": True,
                "unknown_regime_calibration_forbidden": True,
            }
            if trusted
            else {}
        ),
    }


def selection() -> dict:
    top5 = [candidate(rank) for rank in range(1, 6)]
    bindings = [
        {
            "fingerprint_id": top5[index]["three_level_fingerprint"]["id"],
            "minimum_three_level_distance": 0.1 + index * 0.01,
            "uncertainty_bonus": 0.02,
        }
        for index in (3, 4)
    ]
    return {
        "selection_contract": (
            "exact_structural_plan_regime_trusted3_stability_"
            "safe_unknown_maximin_probe2_diagnostic_v1"
        ),
        "top5": top5,
        "diagnostic_only": True,
        "formal_continuation": False,
        "serial_real_replay_required": True,
        "maximum_real_replay_invocations": 5,
        "technical_failures_count_toward_physical_cap": True,
        "technical_failures_count_as_valid_or_accepted": False,
        "unknown_plan_regime_trusted_allowed": False,
        "accepted_update_allowed": False,
        "incumbent_update_allowed": False,
        "range_update_allowed": False,
        "gradient_update_allowed": False,
        "qps_used_for_inner_scoring": False,
        "safe_unknown_probe_recovery_mode": True,
        "stability_acquisition_mode": True,
        "trusted_evidence_acquisition_mode": False,
        "stable_trusted_ready_before_diagnostic": False,
        "automatic_recovery_mode": True,
        "automatic_recovery_reason": "exact_regime_trusted_stability_acquisition",
        "formal_admission_from_this_selection_allowed": False,
        "formal_admission_requires_exact_and_robust_repeat": True,
        "probe_expected_score_must_clear_target": False,
        "selected_safe_unknown_probes_are_credible": False,
        "safe_unknown_probe_score_floor_scope": "diagnostic_only_not_formal_or_gradient",
        "evidence_acquisition_slots_are_formal_candidates": False,
        "trusted_slots": 3,
        "probe_slots": 2,
        "novel_unique_fingerprint_count": 5,
        "selection_audit": {
            "selected_unique_fingerprint_count": 5,
            "trusted_count": 3,
            "probe_count": 2,
            "credible_probe_count": 0,
            "safe_unknown_probe_count": 2,
        },
        "safe_unknown_probe_audit": {
            "contract": "safe_unknown_uncertainty_three_level_maximin_probe2_v1",
            "safe_pool_count": 19,
            "selected_count": 2,
            "selected_bindings": bindings,
            "formal_score_floor_satisfied": False,
            "formal_candidate_eligible": False,
            "optimizer_state_updated": False,
            "manual_candidate_injection": False,
        },
    }


def test_runner_translates_only_valid_safe_unknown_contract() -> None:
    runner = import_path("tpch_v53_runner_positive", RUNNER)
    value = selection()
    original = copy.deepcopy(value)
    validated = runner.validate_selection(value)
    assert [item["selection_role"] for item in validated] == [
        "trusted", "trusted", "trusted", "probe", "probe"
    ]
    assert value == original


def test_runner_delegates_every_other_contract_without_translation() -> None:
    runner = import_path("tpch_v53_runner_delegate", RUNNER)
    observed = []
    runner.v45 = SimpleNamespace(
        validate_selection=lambda value: observed.append(value) or [value],
        STABILITY_ACQUISITION_CONTRACT="sealed-v45",
    )
    value = {"selection_contract": "some_other_contract", "marker": 7}
    assert runner.validate_selection(value) == [value]
    assert observed == [value]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(safe_unknown_probe_recovery_mode=False), "contract mismatch"),
        (lambda value: value["safe_unknown_probe_audit"].update(optimizer_state_updated=True), "contract mismatch"),
        (lambda value: value["top5"][3].update(gradient_update_allowed=True), "state-neutral"),
        (lambda value: value["top5"][3]["predicted_resource"].update(cpu=-1), "resource-safe"),
        (lambda value: value["top5"][3]["three_level_fingerprint"].update(id="three-level-sha256:bad"), "fingerprint mismatch"),
        (lambda value: value["top5"][0]["regime_calibration"].update(evidence_count=1), "evidence mismatch"),
    ],
)
def test_runner_rejects_safe_unknown_contract_tampering(mutation, message) -> None:
    runner = import_path(f"tpch_v53_runner_negative_{message}", RUNNER)
    value = selection()
    mutation(value)
    with pytest.raises(ValueError, match=message):
        runner.validate_safe_unknown_selection(value)


def test_controller_reuses_sealed_pair_and_installs_v53_runner(tmp_path: Path) -> None:
    runner = import_path("tpch_v53_controller_runner", RUNNER)
    controller = import_path("tpch_v53_controller", CONTROLLER)
    first = tmp_path / "probe_top5.a.json"
    second = tmp_path / "probe_top5.b.json"
    payload = json.dumps(selection(), sort_keys=True) + "\n"
    first.write_text(payload, encoding="utf-8")
    second.write_text(payload, encoding="utf-8")
    v48 = SimpleNamespace(
        STABILITY_DIAGNOSTIC_RUNNER=Path("/tmp/old_runner.py"),
        EXPECTED={Path("/tmp/old_runner.py"): "old"},
    )
    v49 = SimpleNamespace(import_v48=lambda: v48)
    boundary_validator = SimpleNamespace(
        validate_boundary=lambda *_args, **_kwargs: {
            "existing_probe": first,
            "preflight": {},
        }
    )
    v50 = SimpleNamespace(
        strict_probe5_selection=lambda value: False,
        import_v49=lambda: v49,
        import_boundary_validator=lambda: boundary_validator,
    )
    v51 = SimpleNamespace(import_v50=lambda: v50, CONTRACT="old")
    controller.install(v51, runner, RUNNER)
    installed_v50 = v51.import_v50()
    assert installed_v50.strict_probe5_selection(selection()) is True
    installed_v49 = installed_v50.import_v49()
    installed_v48 = installed_v49.import_v48()
    assert installed_v48.STABILITY_DIAGNOSTIC_RUNNER == RUNNER
    assert installed_v48.EXPECTED[RUNNER] == controller.sha256(RUNNER)
    boundary = installed_v50.import_boundary_validator().validate_boundary()
    assert boundary["preflight"]["safe_unknown_selection_reused"] is True
    assert boundary["preflight"]["explain_rebuilt"] is False


def test_recovery_quarantines_only_empty_failed_output_and_preserves_probe(tmp_path: Path) -> None:
    recovery = import_path("tpch_v53_recovery", RECOVERY)
    campaign = tmp_path / "campaign"
    loop = campaign / "loop"
    cycle = loop / "cycle_006"
    cycle.mkdir(parents=True)
    formal = campaign / "formal.json"
    counter = campaign / "counter.txt"
    state_path = loop / "controller_state.json"
    formal.write_text('{"accepted_count":1}\n', encoding="utf-8")
    counter.write_text("1\n", encoding="utf-8")
    state = {
        "schema_version": "tpch_fresh10_target10_loop_v26",
        "status": "preparing_cycle",
        "accepted_count": 1,
        "accepted_target": 10,
        "next_cycle": 6,
        "resume_state": str(formal),
    }
    state_path.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
    probe_payload = json.dumps(selection(), sort_keys=True) + "\n"
    first = cycle / "probe_top5.a.json"
    second = cycle / "probe_top5.b.json"
    first.write_text(probe_payload, encoding="utf-8")
    second.write_text(probe_payload, encoding="utf-8")
    failed = cycle / "diagnostic_probe"
    failed.mkdir()
    failure_log = cycle / "diagnostic_probe.log"
    failure_log.write_text("ValueError: selection contract mismatch\n", encoding="utf-8")
    recovery.CAMPAIGN = campaign
    recovery.LOOP = loop
    recovery.STATE = state_path
    recovery.COUNTER = counter
    args = SimpleNamespace(
        expected_controller_state_sha256=recovery.sha256(state_path),
        expected_formal_state_sha256=recovery.sha256(formal),
        expected_counter_sha256=recovery.sha256(counter),
        expected_cycle=6,
        expected_probe_sha256=recovery.sha256(first),
        expected_failure_log_sha256=recovery.sha256(failure_log),
    )
    probe_before = first.read_bytes()
    boundary = recovery.validate_boundary(args)
    result = recovery.apply_recovery(args, boundary)
    assert result["workload_calls_added"] == 0
    assert first.read_bytes() == probe_before == second.read_bytes()
    assert not failed.exists()
    assert not failure_log.exists()
    quarantine = loop / "v53_cycle_006_preworkload_quarantine"
    assert (quarantine / "diagnostic_probe.empty").is_dir()
    assert (quarantine / "diagnostic_probe.log").is_file()


def test_shell_entrypoints_preserve_separate_resume_boundary() -> None:
    launcher = LAUNCHER.read_text(encoding="utf-8")
    deploy = DEPLOY.read_text(encoding="utf-8")
    apply = APPLY.read_text(encoding="utf-8")
    assert "preflight|resume" in launcher
    assert 'verify_sha "$first" "$probe_sha"' in launcher
    assert 'verify_sha "$second" "$probe_sha"' in launcher
    assert "cmp -s \"$first\" \"$second\"" in launcher
    assert "apply_zero_call_recovery" in deploy
    assert deploy.index('"$stage/$recovery_name" preflight') < deploy.index(
        'cp "$source_path" "$temporary"'
    )
    assert "separate_explicit_v53_resume" in apply
    assert "/tmp/launch_tpch_v53_pinned_deployed_runner_compatibility.sh" in apply
    assert BASE_AUTHORITY_SHA256 in launcher
    assert BASE_AUTHORITY_SHA256 in deploy
    assert "af90bc7f" not in launcher
    assert "af90bc7f" not in deploy
    assert "preflight" in apply
    assert "selection_rebuilt: false" in apply
    assert "explain_rebuilt: false" in apply
