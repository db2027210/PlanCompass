#!/usr/bin/env python3
"""Offline regression checks for the MySQL external-baseline campaign."""

from __future__ import annotations

import copy
import hashlib
import json
import signal
import subprocess
import sys
import tempfile
import types
from pathlib import Path

from mysql_external_tuner_policy import (
    AQETUNER_WARM_SAMPLES,
    AQETunerPolicy,
    GPTunerPolicy,
    exact_signature,
)
from mysql_workload_adapter_v2 import MySQLEndpoint
import run_mysql_four_scene_external_baselines as baseline_module
from run_mysql_four_scene_external_baselines import (
    CLEAN_STOP_SIGNALS,
    Campaign,
    EXTERNAL_XP_GRACEFUL_QUIESCENCE_SECONDS,
    MYSQL_NATIVE_RUN_HYBENCH,
    MYSQL_ASYNC_CONTRACT,
    MYSQL_HYBENCH_RUNNER_V4,
    MYSQL_HYBENCH_RUNNER_V4_SHA256,
    EXPECTED_ACTIVE,
    SCENES,
    aggregate_mysql_plans,
    aggregate_scene_repetitions,
    classify_post_wave_candidate_failure,
    external_run_hybench,
    external_apply_costs,
    parse_cpuset,
    validate_complete_baseline_recovery,
    validate_hybench_successor_runtime,
    validate_publication_prelaunch_resume,
)


ROOT = Path(__file__).resolve().parents[1]
ACTIVE = (
    "io_block_read_cost",
    "memory_block_read_cost",
    "row_evaluate_cost",
    "memory_temptable_row_cost",
)
RANGES = {
    "io_block_read_cost": (0.1, 10.0),
    "memory_block_read_cost": (0.025, 2.5),
    "row_evaluate_cost": (0.01, 1.0),
    "memory_temptable_row_cost": (0.01, 1.0),
}
DEFAULTS = {
    "io_block_read_cost": 1.0,
    "memory_block_read_cost": 0.25,
    "row_evaluate_cost": 0.1,
    "key_compare_cost": 0.05,
    "disk_temptable_create_cost": 20.0,
    "disk_temptable_row_cost": 0.5,
    "memory_temptable_create_cost": 1.0,
    "memory_temptable_row_cost": 0.1,
}
RANGES_BY_NAME = {
    "io_block_read_cost": (0.1, 10.0),
    "memory_block_read_cost": (0.025, 2.5),
    "row_evaluate_cost": (0.01, 1.0),
    "key_compare_cost": (0.005, 0.5),
    "disk_temptable_create_cost": (2.0, 200.0),
    "disk_temptable_row_cost": (0.05, 5.0),
    "memory_temptable_row_cost": (0.01, 1.0),
}


def test_contract_shape() -> None:
    assert tuple(EXPECTED_ACTIVE) == SCENES
    assert all(len(names) == 4 and len(set(names)) == 4 for names in EXPECTED_ACTIVE.values())


def test_mysql_plan_conversion() -> None:
    plan = aggregate_mysql_plans(
        {
            "q1": (
                '{"query_block":{"nested_loop":['
                '{"table":{"table_name":"a","rows_examined_per_scan":12}},'
                '{"table":{"table_name":"b","rows_produced_per_join":3}}]}}'
            )
        }
    )
    assert plan["NodeType"] == "Projection"
    assert plan["Children"]
    types = []

    def walk(node):
        types.append(node["NodeType"])
        for child in node["Children"]:
            walk(child)

    walk(plan)
    assert "Join" in types
    assert "TableScan" in types
    balanced = aggregate_mysql_plans(
        {f"q{index}": '{"query_block":{"table":{"rows":1}}}' for index in range(3)},
        maximum_nodes=4,
    )
    assert len(balanced["Children"]) == 3


