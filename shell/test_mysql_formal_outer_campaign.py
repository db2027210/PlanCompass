#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mysql_parameter_optimizer as module
import formal_mysql_sgd_campaign as campaign_entry
from formal_mysql_sgd_orchestration import FormalPointDependencies, FormalSGDNotReady
from common_outer_loop import advance_outer_shrink_progress, decide_outer_acceptance
from gradient_descent_optimizer import ParameterRange


ACTIVE = ["io_block_read_cost", "row_evaluate_cost"]


def _readiness(path: Path, *, ready: bool) -> Path:
    path.write_text(json.dumps({
        "status": "READY" if ready else "NOT_READY",
        "launch_authorized": ready,
    }))
    return path


def _readiness_checks(*, ready: bool):
    return [
        SimpleNamespace(
            passed=ready,
            requirement=requirement,
            item="test_launch_authority",
            reason="test readiness authority",
        )
        for requirement in ["AUTH", "SCHEMA", *(f"R{index:02d}" for index in range(1, 12))]
    ]


def _dependencies(benchmark: str) -> FormalPointDependencies:
    service = lambda *_args, **_kwargs: {}
    return FormalPointDependencies(
        lease_factory=service,
        template_restore=service,
        resource_fixed_readback=service,
        cost_apply_fresh_readback=service,
        session_factories={benchmark: service},
        drain_flush=service,
        correctness=service,
        plan_capture=service,
        finalizer=service,
        recovery=service,
        root_cause_review=service,
    )


def _config(output: str, max_attempts: int):
    return {
        "output": {"directory": output},
        "cost_parameters": {
            "active": list(ACTIVE),
            "baseline_values": {"io_block_read_cost": 1.0, "row_evaluate_cost": 0.1},
            "ranges": {
                "io_block_read_cost": {"min_value": 0.1, "max_value": 10.0},
                "row_evaluate_cost": {"min_value": 0.01, "max_value": 1.0},
            },
        },
        "optimizer": {},
        "formal_outer_protocol": {
            "target_accepted_shrinks": 10,
            "max_outer_attempts": max_attempts,
        },
    }


def _fake_runner(accept_from_attempt: int, captures: list):
    def run(config, **kwargs):
        attempt = int(config["optimizer"]["round_index"])
        outer = config["formal_outer_protocol"]
        baseline = copy.deepcopy(config["cost_parameters"]["baseline_values"])
        ranges = {
            name: ParameterRange(float(item["min_value"]), float(item["max_value"]))
            for name, item in config["cost_parameters"]["ranges"].items()
        }
        accepted = attempt >= accept_from_attempt
        candidate = {
            "io_block_read_cost": baseline["io_block_read_cost"] * 1.01,
            "row_evaluate_cost": baseline["row_evaluate_cost"] * 1.01,
        }
        decision = decide_outer_acceptance(
            incumbent_db_qps=100.0,
            candidate_db_qps=101.0 if accepted else 99.0,
            incumbent_target_utilization=0.9,
            candidate_target_utilization=0.8,
            target_resource="memory",
            incumbent_resource_utilization={"cpu": 0.4, "memory": 0.9, "io": 0.3},
            candidate_resource_utilization={"cpu": 0.5, "memory": 0.8, "io": 0.4},
            non_target_bottleneck_threshold=0.8,
            hard_gate_passed=True,
        )
        progress = advance_outer_shrink_progress(
            ranges=ranges,
            accepted_shrink_count=int(outer["accepted_shrink_count"]),
            rejected_count=int(outer["rejected_count"]),
            target_accepted_shrinks=10,
            decision=decision,
            candidate_params=candidate,
            active_params=ACTIVE,
        )
        validation = {
            "db_qps": 101.0,
            "utilization_pct": {"cpu": 0.4, "memory": 0.8, "io": 0.3},
        }
        chosen = (
            {
                "candidate_optimized_params": copy.deepcopy(candidate),
                "candidate_final_validation": copy.deepcopy(validation),
            }
            if accepted else None
        )
        evaluations = [
            {"recovery": {"verified": True}},
            {"recovery": {"verified": True}},
        ]
        captures.append(
            {
                "attempt": attempt,
                "baseline": baseline,
                "baseline_validation": copy.deepcopy(kwargs["baseline_validation"]),
                "ranges": copy.deepcopy(config["cost_parameters"]["ranges"]),
                "accepted": accepted,
            }
        )
        range_after = {
            name: {"min_value": item.min_value, "max_value": item.max_value}
            for name, item in progress.ranges.items()
        }
        return SimpleNamespace(
            initial_params=copy.deepcopy(baseline),
            optimized_params=copy.deepcopy(candidate),
            outer_shrink_progress=progress,
            real_replay_orchestration={"chosen": chosen, "evaluations": evaluations},
            result_path=f"round_{attempt}.json",
            formal_document={
                "meta": {},
                "outer_loop": [{"iter": attempt, "range_after": range_after}],
                "inner_loop_summary": {"total_outer_calls": 1},
                "result": {"converge_outer_iters": 1},
            },
        )
    return run


