#!/usr/bin/env python3
"""Unique production CLI for the formal ten-acceptance MySQL SGD campaign."""

from __future__ import annotations

import argparse
import copy
from contextlib import nullcontext
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

from formal_mysql_sgd_orchestration import (
    FormalPointDependencies,
    FormalPointJournal,
    FormalSGDNotReady,
    build_replay_driver,
)
from mysql_parameter_optimizer import (
    _validate_replay_baseline,
    load_config,
    run_optimization,
)
from host02_job_xp_mutex import Host02MutexError, mutex_from_campaign
from formal_mysql_sgd_checkpoint import (
    FormalProductionR0Ledger,
    FormalReplayCheckpointError,
)
from shell.verify_mysql_sgd_v4_readiness import verify_document


def _require_ready_manifest(path: Path) -> Mapping[str, Any]:
    """Verify the explicitly selected launch authority before loading services."""
    try:
        raw_path = Path(path)
    except TypeError as exc:
        raise FormalSGDNotReady("NOT_READY: readiness manifest must be explicitly provided") from exc
    manifest_path = raw_path.resolve()
    if raw_path.is_symlink() or not manifest_path.is_file():
        raise FormalSGDNotReady(
            f"NOT_READY: explicit readiness manifest is absent or a symlink: {manifest_path}"
        )
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FormalSGDNotReady(
            f"NOT_READY: readiness manifest cannot be read: {manifest_path}: {exc}"
        ) from exc
    if not isinstance(document, Mapping):
        raise FormalSGDNotReady("NOT_READY: readiness manifest root must be an object")
    if document.get("schema_version") == "mysql_xp_outer_authorization_v1":
        if (
            document.get("status") != "SEALED_OUTER_AUTHORIZED"
            or document.get("launch_authorized") is not True
            or document.get("outer_execution_authorized") is not True
            or document.get("algorithm_c_execution_authorized") is not True
            or document.get("accepted_round_authorized") is not False
            or document.get("formal_r0_passed") is not True
            or document.get("strict_registries_authorized") is not True
            or document.get("formal_scope") != "mysql_only"
            or document.get("cross_engine_comparison_prohibited") is not True
            or document.get("bundle_id")
            != os.environ.get("MYSQL_XP_CONTRACT_BUNDLE_ID")
            or hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            != os.environ.get("MYSQL_XP_CONTRACT_OUTER_AUTHORIZATION_SHA256")
            or document.get("config_sha256")
            != os.environ.get("MYSQL_XP_CONTRACT_CONFIG_SHA256")
            or os.environ.get("MYSQL_XP_CONTRACT_MODE") != "formal_outer"
        ):
            raise FormalSGDNotReady(
                "NOT_READY: XP outer authority differs from launch contract"
            )
        return document
    try:
        checks = verify_document(document)
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        raise FormalSGDNotReady(
            f"NOT_READY: readiness verifier rejected {manifest_path}: {exc}"
        ) from exc
    failed = [check for check in checks if not check.passed]
    required_groups = {"AUTH", "SCHEMA", *(f"R{index:02d}" for index in range(1, 12))}
    observed_groups = {check.requirement for check in checks}
    missing_groups = sorted(required_groups - observed_groups)
    if (
        document.get("status") != "READY"
        or document.get("launch_authorized") is not True
        or failed
        or missing_groups
    ):
        detail = "; ".join(
            f"{check.requirement}.{check.item}: {check.reason}" for check in failed[:3]
        )
        if missing_groups:
            detail = "; ".join(filter(None, (
                detail,
                f"verifier requirement groups absent: {','.join(missing_groups)}",
            )))
        suffix = f": {detail}" if detail else ""
        raise FormalSGDNotReady(
            f"NOT_READY: readiness manifest failed {len(failed)} verifier checks{suffix}"
        )
    return document


def _load_factory(spec: str):
    module_name, separator, attribute = str(spec).partition(":")
    if not separator or not module_name or not attribute:
        raise FormalSGDNotReady("NOT_READY: service factory must be module:function")
    factory = getattr(importlib.import_module(module_name), attribute, None)
    if not callable(factory):
        raise FormalSGDNotReady(f"NOT_READY: service factory is not callable: {spec}")
    return factory