def test_balanced_scene_repetition_aggregate() -> None:
    rows = []
    for worker_id in ("job_w1", "job_w2"):
        for repetition in range(2):
            rows.append(
                {
                    "scene": "job_memory",
                    "worker_id": worker_id,
                    "hard_gate_passed": True,
                    "db_qps": 10.0 + repetition,
                    "resource_means": {"cpu": 0.5, "memory": 0.4, "io": 0.3},
                }
            )
    aggregate = aggregate_scene_repetitions(
        rows,
        "job_memory",
        ("job_w1", "job_w2"),
        2,
        "unit_test",
    )
    assert aggregate["valid"] is True
    assert aggregate["measurement_count"] == 4
    assert aggregate["repetitions_per_worker"] == 2


def build_complete_baseline_failure_fixture(
    root: Path,
) -> tuple[dict, Path, Path]:
    """Build the exact recoverable boundary with sealed physical evidence."""
    output = root / "campaign"
    output.mkdir()
    binding_path = root / "binding.json"
    binding_path.write_text('{"status":"PASS"}\n', encoding="utf-8")
    binding_sha = hashlib.sha256(binding_path.read_bytes()).hexdigest()
    worker_ids_by_scene = {
        scene: [f"{scene}_w1", f"{scene}_w2"] for scene in SCENES
    }
    baseline_rows = {scene: [] for scene in SCENES}
    for repetition in (1, 2):
        batch_result = (
            output / "baseline" / f"rep_{repetition:02d}" / "batch_result.json"
        )
        batch_result.parent.mkdir(parents=True)
        batch_result.write_text(
            json.dumps({"repetition": repetition}) + "\n", encoding="utf-8"
        )
        batch_sha = hashlib.sha256(batch_result.read_bytes()).hexdigest()
        batch_result.with_name(batch_result.name + ".sha256").write_text(
            f"{batch_sha}  {batch_result.name}\n", encoding="ascii"
        )
        for scene_index, scene in enumerate(SCENES):
            for worker_index, worker_id in enumerate(worker_ids_by_scene[scene]):
                evidence_path = batch_result.parent / worker_id / "client_processes.jsonl"
                evidence_path.parent.mkdir()
                evidence_path.write_text(
                    json.dumps({"physical": True}) + "\n", encoding="utf-8"
                )
                evidence_sha = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
                baseline_rows[scene].append({
                    "scene": scene,
                    "worker_id": worker_id,
                    "hard_gate_passed": True,
                    "db_qps": float(10 + scene_index + worker_index + repetition),
                    "resource_means": {"cpu": 0.5, "memory": 0.4, "io": 0.3},
                    "physical_invocation_evidence": {
                        "path": str(evidence_path),
                        "exists": True,
                        "record_count": 1,
                        "physical_invocation_started": True,
                        "sha256": evidence_sha,
                    },
                })
    first_scene = SCENES[0]
    state = {
        "schema_version": "mysql_four_scene_external_baseline_state_v2",
        "status": "FAILED_CLOSED",
        "phase": "FAILED_CLOSED",
        "error": {"type": "NameError", "message": "name 'np' is not defined"},
        "method": "aqetuner",
        "candidate_budget_per_scene": 35,
        "publication_mode": True,
        "candidate_wave_width_per_scene": 1,
        "manual_mutation_after_start": False,
        "physical_workload_invocations": 16,
        "valid_replays": 16,
        "technical_failures": 0,
        "prelaunch_failures": 0,
        "candidate_physical_invocations": 0,
        "failed_wave_invocation_evidence": [],
        "inflight_wave": None,
        "worker_baseline_qps": {},
        "independent_worker_binding": {
            "path": str(binding_path),
            "sha256": binding_sha,
        },
        "worker_ids_by_scene": worker_ids_by_scene,
        "baseline_rows": baseline_rows,
        "baseline": {
            first_scene: aggregate_scene_repetitions(
                baseline_rows[first_scene],
                first_scene,
                worker_ids_by_scene[first_scene],
                2,
                "pre_search_baseline_aggregate",
            )
        },
        "observations": {scene: [] for scene in SCENES},
        "post_baseline_rows": {scene: [] for scene in SCENES},
        "champion_validation_rows": {scene: [] for scene in SCENES},
        "post_search_baseline": {},
        "comparison_baseline": {},
        "champion": {},
        "champion_validation": {},
    }
    return state, output, binding_path