def test_rejections_do_not_consume_target_and_campaign_reaches_ten() -> None:
    captures = []
    original = module._run_optimization_unleased
    module._run_optimization_unleased = _fake_runner(3, captures)
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = module._run_formal_outer_campaign_unleased(
                _config(temp_dir, 20),
                client=None,
                real_replay_callback=lambda *_args, **_kwargs: {},
                baseline_validation={
                    "db_qps": 100.0,
                    "utilization_pct": {"cpu": 0.4, "memory": 0.9, "io": 0.3},
                },
                method_id="experience_table_sgd",
            )
    finally:
        module._run_optimization_unleased = original

    assert result.campaign_complete
    assert result.campaign_status == "complete"
    assert result.accepted_shrink_count == 10
    assert result.rejected_count == 2
    assert len(result.campaign_rounds) == 12
    assert result.formal_document["result"]["converge_outer_iters"] == 12
    assert result.verified_recovery_count == 24
    assert captures[0]["ranges"] == captures[1]["ranges"] == captures[2]["ranges"]
    assert set(result.campaign_rounds[2]["range_changes"]) == set(ACTIVE)
    assert captures[3]["baseline"] == result.campaign_rounds[2]["incumbent_after"]
    assert captures[3]["baseline_validation"] == result.campaign_rounds[2]["incumbent_validation_after"]


def test_safety_cap_returns_incomplete_diagnostic_without_fake_completion() -> None:
    captures = []
    original = module._run_optimization_unleased
    module._run_optimization_unleased = _fake_runner(999, captures)
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = module._run_formal_outer_campaign_unleased(
                _config(temp_dir, 3),
                client=None,
                real_replay_callback=lambda *_args, **_kwargs: {},
                baseline_validation={"db_qps": 100.0, "utilization_pct": {}},
                method_id="experience_table_sgd",
            )
    finally:
        module._run_optimization_unleased = original
    assert not result.campaign_complete
    assert result.campaign_status == "incomplete"
    assert result.accepted_shrink_count == 0
    assert result.rejected_count == 3
    assert result.campaign_diagnostic["remaining_accepted_shrinks"] == 10
    assert all(not row["range_changes"] for row in result.campaign_rounds)


def test_rejections_expand_escape_radius_and_stop_after_five_batches() -> None:
    captures = []
    config = _config("unused", 20)
    config["formal_outer_protocol"]["plan_basin_escape"] = {
        "enabled": True,
        "orthogonal_radius": 0.08,
        "maximum_radius": 0.32,
        "radius_gamma": 0.5,
        "maximum_rejected_batches": 5,
    }
    original = module._run_optimization_unleased
    module._run_optimization_unleased = _fake_runner(999, captures)
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            config["output"] = {"directory": temp_dir}
            result = module._run_formal_outer_campaign_unleased(
                config,
                client=None,
                real_replay_callback=lambda *_args, **_kwargs: {},
                baseline_validation={"db_qps": 100.0, "utilization_pct": {}},
                method_id="experience_table_sgd",
            )
    finally:
        module._run_optimization_unleased = original

    assert result.campaign_status == "incomplete"
    assert result.campaign_diagnostic["code"] == (
        "resource_feedback_escape_batches_exhausted_without_acceptance"
    )
    assert [row["escape_radius_after"] for row in result.campaign_rounds] == [
        0.16, 0.32, 0.32, 0.32, 0.32
    ]
    assert result.campaign_rounds[-1]["local_converged"] is True


