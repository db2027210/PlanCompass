#!/usr/bin/env python3
"""Freeze one VM replay worker's effective workload inputs for a launch snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
from typing import Any, Dict, Tuple


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def resolve_input(project_root: Path, raw_path: Any) -> Path:
    path = Path(str(raw_path))
    return path if path.is_absolute() else project_root / path


def command_flag_path(command: str, flag: str) -> Tuple[re.Match[str], Path]:
    pattern = re.compile(rf"(?P<prefix>(?:^|\s){re.escape(flag)}\s+)(?P<path>[^\s]+)")
    match = pattern.search(command)
    if match is None:
        raise ValueError(f"workload command has no {flag} path: {command}")
    return match, Path(match.group("path"))


def replace_command_path(command: str, match: re.Match[str], path: Path) -> str:
    return command[: match.start("path")] + str(path) + command[match.end("path") :]


def freeze_worker(
    *,
    project_root: Path,
    source_worker: Path,
    target: str,
    output_dir: Path,
) -> Dict[str, Any]:
    worker = read_json(source_worker)
    scenario_paths = worker.get("scenario_configs") or {}
    if target not in scenario_paths:
        raise ValueError(f"worker has no scenario config for {target}: {source_worker}")
    source_scenario = resolve_input(project_root, scenario_paths[target]).resolve()
    scenario = read_json(source_scenario)

    scenario_override = ((worker.get("scenario_overrides") or {}).get(target) or {})
    benchmark_override = scenario_override.get("benchmark") or {}
    workload_cmd = str(benchmark_override.get("workload_cmd") or "")
    if not workload_cmd:
        workload_cmd = str((scenario.get("benchmark") or {}).get("workload_cmd") or "")
    props_match, source_props = command_flag_path(workload_cmd, "-c")
    statement_match, source_statement = command_flag_path(workload_cmd, "-f")
    source_props = resolve_input(project_root, source_props).resolve()
    source_statement = resolve_input(project_root, source_statement).resolve()

    output_dir.mkdir(parents=True, exist_ok=True)
    frozen_scenario = output_dir / "scenario_config.json"
    frozen_props = output_dir / "source.props"
    frozen_statement = output_dir / "source_statements.toml"
    frozen_worker = output_dir / "worker_config.json"
    shutil.copy2(source_scenario, frozen_scenario)
    shutil.copy2(source_props, frozen_props)
    shutil.copy2(source_statement, frozen_statement)

    workload_cmd = replace_command_path(workload_cmd, props_match, frozen_props)
    # Re-match after the first replacement because its length can move the -f span.
    statement_match, _ = command_flag_path(workload_cmd, "-f")
    workload_cmd = replace_command_path(workload_cmd, statement_match, frozen_statement)
    worker.setdefault("scenario_configs", {})[target] = str(frozen_scenario)
    target_override = worker.setdefault("scenario_overrides", {}).setdefault(target, {})
    target_override.setdefault("benchmark", {})["workload_cmd"] = workload_cmd
    write_json(frozen_worker, worker)

    metadata = {
        "target": target,
        "source_worker_config": str(source_worker),
        "source_scenario_config": str(source_scenario),
        "source_props": str(source_props),
        "source_statement_file": str(source_statement),
        "frozen_worker_config": str(frozen_worker),
        "frozen_scenario_config": str(frozen_scenario),
        "frozen_props": str(frozen_props),
        "frozen_statement_file": str(frozen_statement),
        "workload_cmd": workload_cmd,
    }
    write_json(output_dir / "freeze_metadata.json", metadata)
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--source-worker", type=Path, required=True)
    parser.add_argument("--target", choices=("cpu", "memory", "io", "none"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    project_root = args.project_root.resolve()
    source_worker = resolve_input(project_root, args.source_worker).resolve()
    metadata = freeze_worker(
        project_root=project_root,
        source_worker=source_worker,
        target=args.target,
        output_dir=args.output_dir.resolve(),
    )
    print(json.dumps(metadata, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