def test_complete_baseline_recovery_is_narrow_and_evidence_bound() -> None:
    with tempfile.TemporaryDirectory(prefix="mysql_baseline_recovery_") as directory:
        state, output, binding_path = build_complete_baseline_failure_fixture(
            Path(directory)
        )
        audit = validate_complete_baseline_recovery(
            state, output, binding_path, "aqetuner", 35
        )
        assert audit["evidence_file_count"] == 18
        assert len(audit["aggregates"]) == 4
        assert len(audit["worker_medians"]) == 8

        mutations = []
        wrong_count = copy.deepcopy(state)
        wrong_count["physical_workload_invocations"] = 15
        mutations.append(wrong_count)
        wrong_error = copy.deepcopy(state)
        wrong_error["error"]["message"] = "some other failure"
        mutations.append(wrong_error)
        wrong_row = copy.deepcopy(state)
        wrong_row["baseline_rows"][SCENES[1]][0]["hard_gate_passed"] = False
        mutations.append(wrong_row)
        wrong_hash = copy.deepcopy(state)
        wrong_hash["baseline_rows"][SCENES[2]][0][
            "physical_invocation_evidence"
        ]["sha256"] = "0" * 64
        mutations.append(wrong_hash)
        for mutated in mutations:
            try:
                validate_complete_baseline_recovery(
                    mutated, output, binding_path, "aqetuner", 35
                )
            except RuntimeError:
                pass
            else:
                raise AssertionError("invalid complete-baseline recovery was accepted")


def test_complete_baseline_resume_branch_never_calls_run_baseline() -> None:
    args = types.SimpleNamespace(
        output_dir=Path("/tmp/not_used"),
        resume=False,
        resume_after_complete_baseline=True,
    )
    campaign = Campaign(args)
    calls = []
    campaign.load_complete_baseline_recovery_state = lambda: calls.append("load")
    campaign.initialize_runtime = lambda: calls.append("initialize")
    campaign.prepare_complete_baseline_recovery_after_runtime = (
        lambda: calls.append("prepare")
    )
    campaign.run_baseline = lambda: (_ for _ in ()).throw(
        AssertionError("baseline replayed")
    )
    campaign.build_policies = lambda: calls.append("policies")
    campaign.run_search = lambda: calls.append("search")
    campaign.run_post_search_baseline = lambda: calls.append("post")
    campaign.freeze_champions = lambda: calls.append("freeze")
    campaign.validate_champions = lambda: calls.append("validate")
    campaign.restore_defaults = lambda _reason: calls.append("restore")
    campaign.finalize = lambda: calls.append("finalize")
    assert campaign.run() == 0
    assert calls[:3] == ["load", "initialize", "prepare"]
    assert "search" in calls


def test_apply_cost_timeout_retry_is_bounded_and_audited() -> None:
    with tempfile.TemporaryDirectory(prefix="mysql_apply_retry_") as directory:
        calls = []

        def fake_apply(_worker, _params):
            calls.append("apply")
            if len(calls) == 1:
                raise subprocess.TimeoutExpired(["mysql"], 60)
            return {"io_block_read_cost": 1.0}

        previous = (
            baseline_module.MYSQL_NATIVE_APPLY_COSTS,
            baseline_module.MYSQL_NATIVE_MYSQL_ROOT,
            baseline_module.MYSQL_APPLY_COST_RETRY_BACKOFF_SECONDS,
            baseline_module.MYSQL_APPLY_COST_RETRY_LOG,
        )
        baseline_module.MYSQL_NATIVE_APPLY_COSTS = fake_apply
        baseline_module.MYSQL_NATIVE_MYSQL_ROOT = (
            lambda _worker, _sql, timeout_seconds: "1"
        )
        baseline_module.MYSQL_APPLY_COST_RETRY_BACKOFF_SECONDS = (0.0, 0.0)
        retry_log = Path(directory) / "apply_cost_retries.jsonl"
        baseline_module.MYSQL_APPLY_COST_RETRY_LOG = retry_log
        try:
            readback = external_apply_costs(
                {"worker_id": "xp_io_w2"}, {"io_block_read_cost": 1.0}
            )
        finally:
            (
                baseline_module.MYSQL_NATIVE_APPLY_COSTS,
                baseline_module.MYSQL_NATIVE_MYSQL_ROOT,
                baseline_module.MYSQL_APPLY_COST_RETRY_BACKOFF_SECONDS,
                baseline_module.MYSQL_APPLY_COST_RETRY_LOG,
            ) = previous
        assert readback == {"io_block_read_cost": 1.0}
        assert calls == ["apply", "apply"]
        events = [json.loads(line) for line in retry_log.read_text().splitlines()]
        assert [event["event"] for event in events] == [
            "apply_cost_timeout",
            "apply_cost_retry_recovered",
        ]
        assert all(event["physical_invocation_started"] is False for event in events)


