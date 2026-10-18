#!/usr/bin/env python3
"""Refresh the complete Algorithm C runtime/config SHA manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = (
    ROOT
    / "config/formal/frozen_mysql_deployment_schedule_20260723_v4_mysql_only"
    / "experience_table_sgd_method_manifest.json"
)
SCENARIOS = ("job_memory", "hybench_xp_io", "tpch_memory", "tpcc_cpu")
CORE = (
    "gradient_descent_optimizer.py",
    "experience_table_subset_optimizer.py",
    "common_outer_loop.py",
    "resource_utility.py",
    "mysql_plan_clustering.py",
    "trained_model_estimator.py",
    "mysql_parameter_optimizer.py",
    "plan_resource_estimator.py",
    "mysql_cost_adapter.py",
    "formal_stability.py",
    "formal_resource_sampler_20260721.py",
    "formal_mysql_sgd_orchestration.py",
    "formal_mysql_sgd_checkpoint.py",
    "formal_mysql_sgd_campaign.py",
    "host02_job_xp_mutex.py",
    "mysql_algorithm_b_production_services.py",
    "mysql_algorithm_c_production_services.py",
    "shell/manual_mysql_sgd_r0_campaign.py",
    "shell/manual_mysql_sgd_lane_controller.py",
    "shell/launch_mysql_sgd_four_lane_controllers.py",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path_value: str) -> str:
    path = Path(path_value)
    if path.is_absolute():
        return str(path.resolve().relative_to(ROOT.resolve()))
    return str(path)


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries: dict[str, dict[str, object]] = {}

    def add(path_value: str, role: str, scenarios: tuple[str, ...] = SCENARIOS) -> None:
        path_string = relative(path_value)
        path = ROOT / path_string
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError(f"missing or symlinked manifest input: {path}")
        entry = {
            "path": path_string,
            "sha256": sha256(path),
            "role": role,
            "scenario_scope": list(scenarios),
        }
        previous = entries.get(path_string)
        if previous is not None:
            if previous["sha256"] != entry["sha256"]:
                raise ValueError(f"conflicting manifest hashes for {path_string}")
            previous["scenario_scope"] = sorted(
                set(previous["scenario_scope"]) | set(entry["scenario_scope"])
            )
            return
        entries[path_string] = entry

    for path in CORE:
        add(path, "source")
    add(
        "config/formal/frozen_mysql_deployment_schedule_20260723_v4_mysql_only/"
        "experience_table_sgd_formal_config.json",
        "config",
    )
    for scenario in SCENARIOS:
        config_path = (
            ROOT / "config/formal/mysql_sgd_production"
            / f"{scenario}.algorithm_c.optimizer.json"
        )
        config = json.loads(config_path.read_text(encoding="utf-8"))
        add(str(config_path), "config", (scenario,))
        add(str(config["profile"]), "profile", (scenario,))
        add(str(config["workload_registry"]["path"]), "registry", (scenario,))
        table = config.get("evidence", {}).get("experience_table", {}).get("path")
        if not table:
            raise ValueError(f"{scenario} lacks experience table evidence")
        add(str(table), "experience_table", (scenario,))
        for evidence_name, evidence in config.get("evidence", {}).items():
            if evidence_name in {"experience_table", "template_deployment"}:
                continue
            if isinstance(evidence, dict) and evidence.get("path"):
                candidate = ROOT / relative(str(evidence["path"]))
                if candidate.is_file() and not candidate.is_symlink():
                    add(str(candidate), "r0" if "r0" in evidence_name else "evidence", (scenario,))

    manifest["registered_sources"] = [entries[key] for key in sorted(entries)]
    manifest["formal_launch_authorized"] = False
    temporary = MANIFEST.with_suffix(MANIFEST.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(MANIFEST)
    print(f"registered_entries={len(entries)} manifest_sha256={sha256(MANIFEST)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
