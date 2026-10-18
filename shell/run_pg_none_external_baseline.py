#!/usr/bin/env python3
"""Run one external PostgreSQL tuner on an isolated PostgreSQL scene.

The transport is intentionally shared with Algorithm B/C: two isolated Docker
PostgreSQL workers, a fresh template restore before every physical replay, the
scene's complete canonical workload, and the same seven planner-cost knobs.
GPTuner uses a knowledge-shaped coarse-to-fine SMAC search.  AQETuner uses the
public artifact's PSO warm starter and its knob-plan Neural Process with
constrained expected improvement.  The search champion is frozen and then
verified once on both workers; only that aggregate verification is intended
for the final cross-method comparison.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import random
import re
import signal
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from scipy.stats import qmc


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_plan_resource_tuner import (  # noqa: E402
    PlanFeatureProbe,
    aggregate_parallel_baseline_validation,
    initial_params,
    pg_conn_from_worker,
    restore_worker_from_hybench_template,
    run_real_validation_safe,
    validation_workers,
    worker_with_prepared_restore,
    workload_queries_with_sampling,
)
from shell.run_pg_algorithm_c_scene import (  # noqa: E402
    ACTIVE_PARAMS,
    DEFAULT_RANGES,
    apply_ssd_wave_qualification,
    physical_valid,
    worker_key,
)
from shell.pg_container_resource_guard import (  # noqa: E402
    audit_container_resource_domain,
)
from shell.pg_fixed_guc_guard import (  # noqa: E402
    audit_fixed_guc_domain,
    write_receipt,
)


STATE_VERSION = 3
BASELINE_REPETITIONS = 2
POST_BASELINE_REPETITIONS = 2
BASELINE_MAX_WAVES = 10
CHAMPION_MAX_WAVES = 10
CHAMPION_REPETITIONS = 2
CANDIDATE_INFRASTRUCTURE_MAX_ATTEMPTS = 3
AQETUNER_WARM_SAMPLES = 9
GPTUNER_COARSE_TRIALS = 10
DEFAULT_CANDIDATE_BUDGET = 35
RESTORE_WAVE_ATTEMPTS = 3
RESTORE_WAVE_RETRY_DELAY_S = 20.0
PUBLICATION_PROTOCOL = "pg_external_publication_fairness_v1"
GPTUNER_PAPER_MODEL = "gpt-4"


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def optional_finite_float(value: Any) -> Optional[float]:
    """Parse unitless GPTuner cost values without accepting NaN or infinity."""
    if value is None:
        return None
    try:
        parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


def append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def exact_signature(params: Mapping[str, Any]) -> str:
    return canonical_sha256(
        {
            name: float(format(float(params[name]), ".15g"))
            for name in ACTIVE_PARAMS
        }
    )


def trial_from_replay_label(label: str) -> int:
    """Extract the terminal trial token without matching stage names such as dual_task_eic."""
    match = re.search(r"_t(-?\d+)_w(?:1|2)$", str(label or ""))
    return int(match.group(1)) if match else -1


def git_commit(repository: Path) -> str:
    """Return an inspected vendor commit without constructing a shell command."""
    completed = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def normalized_vector(params: Mapping[str, Any]) -> List[float]:
    values: List[float] = []
    for name in ACTIVE_PARAMS:
        low, high = DEFAULT_RANGES[name]
        value = float(params[name])
        values.append((value - low) / max(high - low, 1e-12))
    return values


def denormalized_params(values: Sequence[float]) -> Dict[str, float]:
    params: Dict[str, float] = {}
    for index, name in enumerate(ACTIVE_PARAMS):
        low, high = DEFAULT_RANGES[name]
        fraction = min(1.0, max(0.0, float(values[index])))
        params[name] = low + fraction * (high - low)
    return params


def slim_validation(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "label": row.get("label"),
        "status": row.get("status"),
        "technical_failure": row.get("technical_failure") is True,
        "worker_key": row.get("worker_key"),
        "db_qps": row.get("db_qps"),
        "resources": copy.deepcopy(row.get("resources") or {}),
        "p95_ms": row.get("p95_ms"),
        "p99_ms": row.get("p99_ms"),
        "params": copy.deepcopy(row.get("params") or {}),
        "elapsed_s": row.get("elapsed_s"),
        "workload_completeness": copy.deepcopy(
            row.get("workload_completeness") or {}
        ),
        "ssd_qualification": copy.deepcopy(row.get("ssd_qualification") or {}),
        # Preserve the proof that the frozen champion was replayed on both
        # isolated workers.  The authority verifier intentionally requires
        # this nested receipt; dropping it here makes an otherwise valid
        # two-worker replay unverifiable at handoff time.
        "parallel_worker_aggregate": copy.deepcopy(
            row.get("parallel_worker_aggregate") or {}
        ),
        "physical_valid": physical_valid(row),
    }


def candidate_failure_classification(row: Mapping[str, Any]) -> str:
    """Distinguish learnable configuration failures from infrastructure faults."""
    if physical_valid(row):
        return "valid"
    process_error = str(row.get("process_error") or "").lower()
    monitor_error = str(row.get("monitor_error") or "").strip()
    resource_stats = row.get("resource_stats") or {}
    ssd_errors = list((row.get("ssd_qualification") or {}).get("errors") or [])
    infrastructure_ssd_markers = (
        "unexpected_ssd_observer_devices",
        "unexpected_ssd_utilization_source",
        "ssd_stable_sample_count_below_minimum",
        "ssd_utilization_evidence_invalid",
        "observation_windows_do_not_overlap",
    )
    has_infrastructure_ssd_error = any(
        any(marker in str(error) for marker in infrastructure_ssd_markers)
        for error in ssd_errors
    )
    benchmark_timeout = "timed out after" in process_error
    monitored_timeout = (
        benchmark_timeout
        and not monitor_error
        and resource_stats.get("resource_monitor_ok") is True
        and not has_infrastructure_ssd_error
    )
    database_rejection = (
        bool(row.get("db_errors") or [])
        and not process_error
        and not monitor_error
        and not has_infrastructure_ssd_error
    )
    if monitored_timeout or database_rejection:
        return "configuration_failure"
    return "infrastructure_failure"


def technical_failure_accounting(state: Mapping[str, Any]) -> Dict[str, Any]:
    """Classify every physical replay without hiding retry diagnostics.

    Publication runs may retry the pre/post baseline or frozen-champion
    validation because those attempts are not optimizer observations.  Such
    failures remain part of the measured execution cost and audit trail, but
    they must not be confused with configuration failures that legitimately
    consume the fixed search budget and train a tuner's failure model.  The
    publication gate permits only accounted validation retries and learnable
    configuration failures; candidate infrastructure failures are excluded.
    """
    groups = {
        "pre_search_baseline": list(state.get("baseline_rows") or []),
        "candidate_search": list(state.get("observations") or []),
        "post_search_baseline": list(state.get("post_baseline_rows") or []),
        "champion_validation": list(
            state.get("champion_validation_rows") or []
        ),
    }

    def row_valid(group: str, row: Mapping[str, Any]) -> bool:
        if group == "candidate_search":
            return row.get("valid") is True
        return row.get("physical_valid") is True

    attempts_by_phase = {
        group: len(rows) for group, rows in groups.items()
    }
    failures_by_phase = {
        group: sum(
            not row_valid(group, row)
            for row in rows
            if isinstance(row, Mapping)
        )
        for group, rows in groups.items()
    }
    accounted_attempts = sum(attempts_by_phase.values())
    accounted_failures = sum(failures_by_phase.values())
    recorded_attempts = int(state.get("physical_workload_invocations") or 0)
    recorded_failures = int(state.get("technical_failures") or 0)
    excluded_infrastructure = list(
        state.get("excluded_infrastructure_attempts") or []
    )
    excluded_infrastructure_attempts = sum(
        int(item.get("physical_attempt_count") or 0)
        for item in excluded_infrastructure
        if isinstance(item, Mapping)
    )
    superseded_lineage_attempts = sum(
        int(item.get("physical_attempt_count") or 0)
        for item in list(state.get("superseded_search_lineage") or [])
        if isinstance(item, Mapping)
    )
    retryable_failures = sum(
        failures_by_phase[group]
        for group in (
            "pre_search_baseline",
            "post_search_baseline",
            "champion_validation",
        )
    )
    candidate_configuration_failures = sum(
        row.get("model_failure") is True
        for row in groups["candidate_search"]
        if isinstance(row, Mapping) and row.get("valid") is not True
    )
    candidate_infrastructure_failures = (
        failures_by_phase["candidate_search"]
        - candidate_configuration_failures
    )
    return {
        "contract": "pg_external_technical_failure_accounting_v1",
        "attempts_by_phase": attempts_by_phase,
        "failures_by_phase": failures_by_phase,
        "accounted_physical_workload_invocations": accounted_attempts,
        "recorded_physical_workload_invocations": recorded_attempts,
        "accounted_technical_failures": accounted_failures,
        "recorded_technical_failures": recorded_failures,
        "excluded_infrastructure_physical_attempts": (
            excluded_infrastructure_attempts
        ),
        "superseded_lineage_physical_attempts": superseded_lineage_attempts,
        "total_physical_attempts_including_excluded_infrastructure": (
            recorded_attempts
            + excluded_infrastructure_attempts
            + superseded_lineage_attempts
        ),
        "candidate_technical_failures": failures_by_phase["candidate_search"],
        "candidate_configuration_failures": candidate_configuration_failures,
        "candidate_infrastructure_failures": candidate_infrastructure_failures,
        "retryable_validation_technical_failures": retryable_failures,
        "unattributed_physical_workload_invocations": (
            recorded_attempts - accounted_attempts
        ),
        "unattributed_technical_failures": recorded_failures - accounted_failures,
        "technical_failures_excluded_from_comparisons": True,
        "infrastructure_failures_excluded_from_tuner_feedback": True,
        "configuration_failures_included_in_tuner_feedback": True,
        "all_technical_failures_accounted": (
            recorded_attempts == accounted_attempts
            and recorded_failures == accounted_failures
        ),
    }


def aggregate_repeated_worker_validations(
    rows: Sequence[Dict[str, Any]],
    *,
    expected_workers: Sequence[str],
    repetitions_per_worker: int,
    label: str,
) -> Dict[str, Any]:
    """Aggregate balanced repeated measurements with an explicit audit receipt."""
    aggregate = aggregate_parallel_baseline_validation(rows)
    worker_counts = {
        key: sum(str(row.get("worker_key") or "") == key for row in rows)
        for key in expected_workers
    }
    if any(count != repetitions_per_worker for count in worker_counts.values()):
        raise RuntimeError(
            f"unbalanced repeated validation for {label}: {worker_counts}"
        )
    receipt = copy.deepcopy(aggregate.get("parallel_worker_aggregate") or {})
    receipt.update(
        {
            "measurement_count": len(rows),
            "worker_count": len(expected_workers),
            "worker_keys": list(expected_workers),
            "repetitions_per_worker": repetitions_per_worker,
            "worker_measurement_counts": worker_counts,
        }
    )
    aggregate["label"] = label
    aggregate["parallel_worker_aggregate"] = receipt
    return aggregate


class ReplayHarness:
    """Crash-auditable two-worker physical replay transport."""

    def __init__(
        self,
        config_path: Path,
        output_dir: Path,
        method: str,
        candidate_budget: int,
        seed: int,
        scene: str = "none",
        target: str = "none",
        *,
        publication_mode: bool = False,
        method_input_contract: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.config_path = config_path.resolve()
        self.cfg = read_json(self.config_path)
        self.output_dir = output_dir.resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.method = method
        self.candidate_budget = candidate_budget
        self.seed = seed
        self.scene = str(scene).strip().lower()
        self.target = str(target).strip().lower()
        self.publication_mode = publication_mode
        self.method_input_contract = copy.deepcopy(dict(method_input_contract or {}))
        self.workers = validation_workers(self.cfg, self.target)
        if len(self.workers) != 2:
            raise RuntimeError(
                "external baseline requires exactly two workers for "
                f"scene={self.scene} target={self.target}, got {len(self.workers)}"
            )
        if len({worker_key(worker) for worker in self.workers}) != 2:
            raise RuntimeError("external baseline workers are not distinct")
        self.defaults = {
            name: float(value)
            for name, value in initial_params(self.cfg).items()
            if name in ACTIVE_PARAMS
        }
        if set(self.defaults) != set(ACTIVE_PARAMS):
            raise RuntimeError("external baseline default parameter vector is incomplete")
        self.state_path = self.output_dir / "state.json"
        self.log_path = self.output_dir / "events.jsonl"
        self.stop_requested = False
        self.state = self._load_or_initialize_state()
        self._initialize_execution_domain_guard()

    def repair_observation_trial_ids(self) -> None:
        """Repair labels written by the pre-v2 first-``_t`` parser on resume."""
        repairs: List[Dict[str, Any]] = []
        for index, observation in enumerate(self.state.get("observations") or []):
            parsed = trial_from_replay_label(str(observation.get("label") or ""))
            current = int(observation.get("trial") or -1)
            if parsed <= 0 or parsed == current:
                continue
            observation["trial"] = parsed
            repairs.append(
                {
                    "observation_index": index,
                    "label": observation.get("label"),
                    "old_trial": current,
                    "new_trial": parsed,
                }
            )
        if not repairs:
            return
        migration = {
            "contract": "external_trial_label_parser_migration_v1",
            "time": time.time(),
            "repairs": repairs,
        }
        self.state.setdefault("automatic_state_migrations", []).append(migration)
        self.save()
        self.log(
            "external_trial_id_state_migrated",
            migration_contract=migration["contract"],
            repair_count=len(repairs),
            repairs=repairs,
            manual_mutation=False,
        )

    def import_completed_search_state(self, source_path: Path) -> Dict[str, Any]:
        """Import an audited legacy search into a fresh validation campaign.

        The imported search supplies only the already measured candidate pool
        and its frozen winner.  This campaign still measures a fresh canonical
        baseline and replays that winner on both current workers.  The source
        tree is never modified.
        """
        source_path = source_path.resolve()
        source_bytes = source_path.read_bytes()
        source_sha256 = hashlib.sha256(source_bytes).hexdigest()
        existing_import = self.state.get("imported_completed_search") or {}
        if existing_import:
            if (
                existing_import.get("source_state_sha256") != source_sha256
                or Path(str(existing_import.get("source_state_path"))).resolve()
                != source_path
            ):
                raise RuntimeError("completed-search import provenance mismatch")
            return copy.deepcopy(self.state.get("imported_method_metadata") or {})
        if (
            self.state.get("observations")
            or int(self.state.get("candidate_physical_invocations") or 0) != 0
            or self.state.get("canonical_baseline") is not None
        ):
            raise RuntimeError("completed search can only be imported into fresh state")

        source = json.loads(source_bytes.decode("utf-8"))
        expected_config_sha256 = hashlib.sha256(self.config_path.read_bytes()).hexdigest()
        required_equal = {
            "state_version": STATE_VERSION,
            "method": self.method,
            "candidate_budget": self.candidate_budget,
            "seed": self.seed,
            "config_sha256": expected_config_sha256,
            "parameter_names": list(ACTIVE_PARAMS),
            "parameter_ranges": {
                name: list(DEFAULT_RANGES[name]) for name in ACTIVE_PARAMS
            },
            "replay_parallelism": 2,
            "baseline_repetitions_per_worker": BASELINE_REPETITIONS,
            "publication_mode": self.publication_mode,
            "publication_protocol": (
                PUBLICATION_PROTOCOL if self.publication_mode else None
            ),
            "method_input_contract": self.method_input_contract,
        }
        for key, expected in required_equal.items():
            if source.get(key) != expected:
                raise RuntimeError(
                    f"completed-search import contract mismatch for {key}"
                )
        if source.get("phase") != "complete":
            raise RuntimeError("completed-search source is not complete")
        if source.get("manual_mutation_after_start") is not False:
            raise RuntimeError("completed-search source records manual mutation")
        if source.get("inflight_wave") is not None:
            raise RuntimeError("completed-search source has an inflight wave")
        if int(source.get("candidate_physical_invocations") or 0) != self.candidate_budget:
            raise RuntimeError("completed-search source physical budget mismatch")

        observations = copy.deepcopy(list(source.get("observations") or []))
        if len(observations) != self.candidate_budget:
            raise RuntimeError("completed-search source observation count mismatch")
        signatures: List[str] = []
        trials: List[int] = []
        valid_observations: List[Dict[str, Any]] = []
        for observation in observations:
            params = observation.get("params") or {}
            if set(params) != set(ACTIVE_PARAMS):
                raise RuntimeError("completed-search observation has incomplete params")
            signature = exact_signature(params)
            if observation.get("param_signature") != signature:
                raise RuntimeError("completed-search parameter signature mismatch")
            signatures.append(signature)
            trials.append(int(observation.get("trial") or 0))
            if (
                observation.get("valid") is True
                and observation.get("technical_failure") is not True
                and float(observation.get("db_qps") or 0.0) > 0.0
            ):
                valid_observations.append(observation)
        if len(set(signatures)) != self.candidate_budget:
            raise RuntimeError("completed-search source contains duplicate params")
        if len(set(trials)) != self.candidate_budget:
            raise RuntimeError("completed-search source contains duplicate trials")
        if not valid_observations:
            raise RuntimeError("completed-search source has no valid candidate")
        best = max(valid_observations, key=lambda row: float(row["db_qps"]))
        source_best = source.get("best_observation") or {}
        if (
            source_best.get("label") != best.get("label")
            or exact_signature(source_best.get("params") or {})
            != exact_signature(best.get("params") or {})
            or not math.isclose(
                float(source_best.get("db_qps") or 0.0),
                float(best.get("db_qps") or 0.0),
                rel_tol=0.0,
                abs_tol=1e-12,
            )
        ):
            raise RuntimeError("completed-search source winner is not the true maximum")

        authority_path = source_path.parent / "authority_manifest.json"
        authority = read_json(authority_path)
        stored_authority_sha256 = str(authority.get("authority_sha256") or "")
        authority_without_sha = copy.deepcopy(authority)
        authority_without_sha.pop("authority_sha256", None)
        if canonical_sha256(authority_without_sha) != stored_authority_sha256:
            raise RuntimeError("completed-search source authority SHA mismatch")
        if (
            authority.get("phase") != "complete"
            or authority.get("method") != self.method
            or int(authority.get("candidate_budget") or 0) != self.candidate_budget
            or int(authority.get("candidate_physical_invocations") or 0)
            != self.candidate_budget
            or exact_signature(
                (authority.get("best_observation") or {}).get("params") or {}
            )
            != exact_signature(best.get("params") or {})
        ):
            raise RuntimeError("completed-search source authority contract mismatch")

        self.state["observations"] = observations
        self.state["candidate_physical_invocations"] = self.candidate_budget
        self.state["physical_workload_invocations"] = self.candidate_budget
        self.state["valid_replays"] = len(valid_observations)
        self.state["technical_failures"] = (
            self.candidate_budget - len(valid_observations)
        )
        self.state["best_observation"] = copy.deepcopy(best)
        self.state["phase"] = "imported_search_pending_fresh_baseline"
        method_metadata = copy.deepcopy(source.get("method_metadata") or {})
        method_metadata["completed_search_import"] = {
            "contract": "pg_external_completed_search_import_v1",
            "source_state_path": str(source_path),
            "source_state_sha256": source_sha256,
            "source_authority_path": str(authority_path),
            "source_authority_sha256": stored_authority_sha256,
            "source_tree_modified": False,
            "search_workload_replayed": False,
            "fresh_baseline_required": True,
            "fresh_two_worker_champion_validation_required": True,
        }
        self.state["imported_method_metadata"] = method_metadata
        self.state["imported_completed_search"] = copy.deepcopy(
            method_metadata["completed_search_import"]
        )
        self.save()
        self.log(
            "external_completed_search_imported",
            **self.state["imported_completed_search"],
            candidate_physical_invocations=self.candidate_budget,
            valid_candidate_observations=len(valid_observations),
            technical_candidate_observations=(
                self.candidate_budget - len(valid_observations)
            ),
            manual_mutation=False,
        )
        return method_metadata

    def _load_or_initialize_state(self) -> Dict[str, Any]:
        workload_contract = copy.deepcopy(
            ((self.cfg.get("algorithm_c") or {}).get("workload_contract") or {})
        )
        workload_name = str(
            ((self.cfg.get("binary_plan_tuning") or {}).get("workload_name"))
            or ((self.cfg.get("algorithm_c") or {}).get("benchmark"))
            or self.scene
        )
        contract = {
            "state_version": STATE_VERSION,
            "method": self.method,
            "scene": self.scene,
            "target": self.target,
            "candidate_budget": self.candidate_budget,
            "seed": self.seed,
            "config_sha256": hashlib.sha256(self.config_path.read_bytes()).hexdigest(),
            "workload": workload_name,
            "workload_contract_sha256": canonical_sha256(workload_contract),
            "parameter_names": list(ACTIVE_PARAMS),
            "parameter_ranges": {
                name: list(DEFAULT_RANGES[name]) for name in ACTIVE_PARAMS
            },
            "replay_parallelism": 2,
            "baseline_repetitions_per_worker": BASELINE_REPETITIONS,
            "post_baseline_repetitions_per_worker": (
                POST_BASELINE_REPETITIONS if self.publication_mode else 0
            ),
            "champion_repetitions_per_worker": (
                CHAMPION_REPETITIONS if self.publication_mode else 1
            ),
            "publication_mode": self.publication_mode,
            "publication_protocol": (
                PUBLICATION_PROTOCOL if self.publication_mode else None
            ),
            "method_input_contract": self.method_input_contract,
        }
        contract_sha = canonical_sha256(contract)
        if self.state_path.is_file():
            state = read_json(self.state_path)
            if state.get("contract_sha256") != contract_sha:
                raise RuntimeError("external baseline resume contract mismatch")
            return state
        state = {
            **contract,
            "contract_sha256": contract_sha,
            "phase": "fresh_parallel_baseline",
            "baseline_rows": [],
            "canonical_baseline": None,
            "worker_baseline_qps": {},
            "post_baseline_rows": [],
            "post_search_baseline": None,
            "comparison_baseline": None,
            "observations": [],
            "candidate_physical_invocations": 0,
            "physical_workload_invocations": 0,
            "valid_replays": 0,
            "technical_failures": 0,
            "restore_failures": 0,
            "best_observation": None,
            "frozen_search_champion": None,
            "champion_validation_rows": [],
            "excluded_infrastructure_attempts": [],
            "validated_champion": None,
            "inflight_wave": None,
            "manual_mutation_after_start": False,
            "started_at_epoch_s": time.time(),
            "updated_at_epoch_s": time.time(),
        }
        write_json(self.state_path, state)
        self.log("external_baseline_start", contract=contract)
        return state

    def log(self, event: str, **extra: Any) -> None:
        payload = {
            "event": event,
            "time": time.time(),
            "method": self.method,
            **extra,
        }
        append_jsonl(self.log_path, payload)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)

    def save(self) -> None:
        self.state["updated_at_epoch_s"] = time.time()
        write_json(self.state_path, self.state)

    def _initialize_execution_domain_guard(self) -> None:
        """Freeze Docker resources and every non-search PostgreSQL GUC."""
        existing = self.state.get("execution_domain_guard") or {}
        expected_resource_sha = str(existing.get("container_resource_semantic_sha256") or "")
        expected_guc_sha = str(existing.get("fixed_guc_semantic_sha256") or "")
        receipts = self._audit_execution_domain(
            "initial",
            expected_resource_sha=expected_resource_sha or None,
            expected_guc_sha=expected_guc_sha or None,
        )
        if not existing:
            self.state["execution_domain_guard"] = {
                "contract": "pg_external_search_execution_domain_guard_v1",
                "container_resource_semantic_sha256": receipts[
                    "container_resource_semantic_sha256"
                ],
                "fixed_guc_semantic_sha256": receipts[
                    "fixed_guc_semantic_sha256"
                ],
                "initial_receipts": copy.deepcopy(receipts),
            }
            self.save()

    def _audit_execution_domain(
        self,
        label: str,
        *,
        expected_resource_sha: Optional[str] = None,
        expected_guc_sha: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Persist a fail-closed resource/GUC receipt at one replay boundary."""
        guard = self.state.get("execution_domain_guard") or {}
        resource_sha = expected_resource_sha or str(
            guard.get("container_resource_semantic_sha256") or ""
        )
        guc_sha = expected_guc_sha or str(
            guard.get("fixed_guc_semantic_sha256") or ""
        )
        resource_receipt = audit_container_resource_domain(
            {self.scene: {"workers": self.workers}},
            expected_semantic_sha256=resource_sha or None,
        )
        guc_receipt = audit_fixed_guc_domain(
            self.cfg,
            self.workers,
            expected_semantic_sha256=guc_sha or None,
        )
        receipt_root = self.output_dir / "execution_domain_receipts"
        resource_path = receipt_root / f"{label}_container.json"
        guc_path = receipt_root / f"{label}_fixed_guc.json"
        write_json(resource_path, resource_receipt)
        write_receipt(guc_path, guc_receipt)
        return {
            "label": label,
            "container_resource_receipt_path": str(resource_path),
            "container_resource_receipt_sha256": resource_receipt[
                "receipt_sha256"
            ],
            "container_resource_semantic_sha256": resource_receipt[
                "semantic_sha256"
            ],
            "fixed_guc_receipt_path": str(guc_path),
            "fixed_guc_receipt_sha256": guc_receipt["receipt_sha256"],
            "fixed_guc_semantic_sha256": guc_receipt["semantic_sha256"],
        }

    def request_stop(self, signum: int) -> None:
        self.stop_requested = True
        self.log("external_baseline_stop_requested", signal=signal.Signals(signum).name)

    def recover_inflight_wave(self) -> None:
        journal = self.state.get("inflight_wave")
        if not isinstance(journal, Mapping):
            return
        labels = [str(item.get("label") or "") for item in journal.get("entries") or []]
        completed: Dict[str, Dict[str, Any]] = {}
        if self.log_path.is_file():
            with self.log_path.open("r", encoding="utf-8") as handle:
                for raw in handle:
                    try:
                        event = json.loads(raw)
                    except (ValueError, json.JSONDecodeError):
                        continue
                    if event.get("event") == "external_physical_replay_result":
                        validation = event.get("validation")
                        label = str((validation or {}).get("label") or "")
                        if label in labels:
                            completed[label] = validation
        phase = str(journal.get("phase") or "recovered")
        retryable_validation_phase = (
            phase.startswith("baseline")
            or phase.startswith("post_baseline")
            or phase.startswith("champion_verification")
        )
        if (
            self.publication_mode
            and not retryable_validation_phase
            and len(completed) != len(labels)
        ):
            # No result was committed and no tuner feedback occurred. Preserve
            # the attempted work as an infrastructure receipt, then let the
            # deterministic policy request this formal trial again.
            receipt = {
                "contract": "pg_external_excluded_infrastructure_attempt_v1",
                "journal_id": journal.get("journal_id"),
                "phase": phase,
                "labels": labels,
                "completed_result_count": len(completed),
                "missing_result_labels": [
                    label for label in labels if label not in completed
                ],
                "physical_attempt_count": len(labels),
                "excluded_from_candidate_budget": True,
                "excluded_from_tuner_feedback": True,
                "excluded_from_comparisons": True,
                "reason": "controller_or_audit_failed_before_result_commit",
                "recovered_at_epoch_s": time.time(),
            }
            receipt["receipt_sha256"] = canonical_sha256(receipt)
            self.state.setdefault("excluded_infrastructure_attempts", []).append(
                receipt
            )
            self.state["inflight_wave"] = None
            self.save()
            self.log(
                "external_candidate_infrastructure_attempt_excluded",
                receipt=receipt,
                formal_candidate_budget_unchanged=True,
                tuner_feedback_applied=False,
            )
            return
        # A complete wave can be committed exactly once.  Missing rows consume
        # physical slots as technical failures and never train either tuner.
        rows: List[Dict[str, Any]] = []
        for entry in journal.get("entries") or []:
            label = str(entry.get("label") or "")
            if label in completed:
                rows.append(copy.deepcopy(completed[label]))
                continue
            rows.append(
                {
                    "label": label,
                    "status": "failed",
                    "technical_failure": True,
                    "error": "controller_terminated_during_physical_wave",
                    "params": copy.deepcopy(entry.get("params") or {}),
                    "worker_key": entry.get("worker_key"),
                    "physical_workload_invocations": 1,
                    "workload_completeness": {
                        "passed": False,
                        "errors": ["controller_terminated_during_physical_wave"],
                    },
                }
            )
        self._commit_wave_rows(
            rows,
            phase=phase,
            suggestions=journal.get("suggestions") or [],
            recovered=True,
        )
        self.log(
            "external_inflight_wave_recovered",
            journal_id=journal.get("journal_id"),
            completed_result_count=len(completed),
            synthesized_technical_failures=len(rows) - len(completed),
        )

    def _restore_wave(
        self,
        entries: Sequence[Mapping[str, Any]],
    ) -> Dict[int, Dict[str, Any]]:
        restores: Dict[int, Dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_map = {
                executor.submit(
                    restore_worker_from_hybench_template,
                    self.cfg,
                    self.workers[int(entry.get("worker_index", index))],
                    label=str(entry["label"]),
                ): index
                for index, entry in enumerate(entries)
            }
            for future in as_completed(future_map):
                index = future_map[future]
                restore = future.result()
                if restore.get("status") != "ok":
                    raise RuntimeError(
                        f"external baseline restore failed: {restore}"
                    )
                restores[index] = restore
        return restores

    def replay_wave(
        self,
        suggestions: Sequence[Mapping[str, Any]],
        *,
        phase: str,
    ) -> List[Dict[str, Any]]:
        if not suggestions or len(suggestions) > 2:
            raise ValueError("external baseline wave must contain one or two points")
        wave_index = int(self.state.get("physical_workload_invocations") or 0) // 2 + 1
        self._audit_execution_domain(f"wave_{wave_index:03d}_pre")
        entries: List[Dict[str, Any]] = []
        for index, suggestion in enumerate(suggestions):
            trial = int(suggestion["trial"])
            worker_index = index
            if self.publication_mode and len(suggestions) == 1:
                # Preserve sequential BO semantics while balancing observations
                # across the two otherwise identical physical workers.
                worker_index = max(0, trial - 1) % len(self.workers)
            label = (
                f"{self.scene}_{self.method}_{phase}_t{trial:03d}_"
                f"{worker_key(self.workers[worker_index])}"
            )
            entries.append(
                {
                    "label": label,
                    "worker_key": worker_key(self.workers[worker_index]),
                    "worker_index": worker_index,
                    "params": copy.deepcopy(suggestion["params"]),
                    "trial": trial,
                    "stage": suggestion.get("stage"),
                }
            )
        self.log(
            "external_restore_wave_start",
            phase=phase,
            labels=[entry["label"] for entry in entries],
            wave_width=len(entries),
            physical_workload_invocations=0,
        )
        restores: Optional[Dict[int, Dict[str, Any]]] = None
        for restore_attempt in range(1, RESTORE_WAVE_ATTEMPTS + 1):
            try:
                restores = self._restore_wave(entries)
                break
            except Exception as exc:
                self.state["restore_failures"] = int(
                    self.state.get("restore_failures") or 0
                ) + 1
                self.state["inflight_wave"] = None
                self.save()
                retrying = restore_attempt < RESTORE_WAVE_ATTEMPTS
                self.log(
                    "external_restore_wave_failed",
                    phase=phase,
                    restore_attempt=restore_attempt,
                    restore_attempt_limit=RESTORE_WAVE_ATTEMPTS,
                    retrying=retrying,
                    error=f"{type(exc).__name__}: {exc}",
                    physical_workload_invocations=0,
                    valid_replays=0,
                )
                if not retrying:
                    raise
                time.sleep(RESTORE_WAVE_RETRY_DELAY_S * restore_attempt)
        if restores is None:
            raise RuntimeError("external baseline restore exhausted without result")
        if self.stop_requested:
            self.log(
                "external_stop_after_restore_before_workload",
                phase=phase,
                physical_workload_invocations=0,
            )
            return []
        journal = {
            "contract": "pg_external_baseline_inflight_wave_v2",
            "phase": phase,
            "entries": entries,
            "suggestions": copy.deepcopy(list(suggestions)),
            "workload_starting_at_epoch_s": time.time(),
        }
        journal["journal_id"] = canonical_sha256(journal)
        self.state["inflight_wave"] = journal
        self.save()
        self.log(
            "external_physical_wave_start",
            phase=phase,
            journal_id=journal["journal_id"],
            labels=[entry["label"] for entry in entries],
            wave_width=len(entries),
        )
        rows: List[Optional[Dict[str, Any]]] = [None] * len(entries)
        with ThreadPoolExecutor(max_workers=len(entries)) as executor:
            future_map = {}
            for index, entry in enumerate(entries):
                selected_worker = self.workers[int(entry["worker_index"])]
                future = executor.submit(
                    run_real_validation_safe,
                    self.cfg,
                    dict(entry["params"]),
                    output_dir=self.output_dir / "replays" / phase,
                    label=str(entry["label"]),
                    worker=worker_with_prepared_restore(selected_worker),
                    force_new_replay=True,
                )
                future_map[future] = index
            for future in as_completed(future_map):
                index = future_map[future]
                row = future.result()
                selected_worker = self.workers[int(entries[index]["worker_index"])]
                row["worker_key"] = worker_key(selected_worker)
                row["restore"] = restores[index]
                row["baseline_method"] = self.method
                row["baseline_stage"] = entries[index].get("stage")
                rows[index] = row
        complete_rows = [row for row in rows if isinstance(row, dict)]
        apply_ssd_wave_qualification(
            complete_rows,
            target=self.target,
            phase=f"external_{self.method}_{phase}",
            cfg=self.cfg,
        )
        self._audit_execution_domain(f"wave_{wave_index:03d}_post")
        for row in complete_rows:
            full_path = self.output_dir / "validations" / f"{row['label']}.json"
            write_json(full_path, row)
            append_jsonl(
                self.log_path,
                {
                    "event": "external_physical_replay_result",
                    "time": time.time(),
                    "method": self.method,
                    "phase": phase,
                    "validation": row,
                    "validation_path": str(full_path),
                },
            )
        self._commit_wave_rows(
            complete_rows,
            phase=phase,
            suggestions=suggestions,
            recovered=False,
        )
        return complete_rows

    def _commit_wave_rows(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        phase: str,
        suggestions: Sequence[Mapping[str, Any]],
        recovered: bool,
    ) -> None:
        by_trial = {
            int(item["trial"]): item
            for item in suggestions
            if isinstance(item, Mapping) and item.get("trial") is not None
        }
        baseline_phase = phase.startswith("baseline")
        post_baseline_phase = phase.startswith("post_baseline")
        champion_phase = phase.startswith("champion_verification")
        excluded_infrastructure_rows = 0
        formal_physical_rows = 0
        formal_valid_rows = 0
        formal_technical_rows = 0
        for row in rows:
            valid = physical_valid(row)
            failure_classification = candidate_failure_classification(row)
            candidate_phase = not (
                baseline_phase or post_baseline_phase or champion_phase
            )
            if (
                self.publication_mode
                and candidate_phase
                and failure_classification == "infrastructure_failure"
            ):
                existing_receipts = list(
                    self.state.get("excluded_infrastructure_attempts") or []
                )
                label = str(row.get("label") or "unknown")
                attempt = 1 + sum(
                    label in list(item.get("labels") or [])
                    for item in existing_receipts
                    if isinstance(item, Mapping)
                )
                diagnostic_path = (
                    self.output_dir
                    / "excluded_infrastructure_attempts"
                    / f"{label}_attempt{attempt:02d}.json"
                )
                write_json(diagnostic_path, row)
                receipt = {
                    "contract": "pg_external_excluded_infrastructure_attempt_v1",
                    "phase": phase,
                    "labels": [label],
                    "physical_attempt_count": 1,
                    "failure_classification": failure_classification,
                    "validation_path": str(diagnostic_path),
                    "validation_sha256": hashlib.sha256(
                        diagnostic_path.read_bytes()
                    ).hexdigest(),
                    "excluded_from_candidate_budget": True,
                    "excluded_from_tuner_feedback": True,
                    "excluded_from_comparisons": True,
                    "reason": "candidate_infrastructure_failure",
                    "recorded_at_epoch_s": time.time(),
                }
                receipt["receipt_sha256"] = canonical_sha256(receipt)
                self.state.setdefault(
                    "excluded_infrastructure_attempts", []
                ).append(receipt)
                excluded_infrastructure_rows += 1
                self.log(
                    "external_candidate_infrastructure_attempt_excluded",
                    receipt=receipt,
                    formal_candidate_budget_unchanged=True,
                    tuner_feedback_applied=False,
                )
                continue
            self.state["physical_workload_invocations"] += 1
            formal_physical_rows += 1
            if valid:
                self.state["valid_replays"] += 1
                formal_valid_rows += 1
            else:
                self.state["technical_failures"] += 1
                formal_technical_rows += 1
            slim = slim_validation(row)
            if baseline_phase:
                self.state["baseline_rows"].append(slim)
                continue
            if post_baseline_phase:
                self.state["post_baseline_rows"].append(slim)
                continue
            if champion_phase:
                self.state["champion_validation_rows"].append(slim)
                continue
            label = str(row.get("label") or "")
            trial = trial_from_replay_label(label)
            suggestion = by_trial.get(trial, {})
            params = copy.deepcopy(
                row.get("params") or suggestion.get("params") or {}
            )
            observation = {
                "trial": trial,
                "stage": suggestion.get("stage") or row.get("baseline_stage"),
                "params": params,
                "param_signature": exact_signature(params),
                "label": label,
                "valid": valid,
                "technical_failure": not valid,
                "model_failure": failure_classification == "configuration_failure",
                "failure_classification": failure_classification,
                "db_qps": float(row.get("db_qps") or 0.0),
                "cost": (
                    -float(row.get("db_qps") or 0.0)
                    if valid and float(row.get("db_qps") or 0.0) > 0.0
                    else 1e6
                ),
                "resources": copy.deepcopy(row.get("resources") or {}),
                "elapsed_s": row.get("elapsed_s"),
                "recovered": recovered,
                "policy_metadata": {
                    key: copy.deepcopy(value)
                    for key, value in suggestion.items()
                    if key != "params"
                },
            }
            if self.publication_mode:
                worker_baseline_qps = float(
                    (self.state.get("worker_baseline_qps") or {}).get(
                        str(row.get("worker_key") or "")
                    )
                    or 0.0
                )
                objective_score = (
                    observation["db_qps"] / worker_baseline_qps
                    if valid and worker_baseline_qps > 0.0
                    else 0.0
                )
                observation.update(
                    {
                        "worker_baseline_qps": worker_baseline_qps,
                        "objective_name": "same_worker_qps_ratio",
                        "objective_score": objective_score,
                        "cost": -objective_score if valid else 1e6,
                    }
                )
            self.state["observations"].append(observation)
            self.state["candidate_physical_invocations"] += 1
            current_score = (
                float(observation.get("objective_score") or 0.0)
                if self.publication_mode
                else observation["db_qps"]
            )
            best_score = (
                float(
                    (self.state.get("best_observation") or {}).get(
                        "objective_score"
                    )
                    or 0.0
                )
                if self.publication_mode
                else float(
                    (self.state.get("best_observation") or {}).get("db_qps")
                    or 0.0
                )
            )
            if valid and (
                self.state.get("best_observation") is None
                or current_score > best_score
            ):
                self.state["best_observation"] = copy.deepcopy(observation)
        self.state["inflight_wave"] = None
        self.save()
        self.log(
            "external_physical_wave_committed",
            phase=phase,
            recovered=recovered,
            attempted_rows=len(rows),
            physical_rows=formal_physical_rows,
            valid_rows=formal_valid_rows,
            technical_rows=formal_technical_rows,
            excluded_infrastructure_rows=excluded_infrastructure_rows,
            candidate_physical_invocations=self.state[
                "candidate_physical_invocations"
            ],
        )

    def ensure_baseline(self) -> None:
        required = BASELINE_REPETITIONS * len(self.workers)

        def selected_valid_rows() -> List[Dict[str, Any]]:
            selected_by_worker: Dict[str, List[Dict[str, Any]]] = {
                worker_key(worker): [] for worker in self.workers
            }
            for slim_row in self.state["baseline_rows"]:
                label = str(slim_row.get("label") or "")
                full_path = self.output_dir / "validations" / f"{label}.json"
                if not full_path.is_file():
                    continue
                full_row = read_json(full_path)
                key = str(full_row.get("worker_key") or "")
                if key not in selected_by_worker or not physical_valid(full_row):
                    continue
                if len(selected_by_worker[key]) < BASELINE_REPETITIONS:
                    selected_by_worker[key].append(full_row)
            return [
                row
                for worker in self.workers
                for row in selected_by_worker[worker_key(worker)]
            ]

        full_rows = selected_valid_rows()
        while len(full_rows) < required:
            repetition = len(self.state["baseline_rows"]) // len(self.workers) + 1
            if repetition > BASELINE_MAX_WAVES:
                self.log(
                    "external_parallel_baseline_retry_budget_exhausted",
                    baseline_required_valid=required,
                    baseline_valid=len(full_rows),
                    baseline_physical=len(self.state["baseline_rows"]),
                    baseline_max_waves=BASELINE_MAX_WAVES,
                )
                raise RuntimeError(
                    "external baseline valid replay budget exhausted: "
                    f"valid={len(full_rows)}/{required}, "
                    f"physical={len(self.state['baseline_rows'])}"
                )
            suggestions = [
                {
                    "trial": -100 - repetition * 2 - index,
                    "stage": "baseline",
                    "params": copy.deepcopy(self.defaults),
                }
                for index in range(2)
            ]
            rows = self.replay_wave(
                suggestions,
                phase=f"baseline_rep{repetition:02d}",
            )
            if self.stop_requested:
                return
            full_rows = selected_valid_rows()
            if len(rows) != len(self.workers):
                raise RuntimeError("external baseline wave returned an incomplete row set")
            if not all(physical_valid(row) for row in rows):
                self.log(
                    "external_parallel_baseline_wave_retry",
                    repetition=repetition,
                    baseline_required_valid=required,
                    baseline_valid=len(full_rows),
                    baseline_physical=len(self.state["baseline_rows"]),
                    technical_rows=sum(not physical_valid(row) for row in rows),
                    next_repetition=repetition + 1,
                )
        worker_keys = [worker_key(worker) for worker in self.workers]
        self.state["canonical_baseline"] = slim_validation(
            aggregate_repeated_worker_validations(
                full_rows,
                expected_workers=worker_keys,
                repetitions_per_worker=BASELINE_REPETITIONS,
                label="pre_search_baseline_aggregate",
            )
        )
        worker_baseline_qps: Dict[str, float] = {}
        for worker in self.workers:
            key = worker_key(worker)
            values = [
                float(row.get("db_qps") or 0.0)
                for row in full_rows
                if str(row.get("worker_key") or "") == key
                and float(row.get("db_qps") or 0.0) > 0.0
            ]
            if not values:
                raise RuntimeError(f"missing valid baseline QPS for worker {key}")
            worker_baseline_qps[key] = float(np.median(values))
        self.state["worker_baseline_qps"] = worker_baseline_qps
        baseline_qps = float(
            self.state["canonical_baseline"].get("db_qps") or 0.0
        )
        if (
            self.state.get("best_observation") is None
            or baseline_qps
            > float(self.state["best_observation"].get("db_qps") or 0.0)
        ):
            self.state["best_observation"] = {
                "trial": 0,
                "stage": "canonical_baseline",
                "params": copy.deepcopy(self.defaults),
                "param_signature": exact_signature(self.defaults),
                "label": self.state["canonical_baseline"].get("label"),
                "valid": True,
                "technical_failure": False,
                "db_qps": baseline_qps,
                "cost": -1.0 if self.publication_mode else -baseline_qps,
                "objective_name": (
                    "same_worker_qps_ratio" if self.publication_mode else "db_qps"
                ),
                "objective_score": 1.0 if self.publication_mode else baseline_qps,
                "resources": copy.deepcopy(
                    self.state["canonical_baseline"].get("resources") or {}
                ),
                "elapsed_s": self.state["canonical_baseline"].get("elapsed_s"),
                "champion_role": "default_is_feasible_candidate",
            }
        self.state["phase"] = "search"
        self.save()
        self.log(
            "external_parallel_baseline_complete",
            baseline_count=len(full_rows),
            baseline_physical_count=len(self.state["baseline_rows"]),
            baseline_technical_count=(
                len(self.state["baseline_rows"]) - len(full_rows)
            ),
            canonical_baseline=self.state["canonical_baseline"],
            worker_baseline_qps=worker_baseline_qps,
        )

    def ensure_post_search_baseline(self) -> None:
        """Measure defaults again so the paper comparison is drift-bracketed."""
        if not self.publication_mode:
            return
        required = POST_BASELINE_REPETITIONS * len(self.workers)
        worker_keys = [worker_key(worker) for worker in self.workers]

        def selected_valid_rows() -> List[Dict[str, Any]]:
            selected_by_worker: Dict[str, List[Dict[str, Any]]] = {
                key: [] for key in worker_keys
            }
            for slim_row in self.state["post_baseline_rows"]:
                label = str(slim_row.get("label") or "")
                full_path = self.output_dir / "validations" / f"{label}.json"
                if not full_path.is_file():
                    continue
                full_row = read_json(full_path)
                key = str(full_row.get("worker_key") or "")
                if key not in selected_by_worker or not physical_valid(full_row):
                    continue
                if len(selected_by_worker[key]) < POST_BASELINE_REPETITIONS:
                    selected_by_worker[key].append(full_row)
            return [row for key in worker_keys for row in selected_by_worker[key]]

        full_rows = selected_valid_rows()
        while len(full_rows) < required:
            repetition = (
                len(self.state["post_baseline_rows"]) // len(self.workers) + 1
            )
            if repetition > BASELINE_MAX_WAVES:
                raise RuntimeError(
                    "post-search baseline valid replay budget exhausted: "
                    f"valid={len(full_rows)}/{required}"
                )
            suggestions = [
                {
                    "trial": -500 - repetition * 2 - index,
                    "stage": "post_search_baseline",
                    "params": copy.deepcopy(self.defaults),
                }
                for index in range(len(self.workers))
            ]
            rows = self.replay_wave(
                suggestions,
                phase=f"post_baseline_rep{repetition:02d}",
            )
            if self.stop_requested:
                return
            if len(rows) != len(self.workers):
                raise RuntimeError("post-search baseline wave returned incomplete rows")
            full_rows = selected_valid_rows()

        post_aggregate = aggregate_repeated_worker_validations(
            full_rows,
            expected_workers=worker_keys,
            repetitions_per_worker=POST_BASELINE_REPETITIONS,
            label="post_search_baseline_aggregate",
        )
        pre_rows: List[Dict[str, Any]] = []
        for slim_row in self.state["baseline_rows"]:
            path = self.output_dir / "validations" / f"{slim_row.get('label')}.json"
            if path.is_file():
                row = read_json(path)
                if physical_valid(row):
                    pre_rows.append(row)
        selected_pre: List[Dict[str, Any]] = []
        for key in worker_keys:
            selected_pre.extend(
                [row for row in pre_rows if str(row.get("worker_key") or "") == key][
                    :BASELINE_REPETITIONS
                ]
            )
        comparison = aggregate_repeated_worker_validations(
            selected_pre + full_rows,
            expected_workers=worker_keys,
            repetitions_per_worker=(BASELINE_REPETITIONS + POST_BASELINE_REPETITIONS),
            label="pre_post_search_bracket_aggregate",
        )
        comparison["baseline_bracket"] = {
            "contract": "pre_post_search_default_bracket_v1",
            "aggregation": "arithmetic_mean_of_balanced_physical_measurements",
            "pre_repetitions_per_worker": BASELINE_REPETITIONS,
            "post_repetitions_per_worker": POST_BASELINE_REPETITIONS,
            "pre_db_qps": self.state["canonical_baseline"].get("db_qps"),
            "post_db_qps": post_aggregate.get("db_qps"),
        }
        self.state["post_search_baseline"] = slim_validation(post_aggregate)
        self.state["comparison_baseline"] = slim_validation(comparison)
        self.state["comparison_baseline"]["baseline_bracket"] = copy.deepcopy(
            comparison["baseline_bracket"]
        )
        self.state["phase"] = "champion_verification"
        self.save()
        self.log(
            "external_post_search_baseline_complete",
            post_search_baseline=self.state["post_search_baseline"],
            comparison_baseline=self.state["comparison_baseline"],
        )

    def restore_clean_baseline(self, reason: str) -> None:
        entries = [
            {
                "label": (
                    f"{self.scene}_{self.method}_final_restore_"
                    f"{worker_key(worker)}"
                )
            }
            for worker in self.workers
        ]
        restores = self._restore_wave(entries)
        receipt = {
            "contract": "pg_external_baseline_clean_restore_v1",
            "reason": reason,
            "method": self.method,
            "scene": self.scene,
            "target": self.target,
            "workers": [restores[index] for index in sorted(restores)],
            "physical_workload_invocations": 0,
            "completed_at_epoch_s": time.time(),
        }
        receipt["receipt_sha256"] = canonical_sha256(receipt)
        write_json(self.output_dir / "clean_restore_receipt.json", receipt)
        self.state["clean_restore_receipt"] = receipt
        self.state["final_execution_domain_receipts"] = self._audit_execution_domain(
            f"{reason}_post_restore"
        )
        self.save()

    def clean_stop(self) -> None:
        """Restore both workers after a signal without changing search state."""
        self.restore_clean_baseline(reason="external_baseline_signal_stop")
        self.state["phase"] = "clean_stopped"
        self.state["clean_stopped_at_epoch_s"] = time.time()
        self.save()
        self.log(
            "external_baseline_clean_stopped",
            scene=self.scene,
            target=self.target,
            candidate_physical_invocations=self.state[
                "candidate_physical_invocations"
            ],
            frozen_search_champion_unchanged=True,
        )

    def verify_frozen_champion(self) -> None:
        """Freeze the search champion and measure it symmetrically on both workers."""
        repetitions = CHAMPION_REPETITIONS if self.publication_mode else 1
        required = repetitions * len(self.workers)
        worker_keys = [worker_key(worker) for worker in self.workers]

        def selected_valid_rows() -> List[Dict[str, Any]]:
            selected_by_worker: Dict[str, List[Dict[str, Any]]] = {
                key: [] for key in worker_keys
            }
            for slim_row in self.state.get("champion_validation_rows") or []:
                label = str(slim_row.get("label") or "")
                path = self.output_dir / "validations" / f"{label}.json"
                full_row = read_json(path) if path.is_file() else dict(slim_row)
                key = str(full_row.get("worker_key") or "")
                if key not in selected_by_worker or not physical_valid(full_row):
                    continue
                if len(selected_by_worker[key]) < repetitions:
                    selected_by_worker[key].append(full_row)
            return [row for key in worker_keys for row in selected_by_worker[key]]

        existing = self.state.get("validated_champion")
        if existing is not None:
            worker_receipt = existing.get("parallel_worker_aggregate") or {}
            receipt_complete = (
                int(worker_receipt.get("worker_count") or 0) == len(self.workers)
                and int(worker_receipt.get("measurement_count") or 0) == required
                and int(worker_receipt.get("repetitions_per_worker") or 0)
                == repetitions
                and list(worker_receipt.get("worker_keys") or []) == worker_keys
            )
            if receipt_complete:
                return
            raise RuntimeError("validated champion repetition receipt is incomplete")

        champion = copy.deepcopy(
            self.state.get("frozen_search_champion")
            or self.state.get("best_observation")
            or {}
        )
        params = copy.deepcopy(champion.get("params") or {})
        if set(params) != set(ACTIVE_PARAMS):
            raise RuntimeError("external baseline search champion is incomplete")
        if self.state.get("frozen_search_champion") is None:
            self.state["frozen_search_champion"] = champion
        self.save()
        rows = selected_valid_rows()
        attempt = len(self.state.get("champion_validation_rows") or []) // len(
            self.workers
        )
        while len(rows) < required:
            attempt += 1
            if attempt > CHAMPION_MAX_WAVES:
                raise RuntimeError(
                    "external champion verification valid replay budget exhausted"
                )
            suggestions = [
                {
                    "trial": 9000 + attempt * 2 + index,
                    "stage": "champion_verification",
                    "params": copy.deepcopy(params),
                }
                for index in range(2)
            ]
            rows = self.replay_wave(
                suggestions,
                phase=f"champion_verification_rep{attempt:02d}",
            )
            if self.stop_requested:
                return
            if len(rows) != len(self.workers):
                raise RuntimeError("champion verification returned incomplete rows")
            wave_rows = rows
            rows = selected_valid_rows()
            if not all(physical_valid(row) for row in wave_rows):
                self.log(
                    "external_champion_verification_wave_retry",
                    attempt=attempt,
                    attempt_limit=CHAMPION_MAX_WAVES,
                    valid_rows=sum(physical_valid(row) for row in wave_rows),
                    technical_rows=sum(not physical_valid(row) for row in wave_rows),
                    frozen_champion_unchanged=True,
                )
        aggregate = slim_validation(
            aggregate_repeated_worker_validations(
                rows,
                expected_workers=worker_keys,
                repetitions_per_worker=repetitions,
                label="frozen_champion_aggregate",
            )
        )
        aggregate["params"] = copy.deepcopy(params)
        aggregate["search_champion_label"] = champion.get("label")
        aggregate["verification_attempt"] = attempt
        self.state["validated_champion"] = aggregate
        self.save()
        self.log(
            "external_champion_verification_complete",
            frozen_search_champion=champion,
            validated_champion=aggregate,
            comparison_uses_only_validated_champion=True,
        )

    def finalize(self, method_metadata: Mapping[str, Any]) -> None:
        self.state["phase"] = "finalizing"
        self.state["method_metadata"] = copy.deepcopy(dict(method_metadata))
        self.save()
        self.restore_clean_baseline(reason="external_baseline_complete")
        self.state["phase"] = "complete"
        self.state["completed_at_epoch_s"] = time.time()
        self.state["technical_failure_accounting"] = (
            technical_failure_accounting(self.state)
        )
        self.save()
        comparison_baseline = (
            self.state.get("comparison_baseline")
            if self.publication_mode
            else self.state.get("canonical_baseline")
        ) or {}
        baseline_qps = float(comparison_baseline.get("db_qps") or 0.0)
        search_best_qps = float(
            (self.state.get("best_observation") or {}).get("db_qps") or 0.0
        )
        best_qps = float(
            (self.state.get("validated_champion") or {}).get("db_qps") or 0.0
        )
        authority = {
            "contract": "pg_external_four_scene_baseline_authority_v2",
            "method": self.method,
            "scene": self.scene,
            "target": self.target,
            "phase": self.state["phase"],
            "candidate_budget": self.candidate_budget,
            "candidate_physical_invocations": self.state[
                "candidate_physical_invocations"
            ],
            "physical_workload_invocations": self.state[
                "physical_workload_invocations"
            ],
            "valid_replays": self.state["valid_replays"],
            "technical_failures": self.state["technical_failures"],
            "technical_failure_accounting": copy.deepcopy(
                self.state["technical_failure_accounting"]
            ),
            "restore_failures": self.state.get("restore_failures", 0),
            "canonical_baseline": self.state["canonical_baseline"],
            "pre_search_baseline": self.state["canonical_baseline"],
            "post_search_baseline": self.state.get("post_search_baseline"),
            "comparison_baseline": comparison_baseline,
            "comparison_baseline_source": (
                "balanced_pre_post_search_default_bracket"
                if self.publication_mode
                else "pre_search_canonical_baseline"
            ),
            "best_observation": self.state["best_observation"],
            "frozen_search_champion": self.state["frozen_search_champion"],
            "validated_champion": self.state["validated_champion"],
            "search_best_qps": search_best_qps,
            "comparison_qps_source": "validated_champion_two_worker_aggregate",
            "best_qps_improvement_pct": (
                100.0 * (best_qps / baseline_qps - 1.0)
                if baseline_qps > 0.0
                else None
            ),
            "workload": self.state["workload"],
            "replay_parallelism": self.state["replay_parallelism"],
            "search_parallelism": 1 if self.publication_mode else 2,
            "parameter_names": self.state["parameter_names"],
            "parameter_ranges": self.state["parameter_ranges"],
            "manual_mutation_after_start": self.state[
                "manual_mutation_after_start"
            ],
            "clean_restore_receipt_sha256": (
                self.state["clean_restore_receipt"]["receipt_sha256"]
            ),
            "execution_domain_guard": copy.deepcopy(
                self.state.get("execution_domain_guard") or {}
            ),
            "final_execution_domain_receipts": copy.deepcopy(
                self.state.get("final_execution_domain_receipts") or {}
            ),
            "state_path": str(self.state_path),
            "event_log_path": str(self.log_path),
            "method_metadata": copy.deepcopy(dict(method_metadata)),
            "publication_mode": self.publication_mode,
            "publication_protocol": (
                PUBLICATION_PROTOCOL if self.publication_mode else None
            ),
            "search_objective": (
                "same_worker_qps_ratio" if self.publication_mode else "raw_db_qps"
            ),
            "worker_baseline_qps": copy.deepcopy(
                self.state.get("worker_baseline_qps") or {}
            ),
            "method_input_contract": copy.deepcopy(self.method_input_contract),
            "baseline_repetitions_per_worker": BASELINE_REPETITIONS,
            "post_baseline_repetitions_per_worker": (
                POST_BASELINE_REPETITIONS if self.publication_mode else 0
            ),
            "champion_repetitions_per_worker": (
                CHAMPION_REPETITIONS if self.publication_mode else 1
            ),
        }
        authority["authority_sha256"] = canonical_sha256(authority)
        write_json(self.output_dir / "authority_manifest.json", authority)
        self.log("external_baseline_complete", authority=authority)