def _require_dependencies(dependencies: Any, benchmark: str) -> FormalPointDependencies:
    if not isinstance(dependencies, FormalPointDependencies):
        raise FormalSGDNotReady("NOT_READY: factory did not return FormalPointDependencies")
    required = (
        "lease_factory",
        "template_restore",
        "resource_fixed_readback",
        "cost_apply_fresh_readback",
        "drain_flush",
        "correctness",
        "plan_capture",
        "finalizer",
        "recovery",
        "root_cause_review",
    )
    missing = [name for name in required if not callable(getattr(dependencies, name, None))]
    if not callable(dependencies.session_factories.get(benchmark)):
        missing.append(f"session_factories[{benchmark}]")
    if missing:
        raise FormalSGDNotReady(
            "NOT_READY: missing formal service factories: " + ", ".join(missing)
        )
    return dependencies


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _production_r0_checkpoint_path(config: Mapping[str, Any], benchmark: str) -> Path:
    outer = config.get("formal_outer_protocol") or {}
    explicit = outer.get("formal_real_r0_checkpoint_path") if isinstance(outer, Mapping) else None
    if explicit:
        path = Path(str(explicit))
        if not path.is_absolute():
            raise FormalSGDNotReady(
                "NOT_READY: formal_real_r0_checkpoint_path must be absolute"
            )
        return path
    output = config.get("output") or {}
    directory = Path(str(output.get("directory", "history/mysql"))) if isinstance(output, Mapping) else Path("history/mysql")
    return directory / f"formal_production_real_r0_{benchmark}.json"


