#!/usr/bin/env python3
"""Prepare and verify the fail-closed JOB/Memory Algorithm C launch bundle."""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, Mapping, Optional, Sequence, Set


PACKAGE_ROOT = Path(__file__).resolve().parent
BUNDLE_ROOT = PACKAGE_ROOT.parent
CONTRACT_PATH = PACKAGE_ROOT / "contract.json"
GENERATED_FILES = ("live.json", "launch_manifest.json", "READY.sha256")
PROHIBITED_APPLICATION_REGEXES = (
    r"\balter\s+system\b",
    r"\bapply_params_via_alter_system\b",
    r"\bset\s+session\b",
    r"\bset_config\s*\([^)]*,\s*false\s*\)",
)
LEGACY_FORMAL_DEPENDENCY_KEYS = {
    "dataset_path",
    "dataset_sha256",
    "ranker_path",
    "ranker_sha256",
    "ranker_required",
    "real_replay_direction_ranker",
}


class GateError(RuntimeError):
    """A preparation or launch admission gate failed."""


def read_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GateError(f"missing file: {path}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateError(f"invalid UTF-8 JSON: {path}") from exc
    if not isinstance(value, dict):
        raise GateError(f"JSON root must be an object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except FileNotFoundError as exc:
        raise GateError(f"missing file: {path}") from exc
    return digest.hexdigest()


def canonical_json(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateError(message)


def legacy_dependency_keys(value: Any) -> Set[str]:
    found: Set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in LEGACY_FORMAL_DEPENDENCY_KEYS:
                found.add(str(key))
            found.update(legacy_dependency_keys(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.update(legacy_dependency_keys(child))
    return found


def require_sha(path: Path, expected: str, label: str) -> str:
    actual = sha256_file(path)
    require(actual == expected, f"{label} SHA256 mismatch: actual={actual}, expected={expected}")
    return actual


def resolve_bound_path(bundle_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (bundle_root / path).resolve()


def verify_derived_r0(contract: Mapping[str, Any], bundle_root: Path) -> None:
    evidence = contract.get("r0_derived_evidence")
    require(isinstance(evidence, list) and len(evidence) == 3, "R0 derived evidence must contain three files")
    for index, item in enumerate(evidence, start=1):
        require(isinstance(item, dict), f"R0 evidence {index} is not an object")
        path = resolve_bound_path(bundle_root, str(item.get("path") or ""))
        require_sha(path, str(item.get("sha256") or ""), f"R0 derived evidence {index}")


def float_equal(left: Any, right: Any) -> bool:
    try:
        return abs(float(left) - float(right)) <= 1e-12
    except (TypeError, ValueError):
        return False


def verify_r0_summary(
    contract: Mapping[str, Any],
    r0_path: Path,
) -> str:
    summary = read_json(r0_path)
    require(summary.get("status") == "complete", "strict R0 status is not complete")
    require(summary.get("all_three_r0_passed") is True, "strict R0 three-repeat gate did not pass")
    profiles = summary.get("profiles")
    expected_profiles = contract.get("r0_profiles")
    require(isinstance(profiles, list) and len(profiles) == 3, "strict R0 must contain exactly three profiles")
    require(isinstance(expected_profiles, list) and len(expected_profiles) == 3, "contract R0 profiles are invalid")
    for index, (actual, expected) in enumerate(zip(profiles, expected_profiles), start=1):
        require(isinstance(actual, dict), f"strict R0 profile {index} is invalid")
        require(actual.get("passed") is True, f"strict R0 profile {index} did not pass")
        require(actual.get("status") == "complete", f"strict R0 profile {index} is incomplete")
        require(actual.get("stable") is True, f"strict R0 profile {index} is unstable")
        require(
            int(actual.get("memory_limit_bytes") or 0) == int(expected["memory_limit_bytes"]),
            f"strict R0 profile {index} memory cap drift",
        )
        require(float_equal(actual.get("db_qps"), expected["db_qps"]), f"strict R0 profile {index} QPS drift")
        utilization = actual.get("resource_utilization")
        require(isinstance(utilization, dict), f"strict R0 profile {index} utilization missing")
        for resource in ("cpu", "memory", "io"):
            require(
                float_equal(utilization.get(resource), expected["resource_utilization"][resource]),
                f"strict R0 profile {index} {resource} utilization drift",
            )
    return sha256_file(r0_path)


def active_params(contract: Mapping[str, Any]) -> Sequence[str]:
    ranges = contract.get("parameter_ranges")
    require(isinstance(ranges, dict), "parameter_ranges missing")
    names = tuple(ranges.keys())
    require(len(names) == 7, "Algorithm C requires exactly seven parameter ranges")
    require(set(names) == set((contract.get("initial_params") or {}).keys()), "initial/range parameter set mismatch")
    return names


def verify_experience_table(
    contract: Mapping[str, Any],
    table_path: Path,
) -> str:
    binding = contract["experience_table"]
    digest = require_sha(table_path, binding["sha256"], "experience table T")
    artifact = read_json(table_path)
    require(
        int(artifact.get("schema_version") or 0) == binding["schema_version"],
        "experience table schema mismatch",
    )
    require(artifact.get("table_id") == binding["table_id"], "experience table ID mismatch")
    require(
        artifact.get("benchmark") == binding["benchmark"] == "job",
        "experience table benchmark is not job",
    )
    require(
        artifact.get("target_resource") == binding["target_resource"] == "memory",
        "experience table target is not memory",
    )
    require(
        list(artifact.get("active_params") or []) == list(active_params(contract)),
        "experience table active_params mismatch",
    )
    require(
        float_equal(
            artifact.get("low_confidence_freeze_threshold"),
            binding["low_confidence_freeze_threshold"],
        ),
        "experience table freeze threshold mismatch",
    )
    table = artifact.get("table")
    require(isinstance(table, dict), "experience table body missing")
    require(set(table) == set(active_params(contract)), "experience table seven-parameter coverage mismatch")
    for name, row in table.items():
        require(isinstance(row, dict), f"experience table row is invalid: {name}")
        for field in ("memory", "confidence"):
            try:
                value = float(row[field])
            except (KeyError, TypeError, ValueError) as exc:
                raise GateError(f"experience table {field} is invalid: {name}") from exc
            require(value == value and abs(value) != float("inf"), f"experience table {field} is non-finite: {name}")
        require(0.0 <= float(row["confidence"]) <= 1.0, f"experience table confidence out of range: {name}")
    canonical_table = json.dumps(
        table, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    internal_sha = hashlib.sha256(canonical_table).hexdigest()
    require(internal_sha == binding["table_sha256"], "experience table canonical SHA mismatch")
    require(artifact.get("table_sha256") == internal_sha, "experience table self SHA mismatch")
    return digest


def reject_mysql(value: Mapping[str, Any], label: str) -> None:
    serialized = json.dumps(value, ensure_ascii=False).lower()
    require("mysql" not in serialized, f"{label} contains prohibited MySQL references")


def verify_parameter_application_declaration(
    value: Mapping[str, Any],
    contract: Mapping[str, Any],
    label: str,
) -> None:
    expected = contract.get("parameter_application_contract")
    require(isinstance(expected, dict), "parameter application contract missing")
    actual = value.get("parameter_application_contract")
    require(isinstance(actual, dict), f"{label} parameter_application_contract missing")
    require(actual == expected, f"{label} parameter application semantics drift")


def verify_application_sources(
    workspace: Path,
    paths: Sequence[Path],
    contract: Mapping[str, Any],
) -> Sequence[Dict[str, str]]:
    require(paths, "at least one parameter application source is required")
    semantics = contract["parameter_application_contract"]
    workspace = workspace.resolve()
    allowed_roots = (workspace, PACKAGE_ROOT)
    records = []
    combined = []
    seen = set()
    for raw_path in paths:
        path = raw_path.resolve()
        inside_allowed_root = False
        for root in allowed_roots:
            try:
                path.relative_to(root)
                inside_allowed_root = True
                break
            except ValueError:
                continue
        require(
            inside_allowed_root,
            f"application source escapes workspace and launch package: {path}",
        )
        require(path.is_file(), f"parameter application source missing: {path}")
        require(path not in seen, f"duplicate parameter application source: {path}")
        seen.add(path)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise GateError(f"parameter application source is not UTF-8: {path}") from exc
        lowered = text.lower()
        for pattern in semantics["prohibited_source_patterns"]:
            require(
                re.search(re.escape(str(pattern)), text, flags=re.IGNORECASE) is None,
                f"prohibited parameter application path in {path}: {pattern}",
            )
        for pattern in PROHIBITED_APPLICATION_REGEXES:
            require(
                re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL) is None,
                f"prohibited non-local parameter application path in {path}: {pattern}",
            )
        combined.append(lowered)
        records.append({
            "path": str(path),
            "sha256": sha256_file(path),
        })
    all_source = "\n".join(combined)
    for marker in semantics["required_source_markers"]:
        require(
            str(marker).lower() in all_source,
            f"parameter application source marker missing: {marker}",
        )
    verify_declared_python_imports(workspace, seen)
    return records


def resolve_local_python_import(
    workspace: Path,
    source: Path,
    module: str,
    level: int,
) -> Optional[Path]:
    parts = [part for part in module.split(".") if part]
    bases = [source.parent]
    if level == 0:
        bases.append(workspace)
    else:
        base = source.parent
        for _ in range(max(0, level - 1)):
            base = base.parent
        bases = [base]
    for base in bases:
        candidate = base.joinpath(*parts)
        file_candidate = candidate.with_suffix(".py")
        if file_candidate.is_file():
            return file_candidate.resolve()
        package_candidate = candidate / "__init__.py"
        if package_candidate.is_file():
            return package_candidate.resolve()
    return None


def verify_declared_python_imports(workspace: Path, declared: Set[Path]) -> None:
    for source in declared:
        if source.suffix != ".py":
            continue
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        except SyntaxError as exc:
            raise GateError(f"cannot audit invalid Python application source: {source}") from exc
        for node in ast.walk(tree):
            dependencies = []
            if isinstance(node, ast.Import):
                dependencies = [(alias.name, 0) for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                dependencies = [(module, int(node.level or 0))]
                dependencies.extend(
                    (
                        ".".join(part for part in (module, alias.name) if part),
                        int(node.level or 0),
                    )
                    for alias in node.names
                    if alias.name != "*"
                )
            for module, level in dependencies:
                dependency = resolve_local_python_import(
                    workspace, source, module, level
                )
                if dependency is not None:
                    require(
                        dependency in declared,
                        f"undeclared local application dependency: {source} -> {dependency}",
                    )


def atomic_idempotent_write(path: Path, content: bytes) -> None:
    if path.exists():
        require(path.read_bytes() == content, f"refusing to overwrite drifted prepared file: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
    finally:
        if temporary.exists():
            temporary.unlink()


def build_config(
    base: Mapping[str, Any],
    contract: Mapping[str, Any],
    output_dir: Path,
    r0_path: Path,
    r0_sha256: str,
    experience_table_path: Path,
    experience_table_sha256: str,
) -> Dict[str, Any]:
    config = copy.deepcopy(dict(base))
    reject_mysql(config, "base config")
    verify_parameter_application_declaration(config, contract, "base config")
    session_runner = config.get("job_session_runner")
    require(isinstance(session_runner, dict), "base config job_session_runner missing")
    require(
        isinstance(session_runner.get("endpoint"), dict),
        "base config JOB endpoint missing",
    )
    workload = contract.get("job_workload")
    require(isinstance(workload, dict), "frozen JOB workload contract missing")
    for key in ("sql_dir", "query_sequence", "query_weights", "query_sha256"):
        require(
            session_runner.get(key) == workload.get(key),
            f"base config JOB workload drift: {key}",
        )
    endpoint = session_runner["endpoint"]
    require(
        str(endpoint.get("database") or "") == workload["database"],
        "base config JOB database drift",
    )
    benchmark_text = json.dumps(config.get("benchmark") or {}, ensure_ascii=False).lower()
    require("job" in benchmark_text, "base config benchmark is not explicitly JOB-bound")
    params = list(active_params(contract))
    config["capability"] = {
        "cpu": contract["profile"]["cpu"],
        "memory": contract["profile"]["memory_gib"],
        "io": contract["profile"]["io"]
    }
    config["p_real"] = copy.deepcopy(contract["initial_params"])
    config["p_candidate"] = copy.deepcopy(contract["initial_params"])
    config["initial_params"] = copy.deepcopy(contract["initial_params"])
    config["param_ranges"] = copy.deepcopy(contract["parameter_ranges"])
    optimizer = config.get("optimizer")
    optimizer = copy.deepcopy(optimizer) if isinstance(optimizer, dict) else {}
    optimizer.pop("gradient", None)
    optimizer["mode"] = "bruteforce"
    optimizer["active_params"] = params
    brute = optimizer.get("bruteforce")
    brute = copy.deepcopy(brute) if isinstance(brute, dict) else {}
    brute.update({
        "active_params": params,
        "max_iter": 10,
        "real_replay_top_k": 5,
        "real_replay_max_top_k": 5,
        "candidate_gate": {
            "enabled": True,
            "target_resource": "memory",
            "require_passed_candidate": True,
            "require_predicted_bottleneck_decrease": True,
            "require_explainable_direction": True
        }
    })
    optimizer["bruteforce"] = brute
    config["optimizer"] = optimizer
    config["formal_outer_protocol"] = {
        "formal_search_accepted_count_start": 0,
        "formal_search_accepted_target": 10,
        "real_replay_max_per_outer_round": 5,
        "pilot_accepted_counts_must_not_be_inherited": True,
        "r0_summary_path": str(r0_path),
        "r0_summary_sha256": r0_sha256,
        "experience_table_path": str(experience_table_path),
        "experience_table_sha256": experience_table_sha256,
        "output_root": str(output_dir)
    }
    config["_algorithm_c_ratio_resource_only"] = copy.deepcopy(contract["algorithm_c"])
    config["parameter_application_contract"] = copy.deepcopy(
        contract["parameter_application_contract"]
    )
    config["job_session_runner"] = copy.deepcopy(session_runner)
    session_output = output_dir / "session_workload"
    config["job_session_runner"].update({
        "sql_dir": workload["sql_dir"],
        "query_sequence": copy.deepcopy(workload["query_sequence"]),
        "query_weights": copy.deepcopy(workload["query_weights"]),
        "query_sha256": copy.deepcopy(workload["query_sha256"]),
        "workers": workload["workers"],
        "duration_seconds": workload["duration_seconds"],
        "max_queries_per_worker": 0,
        "resource_sample_interval_seconds": workload[
            "resource_sample_interval_seconds"
        ],
        "stability_window_seconds": workload["stability_window_seconds"],
        "required_stability_windows": workload["required_stability_windows"],
        "output_dir": str(session_output),
        "resource_samples_path": str(
            session_output / "measurement_resource_samples.jsonl"
        ),
    })
    config["job_cgroup_sampler"] = {
        "implementation": "formal_cgroup_v1",
        "fresh_per_candidate": True,
        "sample_interval_seconds": workload["resource_sample_interval_seconds"],
        "profile": copy.deepcopy(contract["profile"]),
    }
    config["_job_attempt8e_binding"] = {
        "contract_id": contract["contract_id"],
        "source_attempt": contract["source_attempt"],
        "profile": copy.deepcopy(contract["profile"]),
        "r0_summary_path": str(r0_path),
        "r0_summary_sha256": r0_sha256,
        "experience_table_sha256": experience_table_sha256
    }
    logging = config.get("logging")
    logging = copy.deepcopy(logging) if isinstance(logging, dict) else {}
    logging["history_path"] = str(output_dir / "history" / "optimization_history.jsonl")
    config["logging"] = logging
    require(
        not legacy_dependency_keys(config),
        "generated config contains legacy dataset/ranker formal dependency",
    )
    reject_mysql(config, "generated config")
    return config


def prepare(args: argparse.Namespace) -> int:
    contract = read_json(CONTRACT_PATH)
    contract_sha = sha256_file(CONTRACT_PATH)
    bundle_root = Path(args.bundle_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    base_path = Path(args.base_config).resolve()
    r0_path = Path(args.r0_summary).resolve()
    workspace = Path(args.workspace).resolve()
    experience_table_path = resolve_bound_path(
        workspace, contract["experience_table"]["path"]
    )
    python_path = Path(args.python).absolute()
    runner_path = Path(args.runner).resolve()
    expected_runner = (PACKAGE_ROOT / "job_algorithm_c_outer.py").resolve()
    application_sources = [Path(item).resolve() for item in args.application_source]
    require(workspace.is_dir(), f"workspace does not exist: {workspace}")
    require(python_path.is_file(), f"Python runtime does not exist: {python_path}")
    require(runner_path == expected_runner, f"runner must be sealed Algorithm C outer: {expected_runner}")
    verify_derived_r0(contract, bundle_root)
    r0_sha = verify_r0_summary(contract, r0_path)
    experience_table_sha = verify_experience_table(contract, experience_table_path)
    source_records = verify_application_sources(workspace, application_sources, contract)
    require(
        any(Path(item["path"]) == runner_path for item in source_records),
        "runner must be included in --application-source",
    )
    base = read_json(base_path)
    config = build_config(
        base, contract, output_dir, r0_path, r0_sha,
        experience_table_path, experience_table_sha
    )
    config_bytes = canonical_json(config)
    config_sha = hashlib.sha256(config_bytes).hexdigest()
    manifest = {
        "schema_version": 1,
        "contract_id": contract["contract_id"],
        "contract_path": str(CONTRACT_PATH),
        "contract_sha256": contract_sha,
        "base_config_path": str(base_path),
        "base_config_sha256": sha256_file(base_path),
        "live_config_path": str(output_dir / "live.json"),
        "live_config_sha256": config_sha,
        "r0_summary_path": str(r0_path),
        "r0_summary_sha256": r0_sha,
        "experience_table_path": str(experience_table_path),
        "experience_table_sha256": experience_table_sha,
        "workspace": str(workspace),
        "python": str(python_path),
        "runner": str(runner_path),
        "runner_sha256": sha256_file(runner_path),
        "entrypoint_role": "job_algorithm_c_outer",
        "parameter_application_sources": source_records,
        "output_dir": str(output_dir),
        "remote_host_shortname": contract["remote_host_shortname"]
    }
    manifest_bytes = canonical_json(manifest)
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_idempotent_write(output_dir / "live.json", config_bytes)
    atomic_idempotent_write(output_dir / "launch_manifest.json", manifest_bytes)
    atomic_idempotent_write(output_dir / "READY.sha256", (manifest_sha + "\n").encode("ascii"))
    verify_prepared(output_dir)
    print(f"prepared={output_dir}", flush=True)
    print(f"manifest_sha256={manifest_sha}", flush=True)
    print(f"experience_table_sha256={experience_table_sha}", flush=True)
    return 0


def verify_prepared(output_dir: Path) -> None:
    output_dir = output_dir.resolve()
    manifest_path = output_dir / "launch_manifest.json"
    manifest = read_json(manifest_path)
    ready = (output_dir / "READY.sha256").read_text(encoding="ascii").strip()
    require(ready == sha256_file(manifest_path), "READY manifest SHA mismatch")
    contract_path = Path(str(manifest.get("contract_path") or "")).resolve()
    require_sha(contract_path, str(manifest.get("contract_sha256") or ""), "contract")
    contract = read_json(contract_path)
    require(str(output_dir) == manifest.get("output_dir"), "prepared output path drift")
    require_sha(Path(manifest["base_config_path"]), manifest["base_config_sha256"], "base config")
    require_sha(Path(manifest["live_config_path"]), manifest["live_config_sha256"], "live config")
    verify_derived_r0(contract, contract_path.parent.parent)
    r0_sha = verify_r0_summary(contract, Path(manifest["r0_summary_path"]))
    require(r0_sha == manifest["r0_summary_sha256"], "R0 manifest SHA mismatch")
    require(
        not LEGACY_FORMAL_DEPENDENCY_KEYS.intersection(manifest),
        "legacy dataset/ranker formal dependency is prohibited",
    )
    experience_table_sha = verify_experience_table(
        contract, Path(manifest["experience_table_path"])
    )
    require(
        experience_table_sha == manifest["experience_table_sha256"],
        "experience table manifest SHA mismatch",
    )
    config = read_json(Path(manifest["live_config_path"]))
    reject_mysql(config, "prepared config")
    binding = config.get("_job_attempt8e_binding")
    require(isinstance(binding, dict), "attempt8e binding missing")
    require(binding.get("r0_summary_sha256") == r0_sha, "config R0 SHA mismatch")
    require(
        binding.get("experience_table_sha256") == experience_table_sha,
        "config experience table SHA mismatch",
    )
    require(config.get("param_ranges") == contract["parameter_ranges"], "seven parameter ranges drift")
    require(config.get("initial_params") == contract["initial_params"], "initial parameter vector drift")
    verify_parameter_application_declaration(config, contract, "generated config")
    session_runner = config.get("job_session_runner")
    require(isinstance(session_runner, dict), "generated JOB session runner missing")
    workload = contract["job_workload"]
    for key in ("sql_dir", "query_sequence", "query_weights", "query_sha256"):
        require(
            session_runner.get(key) == workload[key],
            f"generated JOB workload drift: {key}",
        )
    require(
        session_runner.get("workers") == workload["workers"]
        and session_runner.get("duration_seconds") == workload["duration_seconds"],
        "generated JOB workload execution profile drift",
    )
    require(binding.get("profile") == contract["profile"], "attempt8e profile binding drift")
    sampler = config.get("job_cgroup_sampler")
    require(isinstance(sampler, dict), "formal cgroup sampler config missing")
    require(
        sampler == {
            "implementation": "formal_cgroup_v1",
            "fresh_per_candidate": True,
            "sample_interval_seconds": workload["resource_sample_interval_seconds"],
            "profile": contract["profile"],
        },
        "formal cgroup sampler profile drift",
    )
    require(
        config.get("capability") == {
            "cpu": contract["profile"]["cpu"],
            "memory": contract["profile"]["memory_gib"],
            "io": contract["profile"]["io"],
        },
        "runtime capability profile drift",
    )
    optimizer = config.get("optimizer") or {}
    require(optimizer.get("mode") == "bruteforce", "Algorithm C mode drift")
    require(optimizer.get("active_params") == list(active_params(contract)), "Algorithm C active params drift")
    algorithm = config.get("_algorithm_c_ratio_resource_only")
    require(algorithm == contract["algorithm_c"], "Algorithm C resource-only contract drift")
    protocol = config.get("formal_outer_protocol") or {}
    require(
        not legacy_dependency_keys(config),
        "generated config contains legacy dataset/ranker formal dependency",
    )
    require(
        protocol.get("experience_table_sha256") == experience_table_sha,
        "generated config experience table SHA mismatch",
    )
    workspace = Path(manifest["workspace"])
    require(workspace.is_dir(), "workspace drift")
    require(Path(manifest["python"]).is_file(), "Python runtime drift")
    source_records = manifest.get("parameter_application_sources")
    require(isinstance(source_records, list) and source_records, "application source manifest missing")
    source_paths = []
    for item in source_records:
        require(isinstance(item, dict), "invalid application source manifest entry")
        path = Path(str(item.get("path") or ""))
        require_sha(path, str(item.get("sha256") or ""), "parameter application source")
        source_paths.append(path)
    verified_records = verify_application_sources(workspace, source_paths, contract)
    require(verified_records == source_records, "parameter application source manifest drift")
    runner = Path(str(manifest.get("runner") or ""))
    expected_runner = (PACKAGE_ROOT / "job_algorithm_c_outer.py").resolve()
    require(
        manifest.get("entrypoint_role") == "job_algorithm_c_outer"
        and runner.resolve() == expected_runner,
        "manifest is not bound to the sealed Algorithm C outer",
    )
    require_sha(runner, str(manifest.get("runner_sha256") or ""), "runner")
    require(runner in source_paths, "runner is not an admitted parameter application source")


def verify(args: argparse.Namespace) -> int:
    output_dir = Path(args.prepared_dir).resolve()
    verify_prepared(output_dir)
    print(f"verified={output_dir}", flush=True)
    print(f"manifest_sha256={sha256_file(output_dir / 'launch_manifest.json')}", flush=True)
    return 0


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    subparsers = value.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--base-config", required=True)
    prepare_parser.add_argument("--r0-summary", required=True)
    prepare_parser.add_argument("--workspace", required=True)
    prepare_parser.add_argument("--python", required=True)
    prepare_parser.add_argument("--runner", required=True)
    prepare_parser.add_argument(
        "--application-source",
        action="append",
        required=True,
        help="Repeat for every source file in the parameter application path."
    )
    prepare_parser.add_argument("--output-dir", required=True)
    prepare_parser.add_argument("--bundle-root", default=str(BUNDLE_ROOT))
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--prepared-dir", required=True)
    return value


def main() -> int:
    args = parser().parse_args()
    try:
        return prepare(args) if args.command == "prepare" else verify(args)
    except (GateError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"FAIL_CLOSED: {exc}", flush=True)
        return 42


if __name__ == "__main__":
    raise SystemExit(main())
