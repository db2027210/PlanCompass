from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


PROJECT = Path(__file__).resolve().parents[1]
CONTROLLER = PROJECT / "tools/tpch_v45_clean5_resume_and_liveness.py"
RUNNER = PROJECT / (
    "tools/run_tpch_round29_diagnostic_exact_plan_regime_batch_"
    "v45_none_return.py"
)


def import_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def build_fixture(v45, root: Path):
    loop = root / "loop"
    cycle = loop / "cycle_004"
    cycle.mkdir(parents=True)
    formal = root / "formal_state.json"
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
    formal_sha = v45.sha256(formal)
    counter = root / "counter.txt"
    counter.write_text("1\n", encoding="utf-8")
    counter_sha = v45.sha256(counter)
    state = loop / "controller_state.json"
    write_json(
        state,
        {
            "schema_version": "tpch_fresh10_target10_loop_v26",
            "status": "preparing_cycle",
            "accepted_count": 1,
            "accepted_target": 10,
            "next_cycle": 4,
            "cycles": [],
            "resume_state": str(formal),
            "v42_recovery": {
                "contract": v45.RECOVERY_CONTRACT,
                "formal_state_sha256": formal_sha,
                "accepted_counter_sha256": counter_sha,
            },
        },
    )
    config = cycle / "config.a.json"
    probe = cycle / "probe_top5.a.json"
    write_json(config, {"config": 4})
    write_json(cycle / "config.b.json", {"config": 4})
    roles = ["trusted", "trusted", "trusted", "probe", "probe"]
    top5 = [
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
    write_json(probe, {"selection": "probe", "top5": top5})
    write_json(
        cycle / "probe_top5.b.json",
        {"selection": "probe", "top5": top5},
    )
    probe_sha = v45.sha256(probe)
    diagnostic = cycle / "diagnostic_probe"
    write_json(
        diagnostic / "diagnostic_state.json",
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
            "selection_sha256": probe_sha,
            "resume_state_sha256": formal_sha,
            "full_selection_replay": True,
            "selection_replayed_ranks": [1, 2, 3, 4, 5],
            "selection_excluded_ranks": [],
            "maximum_real_replay_invocations": 5,
        },
    )
    successful = []
    for rank, candidate in enumerate(top5, 1):
        successful.append(
            {
                "rank": rank,
                "selection_role": candidate["selection_role"],
                "three_level_fingerprint": candidate[
                    "three_level_fingerprint"
                ],
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
        diagnostic / "diagnostic_summary.json",
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
            "selection_sha256": probe_sha,
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
    (cycle / "diagnostic_probe.log").write_text(
        "TypeError: int() argument must be a string, a bytes-like object or "
        "a real number, not 'NoneType'\nreturn int(original.main())\n",
        encoding="utf-8",
    )
    handoff = root / "handoff.json"
    formal_run = root / "formal_run"
    formal_run.mkdir()
    write_json(handoff, {"adopted_formal_run": str(formal_run)})
    paths = {
        "state": state,
        "counter": counter,
        "handoff": handoff,
        "clean5_recovery_audit": loop / "v45_v43_clean5_zero_call_recovery.json",
    }
    v41 = SimpleNamespace(V41_LOOP=loop)
    return v41, paths, formal_sha, counter_sha, config, probe, diagnostic


def raw_boundary(v45, fixture):
    v41, paths, formal_sha, counter_sha, *_ = fixture
    return v45.validate_clean5_boundary(
        v41,
        paths,
        expected_state_sha=v45.sha256(paths["state"]),
        expected_formal_sha=formal_sha,
        expected_counter_sha=counter_sha,
        expected_cycle=4,
    )


def test_runner_none_return_is_success_and_non_none_is_preserved():
    runner = import_path("tpch_v45_runner_test", RUNNER)
    assert runner.normalize_return_code(None) == 0
    assert runner.normalize_return_code(7) == 7
    fake = SimpleNamespace(main=lambda: None, validate_selection=lambda value: value)
    runner.original = fake
    runner.sealed_validate_selection = fake.validate_selection
    assert runner.main() == 0
    assert fake.validate_selection is runner.validate_selection


@pytest.mark.parametrize(
    ("target", "field", "value"),
    [
        ("state", "schema_version", "tampered"),
        ("state", "resume_state_sha256", "0" * 64),
        ("summary", "schema_version", "tampered"),
        ("summary", "batch_added_accepted_count", 1),
        ("summary", "full_selection_replay", False),
        ("summary", "technical_failures", [{"rank": 1}]),
    ],
)
def test_clean5_schema_authority_and_ledger_tamper_fail_closed(
    tmp_path, target, field, value
):
    v45 = import_path("tpch_v45_tamper", CONTROLLER)
    fixture = build_fixture(v45, tmp_path)
    diagnostic = fixture[-1]
    path = diagnostic / f"diagnostic_{target}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    write_json(path, payload)
    with pytest.raises(RuntimeError, match="clean state-neutral 5/5/0"):
        raw_boundary(v45, fixture)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("selection_role", "probe"),
        ("three_level_fingerprint", {"id": "three-level-sha256:" + "f" * 64}),
        ("diagnostic_only", False),
        ("accepted_update_eligible", True),
        ("accepted_update_allowed", True),
        ("incumbent_update_allowed", True),
        ("range_update_allowed", True),
        ("gradient_update_allowed", True),
        ("qps_used_for_inner_scoring", True),
    ],
)
def test_clean5_success_row_role_fingerprint_and_policy_tamper_fails_closed(
    tmp_path, field, value
):
    v45 = import_path("tpch_v45_row_tamper", CONTROLLER)
    fixture = build_fixture(v45, tmp_path)
    summary_path = fixture[-1] / "diagnostic_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["successful"][0][field] = value
    write_json(summary_path, summary)
    with pytest.raises(RuntimeError, match="clean state-neutral 5/5/0"):
        raw_boundary(v45, fixture)


