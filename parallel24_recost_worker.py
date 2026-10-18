#!/usr/bin/env python3
"""Session-owned four-lane runner for parallel24_20260721_v1 MySQL Recost.

This adapter deliberately reuses the frozen point implementation and Recost
proposal/acceptance code, while replacing only the old serial campaign's
container, port, cpuset, output, template, and scheduler bindings.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
from typing import Any


WORKSPACE = Path("/home/anonymous/program/resource_adaptive_mysql")
FROZEN = WORKSPACE / "preflight/frozen_formal_config_20260721_v3"
CAMPAIGN_ROOT = WORKSPACE / "history/parallel24_20260721_v1"
OUTPUT_ROOT = CAMPAIGN_ROOT / "mysql/recost_gradient_descent"
RUNTIME_ROOT = Path("/disk2/resource_adaptive/parallel24_20260721_v1/mysql/recost_gradient_descent")
OLD_CONFIGS = WORKSPACE / "history/unified_formal/six_session_20260721_v3/mysql/preflight/benchmark_configs"
CAMPAIGN_ID = "parallel24_20260721_v1"


CASES: dict[str, dict[str, Any]] = {
    "hybench_ap_memory": {
        "case_id": "mysql_hybench_ap_memory", "benchmark": "hybench_ap",
        "scenario": "memory", "container_key": "hybench", "port": 57301,
        "container": "p24_mysql_recost_gradient_descent_ap", "cpuset": "208-215,464-471",
        "template": "hybench", "source": Path("/disk2/resource_adaptive/unified_formal_20260721_v1/templates/hybench"),
    },
    "job_memory": {
        "case_id": "mysql_job_memory", "benchmark": "job",
        "scenario": "memory", "container_key": "job", "port": 57302,
        "container": "p24_mysql_recost_gradient_descent_job", "cpuset": "216-223,472-479",
        "template": "job", "source": Path("/disk2/resource_adaptive/unified_formal_20260721_v1/templates/job"),
    },
    "tpch_memory": {
        "case_id": "mysql_tpch_memory", "benchmark": "tpch",
        "scenario": "memory", "container_key": "tpch", "port": 57303,
        "container": "p24_mysql_recost_gradient_descent_tpch", "cpuset": "224-231,480-487",
        "template": "tpch", "source": RUNTIME_ROOT / "templates/tpch",
    },
    "hybench_xp_io": {
        "case_id": "mysql_hybench_xp_io", "benchmark": "hybench_xp",
        "scenario": "io", "container_key": "hybench", "port": 57304,
        "container": "p24_mysql_recost_gradient_descent_xp", "cpuset": "232-247,488-503",
        "template": "hybench", "source": Path("/disk2/resource_adaptive/unified_formal_20260721_v1/templates/hybench"),
    },
}


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_private_inputs(case_name: str, cfg: dict[str, Any]) -> tuple[Path, Path]:
    case_root = OUTPUT_ROOT / "cases" / case_name
    private = case_root / "private_inputs"
    benchmark_root = private / "benchmark_configs"
    benchmark_root.mkdir(parents=True, exist_ok=True)
    source_config = OLD_CONFIGS / cfg["benchmark"] / "benchmark_config.json"
    payload = json.loads(source_config.read_text(encoding="utf-8"))
    payload["port"] = cfg["port"]
    payload["jdbc_url"] = payload["jdbc_url"].replace(
        f":{json.loads(source_config.read_text(encoding='utf-8'))['port']}/", f":{cfg['port']}/"
    )
    payload["global_campaign_id"] = CAMPAIGN_ID
    target_config = benchmark_root / cfg["benchmark"] / "benchmark_config.json"
    atomic_json(target_config, payload)

    credential = private / "my.cnf"
    if not credential.exists():
        credential.write_text("[client]\nuser=root\npassword=CHANGE_ME\n", encoding="utf-8")
        credential.chmod(0o600)
    return benchmark_root, credential


def template_binding(cfg: dict[str, Any]) -> Path:
    source = cfg["source"]
    if not (source / "datadir").is_dir() or not (source / "database_manifest.json").is_file():
        raise RuntimeError(f"verified template is incomplete: {source}")
    binding_root = RUNTIME_ROOT / "template_bindings" / cfg["container"]
    target = binding_root / cfg["template"]
    target.mkdir(parents=True, exist_ok=True)
    for name in ("datadir", "database_manifest.json"):
        link = target / name
        expected = source / name
        if link.is_symlink() and link.resolve() == expected.resolve():
            continue
        if link.exists() or link.is_symlink():
            raise RuntimeError(f"unexpected existing template binding: {link}")
        link.symlink_to(expected)
    return binding_root


def frozen_configs(machine: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    names = (
        "machine_and_resources.json", "fixed_db_settings.json", "benchmark_matrix.json",
        "preflight_gates.json", "measurement_protocol.json", "search_spaces.json",
        "method_protocols.json", "acceptance_policy.json",
    )
    values = [json.loads((FROZEN / name).read_text(encoding="utf-8")) for name in names]
    values[0] = machine
    return tuple(values)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=sorted(CASES))
    args = parser.parse_args()
    case_name = args.case
    cfg = CASES[case_name]
    case_root = OUTPUT_ROOT / "cases" / case_name
    case_root.mkdir(parents=True, exist_ok=True)
    benchmark_root, credential = prepare_private_inputs(case_name, cfg)
    binding_root = template_binding(cfg)

    machine = json.loads((FROZEN / "machine_and_resources.json").read_text(encoding="utf-8"))
    engine = machine["engines"]["mysql"]
    engine["template_root"] = str(binding_root)
    engine["working_root"] = str(RUNTIME_ROOT / "work" / case_name)
    for key in engine["containers"]:
        engine["containers"][key] = {"name": cfg["container"], "port": cfg["port"]}
    profile = machine["resource_profiles"][cfg["scenario"]]
    profile["cpuset_cpus"] = cfg["cpuset"]
    profile["cpuset_mems"] = "1"
    # Equal memory and memory-swap is Docker's no-additional-swap setting.
    profile["memory_swap_bytes"] = profile["memory_bytes"]

    import formal_mysql_campaign_20260721 as common
    import formal_mysql_random_walk_point as point
    import formal_mysql_random_walk_session as rw
    import formal_mysql_recost_session as recost
    import formal_mysql_recost_spec as spec_module
    import formal_session_runtime_20260721 as runtime
    from formal_mysql_recost_spec import RecostRunSpec
    from formal_recost_core import load_frozen_recost_contract

    # Process-local runtime rebinding. No source/frozen/old campaign file is modified.
    common.GLOBAL_CAMPAIGN_ID = CAMPAIGN_ID
    common.GLOBAL_ROOT = CAMPAIGN_ROOT
    common.SHARED_FROZEN_ROOT = FROZEN
    common.BENCHMARK_CONFIG_ROOT = benchmark_root
    common.MYSQL_CREDENTIAL = credential
    common._configs = lambda: frozen_configs(machine)
    common.validate_shared_bundle = lambda: {"status": "parallel24_direct_frozen_source"}
    frozen_docker_args = common._docker_run_args
    def docker_args_with_real_device(*args: Any, **kwargs: Any) -> list[Any]:
        argv = frozen_docker_args(*args, **kwargs)
        return [
            item.replace("/dev/block/259:6:", "/dev/nvme2n1:")
            if isinstance(item, str) else item
            for item in argv
        ]
    common._docker_run_args = docker_args_with_real_device
    if case_name == "tpch_memory":
        def copy_tpch_template(source: Path, target: Path, working_root: Path) -> None:
            resolved_target = target.resolve(strict=False)
            resolved_root = working_root.resolve()
            if resolved_root != resolved_target.parent and resolved_root not in resolved_target.parents:
                raise RuntimeError(f"TPC-H working target escapes session root: {target}")
            if target.exists():
                raise RuntimeError(f"fresh TPC-H target already exists: {target}")
            target.mkdir(parents=True)
            subprocess.run(["sudo", "-n", "cp", "--archive", "--reflink=auto", f"{source.resolve()}/.", str(target)], check=True)
            subprocess.run(["sudo", "-n", "chown", "-R", "999:999", str(target)], check=True)
            subprocess.run(["sudo", "-n", "chmod", "-R", "u+rwX", str(target)], check=True)
        common._copy_template = copy_tpch_template
    import formal_resource_sampler_20260721 as resource_sampler
    frozen_int = resource_sampler._int
    def int_with_swap_disabled_readback(path: Path) -> int:
        if path.name == "memory.memsw.limit_in_bytes" and not path.is_file():
            # This host has no swap and the memsw controller file is absent.
            # Effective total memory+swap therefore equals the memory hard limit.
            return frozen_int(path.with_name("memory.limit_in_bytes"))
        return frozen_int(path)
    resource_sampler._int = int_with_swap_disabled_readback
    frozen_cgroup_readback = resource_sampler.CgroupV1Adapter._readback
    def readback_with_effective_io_limits(adapter: Any) -> dict[str, Any]:
        blkio = adapter.paths["blkio"]
        for filename, profile_key in (
            ("blkio.throttle.read_bps_device", "io_read_bps"),
            ("blkio.throttle.write_bps_device", "io_write_bps"),
        ):
            requested = adapter.profile.get(profile_key)
            if requested is None:
                continue
            path = blkio / filename
            current = resource_sampler._io_limits(path, adapter.device)
            if current != int(requested):
                subprocess.run(
                    ["sudo", "-n", "tee", str(path)],
                    input=f"{adapter.device} {int(requested)}\n", text=True,
                    stdout=subprocess.DEVNULL, check=True,
                )
        return frozen_cgroup_readback(adapter)
    resource_sampler.CgroupV1Adapter._readback = readback_with_effective_io_limits

    point.common = common
    rw.common = common
    rw.GLOBAL_ROOT = CAMPAIGN_ROOT
    rw.SHARED_FROZEN = FROZEN
    rw.wait_host_admission = lambda output: {
        "status": "parallel24_authorized", "old_global_lock_consulted": False,
        "recorded_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    def environment_snapshot(run_root: Path, spec: Any) -> None:
        atomic_json(run_root / "run_environment.json", {
            "schema_version": 1, "campaign_id": CAMPAIGN_ID,
            "case": case_name, "spec": spec.as_dict(),
            "container": cfg["container"], "port": cfg["port"],
            "cpuset_cpus": cfg["cpuset"], "profile": cfg["scenario"],
            "template_manifest": str(cfg["source"] / "database_manifest.json"),
            "template_manifest_sha256": sha256(cfg["source"] / "database_manifest.json"),
            "old_engine_ready_consumed": False,
            "recorded_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        })
    rw._environment_snapshot = environment_snapshot

    recost.GLOBAL_ROOT = CAMPAIGN_ROOT
    recost.GLOBAL_CAMPAIGN_ID = CAMPAIGN_ID
    recost.SHARED_FROZEN = FROZEN
    recost.METHOD_ROOT = OUTPUT_ROOT
    recost.rw = rw
    spec_module.SHARED_FROZEN = FROZEN
    runtime.GLOBAL_ROOT = CAMPAIGN_ROOT
    runtime.GLOBAL_CAMPAIGN_ID = CAMPAIGN_ID
    runtime.SHARED_FROZEN_ROOT = FROZEN

    settings, parameters = load_frozen_recost_contract(
        FROZEN / "method_protocols.json", FROZEN / "search_spaces.json"
    )
    estimator_sha = sha256(WORKSPACE / "formal_recost_estimator.py")
    expected_sha = json.loads((FROZEN / "method_protocols.json").read_text())["recost_gradient_descent"]["estimator_implementation_sha256"]
    if estimator_sha != expected_sha:
        raise RuntimeError(f"Recost estimator SHA mismatch: {estimator_sha} != {expected_sha}")

    run_id = f"parallel24_{case_name}"
    spec = RecostRunSpec(
        ordinal=0, phase="parallel24", run_id=run_id, repeat_id=1,
        benchmark=cfg["benchmark"], workload_variant=cfg["benchmark"],
        scenario=cfg["scenario"], case_id=cfg["case_id"],
        container_key=cfg["container_key"], repeat_one_source=None,
    )
    validation = {
        "status": "passed_parallel24_direct_frozen_validation",
        "runner_source_sha256": recost._source_hashes(),
        "recost_settings": asdict(settings),
        "parameter_order": [item.name for item in parameters],
        "frozen_checksums_sha256": sha256(FROZEN / "checksums.sha256"),
        "estimator_implementation_sha256": estimator_sha,
    }
    durable_state = OUTPUT_ROOT / f"parallel24_{case_name}_r1/run_state.json"
    if durable_state.is_file():
        durable = json.loads(durable_state.read_text(encoding="utf-8"))
        attempt = durable["attempt_id"]
        failed_result = durable_state.parent / "round_000_baseline/attempt_01/point_result.json"
        recoverable_baseline = False
        if failed_result.is_file():
            recoverable_baseline = json.loads(failed_result.read_text(encoding="utf-8")).get("status") == "failed"
        if durable.get("stage") == "baseline_search" and (durable.get("status") == "blocked" or recoverable_baseline):
            failed = durable_state.parent / "round_000_baseline/attempt_01"
            if failed.is_dir():
                recovery_root = durable_state.parent / "recoverable_failures"
                recovery = recovery_root / f"baseline_attempt_01_{int(time.time())}"
                recovery.parent.mkdir(parents=True, exist_ok=True)
                os.replace(failed, recovery)
            admission = durable_state.parent / "admission/baseline_search/attempt_01_summary.json"
            if admission.is_file():
                recovery_admission = durable_state.parent / "recoverable_failures" / f"admission_attempt_01_{int(time.time())}.json"
                os.replace(admission, recovery_admission)
            durable["status"] = "running"
            durable["blocker"] = None
            atomic_json(durable_state, durable)
    else:
        attempt = f"p24_mysql_recost_{case_name}_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{secrets.token_hex(4)}"
    manifest = {
        "schema_version": 1, "campaign_id": CAMPAIGN_ID, "case": case_name,
        "case_config": {**cfg, "source": str(cfg["source"])},
        "attempt_id": attempt, "frozen_root": str(FROZEN),
        "frozen_checksums_sha256": sha256(FROZEN / "checksums.sha256"),
        "recost_estimator_sha256": estimator_sha,
        "template_manifest": str(cfg["source"] / "database_manifest.json"),
        "template_manifest_sha256": sha256(cfg["source"] / "database_manifest.json"),
        "old_ready_scheduler_and_global_lock_waited": False,
        "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    atomic_json(case_root / "parallel24_case_manifest.json", manifest)
    result = recost.execute_claimed_run(spec, attempt, validation)
    atomic_json(case_root / "case_complete.json", {
        "status": "complete", "result_manifest": str(result),
        "completed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
