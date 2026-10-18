"""Production services backed by fresh per-point MySQL template copies."""

from __future__ import annotations

import copy
from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import signal
import subprocess
import threading
import time
import uuid
from typing import Any, Mapping

from formal_mysql_sgd_orchestration import FormalPointDependencies, FormalSGDNotReady
from mysql_cost_adapter import MYSQL_COST_KEYS, MySQLCLIClient, MySQLCostAdapter
from plan_resource_estimator import build_ratio_scale_resource_estimator
from shell.manual_mysql_sgd_r0_campaign import (
    Campaign,
    LANES,
    MYSQL,
    ROOT,
    atomic_json,
    remote_ssh_command,
    ssh_transport_options,
)


DEFAULT_COSTS = {
    "io_block_read_cost": 1.0,
    "memory_block_read_cost": 0.25,
    "row_evaluate_cost": 0.1,
    "key_compare_cost": 0.05,
    "disk_temptable_create_cost": 20.0,
    "disk_temptable_row_cost": 0.5,
    "memory_temptable_create_cost": 1.0,
    "memory_temptable_row_cost": 0.1,
}
BENCHMARK_LANES = {"job": "job", "hybench_xp": "xp", "tpcc": "tpcc", "tpch": "tpch"}
REPLAY_PORT_OFFSET = 100


def _expand_cpuset(value: str) -> set[int]:
    result: set[int] = set()
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            low, high = (int(part) for part in token.split("-", 1))
            result.update(range(low, high + 1))
        else:
            result.add(int(token))
    return result


def _frozen_host02_lane(key: str) -> Any:
    profile_path = ROOT / f"config/formal/mysql_sgd_production/profiles/{LANES[key].scenario}.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    return replace(
        LANES[key],
        cpuset=str(profile["cpuset"]),
        mems=str(profile["mems"]),
        cpu_cores=float(profile["cpu_cores"]),
        memory_bytes=int(profile["memory_bytes"]),
        pids=int(profile["pids"]),
        read_bps=int(profile["read_bps"]),
        write_bps=int(profile["write_bps"]),
        buffer_pool_bytes=int(profile["buffer_pool_bytes"]),
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _selected_observation(result: Mapping[str, Any]) -> Mapping[str, Any]:
    rolling = result.get("rolling_measurement_control")
    selected = rolling.get("selected_observation") if isinstance(rolling, Mapping) else None
    if not isinstance(selected, Mapping):
        raise RuntimeError("continuous result lacks selected observation")
    return selected


def _measurement_from_result(
    result: Mapping[str, Any], correctness: Mapping[str, Any]
) -> dict[str, Any]:
    selected = _selected_observation(result)
    throughput = selected.get("database_throughput") or selected.get("throughput_evidence")
    resources = selected.get("resource_means")
    stability = selected.get("stability")
    if not isinstance(throughput, Mapping) or not isinstance(resources, Mapping):
        raise RuntimeError("continuous result lacks database throughput or resources")
    if not isinstance(stability, Mapping) or type(stability.get("stable")) is not bool:
        raise RuntimeError("continuous result lacks a stability decision")
    qps = float(throughput["db_qps"])
    p95 = selected.get("latency_p95_ms_audit")
    p99 = selected.get("latency_p99_ms_audit")
    if p95 is None:
        p95 = (result.get("metrics") or {}).get("p95_ms", 0.0)
    if p99 is None:
        p99 = (result.get("metrics") or {}).get("p99_ms", p95)
    return {
        "qps": qps,
        "db_qps": qps,
        "db_qps_source": "database_status_counter",
        "p95_ms": float(p95),
        "p99_ms": float(p99),
        "utilization_pct": {
            name: float(resources[name]) for name in ("cpu", "memory", "io")
        },
        "stable": bool(stability["stable"]),
        "sample_coverage": float(selected.get("resource_sample_coverage", 1.0)),
        "correctness_passed": correctness.get("passed") is True,
        "error_rate": float(correctness.get("error_rate", 0.0)),
        "oom": correctness.get("oom") is True,
        "mysql_restart": correctness.get("mysql_restart") is True,
        "container_restart": correctness.get("container_restart") is True,
    }