def test_unique_production_entry_wires_dependencies_driver_r0_and_optimizer() -> None:
    service = lambda *_args, **_kwargs: {}
    dependencies = FormalPointDependencies(
        lease_factory=service,
        template_restore=service,
        resource_fixed_readback=service,
        cost_apply_fresh_readback=service,
        session_factories={"job": service, "hybench_xp": service},
        drain_flush=service,
        correctness=service,
        plan_capture=service,
        finalizer=service,
        recovery=service,
        root_cause_review=service,
    )
    baseline = {
        "qps": 100.0,
        "db_qps": 100.0,
        "db_qps_source": "database_status_counter",
        "p95_ms": 20.0,
        "p99_ms": 30.0,
        "utilization_pct": {"cpu": 0.4, "memory": 0.9, "io": 0.3},
    }
    bundle = {
        "registration": {"id": "job"},
        "dependencies": dependencies,
        "r0_measurement": lambda _config: baseline,
        "campaign_cleanup": lambda: {"zero_pressure_verified": True},
        "sql_client": object(),
        "host02_pressure_probe": lambda scenario, phase: {
            "observed_at": "2026-07-23T00:00:00+08:00",
            "probe_id": f"test-{scenario}-{phase}",
            "job_pressure_count": 0,
            "xp_pressure_count": 0,
        },
    }
    driver = object()
    captured = {}
    original_load = campaign_entry._load_factory
    original_build = campaign_entry.build_replay_driver
    original_run = campaign_entry.run_optimization
    original_verify = campaign_entry.verify_document
    campaign_entry._load_factory = lambda _spec: lambda _config: bundle
    campaign_entry.build_replay_driver = lambda *args: driver
    campaign_entry.run_optimization = lambda config, **kwargs: (
        captured.update({"config": config, **kwargs}) or "complete-result"
    )
    campaign_entry.verify_document = lambda _document: _readiness_checks(ready=True)
    try:
        with tempfile.TemporaryDirectory(dir=ROOT / "runtime_status") as raw:
            control = Path(raw)
            readiness = _readiness(control / "readiness.json", ready=True)
            lock_path = control / "job_xp_pressure.lock"
            journal_path = control / "job_xp_lease_journal.jsonl"
            result = campaign_entry.run_production_campaign(
                {
                    "benchmark": "job",
                    "target_resource": "memory",
                    "output": {"directory": str(control / "results")},
                    "host02_job_xp_mutex": {
                        "required": True,
                        "scenario": "job_memory",
                        "lock_path": str(lock_path),
                        "journal_path": str(journal_path),
                    },
                },
                readiness_manifest=readiness,
                service_factory_spec="services:build",
            )
            xp_result = campaign_entry.run_production_campaign(
                {
                    "benchmark": "hybench_xp",
                    "target_resource": "memory",
                    "output": {"directory": str(control / "results")},
                    "host02_job_xp_mutex": {
                        "required": True,
                        "scenario": "hybench_xp_io",
                        "lock_path": str(lock_path),
                        "journal_path": str(journal_path),
                    },
                },
                readiness_manifest=readiness,
                service_factory_spec="services:build",
            )
            journal_rows = [
                json.loads(line) for line in journal_path.read_text().splitlines() if line
            ]
            assert {
                row["scenario"] for row in journal_rows
                if row["event"] == "handoff_acquired"
            } == {"job_memory", "hybench_xp_io"}
            try:
                campaign_entry.run_production_campaign(
                    {"benchmark": "job", "target_resource": "memory", "output": {"directory": str(control / "results")}},
                    readiness_manifest=readiness,
                    service_factory_spec="services:build",
                )
            except FormalSGDNotReady as exc:
                assert "host02_job_xp_mutex.required=true" in str(exc)
            else:
                raise AssertionError(
                    "JOB production entry without the shared mutex must fail closed"
                )
    finally:
        campaign_entry._load_factory = original_load
        campaign_entry.build_replay_driver = original_build
        campaign_entry.run_optimization = original_run
        campaign_entry.verify_document = original_verify
    assert result == "complete-result"
    assert xp_result == "complete-result"
    assert captured["real_replay_callback"] is driver
    assert captured["baseline_validation"] == baseline
    assert captured["method_id"] == "experience_table_sgd"

    try:
        campaign_entry._load_factory("missing-factory-spec")
    except FormalSGDNotReady:
        pass
    else:
        raise AssertionError("missing production service factory must fail closed")