class GPTunerPolicy:
    """Common-domain GPTuner adaptation with auditable knowledge provenance."""

    def __init__(
        self,
        harness: ReplayHarness,
        official_root: Path,
        knowledge_snapshot: Optional[Path] = None,
        expected_llm_model: str = GPTUNER_PAPER_MODEL,
        expected_llm_provider: str = "openai-api",
        allow_model_substitution: bool = False,
    ) -> None:
        from ConfigSpace import Categorical, Configuration, ConfigurationSpace, Float

        # SMAC 2.0.2 still calls the pre-1.0 ConfigSpace boolean validator.
        # Keep the experiment environment pinned at its existing versions and
        # supply the removed compatibility method locally.
        if not hasattr(Configuration, "is_valid_configuration"):
            def is_valid_configuration(configuration: Any) -> bool:
                try:
                    configuration.check_valid_configuration()
                except ValueError:
                    return False
                return True

            setattr(
                Configuration,
                "is_valid_configuration",
                is_valid_configuration,
            )
        from smac import HyperparameterOptimizationFacade, Scenario
        from smac.initial_design import LatinHypercubeInitialDesign
        from smac.runhistory.dataclasses import TrialInfo, TrialValue

        self.harness = harness
        self.official_root = official_root
        self.TrialInfo = TrialInfo
        self.TrialValue = TrialValue
        self.pending: Dict[str, Tuple[Any, Any]] = {}
        self.coarse_space = ConfigurationSpace(seed=harness.seed)
        self.expected_llm_model = expected_llm_model
        self.expected_llm_provider = expected_llm_provider
        self.allow_model_substitution = allow_model_substitution
        if (
            getattr(harness, "publication_mode", False)
            and expected_llm_model != GPTUNER_PAPER_MODEL
            and not allow_model_substitution
        ):
            raise RuntimeError("non-paper GPTuner model requires explicit substitution opt-in")
        self.knowledge_snapshot = (
            knowledge_snapshot.resolve() if knowledge_snapshot is not None else None
        )
        self.knowledge_manifest: Dict[str, Any] = {}
        if self.knowledge_snapshot is not None:
            manifest_path = self.knowledge_snapshot / "manifest.json"
            self.knowledge_manifest = read_json(manifest_path)
            required_manifest = {
                "contract": "gptuner_online_knowledge_snapshot_v1",
                "status": "complete",
                "model": self.expected_llm_model,
                "provider": self.expected_llm_provider,
                "dbms": "postgres",
                "scene": harness.scene,
                "active_params": list(ACTIVE_PARAMS),
            }
            for key, expected in required_manifest.items():
                if self.knowledge_manifest.get(key) != expected:
                    raise RuntimeError(
                        f"GPTuner online knowledge manifest mismatch for {key}: "
                        f"expected {expected!r}, got {self.knowledge_manifest.get(key)!r}"
                    )
            if self.expected_llm_model != GPTUNER_PAPER_MODEL:
                if not self.allow_model_substitution:
                    raise RuntimeError("GPTuner model substitution is not authorized")
                if self.knowledge_manifest.get("paper_model_equivalent") is not False:
                    raise RuntimeError("substituted GPTuner snapshot claims paper equivalence")
            stages = self.knowledge_manifest.get("stages") or {}
            required_stages = {
                "llm_source_extraction": True,
                "manual_source_summarization": True,
                "official_constraint_filter": True,
                "cross_source_contradiction_resolution": True,
                "default_range_debias": True,
                "factual_consistency_check": True,
                "prompt_ensemble_votes": 5,
                "hardware_aware_transformation": True,
                "special_value_classification": True,
            }
            for key, expected in required_stages.items():
                if stages.get(key) != expected:
                    raise RuntimeError(
                        f"GPTuner knowledge stage {key} is incomplete: "
                        f"expected {expected!r}, got {stages.get(key)!r}"
                    )
            knowledge_dir = self.knowledge_snapshot / "structured_knowledge/normal"
        else:
            if getattr(harness, "publication_mode", False):
                raise RuntimeError(
                    "publication GPTuner requires --gptuner-knowledge-snapshot "
                    "generated online with model gpt-4"
                )
            knowledge_dir = (
                official_root
                / "knowledge_collection/postgres/structured_knowledge/normal"
            )
        knowledge_summary: Dict[str, Any] = {}
        fine_bounds: Dict[str, Tuple[float, float]] = {}
        for name in ACTIVE_PARAMS:
            low, high = DEFAULT_RANGES[name]
            default = harness.defaults[name]
            path = knowledge_dir / f"{name}.json"
            data = read_json(path) if path.is_file() else {}
            suggested: List[float] = []
            for value in data.get("suggested_values") or []:
                numeric = optional_finite_float(value)
                if numeric is None:
                    continue
                if low <= numeric <= high:
                    suggested.append(numeric)
            knowledge_min = optional_finite_float(data.get("min_value"))
            knowledge_max = optional_finite_float(data.get("max_value"))
            refined_low = low if knowledge_min is None else max(low, knowledge_min)
            refined_high = high if knowledge_max is None else min(high, knowledge_max)
            refined_low = min(refined_low, default)
            refined_high = max(refined_high, default)
            if refined_low > refined_high:
                raise RuntimeError(f"invalid GPTuner refined bounds for {name}")
            fine_bounds[name] = (refined_low, refined_high)
            suggested = [
                value for value in suggested if refined_low <= value <= refined_high
            ]
            sequence = {default}
            for anchor in suggested:
                sequence.update(
                    {
                        anchor,
                        anchor + 0.25 * (refined_low - anchor),
                        anchor + 0.25 * (refined_high - anchor),
                        anchor + 0.50 * (refined_low - anchor),
                        anchor + 0.50 * (refined_high - anchor),
                    }
                )
            if not suggested and (
                refined_low > low or refined_high < high
            ):
                sequence.update(
                    {
                        refined_low,
                        refined_high,
                        default + 0.25 * (refined_high - default),
                        default + 0.50 * (refined_high - default),
                        default + 0.75 * (refined_high - default),
                    }
                )
            if len(sequence) < 2:
                # A one-value categorical dimension breaks SMAC. This fallback
                # is disclosed and stays within the predeclared common domain.
                sequence.update({low, high})
            values = sorted(
                {
                    float(min(refined_high, max(refined_low, value)))
                    for value in sequence
                }
            )
            self.coarse_space.add(
                Categorical(name, items=values, default=default)
            )
            knowledge_summary[name] = {
                "source": str(path),
                "suggested_values": suggested,
                "knowledge_min_value": knowledge_min,
                "knowledge_max_value": knowledge_max,
                "refined_bounds": [refined_low, refined_high],
                "coarse_values": values,
            }
        self.fine_space = ConfigurationSpace(seed=harness.seed)
        for name in ACTIVE_PARAMS:
            low, high = fine_bounds[name]
            self.fine_space.add(
                Float(
                    name,
                    bounds=(low, high),
                    default=harness.defaults[name],
                    # The public GPTuner artifact uses a linear uniform fine
                    # space. The old port silently changed this to log-uniform.
                    log=False,
                )
            )
        self.knowledge_summary = knowledge_summary

        def unused_target(config: Any, seed: int = 0) -> float:
            _ = config, seed
            return 0.0

        valid_history = [
            observation
            for observation in harness.state["observations"]
            if observation.get("valid") is True
        ]
        coarse_valid_history = [
            observation
            for observation in valid_history
            if str(observation.get("stage") or "") == "coarse"
        ]
        self.resume_rehydrated_valid_observations = len(valid_history)
        self.resume_rehydrated_coarse_observations = len(coarse_valid_history)

        scratch = Path(
            tempfile.mkdtemp(prefix=f"pg_{harness.scene}_gptuner_smac_")
        )
        coarse_scenario = Scenario(
            self.coarse_space,
            name=str(scratch / "coarse"),
            seed=harness.seed,
            deterministic=True,
            n_trials=GPTUNER_COARSE_TRIALS,
            use_default_config=True,
        )
        initial = LatinHypercubeInitialDesign(
            coarse_scenario,
            n_configs=6,
            max_ratio=0.8,
        )
        self.coarse_smac = HyperparameterOptimizationFacade(
            coarse_scenario,
            unused_target,
            initial_design=initial,
            overwrite=True,
            logging_level=False,
        )
        fine_scenario = Scenario(
            self.fine_space,
            name=str(scratch / "fine"),
            seed=harness.seed,
            deterministic=True,
            n_trials=harness.candidate_budget,
            use_default_config=True,
        )
        self.fine_smac = HyperparameterOptimizationFacade(
            fine_scenario,
            unused_target,
            overwrite=True,
            logging_level=False,
        )
        self._rehydrate()
        self._prime_rehydrated_intensifier_queue(self.coarse_smac)
        self._prime_rehydrated_intensifier_queue(self.fine_smac)

    def _rehydrate(self) -> None:
        from ConfigSpace import Configuration

        for observation in self.harness.state["observations"]:
            if (
                observation.get("valid") is not True
                and observation.get("model_failure") is not True
            ):
                continue
            stage = str(observation.get("stage") or "")
            targets = [(self.fine_space, self.fine_smac)]
            if stage == "coarse":
                targets.insert(0, (self.coarse_space, self.coarse_smac))
            for space, smac in targets:
                values = {
                    name: float(observation["params"][name])
                    for name in ACTIVE_PARAMS
                }
                config = Configuration(space, values=values)
                info = self.TrialInfo(config=config, seed=self.harness.seed)
                smac.tell(
                    info,
                    self.TrialValue(
                        cost=float(observation["cost"]),
                        time=float(observation.get("elapsed_s") or 0.0),
                    ),
                )

    @staticmethod
    def _prime_rehydrated_intensifier_queue(smac: Any) -> None:
        """Resume SMAC without bulk-enqueuing every historical configuration."""
        if smac.runhistory.finished <= 0:
            return
        incumbents = smac.intensifier.get_incumbents()
        if not incumbents:
            return
        incumbent_id = smac.runhistory.get_config_id(incumbents[0])
        # Intensifier.__iter__ bulk-enqueues all runhistory configs when its
        # queue is empty.  With a resumed history longer than its retry limit,
        # it can stop before requesting a new acquisition candidate.  An
        # incumbent queue entry is the official serialized-state shape; it is
        # removed immediately as already incumbent, then normal generation
        # continues from the rehydrated surrogate history.
        smac.intensifier.set_state({"queue": [(incumbent_id, 1)]})

    def suggest(self, count: int) -> List[Dict[str, Any]]:
        used = {
            str(item.get("param_signature"))
            for item in self.harness.state["observations"]
        }
        stage = (
            "coarse"
            if len(self.harness.state["observations"]) < GPTUNER_COARSE_TRIALS
            else "fine"
        )
        if stage == "coarse":
            count = min(
                count,
                GPTUNER_COARSE_TRIALS
                - len(self.harness.state["observations"]),
            )
        smac = self.coarse_smac if stage == "coarse" else self.fine_smac
        suggestions: List[Dict[str, Any]] = []
        attempts = 0
        while len(suggestions) < count and attempts < 100:
            attempts += 1
            try:
                info = smac.ask()
            except StopIteration as exc:
                raise RuntimeError(
                    "GPTuner SMAC exhausted its internal scheduling allowance "
                    f"with {len(self.harness.state['observations'])}/"
                    f"{self.harness.candidate_budget} physical candidates"
                ) from exc
            params = {name: float(info.config[name]) for name in ACTIVE_PARAMS}
            signature = exact_signature(params)
            if signature in used or signature in self.pending:
                # Keep the duplicate pending but do not add it to runhistory:
                # exact duplicates consume neither physical nor valid budget.
                continue
            trial = len(self.harness.state["observations"]) + len(suggestions) + 1
            self.pending[signature] = (smac, info)
            suggestions.append(
                {
                    "trial": trial,
                    "stage": stage,
                    "params": params,
                    "policy": "gptuner_official_coarse_to_fine_smac_v1",
                }
            )
        if len(suggestions) != count:
            raise RuntimeError("GPTuner could not produce distinct suggestions")
        return suggestions

    def observe(self, suggestion: Mapping[str, Any], observation: Mapping[str, Any]) -> None:
        signature = exact_signature(suggestion["params"])
        smac, info = self.pending.pop(signature)
        smac.tell(
            info,
            self.TrialValue(
                cost=float(observation["cost"]),
                time=float(observation.get("elapsed_s") or 0.0),
            ),
        )

    def metadata(self) -> Dict[str, Any]:
        commit = git_commit(self.official_root)
        online_refresh = self.knowledge_snapshot is not None
        return {
            "contract": "gptuner_common7_coarse_to_fine_smac_v2",
            "official_repository": "https://github.com/SolidLao/GPTuner",
            "official_commit": commit,
            "coarse_trials": GPTUNER_COARSE_TRIALS,
            "fine_total_trials_including_coarse_history": (
                self.harness.candidate_budget
            ),
            "resume_rehydrated_valid_observations": (
                self.resume_rehydrated_valid_observations
            ),
            "smac_resume_intensifier_queue_primed_from_incumbent": (
                self.resume_rehydrated_valid_observations > 0
            ),
            "physical_candidate_budget_unchanged": True,
            "knowledge_summary": self.knowledge_summary,
            "llm_refresh_skipped": not online_refresh,
            "online_llm_refresh": online_refresh,
            "llm_model": (
                self.knowledge_manifest.get("model") if online_refresh else None
            ),
            "llm_provider": self.knowledge_manifest.get("provider"),
            "paper_model": self.knowledge_manifest.get("paper_model"),
            "paper_model_equivalent": self.knowledge_manifest.get(
                "paper_model_equivalent"
            ),
            "model_substitution_explicit": self.allow_model_substitution,
            "llm_call_count": self.knowledge_manifest.get("llm_call_count"),
            "llm_prompt_tokens": self.knowledge_manifest.get("prompt_tokens"),
            "llm_completion_tokens": self.knowledge_manifest.get(
                "completion_tokens"
            ),
            "knowledge_snapshot": (
                str(self.knowledge_snapshot) if online_refresh else None
            ),
            "knowledge_manifest_sha256": (
                hashlib.sha256(
                    (self.knowledge_snapshot / "manifest.json").read_bytes()
                ).hexdigest()
                if online_refresh
                else None
            ),
            "region_discard_from_structured_min_max": online_refresh,
            "tiny_feasible_space_from_suggested_values": True,
            "fine_space_distribution": "linear_uniform",
            "coarse_history_bootstraps_fine_model": True,
            "knob_selection_mode": "fixed_common_seven_parameter_domain",
            "knob_selection_adaptation_reason": (
                "All compared methods must optimize the same predeclared seven "
                "PostgreSQL planner-cost parameters; LLM dimensionality selection "
                "is therefore intentionally disabled and disclosed."
            ),
            "special_value_policy": (
                "Special values are classified and audited in the online snapshot "
                "but excluded when outside the shared positive numeric domain."
            ),
            "budget_adaptation": {
                "paper_artifact_coarse_trials": 30,
                "paper_artifact_total_trials": 110,
                "comparison_coarse_trials": GPTUNER_COARSE_TRIALS,
                "comparison_total_trials": self.harness.candidate_budget,
                "reason": "equal 35-configuration budget for every compared method",
            },
            "domain_adaptation_disclosed": True,
        }