def test_selection_top5_policy_tamper_fails_after_sha_rebinding(tmp_path):
    v45 = import_path("tpch_v45_selection_tamper", CONTROLLER)
    fixture = build_fixture(v45, tmp_path)
    cycle = fixture[0].V41_LOOP / "cycle_004"
    probe_a = cycle / "probe_top5.a.json"
    selection = json.loads(probe_a.read_text(encoding="utf-8"))
    selection["top5"][0]["gradient_update_allowed"] = True
    write_json(probe_a, selection)
    write_json(cycle / "probe_top5.b.json", selection)
    rebound_sha = v45.sha256(probe_a)
    for name in ("diagnostic_state.json", "diagnostic_summary.json"):
        artifact = fixture[-1] / name
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        payload["selection_sha256"] = rebound_sha
        write_json(artifact, payload)
    with pytest.raises(RuntimeError, match="clean state-neutral 5/5/0"):
        raw_boundary(v45, fixture)


def test_prepared_journal_crash_is_idempotently_committed(tmp_path):
    v45 = import_path("tpch_v45_journal", CONTROLLER)
    fixture = build_fixture(v45, tmp_path)
    boundary = raw_boundary(v45, fixture)
    paths = fixture[1]
    raw_sha = v45.sha256(paths["state"])
    with pytest.raises(RuntimeError, match="injected crash"):
        v45.seal_clean5_boundary(
            paths, boundary, crash_after_prepared_journal=True
        )
    assert v45.sha256(paths["state"]) == raw_sha
    retry = raw_boundary(v45, fixture)
    result = v45.seal_clean5_boundary(paths, retry)
    assert result["status"] == "committed_existing_clean5_zero_call"
    assert result["new_physical_workload_calls"] == 0
    sealed = json.loads(paths["state"].read_text(encoding="utf-8"))
    assert sealed["accepted_count"] == 1
    assert sealed["status"] == "v45_clean5_sealed_ready"


