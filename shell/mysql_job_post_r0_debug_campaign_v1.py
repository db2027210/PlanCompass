#!/usr/bin/env python3
"""Executable post-R0 JOB debug campaign using only exact-client services."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping


class CampaignError(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _load(path: Path, *, sidecar: bool = True) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise CampaignError(f"invalid input: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if sidecar:
        digest = Path(str(path) + ".sha256")
        if digest.is_symlink() or not digest.is_file() or digest.read_text().strip() != hashlib.sha256(_canonical(value)).hexdigest():
            raise CampaignError(f"sidecar differs: {path}")
    return value


def _validate_authority(config: Mapping[str, Any], authority: Mapping[str, Any], token_path: Path) -> None:
    if token_path.is_symlink() or not token_path.is_file() or token_path.stat().st_mode & 0o077:
        raise CampaignError("main-agent token must be a private regular file")
    token = token_path.read_text(encoding="utf-8").strip()
    now = datetime.now(timezone.utc)
    try:
        not_before = datetime.fromisoformat(str(authority["not_before_utc"]).replace("Z", "+00:00"))
        expires = datetime.fromisoformat(str(authority["expires_at_utc"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as exc:
        raise CampaignError("debug authority time window differs") from exc
    if (
        authority.get("schema_version") != "mysql_job_post_r0_debug_authority_v1"
        or authority.get("attempt_token") != config.get("attempt_token")
        or authority.get("current_config_sha256") != hashlib.sha256(_canonical(config)).hexdigest()
        or authority.get("main_agent_token_sha256") != hashlib.sha256(token.encode()).hexdigest()
        or authority.get("accepted_round_authorized") is not False
        or authority.get("formal_launch_authorized") is not False
        or authority.get("debug_round_authorized") is not True
        or not_before.tzinfo is None
        or expires.tzinfo is None
        or now < not_before.astimezone(timezone.utc)
        or now >= expires.astimezone(timezone.utc)
    ):
        raise CampaignError("debug authority differs")


def execute(
    config: Mapping[str, Any], baseline: Mapping[str, Any], *, campaign_argv: tuple[str, ...]
) -> Mapping[str, Any]:
    target = Path(str(config["target_bundle_path"])).resolve()
    qualification = target / "qualification"
    if target.name != config.get("target_bundle_id") or not qualification.is_dir():
        raise CampaignError("sealed target bundle differs")
    if hashlib.sha256((target / "bundle_manifest.json").read_bytes()).hexdigest() != config.get("target_manifest_sha256"):
        raise CampaignError("sealed target manifest differs")
    sys.path.insert(0, str(qualification))
    services = None
    try:
        import mysql_parameter_optimizer as optimizer
        import formal_mysql_sgd_orchestration as orchestration
        service_path = Path(str(config["exact_service_module"])).resolve()
        spec = importlib.util.spec_from_file_location("job_post_r0_exact_services", service_path)
        if spec is None or spec.loader is None:
            raise CampaignError("cannot load exact-client service module")
        service_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(service_module)
        services = service_module.build(config)
        resume = services["resume_prepare"](config)
        if resume.get("planner_ready") is not True or resume.get("workload_started") is not False:
            raise CampaignError("same-live resume planner did not fail closed")
        journal = orchestration.FormalPointJournal(
            Path(str(config["output"]["directory"])) / "point_journal",
            campaign_argv,
        )
        driver = orchestration.build_job_replay_driver(
            services["registration"], services["dependencies"], journal
        )
        result = optimizer.run_optimization(
            config,
            client=services["sql_client"],
            real_replay_callback=driver,
            baseline_validation=baseline,
            method_id="experience_table_sgd",
        )
        document = dict(result.formal_document)
        document.update(
            {
                "schema_version": "mysql_job_post_r0_optimizer_result_v1",
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "accepted_rounds_credit": 0,
                "accepted_round_authorized": False,
                "support_bundle_id": config["support_bundle_id"],
                "support_manifest_sha256": config["support_manifest_sha256"],
                "shared_dispatch_sha256": config["shared_dispatch_sha256"],
            }
        )
        return document
    finally:
        if services is not None:
            cleanup = services.get("campaign_cleanup")
            if callable(cleanup):
                cleanup()
        if sys.path and sys.path[0] == str(qualification):
            sys.path.pop(0)


def _write(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists() or path.is_symlink() or Path(str(path) + ".sha256").exists():
        raise CampaignError("refusing to overwrite campaign output")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    Path(str(path) + ".sha256").write_text(hashlib.sha256(_canonical(value)).hexdigest() + "\n", encoding="utf-8")


def main() -> int:
    sys.dont_write_bytecode = True
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--baseline-validation", type=Path, required=True)
    parser.add_argument("--main-agent-authority", type=Path, required=True)
    parser.add_argument("--main-agent-token-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = _load(args.config)
    authority = _load(args.main_agent_authority)
    baseline = _load(args.baseline_validation)
    _validate_authority(config, authority, args.main_agent_token_file)
    value = execute(config, baseline, campaign_argv=tuple(sys.argv))
    _write(args.output, value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