def pg_node_type(node_type: str) -> str:
    text = node_type.lower()
    if "join" in text or text == "nested loop":
        return "Join"
    if "scan" in text:
        return "TableScan"
    if "aggregate" in text or text in {"group", "groupaggregate"}:
        return "Aggregating"
    if "sort" in text:
        return "Sorting"
    if "window" in text:
        return "Window"
    if "limit" in text:
        return "Limit"
    if text in {"append", "merge append", "setop"}:
        return "Union"
    if text in {"gather", "gather merge"}:
        return "Exchange"
    if text in {"materialize", "memoize"}:
        return "Buffer"
    if "values" in text:
        return "Values"
    if text in {"result", "projectset"}:
        return "Projection"
    return "Filter"


def aggregate_postgres_plans(
    explained: Sequence[Mapping[str, Any]],
    *,
    max_nodes: int = 200,
) -> Dict[str, Any]:
    plan_roots = [
        item.get("plan_root")
        for item in explained
        if isinstance(item.get("plan_root"), Mapping)
    ]
    if len(plan_roots) > max_nodes - 1:
        raise RuntimeError(
            "AQETuner aggregate plan cannot represent every query root: "
            f"queries={len(plan_roots)}, available_nodes={max_nodes - 1}"
        )
    next_id = 0

    def convert(node: Mapping[str, Any], budget: List[int]) -> Optional[Dict[str, Any]]:
        nonlocal next_id
        if next_id >= max_nodes or budget[0] <= 0:
            return None
        budget[0] -= 1
        node_id = next_id
        next_id += 1
        result: Dict[str, Any] = {
            "NodeId": node_id,
            "NodeType": pg_node_type(str(node.get("Node Type") or "")),
            "Statistic": {"RowCount": float(node.get("Plan Rows") or 0.0)},
            "Children": [],
        }
        for child in node.get("Plans") or []:
            if not isinstance(child, Mapping):
                continue
            converted = convert(child, budget)
            if converted is not None:
                result["Children"].append(converted)
        return result

    root: Dict[str, Any] = {
        "NodeId": next_id,
        "NodeType": "Projection",
        "Statistic": {"RowCount": 0.0},
        "Children": [],
    }
    next_id += 1
    if not plan_roots:
        return root
    available = max_nodes - 1
    base_budget, remainder = divmod(available, len(plan_roots))
    represented = 0
    for index, plan_root in enumerate(plan_roots):
        query_budget = [base_budget + (1 if index < remainder else 0)]
        child = convert(plan_root, query_budget)
        if child is not None:
            root["Children"].append(child)
            represented += 1
    if represented != len(plan_roots):
        raise RuntimeError(
            "AQETuner aggregate plan silently dropped query plans: "
            f"represented={represented}, expected={len(plan_roots)}"
        )
    return root


