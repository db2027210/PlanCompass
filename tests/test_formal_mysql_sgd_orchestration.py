from __future__ import annotations

import copy
import unittest
from pathlib import Path
import tempfile

from formal_recost_core import MYSQL_COST_NAMES
from formal_mysql_database_qps import FormalDatabaseQPSError
from formal_mysql_sgd_orchestration import (
    FormalMySQLSGDReplayDriver,
    FormalPointJournal,
    FormalPointDependencies,
    FormalSGDNotReady,
    FormalSGDPointError,
    FormalSGDResumeError,
    average_three_valid_independent_runs,
    build_hybench_ap_replay_driver,
    build_hybench_xp_replay_driver,
    build_job_replay_driver,
    build_replay_driver,
    build_tpcc_replay_driver,
    build_tpch_replay_driver,
    _run_continuous_session,
)
from mysql_parameter_optimizer import _orchestrate_staged_real_replays
from mysql_parameter_optimizer import REQUIRED_REAL_REPLAY_EVIDENCE
from mysql_plan_clustering import build_canonical_plan_bundle
from shell.manual_mysql_sgd_lane_controller import (
    release_host02_campaign_lease,
    try_acquire_host02_campaign_lease,
)


def independent_proof(bindings=None):
    return {
        "stable": True,
        "db_qps_source": "database_status_counter",
        "contract_bindings": bindings or {
            "point_parameters_float_hex_sha256": "sha256:params",
            "resource_envelope_sha256": "sha256:envelope",
            "template_sha256": "sha256:template",
            "workload_registry_sha256": "sha256:workload",
            "plan_contract_sha256": "sha256:plan",
        },
        "evidence_bundle": {
            name: f"artifact://independent/{name}"
            for name in REQUIRED_REAL_REPLAY_EVIDENCE
        },
    }


class FakeLease:
    def __init__(self, events):
        self.events = events
        self.evidence = {"lease_id": "job-lane-1"}

    def __enter__(self):
        self.events.append("lease_enter")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.events.append("lease_exit")