class _PointLease(AbstractContextManager[Mapping[str, Any]]):
    def __init__(self, owner: "ProductionServices", context: Mapping[str, Any]) -> None:
        self.owner = owner
        self.context = context

    def __enter__(self) -> Mapping[str, Any]:
        if not self.owner.point_lock.acquire(blocking=False):
            raise RuntimeError("another formal point owns this service factory")
        return {
            "lease_id": uuid.uuid4().hex,
            "scenario": self.owner.lane.scenario,
            "acquired_at_utc": _utc_now(),
        }

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.owner.point_lock.release()


class LivePlannerSQLClient:
    def __init__(self, owner: "ProductionServices") -> None:
        self.owner = owner

    def execute(self, sql: str) -> str:
        campaign = self.owner.planner
        if campaign is None:
            raise RuntimeError("planner MySQL is not live")
        client = self.owner.client_for(campaign)
        return client.execute(sql)


class _StartBarrier:
    def __init__(self, campaign: Campaign, session: Any) -> None:
        self.campaign = campaign
        self.session = session

    def start(self, *, warmup_seconds: int, maximum_measurement_seconds: int) -> None:
        self.session.start(
            warmup_seconds=warmup_seconds,
            maximum_measurement_seconds=maximum_measurement_seconds,
        )
        self.campaign.wait_initial_marker()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.session, name)