class AQETunerPolicy:
    """PostgreSQL port of the public AQETuner artifact."""

    def __init__(self, harness: ReplayHarness, official_root: Path) -> None:
        self.harness = harness
        self.official_root = official_root
        if str(official_root) not in sys.path:
            sys.path.insert(0, str(official_root))
        os.environ.setdefault("DB", f"pg_{harness.scene}")
        import torch as torch_module

        self.torch = torch_module
        self.device = "cpu"
        self.plan_path = harness.output_dir / "aqetuner_postgres_plan.json"
        self.checkpoint_path = harness.output_dir / "aqetuner_model.tar"
        self.sample_path = harness.output_dir / "aqetuner_samples.tsv"
        self.plan = self._load_or_capture_plan()
        self.rng = np.random.default_rng(harness.seed)
        self._initialize_pso()
        self._rehydrate_pso()
        self.model: Any = None
        self.context_pairs: List[Any] = []
        self.context_labels: List[List[float]] = []

    def _load_or_capture_plan(self) -> Dict[str, Any]:
        if self.plan_path.is_file():
            return read_json(self.plan_path)
        probe = PlanFeatureProbe(self.harness.cfg)
        queries, sampling = workload_queries_with_sampling(self.harness.cfg, probe)
        explained = probe.estimator._collect_query_plans(  # pylint: disable=protected-access
            queries,
            explain_workers=probe.explain_workers,
            params=self.harness.defaults,
        )
        errors = [item for item in explained if item.get("error")]
        if errors:
            raise RuntimeError(
                f"AQETuner PostgreSQL plan capture incomplete: {len(errors)} errors"
            )
        plan = aggregate_postgres_plans(explained)
        write_json(self.plan_path, plan)
        write_json(
            self.harness.output_dir / "aqetuner_plan_capture.json",
            {
                "contract": "aqetuner_postgresql_full_workload_plan_adapter_v2",
                "query_count": len(queries),
                "explained_query_count": len(explained),
                "represented_query_count": len(plan.get("Children") or []),
                "all_query_roots_represented": (
                    len(plan.get("Children") or []) == len(explained)
                ),
                "sampling": sampling,
                "aggregate_plan_sha256": canonical_sha256(plan),
                "maximum_nodes": 200,
                "node_budget_policy": (
                    "equal_per_query_root_then_depth_first_within_each_query"
                ),
            },
        )
        return plan

    def _initialize_pso(self) -> None:
        sampler = qmc.Sobol(d=len(ACTIVE_PARAMS), scramble=True, seed=self.harness.seed)
        self.pso_positions = sampler.random_base2(m=2)[:3]
        self.pso_velocities = self.rng.uniform(
            -0.1,
            0.1,
            size=self.pso_positions.shape,
        )
        self.pso_local_best_positions = self.pso_positions.copy()
        self.pso_local_best_scores = np.full(3, np.inf)
        self.pso_global_best_position = self.pso_positions[0].copy()
        self.pso_global_best_score = float("inf")

    @staticmethod
    def _observation_latency(observation: Mapping[str, Any]) -> float:
        if observation.get("valid") is not True:
            return 100.0
        objective_score = float(observation.get("objective_score") or 0.0)
        if objective_score > 0.0:
            return 1.0 / objective_score
        qps = float(observation.get("db_qps") or 0.0)
        return 1.0 / qps if qps > 0.0 else 100.0

    def _update_pso(
        self,
        particle_id: int,
        values: np.ndarray,
        observation: Mapping[str, Any],
    ) -> None:
        if observation.get("valid") is not True:
            # Failed physical configurations are not allowed to pull the swarm.
            self.pso_velocities[particle_id] = self.rng.uniform(
                -0.1,
                0.1,
                size=len(ACTIVE_PARAMS),
            )
            self.pso_positions[particle_id] = self.rng.random(len(ACTIVE_PARAMS))
            return
        score = self._observation_latency(observation)
        if score < self.pso_local_best_scores[particle_id]:
            self.pso_local_best_scores[particle_id] = score
            self.pso_local_best_positions[particle_id] = values.copy()
        if score < self.pso_global_best_score:
            self.pso_global_best_score = score
            self.pso_global_best_position = values.copy()
        velocity = self.pso_velocities[particle_id]
        velocity = (
            velocity
            + 0.5
            * float(self.rng.random())
            * (self.pso_local_best_positions[particle_id] - values)
            + 0.5
            * float(self.rng.random())
            * (self.pso_global_best_position - values)
        )
        self.pso_velocities[particle_id] = velocity
        self.pso_positions[particle_id] = np.clip(values + velocity, 0.0, 1.0)

    def _rehydrate_pso(self) -> None:
        warm = [
            observation
            for observation in self.harness.state.get("observations") or []
            if str(observation.get("stage") or "") == "pso_warm_start"
        ]
        for observation in warm:
            trial = int(observation.get("trial") or 0)
            particle_id = max(0, trial - 1) % 3
            values = np.asarray(
                normalized_vector(observation.get("params") or {}),
                dtype=float,
            )
            self._update_pso(particle_id, values, observation)

    def _write_samples_and_train(self) -> None:
        observations = self.harness.state["observations"]
        warm = [
            observation
            for observation in observations[:AQETUNER_WARM_SAMPLES]
            if observation.get("valid") is True
            or observation.get("model_failure") is True
        ]
        if len(warm) < 4:
            raise RuntimeError(
                "AQETuner requires at least four valid warm-start samples; "
                "technical failures are excluded from training"
            )
        if len(observations) < AQETUNER_WARM_SAMPLES:
            return
        with self.sample_path.open("w", encoding="utf-8") as handle:
            for observation in warm:
                qps = float(observation.get("db_qps") or 0.0)
                fail = 0 if observation.get("valid") is True and qps > 0.0 else 1
                latency = self._observation_latency(observation)
                handle.write(json.dumps({"plan": self.plan}) + "\t")
                handle.write(json.dumps({"plan": self.plan}) + "\t")
                handle.write(
                    json.dumps(normalized_vector(observation["params"])) + "\t"
                )
                handle.write(json.dumps({"elapsed": latency, "fail": fail}) + "\n")
        if not self.checkpoint_path.is_file():
            import train as aqe_train

            random.seed(self.harness.seed)
            np.random.seed(self.harness.seed)
            self.torch.manual_seed(self.harness.seed)
            aqe_train.main(
                str(self.sample_path),
                str(self.checkpoint_path),
                epochs=5,
                lr=0.0005,
            )
        self._load_model_context()

    def _load_model_context(self) -> None:
        if self.model is not None:
            return
        from network import LatentModel
        from pair_encoder import TPair

        checkpoint = self.torch.load(
            self.checkpoint_path,
            map_location=self.device,
            weights_only=False,
        )
        model = LatentModel(32).to(self.device)
        model.load_state_dict(checkpoint["model"])
        model.train(False)
        self.model = model
        for observation in self.harness.state["observations"]:
            if (
                observation.get("valid") is not True
                and observation.get("model_failure") is not True
            ):
                continue
            qps = float(observation.get("db_qps") or 0.0)
            fail = 0 if observation.get("valid") is True and qps > 0.0 else 1
            latency = self._observation_latency(observation)
            self.context_pairs.append(
                TPair(
                    copy.deepcopy(self.plan),
                    list(normalized_vector(observation["params"])),
                )
            )
            self.context_labels.append([latency, float(fail)])

    def suggest(self, count: int) -> List[Dict[str, Any]]:
        completed = len(self.harness.state["observations"])
        used = {
            str(item.get("param_signature"))
            for item in self.harness.state["observations"]
        }
        if completed < AQETUNER_WARM_SAMPLES:
            count = min(count, AQETUNER_WARM_SAMPLES - completed)
            suggestions = []
            for offset in range(count):
                particle_id = (completed + offset) % 3
                values = self.pso_positions[particle_id].tolist()
                params = denormalized_params(values)
                suggestions.append(
                    {
                        "trial": completed + offset + 1,
                        "stage": "pso_warm_start",
                        "params": params,
                        "policy": "aqetuner_feedback_pso_warm_start_v2",
                        "pso_particle_id": particle_id,
                        "pso_feedback_applied": completed + offset >= 3,
                    }
                )
            return suggestions
        self._write_samples_and_train()
        from pair_encoder import TPair

        sampler = qmc.Sobol(
            d=len(ACTIVE_PARAMS),
            scramble=True,
            seed=self.harness.seed + completed,
        )
        values = sampler.random_base2(m=10)
        candidate_pairs = [
            TPair(copy.deepcopy(self.plan), list(row)) for row in values
        ]
        context_y = self.torch.tensor(
            self.context_labels,
            dtype=self.torch.float32,
            device=self.device,
        )
        with self.torch.no_grad():
            latency_dist, failure_dist, _loss = self.model(
                self.context_pairs,
                context_y,
                candidate_pairs,
            )
        mu = latency_dist.mean.detach().cpu().numpy().reshape(-1)
        failure_probability = (
            failure_dist.mean.detach().cpu().numpy().reshape(-1)
        )
        # Match the public tune.py artifact: reject candidates whose predicted
        # failure probability exceeds 0.5, then minimize predicted latency.
        # The previous port used EIC even though the artifact defines but does
        # not call its eic() helper.
        feasible_latency = mu.copy()
        feasible_latency[failure_probability > 0.5] = 1000.0
        order = np.argsort(feasible_latency)
        suggestions: List[Dict[str, Any]] = []
        for index in order:
            params = denormalized_params(values[int(index)])
            signature = exact_signature(params)
            if signature in used:
                continue
            used.add(signature)
            suggestions.append(
                {
                    "trial": completed + len(suggestions) + 1,
                    "stage": "dual_task_failure_threshold",
                    "params": params,
                    "policy": "aqetuner_public_failure_threshold_v2",
                    "predicted_latency": float(mu[int(index)]),
                    "predicted_failure_probability": float(
                        failure_probability[int(index)]
                    ),
                    "failure_probability_threshold": 0.5,
                    "acquisition_value": float(feasible_latency[int(index)]),
                }
            )
            if len(suggestions) >= count:
                break
        if len(suggestions) != count:
            raise RuntimeError(
                "AQETuner could not produce distinct failure-filtered suggestions"
            )
        return suggestions

    def observe(self, suggestion: Mapping[str, Any], observation: Mapping[str, Any]) -> None:
        if str(suggestion.get("stage") or "") == "pso_warm_start":
            particle_id = int(suggestion.get("pso_particle_id") or 0)
            values = np.asarray(normalized_vector(observation["params"]), dtype=float)
            self._update_pso(particle_id, values, observation)
            return
        if self.model is None:
            return
        if (
            observation.get("valid") is not True
            and observation.get("model_failure") is not True
        ):
            return
        from pair_encoder import TPair

        qps = float(observation.get("db_qps") or 0.0)
        fail = 0 if observation.get("valid") is True and qps > 0.0 else 1
        latency = self._observation_latency(observation)
        self.context_pairs.append(
            TPair(
                copy.deepcopy(self.plan),
                list(normalized_vector(observation["params"])),
            )
        )
        self.context_labels.append([latency, float(fail)])

    def metadata(self) -> Dict[str, Any]:
        commit = git_commit(self.official_root)
        plan_capture = read_json(
            self.harness.output_dir / "aqetuner_plan_capture.json"
        )
        return {
            "contract": "aqetuner_public_artifact_postgresql_global_port_v2",
            "official_repository": (
                "https://gitfront.io/r/ml4tuning/aoR8ReCrj4a7/aqetuner/"
            ),
            "official_commit": commit,
            "port_scope": (
                "global seven PostgreSQL planner-cost knobs on one complete "
                f"{self.harness.scene} workload observation"
            ),
            "warm_start_samples": AQETUNER_WARM_SAMPLES,
            "warm_start": "three-particle feedback-updated PSO",
            "pso_feedback_applied_after_each_observation": True,
            "pso_configuration_failure_resampling": True,
            "plan_encoder": "public artifact knob-plan attention encoder",
            "predictor": "public artifact dual-task Neural Process",
            "acquisition": (
                "minimum_predicted_latency_after_failure_probability_0.5_filter"
            ),
            "aggregate_plan_sha256": canonical_sha256(self.plan),
            "plan_capture": plan_capture,
            "model_epochs": 5,
            "model_learning_rate": 0.0005,
            "training_seed": self.harness.seed,
            "candidate_samples_per_iteration": 1024,
            "sequential_feedback": getattr(self.harness, "publication_mode", False),
            "knob_domain": list(ACTIVE_PARAMS),
            "candidate_budget": self.harness.candidate_budget,
            "global_port_limitation": (
                "The public AQETuner artifact tunes one configuration per query. "
                "For equal expressiveness with the evaluated global PostgreSQL "
                "tuners, this port predicts one global configuration from an "
                "all-query composite plan; every query root is represented."
            ),
            "domain_adaptation_disclosed": True,
        }


