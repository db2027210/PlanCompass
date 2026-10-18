#!/usr/bin/env python3
"""Fail-closed verifier for the MySQL experience-table SGD v2 draft."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = (
    REPO_ROOT
    / "config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft/readiness_manifest.json"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PLACEHOLDER_RE = re.compile(
    r"(?:^|[_\-\s])(todo|tbd|placeholder|to[_-]?freeze|unknown|unset|pending|"
    r"not[_-]?ready|draft|example|changeme|missing|null|none)(?:$|[_\-\s])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Check:
    requirement: str
    item: str
    passed: bool
    reason: str


class ReadinessVerifier:
    def __init__(self, document: Mapping[str, Any], repo_root: Path = REPO_ROOT) -> None:
        self.document = document
        self.repo_root = repo_root
        self.checks: list[Check] = []

    def add(self, requirement: str, item: str, passed: bool, reason: str) -> bool:
        self.checks.append(Check(requirement, item, bool(passed), reason))
        return bool(passed)

    @staticmethod
    def concrete(value: Any) -> bool:
        return (
            isinstance(value, str)
            and bool(value.strip())
            and PLACEHOLDER_RE.search(value.strip()) is None
        )

    def resolve(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.repo_root / path

    @staticmethod
    def sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def artifact(
        self,
        requirement: str,
        item: str,
        pointer: Any,
        *,
        json_required: bool = False,
    ) -> tuple[bool, Any]:
        if not isinstance(pointer, Mapping):
            self.add(requirement, item, False, "evidence pointer is missing or not an object")
            return False, None
        path_raw = pointer.get("path")
        expected = pointer.get("sha256")
        if not self.concrete(path_raw):
            self.add(requirement, item, False, f"evidence path is absent or placeholder: {path_raw!r}")
            return False, None
        if not isinstance(expected, str) or SHA256_RE.fullmatch(expected) is None:
            self.add(requirement, item, False, f"evidence SHA-256 is absent, placeholder, or malformed: {expected!r}")
            return False, None
        path = self.resolve(path_raw)
        if not path.is_file() or path.is_symlink():
            self.add(requirement, item, False, f"evidence file is absent or is a symlink: {path}")
            return False, None
        actual = self.sha256(path)
        if actual != expected:
            self.add(requirement, item, False, f"evidence SHA-256 mismatch: expected={expected} actual={actual}")
            return False, None
        payload: Any = None
        if json_required:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                self.add(requirement, item, False, f"evidence is not valid JSON: {exc}")
                return False, None
            if not isinstance(payload, Mapping):
                self.add(requirement, item, False, "JSON evidence root is not an object")
                return False, None
        self.add(requirement, item, True, f"verified {path} sha256={actual}")
        return True, payload

    @staticmethod
    def normalized_envelope(value: Any, fields: list[str]) -> tuple[bool, str]:
        if not isinstance(value, Mapping):
            return False, "normalized envelope is missing"
        missing = [field for field in fields if field not in value]
        extra = sorted(set(value) - set(fields))
        if missing or extra:
            return False, f"envelope fields differ: missing={missing} extra={extra}"
        for field in fields:
            raw = value[field]
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                return False, f"envelope field {field} is not numeric: {raw!r}"
            numeric = float(raw)
            if not math.isfinite(numeric) or numeric <= 0:
                return False, f"envelope field {field} is not finite and positive: {raw!r}"
        return True, "all required envelope fields are finite and positive"

    def verify_sources_and_policy(self) -> None:
        self.add(
            "SCHEMA",
            "method",
            self.document.get("method") == "experience_table_all_subset_sgd",
            f"method={self.document.get('method')!r}",
        )
        excluded = self.document.get("excluded_hosts")
        self.add(
            "SCHEMA",
            "host09_excluded",
            isinstance(excluded, list)
            and "09" in excluded
            and "192.0.2.9" in excluded,
            f"excluded_hosts={excluded!r}",
        )
        policy = self.document.get("policy")
        policy = policy if isinstance(policy, Mapping) else {}
        expected_checks = list(range(180, 601, 30))
        rolling_ok = (
            policy.get("stability_check_seconds") == expected_checks
            and policy.get("latest_window_seconds") == 180
            and policy.get("latest_window_count") == 6
            and policy.get("window_seconds") == 30
        )
        self.add("R05", "rolling_stability_policy", rolling_ok, "requires 180,210,...,600 and latest 6x30s only")
        limits = policy.get("pg_interference_limits")
        limits_ok = isinstance(limits, Mapping) and limits == {
            "throughput_drop_max_ratio": 0.05,
            "p95_increase_max_ratio": 0.10,
            "p99_increase_max_ratio": 0.10,
            "target_resource_shift_max_percentage_points": 5.0,
        }
        self.add("R05", "interference_threshold_policy", limits_ok, f"limits={limits!r}")
        self.add(
            "R02",
            "cross_engine_envelope_equality_required",
            policy.get("cross_engine_envelope_equality_required") is True,
            "formal PostgreSQL and MySQL scenarios must use the same normalized resource envelope",
        )
        sources = self.document.get("source_documents")
        sources = sources if isinstance(sources, Mapping) else {}
        for name in (
            "draft_schedule",
            "sibling_v1",
            "measurement_and_logging_contract",
            "paired_resource_envelopes",
        ):
            self.artifact("SCHEMA", f"source_documents.{name}", sources.get(name))

    def verify_scenario(self, name: str, scenario: Any, fields: list[str], excluded: set[str]) -> None:
        if not isinstance(scenario, Mapping):
            self.add("SCHEMA", f"scenario.{name}", False, "scenario object is missing")
            return
        environment = scenario.get("environment")
        environment = environment if isinstance(environment, Mapping) else {}
        env_ok = (
            environment.get("allocated") is True
            and self.concrete(environment.get("host_id"))
            and self.concrete(environment.get("host_address"))
            and self.concrete(environment.get("device"))
            and self.concrete(environment.get("mount"))
            and environment.get("host_id") not in excluded
            and environment.get("host_address") not in excluded
        )
        requirement = "R04" if name == "tpcc_cpu" else "R01"
        self.add(
            requirement,
            f"{name}.environment_allocation",
            env_ok,
            (
                f"allocated={environment.get('allocated')!r} host={environment.get('host_id')!r}/"
                f"{environment.get('host_address')!r} device={environment.get('device')!r} "
                f"mount={environment.get('mount')!r}"
            ),
        )
        self.artifact(requirement, f"{name}.deployment_evidence", environment.get("deployment_evidence"))

        template = scenario.get("template")
        template = template if isinstance(template, Mapping) else {}
        self.add("R03", f"{name}.template_ready", template.get("ready") is True, f"ready={template.get('ready')!r}")
        self.artifact("R03", f"{name}.template_manifest", template.get("manifest"), json_required=True)

        mysql = scenario.get("mysql_envelope")
        mysql = mysql if isinstance(mysql, Mapping) else {}
        mysql_id_ok = self.concrete(mysql.get("freeze_id"))
        self.add("R01", f"{name}.mysql_freeze_id", mysql_id_ok, f"freeze_id={mysql.get('freeze_id')!r}")
        mysql_shape, mysql_reason = self.normalized_envelope(mysql.get("normalized"), fields)
        self.add("R01", f"{name}.mysql_normalized_envelope", mysql_shape, mysql_reason)
        mysql_pointer_ok, mysql_payload = self.artifact(
            "R01", f"{name}.mysql_readback", mysql.get("readback"), json_required=True
        )
        mysql_payload_ok = mysql_pointer_ok and mysql_payload.get("normalized_envelope") == mysql.get("normalized")
        self.add("R01", f"{name}.mysql_readback_matches_manifest", mysql_payload_ok, "readback normalized_envelope must exactly match manifest")

        postgres = scenario.get("postgresql_envelope")
        postgres = postgres if isinstance(postgres, Mapping) else {}
        postgres_id_ok = self.concrete(postgres.get("freeze_id"))
        self.add("R02", f"{name}.postgresql_freeze_id", postgres_id_ok, f"freeze_id={postgres.get('freeze_id')!r}")
        postgres_shape, postgres_reason = self.normalized_envelope(postgres.get("normalized"), fields)
        self.add("R02", f"{name}.postgresql_normalized_envelope", postgres_shape, postgres_reason)
        postgres_pointer_ok, postgres_payload = self.artifact(
            "R02", f"{name}.postgresql_readback", postgres.get("readback"), json_required=True
        )
        postgres_payload_ok = (
            postgres_pointer_ok
            and postgres_payload.get("normalized_envelope") == postgres.get("normalized")
        )
        self.add(
            "R02",
            f"{name}.postgresql_readback_matches_manifest",
            postgres_payload_ok,
            "readback normalized_envelope must exactly match manifest",
        )
        paired_equal = (
            mysql_shape
            and postgres_shape
            and mysql.get("normalized") == postgres.get("normalized")
        )
        self.add(
            "R02",
            f"{name}.cross_engine_envelope_equal",
            paired_equal,
            "MySQL and PostgreSQL normalized envelopes must match field-for-field",
        )

        static = scenario.get("static_isolation")
        static = static if isinstance(static, Mapping) else {}
        static_ok, static_payload = self.artifact(
            "R05", f"{name}.static_isolation_evidence", static.get("evidence"), json_required=True
        )
        static_semantics = (
            static.get("passed") is True
            and static_ok
            and static_payload.get("scenario") == name
            and static_payload.get("passed") is True
            and all(
                static_payload.get(field) is True
                for field in ("cpu_nonoverlap", "numa_nonoverlap", "memory_swap_nonoverlap", "physical_disk_nonoverlap", "host_admission_passed")
            )
        )
        self.add("R05", f"{name}.static_isolation_semantics", static_semantics, "all non-overlap and host-admission fields must be true")

        dynamic = scenario.get("dynamic_interference")
        dynamic = dynamic if isinstance(dynamic, Mapping) else {}
        dynamic_ok, dynamic_payload = self.artifact(
            "R05", f"{name}.dynamic_interference_evidence", dynamic.get("evidence"), json_required=True
        )
        limits = self.document["policy"]["pg_interference_limits"]
        dynamic_semantics = (
            dynamic.get("passed") is True
            and dynamic_ok
            and dynamic_payload.get("scenario") == name
            and dynamic_payload.get("passed") is True
            and dynamic_payload.get("latest_window_seconds") == 180
            and dynamic_payload.get("latest_window_count") == 6
            and dynamic_payload.get("max_continuous_seconds") == 600
            and dynamic_payload.get("sample_coverage", 0) >= 0.90
            and dynamic_payload.get("limits") == limits
        )
        self.add("R05", f"{name}.dynamic_interference_semantics", dynamic_semantics, "latest-180s 5%/10%/5pp evidence with >=90% coverage is required")

        r0 = scenario.get("r0_calibration")
        r0 = r0 if isinstance(r0, Mapping) else {}
        r0_ok, r0_payload = self.artifact(
            "R05", f"{name}.r0_calibration_evidence", r0.get("evidence"), json_required=True
        )
        r0_payload = r0_payload if isinstance(r0_payload, Mapping) else {}
        target = str(scenario.get("bottleneck") or "")
        resources = {"cpu", "memory", "io"}
        utilizations = r0_payload.get("resource_utilization")
        utilizations = utilizations if isinstance(utilizations, Mapping) else {}
        target_range = self.document["policy"].get("r0_target_utilization_range")
        non_target_range = self.document["policy"].get("r0_non_target_utilization_range")
        r0_semantics = (
            r0.get("passed") is True
            and r0_ok
            and r0_payload.get("scenario") == name
            and r0_payload.get("passed") is True
            and r0_payload.get("latest_window_seconds") == 180
            and r0_payload.get("latest_window_count") == 6
            and r0_payload.get("sample_coverage", 0) >= 0.90
            and target in resources
            and target_range == [0.85, 0.95]
            and non_target_range == [0.30, 0.50]
            and set(utilizations) == resources
            and 0.85 <= utilizations.get(target, -1) <= 0.95
            and all(0.30 <= utilizations.get(resource, -1) <= 0.50 for resource in resources - {target})
        )
        self.add(
            "R05", f"{name}.r0_calibration_semantics", r0_semantics,
            "R0 latest-180s target must be 85-95% and both non-target resources 30-50% with >=90% coverage",
        )

        table = scenario.get("experience_table")
        table = table if isinstance(table, Mapping) else {}
        version_ok = self.concrete(table.get("version"))
        self.add("R06", f"{name}.experience_table_version", version_ok, f"version={table.get('version')!r}")
        table_ok, table_payload = self.artifact("R06", f"{name}.experience_table_artifact", table.get("artifact"), json_required=True)
        table_payload = table_payload if isinstance(table_payload, Mapping) else {}
        pointer = table.get("artifact") if isinstance(table.get("artifact"), Mapping) else {}
        artifact_path = self.resolve(pointer.get("path", "")) if pointer.get("path") else None
        sidecar_ok = False
        if table_ok and artifact_path is not None:
            sidecar = artifact_path.with_name(artifact_path.name + ".sha256")
            expected_line = f"{pointer.get('sha256')}  {artifact_path.name}\n"
            try:
                sidecar_ok = (
                    sidecar.is_file() and not sidecar.is_symlink()
                    and sidecar.read_text(encoding="ascii") == expected_line
                )
            except (OSError, UnicodeError):
                sidecar_ok = False
        self.add(
            "R06", f"{name}.experience_table_sidecar", sidecar_ok,
            "canonical artifact requires an exact adjacent .sha256 sidecar",
        )
        parameter_space = table_payload.get("parameter_space") if table_ok else {}
        parameter_space = parameter_space if isinstance(parameter_space, Mapping) else {}
        active_params = parameter_space.get("active_params")
        ranges = parameter_space.get("ranges")
        active_ok = (
            isinstance(active_params, list) and bool(active_params)
            and len(active_params) == len(set(active_params))
            and all(isinstance(param, str) and bool(param) for param in active_params)
        )
        active_set = set(active_params) if active_ok else set()
        ranges_ok = isinstance(ranges, Mapping) and set(ranges) == active_set
        if ranges_ok:
            for param in active_params:
                bounds = ranges[param]
                ranges_ok = (
                    isinstance(bounds, Mapping) and set(bounds) == {"lower", "upper"}
                    and isinstance(bounds["lower"], (int, float)) and not isinstance(bounds["lower"], bool)
                    and isinstance(bounds["upper"], (int, float)) and not isinstance(bounds["upper"], bool)
                    and math.isfinite(float(bounds["lower"])) and math.isfinite(float(bounds["upper"]))
                    and 0 < float(bounds["lower"]) < float(bounds["upper"])
                )
                if not ranges_ok:
                    break
        matrix = table_payload.get("table") if table_ok else {}
        effects = table_payload.get("effect_evidence") if table_ok else {}
        resources = {"cpu", "memory", "io"}
        matrix_ok = active_ok and isinstance(matrix, Mapping) and set(matrix) == active_set
        effects_ok = active_ok and isinstance(effects, Mapping) and set(effects) == active_set
        if matrix_ok and effects_ok:
            for param in active_params:
                row = matrix[param]
                detail = effects[param]
                if not isinstance(row, Mapping) or set(row) != resources or not isinstance(detail, Mapping) or set(detail) != resources:
                    matrix_ok = effects_ok = False
                    break
                for resource in resources:
                    value = row[resource]
                    item = detail[resource]
                    if (
                        isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(float(value)) or not isinstance(item, Mapping)
                        or item.get("direction") not in {"increase", "decrease", "neutral"}
                        or item.get("signed_effect_full_range") != value
                        or not isinstance(item.get("confidence"), Mapping)
                        or item["confidence"].get("decision") not in {"SUPPORTED", "NO_DETECTABLE_EFFECT"}
                    ):
                        matrix_ok = effects_ok = False
                        break
                if not matrix_ok:
                    break
        provenance = table_payload.get("provenance") if table_ok else {}
        provenance = provenance if isinstance(provenance, Mapping) else {}
        source = provenance.get("source")
        source_ok = (
            provenance.get("cross_engine_prior_used") is False
            and source in {"real_measurement", "trained_estimator_experience"}
            and table_payload.get("calibration", {}).get("source") == source
        )
        if source == "real_measurement":
            point_artifacts = provenance.get("point_artifacts")
            point_checks: list[bool] = []
            if isinstance(point_artifacts, list):
                for index, point_pointer in enumerate(point_artifacts):
                    point_ok, _ = self.artifact(
                        "R06", f"{name}.experience_table_point_provenance[{index}]",
                        point_pointer, json_required=True,
                    )
                    point_checks.append(point_ok)
            source_ok = source_ok and provenance.get("real_measurement") is True and (
                provenance.get("evidence_class") == "mysql_complete_formal_two_sided_oat"
                and isinstance(point_artifacts, list) and bool(point_artifacts)
                and bool(point_checks) and all(point_checks)
            )
        elif source == "trained_estimator_experience":
            estimator = table_payload.get("estimator")
            registry = table_payload.get("sql_registry")
            models = estimator.get("model_artifact_sha256") if isinstance(estimator, Mapping) else None
            endpoint_ok, _ = self.artifact(
                "R06", f"{name}.experience_table_endpoint_provenance",
                provenance.get("endpoint_evidence"), json_required=True,
            )
            source_ok = source_ok and provenance.get("real_measurement") is False and (
                provenance.get("evidence_class") == "mysql_trained_estimator_complete_endpoint_features"
                and isinstance(registry, Mapping) and SHA256_RE.fullmatch(str(registry.get("sha256", ""))) is not None
                and isinstance(models, Mapping) and set(models) == resources
                and all(SHA256_RE.fullmatch(str(models.get(resource, ""))) is not None for resource in resources)
                and endpoint_ok
                and all(
                    effects[param][resource]["confidence"].get("source") == "trained_estimator_experience"
                    and effects[param][resource]["confidence"].get("real_measurement") is False
                    and effects[param][resource]["confidence"].get("query_coverage_ratio") == 1.0
                    and (
                        effects[param][resource]["confidence"].get("decision") == "SUPPORTED"
                        or (
                            effects[param][resource]["confidence"].get("decision") == "NO_DETECTABLE_EFFECT"
                            and effects[param][resource].get("evidence_status") == "NO_DETECTABLE_EFFECT"
                            and effects[param][resource].get("direction") == "neutral"
                            and matrix[param][resource] == 0
                            and isinstance(effects[param][resource].get("observed_delta"), (int, float))
                            and isinstance(effects[param][resource].get("tolerance"), (int, float))
                            and abs(float(effects[param][resource]["observed_delta"]))
                                <= float(effects[param][resource]["tolerance"])
                        )
                    )
                    for param in active_params for resource in resources
                ) if active_ok and effects_ok else False
            )
        schema_ok = (
            table_payload.get("schema_version") == "mysql_experience_table_t_v1"
            and table_payload.get("database") == "mysql"
            and table_payload.get("resource_columns") == ["cpu", "memory", "io"]
            and SHA256_RE.fullmatch(str(table_payload.get("workload_registry_sha256", ""))) is not None
            and isinstance(table_payload.get("estimator"), Mapping)
            and SHA256_RE.fullmatch(str(table_payload.get("estimator", {}).get("sha256", ""))) is not None
            and SHA256_RE.fullmatch(str(parameter_space.get("sha256", ""))) is not None
        )
        table_semantics = (
            table_ok
            and table_payload.get("scenario") == name
            and table_payload.get("benchmark") == scenario.get("benchmark")
            and table_payload.get("version") == table.get("version")
            and schema_ok and active_ok and ranges_ok and matrix_ok and effects_ok
            and source_ok and sidecar_ok
        )
        self.add(
            "R06", f"{name}.experience_table_semantics", table_semantics,
            "T artifact must satisfy schema, full active coverage, ranges, sidecar and source-specific provenance",
        )

        lease = scenario.get("lane_lease")
        lease = lease if isinstance(lease, Mapping) else {}
        lease_id_ok = self.concrete(lease.get("lease_id")) and lease.get("exclusive") is True
        self.add("R07", f"{name}.exclusive_lane_lease", lease_id_ok, f"lease_id={lease.get('lease_id')!r} exclusive={lease.get('exclusive')!r}")
        lease_ok, lease_payload = self.artifact("R07", f"{name}.lane_lease_evidence", lease.get("evidence"), json_required=True)
        lease_semantics = (
            lease_ok
            and lease_payload.get("scenario") == name
            and lease_payload.get("lease_id") == lease.get("lease_id")
            and lease_payload.get("exclusive") is True
            and lease_payload.get("active") is True
            and lease_payload.get("method") == "experience_table_all_subset_sgd"
        )
        self.add("R07", f"{name}.lane_lease_semantics", lease_semantics, "active exclusive lease must bind scenario and method")

    def verify_globals(self) -> None:
        globals_raw = self.document.get("global_requirements")
        globals_raw = globals_raw if isinstance(globals_raw, Mapping) else {}
        method = globals_raw.get("method_bundle")
        method = method if isinstance(method, Mapping) else {}
        sources = method.get("registered_sources")
        sources_ok = isinstance(sources, list) and bool(sources)
        if sources_ok:
            for index, pointer in enumerate(sources):
                ok, _ = self.artifact("R07", f"method_bundle.registered_source[{index}]", pointer)
                sources_ok = sources_ok and ok
        else:
            self.add("R07", "method_bundle.registered_sources", False, "registered source list is missing")
        method_manifest_ok, method_manifest = self.artifact("R07", "method_bundle.method_manifest", method.get("method_manifest"), json_required=True)
        config_ok, _ = self.artifact("R07", "method_bundle.formal_config", method.get("formal_config"), json_required=True)
        method_semantics = (
            method.get("ready") is True
            and sources_ok
            and method_manifest_ok
            and config_ok
            and method_manifest.get("draft_id") == self.document.get("draft_id")
            and method_manifest.get("method") == "experience_table_all_subset_sgd"
        )
        self.add("R07", "method_bundle.semantics", method_semantics, "immutable manifest/config and registered source checksums are required")

        session = globals_raw.get("session_adapter")
        session = session if isinstance(session, Mapping) else {}
        session_ok, session_payload = self.artifact("R08", "session_adapter.evidence", session.get("evidence"), json_required=True)
        capabilities = session.get("capabilities")
        required_capabilities = {
            "template_restore", "formal_cost_protocol", "digest_db_qps",
            "rolling_stability_controller", "pairwise_outer", "staged_top5_top10_replay",
        }
        session_semantics = (
            session.get("ready") is True
            and session_ok
            and isinstance(capabilities, Mapping)
            and set(capabilities) == required_capabilities
            and all(value is True for value in capabilities.values())
            and session_payload.get("passed") is True
            and session_payload.get("capabilities") == capabilities
        )
        self.add("R08", "session_adapter.semantics", session_semantics, "all six formal session capabilities require tested evidence")

        logging = globals_raw.get("logging_contract")
        logging = logging if isinstance(logging, Mapping) else {}
        logging_ok, logging_payload = self.artifact("R09", "logging_contract.evidence", logging.get("evidence"), json_required=True)
        logging_fields = (
            "section23_complete", "section26_complete", "trace_sha256_complete",
            "recovery_journal_complete", "failure_injection_tested", "resume_tested",
        )
        logging_semantics = (
            logging.get("ready") is True
            and logging_ok
            and all(logging.get(field) is True for field in logging_fields)
            and logging_payload.get("passed") is True
            and all(logging_payload.get(field) is True for field in logging_fields)
        )
        self.add("R09", "logging_contract.semantics", logging_semantics, "sections 23/26, trace SHA and recovery tests must all pass")

        mutex = globals_raw.get("host02_job_xp_mutex")
        mutex = mutex if isinstance(mutex, Mapping) else {}
        mutex_ok, mutex_payload = self.artifact("R10", "host02_mutex.evidence", mutex.get("evidence"), json_required=True)
        mutex_payload = mutex_payload if isinstance(mutex_payload, Mapping) else {}
        lock_path = Path(str(mutex_payload.get("lock_path") or ""))
        journal_path = Path(str(mutex_payload.get("journal_path") or ""))
        control_paths_ok = (
            lock_path.is_absolute()
            and journal_path.is_absolute()
            and lock_path != journal_path
            and lock_path != Path("/tmp")
            and Path("/tmp") not in lock_path.parents
            and journal_path != Path("/tmp")
            and Path("/tmp") not in journal_path.parents
        )
        self.add(
            "R10", "host02_mutex.non_tmp_control_paths", control_paths_ok,
            f"lock={lock_path} journal={journal_path}",
        )
        launch_paths = mutex_payload.get("launch_paths")
        launch_paths_ok = (
            isinstance(launch_paths, Mapping)
            and set(launch_paths) == {"job_memory", "hybench_xp_io"}
            and len(set(launch_paths.values())) == 1
            and next(iter(launch_paths.values()), None)
                == "formal_mysql_sgd_campaign.run_production_campaign"
        )
        self.add(
            "R10", "host02_mutex.both_launch_paths_wired", launch_paths_ok,
            f"launch_paths={launch_paths!r}",
        )
        source_names = {
            "host02_job_xp_mutex.py", "formal_mysql_sgd_campaign.py",
            "shell/test_host02_job_xp_mutex.py",
            "shell/test_mysql_formal_outer_campaign.py",
            "shell/verify_mysql_sgd_v2_readiness.py",
            "shell/test_verify_mysql_sgd_v2_readiness.py",
        }
        source_pointers = mutex_payload.get("sources")
        sources_ok = isinstance(source_pointers, Mapping) and set(source_pointers) == source_names
        if sources_ok:
            for source_name in sorted(source_names):
                valid, _ = self.artifact(
                    "R10", f"host02_mutex.source.{source_name}",
                    source_pointers.get(source_name),
                )
                sources_ok = sources_ok and valid
        else:
            self.add(
                "R10", "host02_mutex.sources", False,
                f"required source set={sorted(source_names)}",
            )
        tests = mutex_payload.get("tests")
        required_tests = {
            "preflight_zero_pressure_rejection",
            "atomic_handoff_journal",
            "second_holder_rejected",
            "crashed_owner_kernel_release",
            "stale_state_audited",
            "both_launch_paths_same_lock",
            "full_scope_through_recovery_checksum",
            "journal_checksum_chain",
            "aborted_scope_zero_pressure_before_unlock",
        }
        tests_ok = (
            isinstance(tests, Mapping)
            and set(tests) == required_tests
            and all(value is True for value in tests.values())
        )
        self.add(
            "R10", "host02_mutex.enforcement_tests", tests_ok,
            f"required tests={sorted(required_tests)}",
        )
        mutex_semantics = (
            mutex.get("ready") is True
            and mutex.get("job_xp_pressure_mutually_exclusive") is True
            and mutex.get("zero_pressure_handoff_verified") is True
            and mutex_ok
            and mutex_payload.get("passed") is True
            and mutex_payload.get("job_xp_pressure_mutually_exclusive") is True
            and mutex_payload.get("zero_pressure_handoff_verified") is True
            and mutex_payload.get("implementation") == "posix_flock_exclusive_nonblocking"
            and mutex_payload.get("pressure_scope")
                == "before_R0_through_recovery_and_checksum"
            and mutex_payload.get("preflight_zero_pressure_required") is True
            and mutex_payload.get("postrun_zero_pressure_required") is True
            and mutex_payload.get("atomic_handoff_journal") is True
            and mutex_payload.get("journal_sha256_chain") is True
            and control_paths_ok and launch_paths_ok and sources_ok and tests_ok
        )
        self.add(
            "R10", "host02_mutex.semantics", mutex_semantics,
            "shared non-/tmp flock, two launch paths, zero-pressure handoff, full hold scope, journal chain and crash/concurrency tests must pass",
        )

        publication = globals_raw.get("bundle_publication")
        publication = publication if isinstance(publication, Mapping) else {}
        checksums_ok, _ = self.artifact("R11", "bundle_publication.checksums", publication.get("checksums"))
        audit_ok, audit = self.artifact("R11", "bundle_publication.independent_audit", publication.get("independent_audit"), json_required=True)
        ready_ok, ready = self.artifact("R11", "bundle_publication.ready_file", publication.get("ready_file"), json_required=True)
        publication_semantics = (
            publication.get("ready") is True
            and publication.get("ready_file_authorizes_launch") is True
            and checksums_ok and audit_ok and ready_ok
            and audit.get("passed") is True
            and ready.get("status") == "READY"
            and ready.get("launch_authorized") is True
            and ready.get("draft_id") == self.document.get("draft_id")
        )
        self.add("R11", "bundle_publication.semantics", publication_semantics, "checksums, passing independent audit and explicit launch authorization are required")

    def run(self) -> list[Check]:
        self.verify_sources_and_policy()
        scenarios = self.document.get("scenarios")
        scenarios = scenarios if isinstance(scenarios, Mapping) else {}
        policy = self.document.get("policy")
        policy = policy if isinstance(policy, Mapping) else {}
        expected = policy.get("all_scenarios")
        expected = expected if isinstance(expected, list) else []
        self.add("SCHEMA", "five_scenarios", len(expected) == 5 and set(scenarios) == set(expected), f"expected={expected!r} actual={sorted(scenarios)!r}")
        fields = policy.get("required_envelope_fields")
        fields = [str(item) for item in fields] if isinstance(fields, list) else []
        self.add("SCHEMA", "required_envelope_fields", len(fields) == 8 and len(set(fields)) == 8, f"fields={fields!r}")
        excluded = set(str(item) for item in self.document.get("excluded_hosts", []))
        for name in expected:
            self.verify_scenario(name, scenarios.get(name), fields, excluded)
        self.verify_globals()
        requirements = self.document.get("requirement_index")
        requirements = requirements if isinstance(requirements, Mapping) else {}
        self.add("SCHEMA", "requirement_index_R01_R11", set(requirements) == {f"R{index:02d}" for index in range(1, 12)}, f"keys={sorted(requirements)!r}")
        substantive_ok = all(check.passed for check in self.checks)
        declared = self.document.get("status") == "READY" and self.document.get("launch_authorized") is True
        self.add("AUTH", "manifest_declares_ready", declared, f"status={self.document.get('status')!r} launch_authorized={self.document.get('launch_authorized')!r}")
        if substantive_ok and not declared:
            return self.checks
        return self.checks


def verify_document(document: Mapping[str, Any], repo_root: Path = REPO_ROOT) -> list[Check]:
    return ReadinessVerifier(document, repo_root).run()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--json", action="store_true", help="emit one JSON report")
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.manifest.read_text(encoding="utf-8"))
        if not isinstance(document, Mapping):
            raise ValueError("manifest root must be an object")
        checks = verify_document(document)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        if args.json:
            print(json.dumps({"status": "INVALID", "ready": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"INVALID: {type(exc).__name__}: {exc}")
        return 2
    ready = all(check.passed for check in checks)
    report = {
        "status": "READY" if ready else "NOT_READY",
        "ready": ready,
        "passed": sum(check.passed for check in checks),
        "failed": sum(not check.passed for check in checks),
        "checks": [asdict(check) for check in checks],
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"{report['status']}: passed={report['passed']} failed={report['failed']}")
        for check in checks:
            marker = "PASS" if check.passed else "FAIL"
            print(f"[{marker}] {check.requirement} {check.item}: {check.reason}")
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