def build_publication_prelaunch_resume_fixture(
    root: Path,
) -> tuple[dict, Path, Path]:
    state, output, binding_path = build_complete_baseline_failure_fixture(root)
    workers = [
        {"worker_id": worker_id, "scene": scene}
        for scene, worker_ids in state["worker_ids_by_scene"].items()
        for worker_id in worker_ids
    ]
    binding_path.write_text(
        json.dumps({"workers": workers}) + "\n", encoding="utf-8"
    )
    state["independent_worker_binding"]["sha256"] = hashlib.sha256(
        binding_path.read_bytes()
    ).hexdigest()
    state.update({
        "status": "FAILED_CLOSED",
        "phase": "FAILED_CLOSED",
        "error": {
            "type": "TimeoutExpired",
            "message": "Command ['mysql'] timed out after 60 seconds",
        },
        "baseline_wave_count": 2,
        "active_parameters_by_scene": {
            scene: list(EXPECTED_ACTIVE[scene]) for scene in SCENES
        },
        "ranges_by_scene": {
            scene: {
                name: {
                    "lower": RANGES_BY_NAME[name][0],
                    "upper": RANGES_BY_NAME[name][1],
                }
                for name in EXPECTED_ACTIVE[scene]
            }
            for scene in SCENES
        },
        "clean_restore": {"status": "PASS"},
        "completed_search_waves": 2,
        "candidate_physical_invocations": 8,
        "physical_workload_invocations": 24,
        "valid_replays": 24,
        "technical_failures": 0,
        "prelaunch_failures": 4,
        "post_search_baseline": {},
        "comparison_baseline": {},
        "champion": {},
        "champion_validation": {},
    })
    state["baseline"] = {
        scene: aggregate_scene_repetitions(
            state["baseline_rows"][scene],
            scene,
            state["worker_ids_by_scene"][scene],
            2,
            "pre_search_baseline_aggregate",
        )
        for scene in SCENES
    }
    observations = {scene: [] for scene in SCENES}
    for wave in (1, 2):
        batch = output / "search" / f"wave_{wave:03d}" / "batch_result.json"
        batch.parent.mkdir(parents=True)
        batch.write_text(json.dumps({"wave": wave}) + "\n", encoding="utf-8")
        baseline_module.atomic(batch, {"wave": wave})
        for scene in SCENES:
            worker_id = state["worker_ids_by_scene"][scene][(wave - 1) % 2]
            client = batch.parent / worker_id / "client_processes.jsonl"
            client.parent.mkdir()
            client.write_text('{"physical":true}\n', encoding="utf-8")
            params = {name: DEFAULTS[name] for name in EXPECTED_ACTIVE[scene]}
            observations[scene].append({
                "wave": wave,
                "trial": wave,
                "worker_id": worker_id,
                "params": params,
                "valid": True,
                "technical_failure": False,
                "physical_invocation_evidence": {
                    "path": str(client),
                    "exists": True,
                    "record_count": 1,
                    "physical_invocation_started": True,
                    "sha256": hashlib.sha256(client.read_bytes()).hexdigest(),
                },
            })
    state["observations"] = observations
    logical_wave = 3
    wave_dir = output / "search" / f"wave_{logical_wave:03d}"
    wave_dir.mkdir()
    assignments = {}
    receipts = {}
    for scene in SCENES:
        worker_id = state["worker_ids_by_scene"][scene][0]
        assignments[worker_id] = {
            "rank": logical_wave,
            "params": {name: DEFAULTS[name] for name in EXPECTED_ACTIVE[scene]},
        }
        client = wave_dir / worker_id / "client_processes.jsonl"
        receipts[worker_id] = {
            "path": str(client),
            "exists": False,
            "record_count": 0,
            "physical_invocation_started": False,
            "sha256": None,
        }
    state["inflight_wave"] = {
        "stage": "SEARCH_WAVE_003",
        "output": str(wave_dir),
        "assignments": assignments,
    }
    state["failed_wave_invocation_evidence"] = [{
        "stage": "SEARCH_WAVE_003",
        "receipts": receipts,
    }]
    baseline_module.atomic(output / "state.json", state)
    return state, output, binding_path