def run_search(harness: ReplayHarness, policy: Any) -> None:
    while (
        harness.state["candidate_physical_invocations"]
        < harness.candidate_budget
    ):
        if harness.stop_requested:
            return
        remaining = (
            harness.candidate_budget
            - harness.state["candidate_physical_invocations"]
        )
        request_count = 1 if harness.publication_mode else min(2, remaining)
        suggestions = policy.suggest(request_count)
        for infrastructure_attempt in range(
            1, CANDIDATE_INFRASTRUCTURE_MAX_ATTEMPTS + 1
        ):
            observation_count_before = len(harness.state["observations"])
            rows = harness.replay_wave(
                suggestions,
                phase=str(suggestions[0]["stage"]),
            )
            if harness.stop_requested:
                return
            if len(rows) != len(suggestions):
                raise RuntimeError(
                    "external tuner physical wave returned incomplete rows"
                )
            new_observations = harness.state["observations"][
                observation_count_before:
            ]
            if harness.publication_mode and not new_observations:
                retrying = (
                    infrastructure_attempt
                    < CANDIDATE_INFRASTRUCTURE_MAX_ATTEMPTS
                )
                harness.log(
                    "external_candidate_infrastructure_retry",
                    labels=[str(row.get("label") or "") for row in rows],
                    attempt=infrastructure_attempt,
                    attempt_limit=CANDIDATE_INFRASTRUCTURE_MAX_ATTEMPTS,
                    retrying=retrying,
                    candidate_physical_invocations=harness.state[
                        "candidate_physical_invocations"
                    ],
                    tuner_feedback_applied=False,
                )
                if retrying:
                    continue
                raise RuntimeError(
                    "candidate infrastructure retry budget exhausted without "
                    "consuming the formal candidate budget"
                )
            if len(new_observations) != len(suggestions):
                raise RuntimeError(
                    "external tuner observation commit count mismatch"
                )
            for suggestion, observation in zip(suggestions, new_observations):
                policy.observe(suggestion, observation)
            break


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--method", required=True, choices=("gptuner", "aqetuner"))
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--scene", default="none")
    parser.add_argument(
        "--target",
        default="none",
        choices=("memory", "cpu", "io", "none"),
    )
    parser.add_argument(
        "--candidate-budget",
        type=int,
        default=DEFAULT_CANDIDATE_BUDGET,
    )
    parser.add_argument("--seed", type=int, default=20260825)
    parser.add_argument(
        "--gptuner-root",
        type=Path,
        default=ROOT / "baselines/GPTuner_official",
    )
    parser.add_argument(
        "--aqetuner-root",
        type=Path,
        default=ROOT / "baselines/AQETuner_official",
    )
    parser.add_argument(
        "--publication-mode",
        action="store_true",
        help=(
            "Enable sequential feedback, same-worker baseline normalization, "
            "and fail-closed method provenance required for paper results"
        ),
    )
    parser.add_argument(
        "--gptuner-knowledge-snapshot",
        type=Path,
        default=None,
        help="Scene-specific online GPT-4 knowledge snapshot",
    )
    parser.add_argument(
        "--gptuner-expected-llm-model",
        default=GPTUNER_PAPER_MODEL,
    )
    parser.add_argument(
        "--gptuner-expected-llm-provider",
        default="openai-api",
    )
    parser.add_argument(
        "--allow-gptuner-model-substitution",
        action="store_true",
        help="Run a labelled non-paper-model GPTuner sensitivity variant.",
    )
    parser.add_argument(
        "--import-completed-search-state",
        type=Path,
        default=None,
        help=(
            "Import an audited completed search into a fresh campaign; only "
            "the fresh baseline and frozen champion are physically replayed"
        ),
    )
    return parser.parse_args()