def _bind_runtime_estimator_baseline(
    config: Mapping[str, Any],
    baseline_validation: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind plan/ratio prediction scale to this campaign's verified fresh R0."""
    bound = copy.deepcopy(dict(config))
    estimator = bound.get("estimator")
    if not isinstance(estimator, Mapping):
        return bound
    estimator_type = str(estimator.get("type") or "")
    if estimator_type not in {"plan_physics", "ratio_scale"}:
        return bound
    utilization = baseline_validation.get("utilization_pct")
    if not isinstance(utilization, Mapping):
        raise FormalSGDNotReady(
            "NOT_READY: fresh R0 lacks estimator utilization calibration"
        )
    try:
        runtime_baseline = {
            resource: float(utilization[resource])
            for resource in ("cpu", "memory", "io")
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise FormalSGDNotReady(
            "NOT_READY: fresh R0 estimator calibration is incomplete"
        ) from exc
    if any(
        not math.isfinite(value) or value < 0.0
        for value in runtime_baseline.values()
    ):
        raise FormalSGDNotReady(
            "NOT_READY: fresh R0 estimator calibration is non-finite"
        )
    bound_estimator = copy.deepcopy(dict(estimator))
    bound_estimator["baseline_real"] = runtime_baseline
    bound_estimator["baseline_real_authority"] = {
        "source": "verified_fresh_in_band_r0",
        "qps_used": False,
        "resources": ["cpu", "memory", "io"],
    }
    bound["estimator"] = bound_estimator
    return bound


def _production_r0_identity(
    config: Mapping[str, Any], *, benchmark: str, readiness_manifest: Path,
    registration: Mapping[str, Any], service_factory_spec: str,
) -> dict[str, Any]:
    explicit = config.get("formal_r0_identity")
    if explicit is not None:
        if (
            benchmark != "hybench_xp"
            or not isinstance(explicit, Mapping)
            or explicit.get("schema_version")
            != "mysql_xp_formal_r0_identity_v1"
            or explicit.get("formal_scope") != "mysql_only"
            or explicit.get("cross_engine_comparison_prohibited") is not True
            or not isinstance(explicit.get("bundle_id"), str)
            or len(explicit["bundle_id"]) != 64
            or not isinstance(explicit.get("capture_at_seal_sha256"), str)
            or len(explicit["capture_at_seal_sha256"]) != 64
            or not isinstance(explicit.get("capture_trace_sha256"), str)
            or len(explicit["capture_trace_sha256"]) != 64
            or explicit.get("mysql_port") != 65304
        ):
            raise FormalSGDNotReady(
                "NOT_READY: explicit XP formal R0 identity is incomplete"
            )
        return {
            "benchmark": benchmark,
            "formal_r0_identity_sha256": _canonical_sha256(explicit),
            "service_factory_spec": str(service_factory_spec),
        }
    return {
        "benchmark": benchmark,
        "config_sha256": _canonical_sha256(config),
        "readiness_manifest_sha256": hashlib.sha256(
            Path(readiness_manifest).resolve().read_bytes()
        ).hexdigest(),
        "registration_sha256": _canonical_sha256(registration),
        "service_factory_spec": str(service_factory_spec),
    }



def _historical_r0_authority_manifest(path: Path) -> Path:
    """Resolve historical launch evidence used only to verify an existing R0."""
    try:
        raw_path = Path(path)
    except TypeError as exc:
        raise FormalSGDNotReady(
            "NOT_READY: historical R0 authority manifest path is invalid"
        ) from exc
    resolved = raw_path.resolve()
    if raw_path.is_symlink() or not resolved.is_file():
        raise FormalSGDNotReady(
            "NOT_READY: historical R0 authority manifest is absent or a symlink: "
            f"{resolved}"
        )
    return resolved


def run_production_campaign(
    config: Mapping[str, Any],
    *,
    readiness_manifest: Path,
    service_factory_spec: str,
    r0_authority_readiness_manifest: Path | None = None,
    journal_root: Path | None = None,
    resume_command_prefix: tuple[str, ...] | None = None,
) -> Any:
    """Assemble formal services, R0 and replay driver, then run the campaign."""
    # The factory may allocate leases, restore templates, or otherwise create
    # pressure.  Launch authorization must therefore precede even importing it.
    _require_ready_manifest(readiness_manifest)
    benchmark = str(config.get("benchmark") or "").strip().lower()
    if not benchmark:
        raise FormalSGDNotReady("NOT_READY: benchmark is missing")
    bundle = _load_factory(service_factory_spec)(config)
    if not isinstance(bundle, Mapping):
        raise FormalSGDNotReady("NOT_READY: service factory bundle must be a mapping")
    registration = bundle.get("registration")
    r0_measurement = bundle.get("r0_measurement")
    campaign_cleanup = bundle.get("campaign_cleanup")
    resume_prepare = bundle.get("resume_prepare")
    dependencies = _require_dependencies(bundle.get("dependencies"), benchmark)
    if not isinstance(registration, Mapping):
        raise FormalSGDNotReady("NOT_READY: benchmark registration is missing")
    if not callable(r0_measurement):
        raise FormalSGDNotReady("NOT_READY: fresh R0 measurement service is missing")
    if not callable(campaign_cleanup):
        raise FormalSGDNotReady("NOT_READY: campaign cleanup service is missing")
    try:
        mutex = mutex_from_campaign(config, bundle, benchmark)
    except Host02MutexError as exc:
        raise FormalSGDNotReady(f"NOT_READY: {exc}") from exc
    # JOB and XP enter the same host-wide flock before the first R0 pressure.
    # The context is not released until run_optimization has returned after its
    # final recovery/checksum work and the wrapper's post-run zero-pressure gate.
    with mutex if mutex is not None else nullcontext():
        try:
            real_r0_path = _production_r0_checkpoint_path(config, benchmark)
            real_r0_exists = (
                real_r0_path.exists()
                or real_r0_path.with_name(real_r0_path.name + ".sha256").exists()
            )
            if r0_authority_readiness_manifest is not None and not real_r0_exists:
                raise FormalSGDNotReady(
                    "NOT_READY: historical R0 authority cannot create a fresh R0"
                )
            identity_readiness_manifest = (
                _historical_r0_authority_manifest(r0_authority_readiness_manifest)
                if r0_authority_readiness_manifest is not None
                else Path(readiness_manifest)
            )
            real_r0_identity = _production_r0_identity(
                config, benchmark=benchmark,
                readiness_manifest=identity_readiness_manifest,
                registration=registration, service_factory_spec=service_factory_spec,
            )
            if real_r0_exists:
                baseline_validation = FormalProductionR0Ledger.load_verified(
                    real_r0_path, campaign_identity=real_r0_identity,
                )
                _validate_replay_baseline(
                    baseline_validation,
                    str(config.get("target_resource", config.get("bottleneck", ""))).lower(),
                )
                if callable(resume_prepare):
                    resume_evidence = resume_prepare(config)
                    if not isinstance(resume_evidence, Mapping):
                        raise FormalSGDNotReady(
                            "NOT_READY: resume planner preparation did not return evidence"
                        )
                    if (
                        resume_evidence.get("planner_ready") is not True
                        or resume_evidence.get("fresh_copy") is not True
                        or resume_evidence.get("workload_started") is not False
                    ):
                        raise FormalSGDNotReady(
                            "NOT_READY: resume planner preparation evidence is incomplete"
                        )
            else:
                baseline_validation = r0_measurement(config)
                if not isinstance(baseline_validation, Mapping):
                    raise FormalSGDNotReady(
                        "NOT_READY: fresh R0 measurement did not return a mapping"
                    )
                _validate_replay_baseline(
                    baseline_validation,
                    str(config.get("target_resource", config.get("bottleneck", ""))).lower(),
                )
                baseline_validation = FormalProductionR0Ledger.create(
                    real_r0_path, campaign_identity=real_r0_identity,
                    measurement=baseline_validation,
                )
            journal = None
            if journal_root is not None:
                if not resume_command_prefix:
                    raise FormalSGDNotReady(
                        "NOT_READY: journal_root requires an exact resume_command_prefix"
                    )
                journal = FormalPointJournal(
                    Path(journal_root),
                    tuple(resume_command_prefix),
                )
            driver = build_replay_driver(benchmark, registration, dependencies, journal)
            search_config = _bind_runtime_estimator_baseline(
                config, baseline_validation
            )
            return run_optimization(
                search_config,
                client=bundle.get("sql_client"),
                real_replay_callback=driver,
                baseline_validation=baseline_validation,
                method_id="experience_table_sgd",
            )
        except (FormalReplayCheckpointError, ValueError) as exc:
            raise FormalSGDNotReady(f"NOT_READY: production real R0 checkpoint: {exc}") from exc
        finally:
            cleanup = campaign_cleanup()
            if not isinstance(cleanup, Mapping):
                raise FormalSGDNotReady(
                    "NOT_READY: campaign cleanup did not return evidence"
                )
            if cleanup.get("zero_pressure_verified") is not True:
                raise FormalSGDNotReady(
                    "NOT_READY: campaign cleanup did not prove zero pressure"
                )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--readiness-manifest", type=Path, required=True)
    parser.add_argument("--r0-authority-readiness-manifest", type=Path)
    parser.add_argument("--service-factory", required=True, help="module:function")
    parser.add_argument("--journal-root", type=Path)
    args = parser.parse_args()
    result = run_production_campaign(
        load_config(args.config),
        readiness_manifest=args.readiness_manifest,
        service_factory_spec=args.service_factory,
        r0_authority_readiness_manifest=args.r0_authority_readiness_manifest,
        journal_root=args.journal_root,
        resume_command_prefix=(
            "python",
            str(Path(__file__).resolve()),
            "--config",
            str(args.config.resolve()),
            "--readiness-manifest",
            str(args.readiness_manifest.resolve()),
            *(
                (
                    "--r0-authority-readiness-manifest",
                    str(args.r0_authority_readiness_manifest.resolve()),
                )
                if args.r0_authority_readiness_manifest is not None
                else ()
            ),
            "--service-factory",
            args.service_factory,
        ),
    )
    print(json.dumps({
        "campaign_status": result.campaign_status,
        "campaign_complete": result.campaign_complete,
        "accepted_shrink_count": result.accepted_shrink_count,
        "rejected_count": result.rejected_count,
        "result_path": result.result_path,
        "diagnostic": result.campaign_diagnostic,
    }, ensure_ascii=False), flush=True)
    return 0 if result.campaign_complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