class FakeSession:
    def __init__(
        self, events, stable_at=210, source="database_status_counter",
        boundary_failures=(),
    ):
        self.events = events
        self.stable_at = stable_at
        self.source = source
        self.boundary_failures = set(boundary_failures)

    def start(self, *, warmup_seconds, maximum_measurement_seconds):
        self.events.append(("session_start", warmup_seconds, maximum_measurement_seconds))

    def wait_until_measurement(self, measurement_seconds):
        self.events.append(("session_wait", measurement_seconds))

    def observation(self, measurement_seconds):
        if measurement_seconds in self.boundary_failures:
            raise FormalDatabaseQPSError(
                "database counter boundary coverage is too sparse"
            )
        windows = []
        for index in range(1, measurement_seconds // 30 + 1):
            resource_stable = measurement_seconds >= self.stable_at
            windows.append({
                "index": index,
                "sample_coverage": 1.0,
                "stable": resource_stable,
                "metrics": {
                    resource: {"stable": resource_stable}
                    for resource in ("cpu", "memory", "io")
                },
            })
        return {
            "measurement_seconds": measurement_seconds,
            "throughput_evidence": {
                "reliable": True, "source": self.source, "db_qps": 110.0,
            },
            "database_throughput": {
                "reliable": True, "source": self.source, "db_qps": 110.0,
            },
            "stability": {
                "stable": measurement_seconds >= self.stable_at,
                "decision_window_seconds": 180,
                "windows": windows,
            },
        }

    def stop(self, measurement_seconds):
        self.events.append(("session_stop", measurement_seconds))
        return {"samples_path": "artifact://samples"}


def registration():
    return {
        "default_costs": {name: float(index + 1) for index, name in enumerate(MYSQL_COST_NAMES)},
        "evidence": {
            "campaign_method_benchmark_scenario": "artifact://identity",
            "envelope_and_template_versions": "artifact://versions",
            "lane_host_container_port": "artifact://lane",
            "software_checksums": "artifact://checksums",
            "workload_seed_registry_weights_clients": "artifact://job-registry",
            "cpuset_and_numa": "artifact://placement",
            "range_before_after": "artifact://ranges",
            "experience_table_direction_eta": "artifact://experience-table",
        },
    }


class Fixture:
    def __init__(
        self, *, source="database_status_counter", with_job=True,
        benchmarks=None, fail_stage=None, stable_at=210, unstable_point_repeater=None,
    ):
        self.events = []
        self.session_contexts = []
        self.source = source
        self.fail_stage = fail_stage
        self.stable_at = stable_at
        registered = set(benchmarks if benchmarks is not None else ({"job"} if with_job else set()))
        factories = {name: self.session_factory for name in registered}
        self.dependencies = FormalPointDependencies(
            lease_factory=lambda context: FakeLease(self.events),
            template_restore=self.template_restore,
            resource_fixed_readback=self.readback,
            cost_apply_fresh_readback=self.cost_apply,
            session_factories=factories,
            drain_flush=self.drain_flush,
            correctness=self.correctness,
            plan_capture=self.plan,
            finalizer=self.finalizer,
            recovery=self.recovery,
            unstable_point_repeater=unstable_point_repeater,
        )

    def template_restore(self, context):
        self.events.append("template_restore_service")
        return {"template_manifest": "artifact://template"}

    def readback(self, context, phase):
        self.events.append(f"readback_{phase}_service")
        return {
            "phase": phase,
            "resource_limits": "artifact://limits",
            "fixed_settings": "artifact://fixed",
        }

    def cost_apply(self, params, context):
        self.events.append("cost_apply_service")
        assert set(params) == set(MYSQL_COST_NAMES)
        return {
            "params_requested": dict(params),
            "params_readback": dict(params),
            "commit": True,
            "flush": "FLUSH OPTIMIZER_COSTS",
            "fresh_connection_generation": 7,
        }

    def session_factory(self, context):
        self.events.append("session_factory")
        self.session_contexts.append(context)
        return FakeSession(self.events, stable_at=self.stable_at, source=self.source)

    def drain_flush(self, context):
        self.events.append("drain_flush_service")
        return {"drained": True, "flushed": True, "artifact": "artifact://drain"}

    def correctness(self, context):
        self.events.append("correctness_service")
        result = {
            "passed": True,
            "error_rate": 0.0,
            "oom": False,
            "mysql_restart": False,
            "container_restart": False,
            "artifact": "artifact://correctness",
        }
        if context["benchmark"] == "tpcc":
            result.update({
                "tpcc_invariant_passed": True,
                "tpcc_pre_signature": "sha256:tpcc-pre",
                "tpcc_post_signature": "sha256:tpcc-post",
            })
        return result

    def plan(self, context):
        self.events.append("plan_service")
        if self.fail_stage == "plan":
            raise RuntimeError("injected plan failure")
        bundle = build_canonical_plan_bundle(
            [{
                "query_id": "q1",
                "query_sql_sha256": "a" * 64,
                "query_order": 1,
                "query_weight": 1.0,
                "dynamic": False,
                "binding_id": "static",
                "binding_values": None,
                "plan_mode": "custom",
                "raw_explain": {"query_block": {"select_id": 1, "table": {
                    "table_name": "t", "access_type": "ALL", "key": None,
                    "rows_examined_per_scan": 1,
                }}},
            }],
            registry_query_ids=["q1"],
            registry_sha256="b" * 64,
            registry_authority_verified=True,
        )
        return {"canonical_plan_bundle": bundle, "artifact": "artifact://explain"}

    def finalizer(self, result, context):
        self.events.append("finalizer_service")
        measurement = {
                "qps": 110.0,
                "db_qps": 110.0,
                "db_qps_source": "database_status_counter",
                "p95_ms": 20.0,
                "p99_ms": 30.0,
                "correctness_passed": True,
                "error_rate": 0.0,
                "stable": True,
                "sample_coverage": 0.95,
                "oom": False,
                "mysql_restart": False,
                "container_restart": False,
                "utilization_pct": {"cpu": 0.4, "memory": 0.88, "io": 0.3},
                "three_level_fingerprint_evidence": {
                    "execution_state_by_query": {
                        "q1": {
                            "workers": 0,
                            "hash_batches": 1,
                            "sort_mode": "none",
                            "spill_mode": "none",
                            "spill_bytes": 0,
                            "bitmap_lossy_pages": 0,
                            "intermediate_rows": 1,
                        },
                    },
                    "resource_vectors": {
                        "q1": {
                            "memory_peak_bytes": 1024,
                            "dynamic_execution_memory_bytes": 512,
                            "spill_bytes": 0,
                        },
                    },
                },
        }
        if context["benchmark"] == "tpcc":
            measurement.update({
                "native_tps": 90.0,
                "sensitive_tps": 0.06,
                "combined_tps": 90.06,
            })
        return {
            "measurement": measurement,
            "evidence": {
                "estimator_model_formula": "artifact://model",
                "per_query_operator_breakdown": "artifact://operators",
                "per_second_samples": "artifact://samples",
                "stability_windows_and_coverage": "artifact://stability",
                "utility_components": "artifact://utility",
            },
        }

    def recovery(self, context):
        self.events.append("recovery_service")
        return {
            "succeeded": True,
            "effective_all_costs": dict(context["baseline_all_costs"]),
            "fixed_settings_verified": True,
            "clean_template_verified": True,
            "evidence": "artifact://recovery",
        }


class FormalMySQLSGDOrchestrationTest(unittest.TestCase):
    def test_sparse_database_boundary_is_skipped_without_relaxing_evidence(self):
        events = []
        session = FakeSession(
            events, stable_at=240, boundary_failures={210}
        )
        result = _run_continuous_session(
            session,
            maximum_measurement_seconds=300,
            minimum_selection_seconds=180,
        )

        control = result["rolling_measurement_control"]
        self.assertEqual(control["selected_measurement_seconds"], 240)
        self.assertEqual(
            control["skipped_database_boundaries"],
            [{
                "measurement_seconds": 210,
                "reason": "database counter boundary coverage is too sparse",
                "evidence_status": (
                    "invalid_not_used_for_stability_or_acceptance"
                ),
            }],
        )
        self.assertIn(("session_wait", 240), events)

    def test_finalizer_cannot_replace_database_side_dbqps(self):
        class MismatchedFinalizerFixture(Fixture):
            def finalizer(inner_self, result, context):
                payload = super().finalizer(result, context)
                payload["measurement"]["qps"] = 109.0
                payload["measurement"]["db_qps"] = 109.0
                return payload

        fixture = MismatchedFinalizerFixture()
        driver = build_job_replay_driver(registration(), fixture.dependencies)
        defaults = registration()["default_costs"]
        candidate = {"subset_id": "s1", "optimized_params": defaults}
        context = {"baseline_params": defaults, "replay_ordinal": 1}
        with self.assertRaisesRegex(FormalSGDPointError, "selected database-side"):
            driver(candidate, context)
        driver.restore(candidate, context)

    def test_outer_attempt_token_reconciles_from_completed_point_journal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = Fixture()
            journal = FormalPointJournal(root, ("formal-sgd-resume",))
            driver = build_job_replay_driver(registration(), fixture.dependencies, journal)
            defaults = registration()["default_costs"]
            token = "a" * 32
            candidate = {"rank": 1, "subset_id": "s1", "optimized_params": defaults}
            context = {
                "baseline_params": defaults, "replay_ordinal": 1,
                "outer_replay_attempt_token": token,
            }
            measurement = driver(candidate, context)
            driver.restore(candidate, context)

            resumed_driver = build_job_replay_driver(
                registration(), fixture.dependencies,
                FormalPointJournal(root, ("formal-sgd-resume",)),
            )
            reconciled = resumed_driver.reconcile_started_replay({
                "attempt_token": token, "rank": 1, "stage": "top_5",
                "candidate": candidate,
            })
            self.assertEqual(reconciled["status"], "completed")
            self.assertEqual(reconciled["measurement"]["db_qps"], measurement["db_qps"])
            self.assertTrue(reconciled["recovery"]["succeeded"])

    @staticmethod
    def _dynamic_custom_only_bundle(*, include_large=True):
        captures = []
        bindings = [("small", 1)]
        if include_large:
            bindings.append(("large", 1_000_000))
        for binding_id, value in bindings:
            captures.append({
                "query_id": "qd",
                "query_sql_sha256": "c" * 64,
                "query_order": 1,
                "query_weight": 1.0,
                "dynamic": True,
                "binding_id": binding_id,
                "binding_values": [value],
                "plan_mode": "custom",
                "generic_plan_semantics_available": False,
                "required_binding_ids": ["small", "large"],
                "required_plan_modes": ["generic", "custom"],
                "raw_explain": {"query_block": {"table": {
                    "table_name": "t", "access_type": "ALL",
                }}},
            })
        return build_canonical_plan_bundle(
            captures,
            registry_query_ids=["qd"],
            registry_sha256="d" * 64,
            registry_authority_verified=True,
        )

    def test_post_replay_plan_capture_requires_verified_strict_bundle(self):
        class InvalidPlanFixture(Fixture):
            def plan(self, context):
                self.events.append("plan_service")
                return {"canonical_plan_bundle": {
                    "schema_version": "mysql_strict_physical_plan_bundle_v1",
                    "strict_cluster_usable": True,
                    "plan_similarity_cluster_id": "forged",
                }}

        fixture = InvalidPlanFixture()
        driver = build_job_replay_driver(registration(), fixture.dependencies)
        defaults = registration()["default_costs"]
        with self.assertRaisesRegex(FormalSGDPointError, "invalid canonical bundle"):
            driver(
                {"subset_id": "s1", "rank": 1, "optimized_params": defaults},
                {"baseline_params": defaults, "replay_ordinal": 1},
            )
        driver.restore({}, {"baseline_params": defaults})

    def test_dynamic_custom_only_capture_remains_valid_for_real_replay(self):
        bundle = self._dynamic_custom_only_bundle()
        self.assertFalse(bundle["strict_cluster_usable"])
        self.assertEqual(
            bundle["strict_cluster_ineligible_reasons"],
            ["generic_plan_semantics_unavailable:qd"],
        )

        class DynamicPlanFixture(Fixture):
            def plan(inner_self, context):
                inner_self.events.append("plan_service")
                return {"canonical_plan_bundle": copy.deepcopy(bundle)}

        fixture = DynamicPlanFixture()
        driver = build_job_replay_driver(registration(), fixture.dependencies)
        defaults = registration()["default_costs"]
        candidate = {"subset_id": "s1", "rank": 1, "optimized_params": defaults}
        context = {"baseline_params": defaults, "replay_ordinal": 1}
        measurement = driver(candidate, context)
        captured = measurement["evidence"]["explain_and_fingerprints"][
            "canonical_plan_bundle"
        ]
        self.assertFalse(captured["strict_cluster_usable"])
        self.assertIsNone(captured["plan_similarity_cluster_id"])
        driver.restore(candidate, context)

    def test_dynamic_capture_missing_representative_custom_binding_is_rejected(self):
        bundle = self._dynamic_custom_only_bundle(include_large=False)
        self.assertIn(
            "incomplete_binding_or_plan_mode_coverage:qd",
            bundle["strict_cluster_ineligible_reasons"],
        )

        class IncompleteDynamicPlanFixture(Fixture):
            def plan(inner_self, context):
                inner_self.events.append("plan_service")
                return {"canonical_plan_bundle": copy.deepcopy(bundle)}

        fixture = IncompleteDynamicPlanFixture()
        driver = build_job_replay_driver(registration(), fixture.dependencies)
        defaults = registration()["default_costs"]
        candidate = {"subset_id": "s1", "rank": 1, "optimized_params": defaults}
        context = {"baseline_params": defaults, "replay_ordinal": 1}
        with self.assertRaisesRegex(
            FormalSGDPointError, "complete representative custom coverage"
        ):
            driver(candidate, context)
        driver.restore(candidate, context)

    def test_three_valid_independent_runs_exclude_technical_failures_and_average(self):
        attempts = [
            {"run_id": "tech-1", "technical_failure": True, "failure_type": "ssh"},
            {"run_id": "r1", "point_id": "p", "valid_independent_run": True,
             **independent_proof(),
             "correctness_passed": True, "oom": False, "mysql_restart": False, "container_restart": False,
             "resource_means": {"cpu": 0.3, "memory": 0.6, "io": 0.9},
             "metrics": {"qps": 90.0, "db_qps": 90.0, "p95_ms": 12.0,
                         "p99_ms": 18.0, "sample_coverage": 0.95, "error_rate": 0.0}},
            {"run_id": "tech-2", "technical_failure": True, "failure_type": "collector"},
            {"run_id": "r2", "point_id": "p", "valid_independent_run": True,
             **independent_proof(),
             "correctness_passed": True, "oom": False, "mysql_restart": False, "container_restart": False,
             "resource_means": {"cpu": 0.6, "memory": 0.3, "io": 0.6},
             "metrics": {"qps": 120.0, "db_qps": 120.0, "p95_ms": 15.0,
                         "p99_ms": 21.0, "sample_coverage": 0.95, "error_rate": 0.0}},
            {"run_id": "r3", "point_id": "p", "valid_independent_run": True,
             **independent_proof(),
             "correctness_passed": True, "oom": False, "mysql_restart": False, "container_restart": False,
             "resource_means": {"cpu": 0.9, "memory": 0.9, "io": 0.3},
             "metrics": {"qps": 150.0, "db_qps": 150.0, "p95_ms": 18.0,
                         "p99_ms": 24.0, "sample_coverage": 0.95, "error_rate": 0.0}},
        ]
        result = average_three_valid_independent_runs(attempts)
        self.assertEqual(result["valid_run_ids"], ["r1", "r2", "r3"])
        self.assertEqual(len(result["technical_failures_excluded"]), 2)
        self.assertAlmostEqual(result["resource_means"]["cpu"], 0.6)
        self.assertAlmostEqual(result["metric_means"]["qps"], 120.0)

    def test_three_valid_independent_runs_fail_closed_on_wrong_count_or_duplicate(self):
        valid = lambda run_id: {
            "run_id": run_id, "point_id": "p", "valid_independent_run": True,
            **independent_proof(),
            "correctness_passed": True, "oom": False, "mysql_restart": False, "container_restart": False,
            "resource_means": {"cpu": 0.1, "memory": 0.2, "io": 0.3},
            "metrics": {"qps": 10.0, "db_qps": 10.0, "p95_ms": 1.0,
                        "p99_ms": 2.0, "sample_coverage": 0.95, "error_rate": 0.0},
        }
        with self.assertRaisesRegex(FormalSGDPointError, "exactly 3"):
            average_three_valid_independent_runs([valid("r1"), valid("r2")])
        with self.assertRaisesRegex(FormalSGDPointError, "unique run_id"):
            average_three_valid_independent_runs([valid("r1"), valid("r1"), valid("r3")])

    def test_unstable_first_run_requires_and_applies_three_independent_runs(self):
        repeater_calls = []

        def repeater(candidate, context):
            repeater_calls.append((copy.deepcopy(candidate), copy.deepcopy(context)))
            point_id = "same-point"
            rows = []
            # Deliberately asymmetric around neither the first continuous run's
            # 110 DBQPS nor any repeated run.  The aggregate is 111 DBQPS.
            for run_id, qps, memory in (("r1", 101.0, 0.75), ("r2", 113.0, 0.81), ("r3", 119.0, 0.78)):
                rows.append({
                    "run_id": run_id, "point_id": point_id,
                    "valid_independent_run": True, "correctness_passed": True,
                    **independent_proof(
                        context["expected_independent_run_contract_bindings"]
                    ),
                    "oom": False, "mysql_restart": False, "container_restart": False,
                    "resource_means": {"cpu": 0.4, "memory": memory, "io": 0.3},
                    "metrics": {"qps": qps, "db_qps": qps, "p95_ms": 20.0,
                                "p99_ms": 30.0, "sample_coverage": 0.95,
                                "error_rate": 0.0},
                })
            return rows

        fixture = Fixture(stable_at=601, unstable_point_repeater=repeater)
        driver = build_job_replay_driver(registration(), fixture.dependencies)
        defaults = registration()["default_costs"]
        measurement = driver(
            {"subset_id": "s1", "rank": 1, "optimized_params": defaults},
            {"baseline_params": defaults, "replay_ordinal": 1},
        )
        self.assertTrue(measurement["stable"])
        self.assertAlmostEqual(measurement["db_qps"], 111.0)
        self.assertNotEqual(measurement["db_qps"], 110.0)
        self.assertAlmostEqual(measurement["utilization_pct"]["memory"], 0.78)
        self.assertEqual(
            measurement["independent_run_aggregation"]["valid_run_ids"],
            ["r1", "r2", "r3"],
        )
        self.assertEqual(len(repeater_calls), 1)
        self.assertEqual(
            [event for event in fixture.events if isinstance(event, tuple) and event[0] == "session_wait"],
            [("session_wait", seconds) for seconds in range(180, 601, 30)],
        )
        self.assertIn(("session_stop", 600), fixture.events)
        driver.restore({}, {"baseline_params": defaults})

    def test_stable_single_measurement_requires_database_status_counter(self):
        class WrongDBQPSSourceFixture(Fixture):
            def finalizer(inner_self, result, context):
                finalized = super().finalizer(result, context)
                finalized["measurement"]["db_qps_source"] = "client_completed_jobs"
                return finalized

        fixture = WrongDBQPSSourceFixture(stable_at=180)
        driver = build_job_replay_driver(registration(), fixture.dependencies)
        defaults = registration()["default_costs"]
        with self.assertRaisesRegex(FormalSGDPointError, "database_status_counter"):
            driver(
                {"subset_id": "s1", "rank": 1, "optimized_params": defaults},
                {"baseline_params": defaults, "replay_ordinal": 1},
            )
        driver.restore({}, {"baseline_params": defaults})

    def test_job_driver_connects_to_optimizer_and_obeys_point_timeline(self):
        fixture = Fixture()
        driver = build_job_replay_driver(registration(), fixture.dependencies)
        defaults = registration()["default_costs"]
        plan_bundle = fixture.plan({})["canonical_plan_bundle"]
        fixture.events.clear()
        candidates = [{
            "rank": 1,
            "subset_id": "subset_0001",
            "optimized_params": dict(defaults),
            "canonical_plan_bundle": plan_bundle,
        }]
        baseline = {
            "qps": 100.0,
            "db_qps": 100.0,
            "db_qps_source": "database_status_counter",
            "p95_ms": 20.0,
            "p99_ms": 30.0,
            "utilization_pct": {"cpu": 0.4, "memory": 0.9, "io": 0.3},
        }

        result = _orchestrate_staged_real_replays(
            candidates,
            callback=driver,
            baseline_params=defaults,
            baseline_validation=baseline,
            sensitivity_table={name: {"memory": -0.1, "qps": 0.1} for name in defaults},
            target_resource="memory",
            benchmark="job",
            acceptance_tier="qps_target",
        )

        self.assertEqual(result["eligible_count"], 1)
        evaluation = result["evaluations"][0]
        self.assertTrue(evaluation["recovery"]["verified"])
        self.assertFalse(
            evaluation["candidate_final_validation"]["outer_gate"]["missing_evidence_references"]
        )
        self.assertEqual(
            fixture.events,
            [
                "lease_enter",
                "template_restore_service",
                "readback_before_service",
                "cost_apply_service",
                "session_factory",
                ("session_start", 60, 600),
                ("session_wait", 180),
                ("session_wait", 210),
                ("session_stop", 210),
                "drain_flush_service",
                "correctness_service",
                "plan_service",
                "readback_after_service",
                "finalizer_service",
                "recovery_service",
                "lease_exit",
            ],
        )

    def test_four_unregistered_benchmarks_and_missing_job_factory_are_not_ready(self):
        fixture = Fixture()
        for benchmark in ("hybench_ap", "tpch", "tpcc", "hybench_xp"):
            with self.assertRaisesRegex(FormalSGDNotReady, "NOT_READY"):
                FormalMySQLSGDReplayDriver(benchmark, registration(), fixture.dependencies)
        missing = Fixture(with_job=False)
        with self.assertRaisesRegex(FormalSGDNotReady, "job has no formal"):
            build_job_replay_driver(registration(), missing.dependencies)

    def test_ap_tpch_xp_factories_use_the_identical_formal_point_protocol(self):
        builders = {
            "hybench_ap": build_hybench_ap_replay_driver,
            "tpch": build_tpch_replay_driver,
            "hybench_xp": build_hybench_xp_replay_driver,
        }
        defaults = registration()["default_costs"]
        expected_services = [
            "lease_enter", "template_restore_service", "readback_before_service",
            "cost_apply_service", "session_factory",
            ("session_start", 60, 600), ("session_wait", 180),
            ("session_wait", 210), ("session_stop", 210),
            "drain_flush_service", "correctness_service", "plan_service",
            "readback_after_service", "finalizer_service", "recovery_service",
            "lease_exit",
        ]
        for benchmark, builder in builders.items():
            with self.subTest(benchmark=benchmark):
                fixture = Fixture(benchmarks={benchmark})
                driver = builder(registration(), fixture.dependencies)
                candidate = {"subset_id": "s1", "optimized_params": defaults}
                context = {"baseline_params": defaults, "replay_ordinal": 1}
                measurement = driver(candidate, context)
                recovery = driver.restore(candidate, context)
                self.assertEqual(fixture.events, expected_services)
                self.assertEqual(fixture.session_contexts[0]["benchmark"], benchmark)
                self.assertEqual(
                    set(fixture.session_contexts[0]["requested_all_costs"]),
                    set(MYSQL_COST_NAMES),
                )
                self.assertTrue(recovery["succeeded"])
                self.assertEqual(measurement["db_qps"], 110.0)

    def test_registered_factory_still_rejects_questions_for_every_ready_benchmark(self):
        defaults = registration()["default_costs"]
        for benchmark in ("hybench_ap", "job", "tpch", "hybench_xp"):
            with self.subTest(benchmark=benchmark):
                fixture = Fixture(
                    benchmarks={benchmark}, source="performance_schema.questions"
                )
                driver = build_replay_driver(benchmark, registration(), fixture.dependencies)
                candidate = {"subset_id": "s1", "optimized_params": defaults}
                context = {"baseline_params": defaults, "replay_ordinal": 1}
                with self.assertRaisesRegex(FormalSGDPointError, "Questions"):
                    driver(candidate, context)
                driver.restore(candidate, context)

    def test_tpcc_requires_factory_and_frozen_invariant_contract(self):
        fixture = Fixture(benchmarks={"tpcc"})
        with self.assertRaisesRegex(FormalSGDNotReady, "frozen invariant"):
            build_tpcc_replay_driver(registration(), fixture.dependencies)

        registered = registration()
        registered["tpcc_frozen_invariant"] = {
            "version": "tpcc-invariant-v1",
            "sha256": "sha256:invariant",
            "native_terminals": 32,
            "warehouses": 10,
            "sensitive_injection_interval_seconds": 15,
            "throughput_fields": ["native_tps", "sensitive_tps", "combined_tps"],
            "pre_run_signature_ref": "artifact://tpcc-pre",
            "post_run_validator_ref": "artifact://tpcc-post",
            "business_abort_policy_ref": "artifact://tpcc-aborts",
            "durability_readback_ref": "artifact://tpcc-durability",
        }
        driver = build_tpcc_replay_driver(registered, fixture.dependencies)
        defaults = registered["default_costs"]
        candidate = {"subset_id": "s1", "optimized_params": defaults}
        context = {"baseline_params": defaults, "replay_ordinal": 1}
        driver(candidate, context)
        recovery = driver.restore(candidate, context)
        self.assertTrue(recovery["succeeded"])

    def test_measurement_failure_keeps_lease_until_explicit_recovery(self):
        fixture = Fixture(source="performance_schema.questions")
        driver = build_job_replay_driver(registration(), fixture.dependencies)
        defaults = registration()["default_costs"]
        candidate = {"subset_id": "s1", "optimized_params": defaults}
        context = {"baseline_params": defaults, "replay_ordinal": 1}
        with self.assertRaisesRegex(FormalSGDPointError, "Questions"):
            driver(candidate, context)
        self.assertNotIn("lease_exit", fixture.events)

        recovery = driver.restore(candidate, context)
        self.assertTrue(recovery["succeeded"])
        self.assertEqual(fixture.events[-1], "lease_exit")

    def test_checkpoint_chain_resume_and_exactly_once_accounting(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = Fixture()
            journal = FormalPointJournal(root, ("formal-sgd-resume",))
            driver = build_job_replay_driver(registration(), fixture.dependencies, journal)
            defaults = registration()["default_costs"]
            candidate = {"subset_id": "s1", "optimized_params": defaults}
            context = {
                "baseline_params": defaults,
                "outer_iter": 2,
                "replay_ordinal": 4,
                "stage": "initial_top_k",
            }

            measurement = driver(candidate, context)
            token = measurement["resume_token"]
            self.assertIn("--resume-token " + token, measurement["resume_command"])
            recovery = driver.restore(candidate, context)
            self.assertEqual(recovery["accounting_token"], token)
            current = FormalPointJournal.load_verified(root, token)
            self.assertIsNone(current["pending"])
            self.assertTrue(current["measurement_complete"])
            self.assertTrue(current["recovery_verified"])
            self.assertTrue(current["lease_released"])
            self.assertTrue(FormalPointJournal.claim_accounting(root, token))
            self.assertFalse(FormalPointJournal.claim_accounting(root, token))

    def test_failure_injection_requires_new_outer_attempt_without_duplicate_count(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            failing = Fixture(fail_stage="plan")
            first_journal = FormalPointJournal(root, ("formal-sgd-resume",))
            first = build_job_replay_driver(registration(), failing.dependencies, first_journal)
            defaults = registration()["default_costs"]
            candidate = {"subset_id": "s2", "optimized_params": defaults}
            context = {
                "baseline_params": defaults,
                "outer_iter": 3,
                "replay_ordinal": 6,
                "stage": "expanded_after_online_t",
            }
            with self.assertRaisesRegex(RuntimeError, "injected plan failure"):
                first(candidate, context)
            token = first._journal_token
            self.assertIsNotNone(token)
            checkpoint = FormalPointJournal.load_verified(root, token)
            self.assertEqual(checkpoint["pending"]["outer_iter"], 3)
            self.assertEqual(checkpoint["pending"]["subset_id"], "s2")
            self.assertEqual(checkpoint["pending"]["replay_ordinal"], 6)
            self.assertFalse(checkpoint["measurement_complete"])

            resumed_fixture = Fixture()
            resumed_journal = FormalPointJournal(root, ("formal-sgd-resume",))
            resumed = build_job_replay_driver(
                registration(), resumed_fixture.dependencies, resumed_journal
            )
            decision = resumed.recover_resume(token)
            self.assertTrue(decision["restart_outer_attempt_required"])
            self.assertFalse(decision["replay_required"])
            with self.assertRaisesRegex(FormalSGDResumeError, "cannot be counted"):
                FormalPointJournal.claim_accounting(root, token)

    def test_completed_measurement_is_reused_after_recovery_and_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = Fixture()
            first_journal = FormalPointJournal(root, ("formal-sgd-resume",))
            first = build_job_replay_driver(registration(), fixture.dependencies, first_journal)
            defaults = registration()["default_costs"]
            candidate = {"subset_id": "s3", "optimized_params": defaults}
            context = {"baseline_params": defaults, "outer_iter": 1, "replay_ordinal": 1}
            measurement = first(candidate, context)
            token = measurement["resume_token"]

            resumed_fixture = Fixture()
            resumed = build_job_replay_driver(
                registration(), resumed_fixture.dependencies,
                FormalPointJournal(root, ("formal-sgd-resume",)),
            )
            decision = resumed.recover_resume(token)
            self.assertEqual(decision["status"], "measurement_recovered")
            self.assertEqual(decision["measurement"]["db_qps"], 110.0)
            self.assertFalse(decision["replay_required"])
            self.assertTrue(FormalPointJournal.claim_accounting(root, token))

            history = sorted((root / token).glob("[0-9][0-9][0-9][0-9]_*.json"))[0]
            history.write_bytes(history.read_bytes() + b" ")
            with self.assertRaisesRegex(FormalSGDResumeError, "checksum mismatch"):
                FormalPointJournal.load_verified(root, token)


class Host02CampaignLeaseTest(unittest.TestCase):
    def test_job_and_xp_do_not_acquire_cross_scenario_lease(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "host02.lock"
            self.assertIsNone(
                try_acquire_host02_campaign_lease("job_memory", lock_path)
            )
            self.assertIsNone(
                try_acquire_host02_campaign_lease("hybench_xp_io", lock_path)
            )
            self.assertFalse(lock_path.exists())


if __name__ == "__main__":
    unittest.main()