def test_readiness_gate_precedes_service_factory_and_r0() -> None:
    calls = {"factory": 0, "r0": 0, "optimizer": 0}

    def r0(_config):
        calls["r0"] += 1
        return {
            "qps": 1.0, "db_qps": 1.0,
            "db_qps_source": "database_status_counter",
            "p95_ms": 1.0, "p99_ms": 2.0,
            "utilization_pct": {"cpu": 0.9, "memory": 0.4, "io": 0.4},
        }

    bundle = {
        "registration": {"id": "tpch"},
        "dependencies": _dependencies("tpch"),
        "r0_measurement": r0,
        "campaign_cleanup": lambda: {"zero_pressure_verified": True},
    }

    def factory(_config):
        calls["factory"] += 1
        return bundle

    original_load = campaign_entry._load_factory
    original_build = campaign_entry.build_replay_driver
    original_run = campaign_entry.run_optimization
    original_verify = campaign_entry.verify_document
    campaign_entry._load_factory = lambda _spec: factory
    campaign_entry.build_replay_driver = lambda *_args: object()
    campaign_entry.run_optimization = lambda *_args, **_kwargs: (
        calls.__setitem__("optimizer", calls["optimizer"] + 1) or "entered"
    )
    try:
        with tempfile.TemporaryDirectory() as raw:
            manifest = _readiness(Path(raw) / "readiness.json", ready=False)
            campaign_entry.verify_document = lambda _document: _readiness_checks(ready=False)
            try:
                campaign_entry.run_production_campaign(
                    {"benchmark": "tpch"},
                    readiness_manifest=manifest,
                    service_factory_spec="services:build",
                )
            except FormalSGDNotReady as exc:
                assert "readiness manifest failed" in str(exc)
            else:
                raise AssertionError("NOT_READY manifest must fail closed")
            assert calls == {"factory": 0, "r0": 0, "optimizer": 0}

            _readiness(manifest, ready=True)
            campaign_entry.verify_document = lambda _document: [SimpleNamespace(
                passed=True,
                requirement="AUTH",
                item="partial_verifier",
                reason="missing R01-R11 and SCHEMA",
            )]
            try:
                campaign_entry.run_production_campaign(
                    {"benchmark": "tpch"},
                    readiness_manifest=manifest,
                    service_factory_spec="services:build",
                )
            except FormalSGDNotReady as exc:
                assert "verifier requirement groups absent" in str(exc)
            else:
                raise AssertionError("partial readiness verification must fail closed")
            assert calls == {"factory": 0, "r0": 0, "optimizer": 0}

            campaign_entry.verify_document = lambda _document: _readiness_checks(ready=True)
            result = campaign_entry.run_production_campaign(
                {"benchmark": "tpch", "target_resource": "cpu", "output": {"directory": str(Path(raw) / "results")}},
                readiness_manifest=manifest,
                service_factory_spec="services:build",
            )
            assert result == "entered"
            assert calls == {"factory": 1, "r0": 1, "optimizer": 1}
    finally:
        campaign_entry._load_factory = original_load
        campaign_entry.build_replay_driver = original_build
        campaign_entry.run_optimization = original_run
        campaign_entry.verify_document = original_verify