def test_publication_prelaunch_resume_preserves_completed_evidence() -> None:
    with tempfile.TemporaryDirectory(prefix="mysql_publication_resume_") as directory:
        state, output, binding_path = build_publication_prelaunch_resume_fixture(
            Path(directory)
        )
        audit = validate_publication_prelaunch_resume(
            state, output, binding_path, "aqetuner", 35
        )
        assert audit["status"] == "PASS"
        assert audit["logical_wave"] == 3
        assert audit["prior_physical_invocation_started"] is False
        assert audit["verified_evidence_file_count"] == 28

        invalid = copy.deepcopy(state)
        first_worker = next(iter(invalid["inflight_wave"]["assignments"]))
        invalid["failed_wave_invocation_evidence"][-1]["receipts"][first_worker][
            "physical_invocation_started"
        ] = True
        try:
            validate_publication_prelaunch_resume(
                invalid, output, binding_path, "aqetuner", 35
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("physical publication wave was accepted as retryable")


def test_gptuner_pair_suggestions() -> None:
    observations = []
    policy = GPTunerPolicy(
        scene="job_memory",
        active_params=ACTIVE,
        ranges=RANGES,
        defaults=DEFAULTS,
        observations=observations,
        candidate_budget=18,
        seed=20260826,
        official_root=ROOT / "baselines/GPTuner_official",
    )
    for _wave in range(4):
        suggestions = policy.suggest(2)
        assert len(suggestions) == 2
        assert len({exact_signature(row["params"]) for row in suggestions}) == 2
        assert all(set(row["params"]) == set(ACTIVE) for row in suggestions)
        for row in suggestions:
            observation = {
                "params": row["params"],
                "param_signature": exact_signature(row["params"]),
                "stage": row["stage"],
                "valid": True,
                "cost": -1.0,
                "elapsed_s": 1.0,
            }
            observations.append(observation)
            policy.observe(row, observation)
    boundary = policy.suggest(2)
    assert [row["stage"] for row in boundary] == ["coarse", "coarse"]
    assert [row["trial"] for row in boundary] == [9, 10]


def test_publication_gptuner_uses_online_refined_bounds() -> None:
    with tempfile.TemporaryDirectory(prefix="mysql_gpt4_snapshot_") as directory:
        snapshot = Path(directory)
        normal = snapshot / "structured_knowledge" / "normal"
        normal.mkdir(parents=True)
        for name in ACTIVE:
            low, high = RANGES[name]
            refined_low = DEFAULTS[name] - 0.25 * (DEFAULTS[name] - low)
            refined_high = DEFAULTS[name] + 0.25 * (high - DEFAULTS[name])
            (normal / f"{name}.json").write_text(
                json.dumps(
                    {
                        "suggested_values": [DEFAULTS[name]],
                        "min_value": refined_low,
                        "max_value": refined_high,
                    }
                ),
                encoding="utf-8",
            )
        (snapshot / "manifest.json").write_text(
            json.dumps(
                {
                    "contract": "gptuner_online_knowledge_snapshot_v1",
                    "status": "complete",
                    "model": "gpt-4",
                    "provider": "openai-api",
                    "dbms": "mysql",
                    "scene": "job_memory",
                    "active_params": list(ACTIVE),
                    "stages": {"prompt_ensemble_votes": 5},
                }
            ),
            encoding="utf-8",
        )
        policy = GPTunerPolicy(
            scene="job_memory",
            active_params=ACTIVE,
            ranges=RANGES,
            defaults=DEFAULTS,
            observations=[],
            candidate_budget=35,
            seed=20260921,
            official_root=ROOT / "baselines/GPTuner_official",
            publication_mode=True,
            knowledge_snapshot=snapshot,
        )
        metadata = policy.metadata()
        assert metadata["online_llm_refresh"] is True
        assert metadata["llm_model"] == "gpt-4"
        assert metadata["region_discard_from_structured_min_max"] is True
        for name in ACTIVE:
            hyperparameter = policy.fine_space[name]
            assert hyperparameter.log is False
            assert hyperparameter.lower > RANGES[name][0]
            assert hyperparameter.upper < RANGES[name][1]


def test_gptuner_four_scene_fine_stage_isolation() -> None:
    observations_by_scene = {scene: [] for scene in SCENES}
    policies = {}
    for index, scene in enumerate(SCENES):
        active = EXPECTED_ACTIVE[scene]
        policies[scene] = GPTunerPolicy(
            scene=scene,
            active_params=active,
            ranges={name: RANGES_BY_NAME[name] for name in active},
            defaults=DEFAULTS,
            observations=observations_by_scene[scene],
            candidate_budget=18,
            seed=20260826 + index,
            official_root=ROOT / "baselines/GPTuner_official",
        )

    for wave in range(9):
        for scene in SCENES:
            policy = policies[scene]
            suggestions = policy.suggest(2)
            assert len(suggestions) == 2
            assert [row["trial"] for row in suggestions] == [wave * 2 + 1, wave * 2 + 2]
            assert len({exact_signature(row["params"]) for row in suggestions}) == 2
            assert all(set(row["params"]) == set(EXPECTED_ACTIVE[scene]) for row in suggestions)
            for offset, row in enumerate(suggestions):
                observation = {
                    "params": row["params"],
                    "param_signature": exact_signature(row["params"]),
                    "stage": row["stage"],
                    "valid": True,
                    "cost": -float(wave * 2 + offset + 1),
                    "elapsed_s": 1.0,
                }
                observations_by_scene[scene].append(observation)
                policy.observe(row, observation)

    assert all(len(rows) == 18 for rows in observations_by_scene.values())
    assert all(
        policy.metadata()["incompatible_asks_skipped"] == []
        for policy in policies.values()
    )


def test_aqetuner_feedback_warm_suggestions() -> None:
    with tempfile.TemporaryDirectory(prefix="mysql_aqe_policy_test_") as directory:
        policy = AQETunerPolicy(
            scene="job_memory",
            active_params=ACTIVE,
            ranges=RANGES,
            defaults=DEFAULTS,
            observations=[],
            candidate_budget=18,
            seed=20260826,
            official_root=ROOT / "baselines/AQETuner_official",
            plan={
                "NodeId": 0,
                "NodeType": "Projection",
                "Statistic": {"RowCount": 1.0},
                "Children": [],
            },
            output_dir=Path(directory),
        )
        suggestions = policy.suggest(2)
        assert len(suggestions) == 2
        assert len({exact_signature(row["params"]) for row in suggestions}) == 2
        for suggestion in suggestions:
            assert set(suggestion["params"]) == set(ACTIVE)
            for name, value in suggestion["params"].items():
                lower, upper = RANGES[name]
                assert lower <= float(value) <= upper

        observations = policy.observations
        for index in range(8):
            observations.append(
                {
                    "params": dict(DEFAULTS),
                    "param_signature": f"prior-{index}",
                    "valid": True,
                    "db_qps": 1.0,
                }
            )
        boundary = policy.suggest(1)
        assert AQETUNER_WARM_SAMPLES == 9
        assert [row["stage"] for row in boundary] == ["pso_warm_start"]
        assert [row["trial"] for row in boundary] == [9]
        assert boundary[0]["pso_feedback_applied"] is True


def test_aqetuner_training_uses_frozen_cpu_device() -> None:
    with tempfile.TemporaryDirectory(prefix="mysql_aqe_device_test_") as directory:
        policy = AQETunerPolicy(
            scene="job_memory",
            active_params=ACTIVE,
            ranges=RANGES,
            defaults=DEFAULTS,
            observations=[],
            candidate_budget=18,
            seed=20260826,
            official_root=ROOT / "baselines/AQETuner_official",
            plan={
                "NodeId": 0,
                "NodeType": "Projection",
                "Statistic": {"RowCount": 1.0},
                "Children": [],
            },
            output_dir=Path(directory),
        )
        calls = []

        def fake_main(sample_path, checkpoint_path, epochs, lr):
            calls.append(
                {
                    "device": fake_train.DEVICE,
                    "sample_path": sample_path,
                    "checkpoint_path": checkpoint_path,
                    "epochs": epochs,
                    "lr": lr,
                }
            )

        fake_train = types.SimpleNamespace(DEVICE="cuda", main=fake_main)
        previous = sys.modules.get("train")
        sys.modules["train"] = fake_train
        try:
            policy._train_official_model()
        finally:
            if previous is None:
                del sys.modules["train"]
            else:
                sys.modules["train"] = previous

        assert calls == [
            {
                "device": "cpu",
                "sample_path": str(policy.sample_path),
                "checkpoint_path": str(policy.checkpoint_path),
                "epochs": 5,
                "lr": 0.0005,
            }
        ]
        assert policy.metadata()["training_device"] == "cpu"
        assert policy.metadata()["inference_device"] == "cpu"


def test_external_xp_quiescence_contract() -> None:
    assert EXTERNAL_XP_GRACEFUL_QUIESCENCE_SECONDS == 300
    assert MYSQL_NATIVE_RUN_HYBENCH.__module__ == "mysql_workload_adapter_v2"
    closure = validate_hybench_successor_runtime()
    assert closure["status"] == "PASS"
    assert closure["runner"]["bytecode_300_second_contract"] is True
    successor = json.loads(MYSQL_ASYNC_CONTRACT.read_text(encoding="utf-8"))
    runner_binding = successor["source_bindings"]["hybench_runner_v4"]
    assert successor["status"] == "FROZEN_RUNTIME_VALIDATED"
    assert (
        Path("/home/anonymous/program/resource_adaptive_mysql", runner_binding["path"])
        .resolve()
        == MYSQL_HYBENCH_RUNNER_V4
    )
    assert runner_binding["sha256"] == MYSQL_HYBENCH_RUNNER_V4_SHA256
    assert (
        hashlib.sha256(MYSQL_HYBENCH_RUNNER_V4.read_bytes()).hexdigest()
        == MYSQL_HYBENCH_RUNNER_V4_SHA256
    )
    assert set(CLEAN_STOP_SIGNALS) == {signal.SIGTERM, signal.SIGINT}
    assert signal.SIGHUP not in CLEAN_STOP_SIGNALS

    with tempfile.TemporaryDirectory(prefix="mysql_xp_v2_contract_") as directory:
        root = Path(directory)
        artifact = root / "artifact"
        runtime = artifact / "runtime"
        runtime.mkdir(parents=True)
        native_jar = runtime / "native.jar"
        native_jar.write_bytes(b"native-xp")
        (artifact / "manifest.json").write_text(
            json.dumps({
                "runtime_jar_sha256": hashlib.sha256(
                    native_jar.read_bytes()
                ).hexdigest(),
                "execution_authorized": True,
            }),
            encoding="utf-8",
        )
        adaptive_jar = root / "adaptive.jar"
        adaptive_jar.write_bytes(b"adaptive")
        hybench_home = root / "home"
        hybench_home.mkdir()
        result = external_run_hybench(
            "xp",
            MySQLEndpoint("127.0.0.1", 3306, "hybench_sf10x", "u", "p"),
            root / "output",
            hybench_home=hybench_home,
            duration_s=900,
            clients=8,
            runtime_jar=native_jar,
            formal_control={
                "runner_jar": adaptive_jar,
                "stop_file": root / "output" / "stop.txt",
                "minimum_measurement_seconds": 300,
                "maximum_measurement_seconds": 900,
                "graceful_quiescence_seconds": 300,
            },
            dry_run=True,
        )
        assert result["formal_control"]["graceful_quiescence_seconds"] == 300


def test_cpuset_parser_and_v4_binding_disjointness() -> None:
    assert parse_cpuset("0-3,8,10-11") == {0, 1, 2, 3, 8, 10, 11}
    for invalid in ("", "4-3", "-1"):
        try:
            parse_cpuset(invalid)
        except (RuntimeError, ValueError):
            pass
        else:
            raise AssertionError(f"invalid cpuset accepted: {invalid!r}")

    binding_path = (
        ROOT
        / "config/formal/"
        "mysql_external_baseline_independent_workers_disk1_20260826_v4.json"
    )
    formal_path = Path(
        "/home/anonymous/program/resource_adaptive_mysql/history/"
        "mysql_four_scene_20260809/"
        "v301_to_four_scene_two_worker_live_authority_20260826/"
        "prepared_binding.json"
    )
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    formal = json.loads(formal_path.read_text(encoding="utf-8"))
    seen = set()
    for worker in binding["workers"]:
        for key in ("database_cpuset", "client_cpuset"):
            cpus = parse_cpuset(str(worker[key]))
            assert not seen.intersection(cpus)
            seen.update(cpus)
    formal_cpus = set(
        formal["rolling_dynamic_noninterference_gate"]
        ["actual_target_cpu_scope_release"]["target_cpus"]
    )
    assert not seen.intersection(formal_cpus)
    replacement = next(
        row for row in binding["workers"] if row["worker_id"] == "xp_cpu_w1"
    )
    assert replacement["container"].endswith("_r2_20260826")
    assert replacement["host_port"] == 65531


def test_post_wave_candidate_failure_classification() -> None:
    assert classify_post_wave_candidate_failure(
        {"type": "KeyError", "message": "'disk_temptable_row_cost'"},
        EXPECTED_ACTIVE,
    ) == "post_wave_candidate_configuration_failure"
    assert classify_post_wave_candidate_failure(
        {
            "type": "RuntimeError",
            "message": "job_memory: external search wave is not paired",
        },
        EXPECTED_ACTIVE,
    ) == "post_wave_candidate_pairing_failure"
    assert classify_post_wave_candidate_failure(
        {
            "type": "RuntimeError",
            "message": (
                "Expected all tensors to be on the same device, but got mat2 "
                "is on cuda:0, different from other tensors on cpu"
            ),
        },
        EXPECTED_ACTIVE,
    ) == "post_wave_candidate_model_device_failure"
    assert classify_post_wave_candidate_failure(
        {"type": "KeyError", "message": "'unrelated_key'"},
        EXPECTED_ACTIVE,
    ) is None
    assert classify_post_wave_candidate_failure(
        {"type": "KeyError", "message": "'formal_client_log'"},
        EXPECTED_ACTIVE,
    ) == "post_wave_plan_capture_runtime_field_failure"


if __name__ == "__main__":
    test_contract_shape()
    test_mysql_plan_conversion()
    test_balanced_scene_repetition_aggregate()
    test_complete_baseline_recovery_is_narrow_and_evidence_bound()
    test_complete_baseline_resume_branch_never_calls_run_baseline()
    test_apply_cost_timeout_retry_is_bounded_and_audited()
    test_publication_prelaunch_resume_preserves_completed_evidence()
    test_gptuner_pair_suggestions()
    test_publication_gptuner_uses_online_refined_bounds()
    test_gptuner_four_scene_fine_stage_isolation()
    test_aqetuner_feedback_warm_suggestions()
    test_aqetuner_training_uses_frozen_cpu_device()
    test_external_xp_quiescence_contract()
    test_cpuset_parser_and_v4_binding_disjointness()
    test_post_wave_candidate_failure_classification()
    print("mysql four-scene external baseline offline tests passed", flush=True)