def fail_closed_after_exception(harness: ReplayHarness, exc: Exception) -> None:
    """Persist the original error and restore the formal baseline if possible."""
    harness.state["last_error"] = {
        "type": type(exc).__name__,
        "message": str(exc),
        "time": time.time(),
    }
    try:
        harness.restore_clean_baseline(reason="external_baseline_exception")
        harness.state["phase"] = "failed_closed_clean"
    except Exception as cleanup_exc:
        harness.state["phase"] = "failed_closed_cleanup_error"
        harness.state["cleanup_error"] = {
            "type": type(cleanup_exc).__name__,
            "message": str(cleanup_exc),
            "time": time.time(),
        }
    harness.save()


def main() -> int:
    args = parse_args()
    if args.candidate_budget < AQETUNER_WARM_SAMPLES:
        raise ValueError(
            f"candidate budget must be at least {AQETUNER_WARM_SAMPLES}"
        )
    if args.publication_mode and args.import_completed_search_state is not None:
        raise ValueError(
            "publication mode forbids importing a search generated under an older method contract"
        )
    if (
        args.gptuner_expected_llm_model != GPTUNER_PAPER_MODEL
        and not args.allow_gptuner_model_substitution
    ):
        raise ValueError(
            "non-paper GPTuner model requires --allow-gptuner-model-substitution"
        )
    method_input_contract: Dict[str, Any] = {
        "method": args.method,
        "candidate_budget": args.candidate_budget,
        "sequential_feedback": args.publication_mode,
        "common_parameter_domain": list(ACTIVE_PARAMS),
    }
    if args.method == "gptuner":
        if args.publication_mode and args.gptuner_knowledge_snapshot is None:
            raise ValueError(
                "publication GPTuner requires --gptuner-knowledge-snapshot"
            )
        if args.gptuner_knowledge_snapshot is not None:
            manifest_path = args.gptuner_knowledge_snapshot.resolve() / "manifest.json"
            method_input_contract.update(
                {
                    "knowledge_manifest_path": str(manifest_path),
                    "knowledge_manifest_sha256": hashlib.sha256(
                        manifest_path.read_bytes()
                    ).hexdigest(),
                    "required_llm_model": args.gptuner_expected_llm_model,
                    "required_llm_provider": args.gptuner_expected_llm_provider,
                    "paper_llm_model": GPTUNER_PAPER_MODEL,
                    "model_substitution_explicit": (
                        args.allow_gptuner_model_substitution
                    ),
                }
            )
    else:
        method_input_contract.update(
            {
                "warm_start": "feedback_updated_three_particle_pso",
                "predictor": "dual_task_neural_process",
                "acquisition": "failure_thresholded_predicted_latency",
            }
        )
    harness = ReplayHarness(
        args.config,
        args.output_dir,
        args.method,
        args.candidate_budget,
        args.seed,
        args.scene,
        args.target,
        publication_mode=args.publication_mode,
        method_input_contract=method_input_contract,
    )
    imported_method_metadata: Optional[Dict[str, Any]] = None
    if args.import_completed_search_state is not None:
        imported_method_metadata = harness.import_completed_search_state(
            args.import_completed_search_state
        )
    harness.repair_observation_trial_ids()

    def request_stop(signum: int, _frame: Any) -> None:
        harness.request_stop(signum)

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, request_stop)
    try:
        harness.recover_inflight_wave()
        harness.ensure_baseline()
        if harness.stop_requested:
            harness.clean_stop()
            return 0
        policy: Optional[Any] = None
        if imported_method_metadata is None:
            if args.method == "gptuner":
                policy = GPTunerPolicy(
                    harness,
                    args.gptuner_root.resolve(),
                    args.gptuner_knowledge_snapshot,
                    args.gptuner_expected_llm_model,
                    args.gptuner_expected_llm_provider,
                    args.allow_gptuner_model_substitution,
                )
            else:
                policy = AQETunerPolicy(harness, args.aqetuner_root.resolve())
            run_search(harness, policy)
        if harness.stop_requested:
            harness.clean_stop()
            return 0
        harness.ensure_post_search_baseline()
        if harness.stop_requested:
            harness.clean_stop()
            return 0
        harness.verify_frozen_champion()
        if harness.stop_requested:
            harness.clean_stop()
            return 0
        method_metadata = (
            imported_method_metadata
            if imported_method_metadata is not None
            else policy.metadata()
        )
        harness.finalize(method_metadata)
        return 0
    except Exception as exc:
        fail_closed_after_exception(harness, exc)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