def test_production_real_r0_is_persisted_reused_and_identity_bound() -> None:
    calls = {"r0": 0, "optimizer": 0}
    baseline = {
        "qps": 100.0,
        "db_qps": 100.0,
        "db_qps_source": "database_status_counter",
        "p95_ms": 20.0,
        "p99_ms": 30.0,
        "utilization_pct": {"cpu": 0.9, "memory": 0.4, "io": 0.4},
    }

    def r0(_config):
        calls["r0"] += 1
        return copy.deepcopy(baseline)

    bundle = {
        "registration": {"id": "tpch", "freeze": "v1"},
        "dependencies": _dependencies("tpch"),
        "r0_measurement": r0,
        "campaign_cleanup": lambda: {"zero_pressure_verified": True},
    }
    original_load = campaign_entry._load_factory
    original_build = campaign_entry.build_replay_driver
    original_run = campaign_entry.run_optimization
    original_verify = campaign_entry.verify_document
    campaign_entry._load_factory = lambda _spec: lambda _config: bundle
    campaign_entry.build_replay_driver = lambda *_args: object()
    campaign_entry.run_optimization = lambda _config, **kwargs: (
        calls.__setitem__("optimizer", calls["optimizer"] + 1)
        or copy.deepcopy(kwargs["baseline_validation"])
    )
    campaign_entry.verify_document = lambda _document: _readiness_checks(ready=True)
    try:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            readiness = _readiness(root / "readiness.json", ready=True)
            config = {
                "benchmark": "tpch",
                "target_resource": "cpu",
                "output": {"directory": str(root / "results")},
            }
            first = campaign_entry.run_production_campaign(
                config, readiness_manifest=readiness,
                service_factory_spec="services:build",
            )
            second = campaign_entry.run_production_campaign(
                config, readiness_manifest=readiness,
                service_factory_spec="services:build",
            )
            assert first == second == baseline
            assert calls == {"r0": 1, "optimizer": 2}
            checkpoint = root / "results/formal_production_real_r0_tpch.json"
            document = json.loads(checkpoint.read_text())
            assert document["measurement_kind"] == "production_real_database_r0"
            assert document["measurement"] == baseline
            assert checkpoint.with_name(checkpoint.name + ".sha256").is_file()

            changed = copy.deepcopy(config)
            changed["target_resource"] = "memory"
            try:
                campaign_entry.run_production_campaign(
                    changed, readiness_manifest=readiness,
                    service_factory_spec="services:build",
                )
            except FormalSGDNotReady as exc:
                assert "campaign identity mismatch" in str(exc)
            else:
                raise AssertionError("changed campaign must not reuse the sealed real R0")
            assert calls["r0"] == 1
    finally:
        campaign_entry._load_factory = original_load
        campaign_entry.build_replay_driver = original_build
        campaign_entry.run_optimization = original_run
        campaign_entry.verify_document = original_verify


def test_campaign_cleanup_is_required_and_runs_on_optimizer_failure() -> None:
    calls = {"cleanup": 0}
    baseline = {
        "qps": 100.0,
        "db_qps": 100.0,
        "db_qps_source": "database_status_counter",
        "p95_ms": 20.0,
        "p99_ms": 30.0,
        "utilization_pct": {"cpu": 0.9, "memory": 0.4, "io": 0.4},
    }
    bundle = {
        "registration": {"id": "tpch"},
        "dependencies": _dependencies("tpch"),
        "r0_measurement": lambda _config: baseline,
    }
    original_load = campaign_entry._load_factory
    original_build = campaign_entry.build_replay_driver
    original_run = campaign_entry.run_optimization
    original_verify = campaign_entry.verify_document
    campaign_entry._load_factory = lambda _spec: lambda _config: bundle
    campaign_entry.build_replay_driver = lambda *_args: object()
    campaign_entry.run_optimization = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("optimizer failed")
    )
    campaign_entry.verify_document = lambda _document: _readiness_checks(ready=True)
    try:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            readiness = _readiness(root / "readiness.json", ready=True)
            config = {
                "benchmark": "tpch",
                "target_resource": "cpu",
                "output": {"directory": str(root / "results")},
            }
            try:
                campaign_entry.run_production_campaign(
                    config,
                    readiness_manifest=readiness,
                    service_factory_spec="services:build",
                )
            except FormalSGDNotReady as exc:
                assert "cleanup service is missing" in str(exc)
            else:
                raise AssertionError("missing campaign cleanup must fail closed")
            bundle["campaign_cleanup"] = lambda: (
                calls.__setitem__("cleanup", calls["cleanup"] + 1)
                or {"zero_pressure_verified": True}
            )
            try:
                campaign_entry.run_production_campaign(
                    config,
                    readiness_manifest=readiness,
                    service_factory_spec="services:build",
                )
            except RuntimeError as exc:
                assert str(exc) == "optimizer failed"
            else:
                raise AssertionError("optimizer failure should propagate")
            assert calls == {"cleanup": 1}
    finally:
        campaign_entry._load_factory = original_load
        campaign_entry.build_replay_driver = original_build
        campaign_entry.run_optimization = original_run
        campaign_entry.verify_document = original_verify


if __name__ == "__main__":
    test_rejections_do_not_consume_target_and_campaign_reaches_ten()
    test_safety_cap_returns_incomplete_diagnostic_without_fake_completion()
    test_rejections_expand_escape_radius_and_stop_after_five_batches()
    test_unique_production_entry_wires_dependencies_driver_r0_and_optimizer()
    test_readiness_gate_precedes_service_factory_and_r0()
    test_production_real_r0_is_persisted_reused_and_identity_bound()
    print("formal outer campaign tests passed")