def test_raw_boundary_requires_v42_formal_and_counter_cross_binding(tmp_path):
    v45 = import_path("tpch_v45_v42_binding", CONTROLLER)
    fixture = build_fixture(v45, tmp_path)
    paths = fixture[1]
    state = json.loads(paths["state"].read_text(encoding="utf-8"))
    state["v42_recovery"]["accepted_counter_sha256"] = "0" * 64
    write_json(paths["state"], state)
    with pytest.raises(RuntimeError, match="raw v43 clean5 boundary"):
        raw_boundary(v45, fixture)


def test_resume_completed_probe_calls_probe_zero_times_and_crash_reenters(
    tmp_path, monkeypatch
):
    v45 = import_path("tpch_v45_resume", CONTROLLER)
    fixture = build_fixture(v45, tmp_path)
    v41, paths, formal_sha, counter_sha, config, probe, diagnostic = fixture
    boundary = raw_boundary(v45, fixture)
    v45.seal_clean5_boundary(paths, boundary)
    sealed_sha = v45.sha256(paths["state"])
    sealed = v45.validate_sealed_clean5_boundary(
        v41,
        paths,
        expected_state_sha=sealed_sha,
        expected_formal_sha=formal_sha,
        expected_counter_sha=counter_sha,
        expected_cycle=4,
    )
    state = dict(sealed["state"])
    calls = {"config": 0, "probe": 0, "diagnostic": 0, "exact": 0}

    def forbidden(name):
        def invoke(*_args, **_kwargs):
            calls[name] += 1
            raise AssertionError(f"{name} must not be called")
        return invoke

    def exact(_common, cycle_root, *_args):
        calls["exact"] += 1
        (cycle_root / "exact_builder_a.log").write_text(
            "ValueError: repeat fingerprints are not distinct\n",
            encoding="utf-8",
        )
        raise RuntimeError("subprocess failed")

    fake_v26 = SimpleNamespace(
        TARGET=10,
        MAX_CYCLES=1,
        LOOP_ROOT=v41.V41_LOOP,
        atomic_json=v45.atomic_json,
        sha256=v45.sha256,
        accepted_counter=lambda: 1,
        dynamic_config=forbidden("config"),
        build_probe=forbidden("probe"),
        run_clean_diagnostic=forbidden("diagnostic"),
        build_exact_repeat=exact,
    )
    common = SimpleNamespace(runtime_environment=lambda: {})
    adoption = tmp_path / "adoption/a"
    adoption.mkdir(parents=True)
    write_json(adoption / "selection.json", {"initial": True})
    v41.ADOPTION_ROOT = tmp_path / "adoption"
    v41.configure_controller = lambda *_args: (fake_v26, common)
    monkeypatch.setattr(v45, "executed_history", lambda *_args: [])

    with pytest.raises(RuntimeError, match="injected crash"):
        v45.run_loop(
            v41,
            paths,
            dict(state),
            start_cycle=4,
            resume_completed_probe=(config, probe, diagnostic),
            crash_after_preparing_completed_probe=True,
        )
    assert calls == {"config": 0, "probe": 0, "diagnostic": 0, "exact": 0}
    preparing_sha = v45.sha256(paths["state"])
    reentered = v45.validate_sealed_clean5_boundary(
        v41,
        paths,
        expected_state_sha=preparing_sha,
        expected_formal_sha=formal_sha,
        expected_counter_sha=counter_sha,
        expected_cycle=4,
    )
    with pytest.raises(RuntimeError, match="exhausted maximum cycles"):
        v45.run_loop(
            v41,
            paths,
            dict(reentered["state"]),
            start_cycle=4,
            resume_completed_probe=(config, probe, diagnostic),
        )
    assert calls == {"config": 0, "probe": 0, "diagnostic": 0, "exact": 1}
    persisted = json.loads(paths["state"].read_text(encoding="utf-8"))
    assert persisted["cycles"][-1]["physical_workload_calls"] == 5
    assert persisted["cycles"][-1]["negative_gate_new_workload_calls"] == 0