class ProductionServices:
    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = copy.deepcopy(dict(config))
        benchmark = str(config.get("benchmark") or "").lower()
        lane_key = BENCHMARK_LANES.get(benchmark)
        if lane_key is None or lane_key not in LANES:
            raise FormalSGDNotReady(f"NOT_READY: unsupported production benchmark {benchmark!r}")
        self.benchmark = benchmark
        profile_ref = config.get("profile")
        if not isinstance(profile_ref, str) or not profile_ref.strip():
            raise FormalSGDNotReady("NOT_READY: frozen production profile is missing")
        profile_path = Path(profile_ref)
        if not profile_path.is_absolute():
            profile_path = ROOT / profile_path
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise FormalSGDNotReady(
                f"NOT_READY: frozen production profile cannot be loaded: {profile_path}: {exc}"
            ) from exc
        required_profile = {
            "cpuset", "mems", "cpu_cores", "memory_bytes", "pids",
            "read_bps", "write_bps", "buffer_pool_bytes",
        }
        missing_profile = sorted(required_profile - set(profile))
        if missing_profile:
            raise FormalSGDNotReady(
                "NOT_READY: frozen production profile is incomplete: "
                + ", ".join(missing_profile)
            )
        self.lane = replace(
            LANES[lane_key],
            cpuset=str(profile["cpuset"]),
            mems=str(profile["mems"]),
            cpu_cores=float(profile["cpu_cores"]),
            memory_bytes=int(profile["memory_bytes"]),
            pids=int(profile["pids"]),
            read_bps=int(profile["read_bps"]),
            write_bps=int(profile["write_bps"]),
            buffer_pool_bytes=int(profile["buffer_pool_bytes"]),
        )
        self.replay_lane = replace(
            self.lane, remote_port=self.lane.remote_port + REPLAY_PORT_OFFSET
        )
        output = Path(str((config.get("output") or {}).get("directory", "history/mysql")))
        self.output = output if output.is_absolute() else ROOT / output
        self.points = self.output / "production_points"
        self.planner: Campaign | None = None
        self.active: Campaign | None = None
        self.point_lock = threading.Lock()
        self.sql_client = LivePlannerSQLClient(self)

    def client_for(self, campaign: Campaign) -> MySQLCLIClient:
        return MySQLCLIClient({
            "mysql_binary": str(MYSQL),
            "defaults_extra_file": str(campaign.output / "private/mysql.cnf"),
            "defaults_file_only": True,
            "host": "127.0.0.1",
            "port": campaign.lane.remote_port,
            "user": "root",
            "database": campaign.lane.database,
            "query_timeout_sec": 120,
        })

    def _run_id(self, role: str) -> str:
        return f"alg_c_{self.lane.key}_{role}_{datetime.now().strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}"

    def _new_campaign(
        self, lane: Any, role: str, costs: Mapping[str, float] | None = None
    ) -> Campaign:
        run_id = self._run_id(role)
        return Campaign(
            lane,
            self.points / run_id,
            run_id,
            candidate_costs=costs,
            measurement_seconds=int(self.config.get("measurement_seconds", 600)),
            minimum_measurement_seconds=(
                int(self.config.get("measurement_seconds", 600))
                if role == "r0"
                else int(self.config.get("minimum_measurement_seconds", 180))
            ),
            minimum_client_completions=int(self.config.get("minimum_client_completions", 0)),
            require_r0_profile_gate=(role == "r0"),
        )

    @staticmethod
    def _stop_sampling(campaign: Campaign) -> None:
        campaign.database_sampler_stop.set()
        for thread in campaign.database_sampler_threads:
            thread.join(timeout=10)
        if campaign.sampler is not None and campaign.sampler.poll() is None:
            campaign.sampler.terminate()
            try:
                campaign.sampler.wait(timeout=10)
            except subprocess.TimeoutExpired:
                campaign.sampler.kill()
                campaign.sampler.wait(timeout=10)


    def resume_prepare(self, _config: Mapping[str, Any]) -> Mapping[str, Any]:
        """Create a fresh planner with compiled defaults and no R0 workload."""
        if self.planner is not None:
            raise RuntimeError("resume planner already exists")
        campaign = self._new_campaign(self.lane, "resume_planner")
        campaign.output.mkdir(parents=True, exist_ok=False)
        try:
            campaign.prepare_remote()
            campaign.start_tunnel()
            adapter = MySQLCostAdapter(self.client_for(campaign))
            snapshot = adapter.snapshot()
            overrides = sorted(
                item.name
                for item in snapshot
                if item.name in MYSQL_COST_KEYS and item.raw_value is not None
            )
            if overrides:
                raise RuntimeError(
                    "resume planner optimizer-cost overrides are not NULL: "
                    + json.dumps(overrides)
                )
            effective = adapter.effective_values()
            self.planner = campaign
            evidence = {
                "planner_ready": True,
                "fresh_copy": True,
                "workload_started": False,
                "sealed_r0_replayed": False,
                "optimizer_cost_overrides_all_null": True,
                "run_id": campaign.run_id,
                "effective_all_costs": {
                    name: float(effective[name]) for name in MYSQL_COST_KEYS
                },
                "resource_readback": str(campaign.output / "resource_readback.json"),
            }
            atomic_json(campaign.output / "formal_resume_planner_evidence.json", evidence)
            return evidence
        except BaseException:
            campaign.capture_failure_diagnostics()
            campaign.cleanup()
            self.planner = None
            raise

    def r0_measurement(self, _config: Mapping[str, Any]) -> Mapping[str, Any]:
        if self.planner is not None:
            raise RuntimeError("fresh R0 was already created")
        campaign = self._new_campaign(self.lane, "r0")
        self.planner = campaign
        try:
            campaign.execute()
            result = json.loads((campaign.output / "r0_result.json").read_text(encoding="utf-8"))
            correctness_raw = result.get("correctness") or {}
            correctness = {
                "passed": correctness_raw.get("passed") is True,
                "error_rate": 0.0,
                "oom": False,
                "mysql_restart": False,
                "container_restart": False,
            }
            measurement = _measurement_from_result(result, correctness)
            self._stop_sampling(campaign)
            atomic_json(campaign.output / "formal_r0_measurement.json", measurement)
            return measurement
        except BaseException:
            campaign.capture_failure_diagnostics()
            campaign.cleanup()
            self.planner = None
            raise

    def lease_factory(self, context: Mapping[str, Any]) -> _PointLease:
        return _PointLease(self, context)

    def template_restore(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        if self.active is not None:
            raise RuntimeError("previous replay campaign is still active")
        campaign = self._new_campaign(self.replay_lane, "replay")
        campaign.output.mkdir(parents=True, exist_ok=False)
        campaign.prepare_remote()
        campaign.start_tunnel()
        self.active = campaign
        return {
            "fresh_copy": True,
            "sealed_template": self.replay_lane.template,
            "remote_copy": campaign.remote_data,
            "run_id": campaign.run_id,
            "evidence": str(campaign.output / "remote_prepare.stdout.log"),
        }

    def resource_fixed_readback(
        self, context: Mapping[str, Any], phase: str
    ) -> Mapping[str, Any]:
        campaign = self.active
        if campaign is None:
            raise RuntimeError("resource readback without active replay")
        document = json.loads(
            (campaign.output / "resource_readback.json").read_text(encoding="utf-8")
        )
        if document.get("verified") is not True:
            raise RuntimeError("resource readback is not verified")
        return {**document, "phase": phase, "fresh_read_at_utc": _utc_now()}

    def cost_apply_fresh_readback(
        self, all_costs: Mapping[str, float], context: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        campaign = self.active
        if campaign is None:
            raise RuntimeError("cost apply without active replay")
        campaign.candidate_costs = {name: float(all_costs[name]) for name in MYSQL_COST_KEYS}
        campaign.apply_candidate_costs()
        artifact = json.loads(
            (campaign.output / "candidate_cost_readback.json").read_text(encoding="utf-8")
        )
        effective = artifact["effective_all_costs"]
        return {
            "params_requested": copy.deepcopy(campaign.candidate_costs),
            "params_readback": {name: float(effective[name]) for name in MYSQL_COST_KEYS},
            "commit": True,
            "new_connection_after_flush": True,
            "fresh_connection_generation": uuid.uuid4().hex,
            "artifact": str(campaign.output / "candidate_cost_readback.json"),
        }

    def session_factory(self, context: Mapping[str, Any]) -> Any:
        campaign = self.active
        if campaign is None:
            raise RuntimeError("session factory without active replay")
        campaign.start_sampler()
        session = campaign.make_session()
        campaign.session = session
        return _StartBarrier(campaign, session)

    def drain_flush(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        campaign = self.active
        if campaign is None:
            raise RuntimeError("drain without active replay")
        self.client_for(campaign).execute("FLUSH TABLES")
        return {
            "drained": True,
            "flushed": True,
            "workload_process_exited": getattr(campaign.session, "process", None).poll() is not None,
            "at_utc": _utc_now(),
        }

    def correctness(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        campaign = self.active
        if campaign is None:
            raise RuntimeError("correctness without active replay")
        result = context["continuous_measurement"]
        workload = result.get("correctness") or {}
        inspect = json.loads(campaign.remote(f"docker inspect {campaign.container}").stdout)[0]
        state = inspect.get("State") or {}
        unexpected = int(workload.get("unexpected_errors", 0))
        return {
            "passed": workload.get("passed") is True and unexpected == 0,
            "error_rate": 0.0 if unexpected == 0 else 1.0,
            "oom": state.get("OOMKilled") is True,
            "mysql_restart": int(inspect.get("RestartCount", 0)) != 0,
            "container_restart": int(inspect.get("RestartCount", 0)) != 0,
            "workload": copy.deepcopy(workload),
            "container_state": state,
        }

    def plan_capture(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        campaign = self.active
        if campaign is None:
            raise RuntimeError("plan capture without active replay")
        adapter = MySQLCostAdapter(self.client_for(campaign))
        estimator_config = copy.deepcopy(dict(self.config["estimator"]))
        estimator_config["baseline_params"] = copy.deepcopy(
            context["requested_all_costs"]
        )
        estimator = build_ratio_scale_resource_estimator(adapter, estimator_config)
        estimator(None, dict(context["requested_all_costs"]))
        bundle = estimator.last_metadata.get("plan_bundle")
        if not isinstance(bundle, Mapping):
            raise RuntimeError("ratio estimator did not produce a canonical plan bundle")
        artifact = campaign.output / "post_replay_canonical_plan_bundle.json"
        atomic_json(artifact, bundle)
        return {"canonical_plan_bundle": copy.deepcopy(dict(bundle)), "artifact": str(artifact)}

    def finalizer(
        self, continuous: Mapping[str, Any], context: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        campaign = self.active
        if campaign is None:
            raise RuntimeError("finalizer without active replay")
        measurement = _measurement_from_result(continuous, context["correctness"])
        evidence = {
            "estimator_model_formula": str(campaign.output / "post_replay_canonical_plan_bundle.json"),
            "per_query_operator_breakdown": str(campaign.output / "post_replay_canonical_plan_bundle.json"),
            "per_second_samples": str(campaign.output / "resource_samples.jsonl"),
            "stability_windows_and_coverage": copy.deepcopy(
                continuous["rolling_measurement_control"]
            ),
            "utility_components": {
                "resource_only_candidate_ranking": True,
                "qps_role": "outer_acceptance_only",
            },
        }
        atomic_json(campaign.output / "formal_final_measurement.json", measurement)
        return {"measurement": measurement, "evidence": evidence}

    def recovery(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        campaign = self.active
        if campaign is not None:
            campaign.cleanup()
            self.active = None
        planner = self.planner
        if planner is None:
            raise RuntimeError("recovery has no live planner")
        baseline = {name: float(context["baseline_all_costs"][name]) for name in MYSQL_COST_KEYS}
        adapter = MySQLCostAdapter(self.client_for(planner))
        adapter.apply(baseline)
        effective = adapter.effective_values()
        for name, value in baseline.items():
            if float(effective[name]).hex() != value.hex():
                raise RuntimeError(f"planner baseline recovery mismatch: {name}")
        return {
            "succeeded": True,
            "effective_all_costs": {name: float(effective[name]) for name in MYSQL_COST_KEYS},
            "effective_params": baseline,
            "fixed_settings_verified": True,
            "clean_template_verified": True,
            "candidate_template_destroyed": True,
            "evidence": str(planner.output / "resource_readback.json"),
        }

    @staticmethod
    def root_cause_review(context: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "approved": False,
            "reason": "global_fixed_top5_has_no_topk_expansion",
            "diagnostic": copy.deepcopy(dict(context)),
        }

    def pressure_probe(self, scenario: str, phase: str) -> Mapping[str, Any]:
        result = None
        last_error = None
        for attempt in range(1, 4):
            try:
                result = subprocess.run(
                    remote_ssh_command(self.lane, "docker", "ps", "--no-trunc"),
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    timeout=30, check=True,
                )
                break
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                last_error = error
                retryable = (
                    isinstance(error, subprocess.TimeoutExpired)
                    or getattr(error, "returncode", None) == 255
                )
                if not retryable or attempt == 3:
                    raise
                time.sleep(float(attempt * 2))
        if result is None:
            assert last_error is not None
            raise last_error
        text = result.stdout
        job_count = text.count("ra_mysql_sgd_job_")
        xp_count = text.count("ra_mysql_sgd_xp_")
        job_lane = _frozen_host02_lane("job")
        xp_lane = _frozen_host02_lane("xp")
        job_cpus = _expand_cpuset(job_lane.cpuset)
        xp_cpus = _expand_cpuset(xp_lane.cpuset)
        isolation_verified = (
            not job_cpus.intersection(xp_cpus)
            and job_lane.device != xp_lane.device
            and job_lane.remote_port != xp_lane.remote_port
            and job_lane.memory_bytes > 0
            and xp_lane.memory_bytes > 0
            and job_lane.pids > 0
            and xp_lane.pids > 0
        )
        return {
            "observed_at": _utc_now(),
            "probe_id": uuid.uuid4().hex,
            "scenario": scenario,
            "phase": phase,
            "job_pressure_count": job_count,
            "xp_pressure_count": xp_count,
            "isolation_verified": isolation_verified,
            "isolation_readback": {
                "job": {
                    "cpuset": job_lane.cpuset, "mems": job_lane.mems,
                    "device": job_lane.device, "memory_bytes": job_lane.memory_bytes,
                    "pids": job_lane.pids,
                },
                "xp": {
                    "cpuset": xp_lane.cpuset, "mems": xp_lane.mems,
                    "device": xp_lane.device, "memory_bytes": xp_lane.memory_bytes,
                    "pids": xp_lane.pids,
                },
            },
            "cross_engine_comparison_prohibited": True,
            "formal_scope": "mysql_only",
        }

    def cleanup(self) -> Mapping[str, Any]:
        errors: list[str] = []
        for name in ("active", "planner"):
            campaign = getattr(self, name)
            if campaign is not None:
                try:
                    campaign.cleanup()
                except BaseException as exc:
                    errors.append(f"{name}: {type(exc).__name__}: {exc}")
                setattr(self, name, None)
        zero = not errors
        if self.benchmark in {"job", "hybench_xp"}:
            probe = self.pressure_probe(self.lane.scenario, "campaign_cleanup")
            own_field = (
                "job_pressure_count" if self.benchmark == "job" else "xp_pressure_count"
            )
            zero = zero and probe[own_field] == 0 and probe["isolation_verified"] is True
        else:
            probe = {"observed_at": _utc_now(), "scenario": self.lane.scenario}
        return {
            "zero_pressure_verified": zero,
            "errors": errors,
            "probe": probe,
        }

    def registration(self) -> Mapping[str, Any]:
        evidence = self.config.get("evidence") or {}
        static = {
            "campaign_method_benchmark_scenario": {
                "method": "experience_table_sgd",
                "benchmark": self.benchmark,
                "scenario": self.lane.scenario,
            },
            "envelope_and_template_versions": evidence,
            "lane_host_container_port": {
                "host": self.lane.host,
                "hostname": self.lane.hostname,
                "planner_port": self.lane.remote_port,
                "replay_port": self.replay_lane.remote_port,
            },
            "software_checksums": evidence,
            "workload_seed_registry_weights_clients": self.config.get("workload_registry"),
            "cpuset_and_numa": {"cpuset": self.lane.cpuset, "mems": self.lane.mems},
            "range_before_after": self.config.get("cost_parameters"),
            "experience_table_direction_eta": {
                "sensitivity_table": self.config.get("sensitivity_table"),
                "optimizer": self.config.get("optimizer"),
            },
        }
        return {
            "id": (
                f"{self.lane.scenario}."
                f"{self.config.get('method_id', 'experience_table_sgd')}.production_v1"
            ),
            "default_costs": copy.deepcopy(DEFAULT_COSTS),
            "evidence": static,
        }

    def bundle(self) -> Mapping[str, Any]:
        dependencies = FormalPointDependencies(
            lease_factory=self.lease_factory,
            template_restore=self.template_restore,
            resource_fixed_readback=self.resource_fixed_readback,
            cost_apply_fresh_readback=self.cost_apply_fresh_readback,
            session_factories={self.benchmark: self.session_factory},
            drain_flush=self.drain_flush,
            correctness=self.correctness,
            plan_capture=self.plan_capture,
            finalizer=self.finalizer,
            recovery=self.recovery,
            root_cause_review=self.root_cause_review,
        )
        return {
            "registration": self.registration(),
            "dependencies": dependencies,
            "r0_measurement": self.r0_measurement,
            "resume_prepare": self.resume_prepare,
            "campaign_cleanup": self.cleanup,
            "sql_client": self.sql_client,
            "host02_pressure_probe": self.pressure_probe,
            "service_owner": self,
        }


def build(config: Mapping[str, Any]) -> Mapping[str, Any]:
    return ProductionServices(config).bundle()
