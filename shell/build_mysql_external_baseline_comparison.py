#!/usr/bin/env python3
"""Build the formal MySQL B/C versus external-baseline comparison.

The external methods are compared only by improvement relative to their own
contemporaneous default-cost baseline.  Absolute QPS is intentionally not used
across methods because the campaigns were measured at different times.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FORMAL_DATA = (
    ROOT
    / "figures/algorithm_abc_mysql_bc_refresh_20260825/formal_results_data.json"
)
DEFAULT_EXTERNAL_ROOT = Path(
    "/disk1/resource_adaptive/mysql_external_baselines_20260826/"
    "20260826_gptuner_aqetuner_four_disk1_r5_affinity_replacement_clean"
)
DEFAULT_JSON_OUTPUT = (
    ROOT / "docs/mysql_bc_gptuner_aqetuner_final_comparison_20260826.json"
)
DEFAULT_MARKDOWN_OUTPUT = (
    ROOT / "docs/mysql_bc_gptuner_aqetuner_final_comparison_20260826.md"
)

SCENES = (
    "job_memory",
    "tpch_sf4_memory",
    "hybench_xp_cpu",
    "hybench_xp_io",
)
EXTERNAL_METHODS = ("gptuner", "aqetuner")
EXTERNAL_LABELS = {"gptuner": "GPTuner", "aqetuner": "AQETuner"}
INTERNAL_METHODS = ("Algorithm B", "Algorithm C")


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def finite_number(value: Any, label: str) -> float:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise RuntimeError(f"{label} is not finite: {value!r}")
    return float(value)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def relative_change_pct(before: float, after: float) -> float:
    require(before > 0.0, "baseline QPS must be positive")
    return (after / before - 1.0) * 100.0


def load_internal_rows(formal_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    mysql_scenes = ((formal_data.get("mysql") or {}).get("scenes") or {})
    rows: List[Dict[str, Any]] = []
    for scene in SCENES:
        scene_data = mysql_scenes.get(scene) or {}
        require(bool(scene_data), f"missing MySQL formal scene: {scene}")
        for method in INTERNAL_METHODS:
            record = ((scene_data.get("algorithms") or {}).get(method) or {})
            baseline_qps = finite_number(
                record.get("baseline_qps"), f"{scene}/{method} baseline_qps"
            )
            final_qps = finite_number(
                record.get("champion_qps"), f"{scene}/{method} champion_qps"
            )
            stored_gain = finite_number(
                record.get("qps_gain_pct"), f"{scene}/{method} qps_gain_pct"
            )
            computed_gain = relative_change_pct(baseline_qps, final_qps)
            require(
                math.isclose(stored_gain, computed_gain, rel_tol=1e-9, abs_tol=1e-9),
                f"{scene}/{method}: stored and computed gain differ",
            )
            rows.append(
                {
                    "scene": scene,
                    "scene_label": scene_data.get("label"),
                    "target_resource": scene_data.get("target_resource"),
                    "method": method,
                    "method_kind": "resource_adaptive",
                    "baseline_qps": baseline_qps,
                    "final_qps": final_qps,
                    "relative_qps_improvement_pct": computed_gain,
                    "comparison_metric": (
                        "method_own_baseline_relative_qps_improvement_pct"
                    ),
                    "absolute_qps_cross_method_comparable": False,
                    "champion_resources": record.get("champion_resources"),
                    "resource_change_pct": record.get("resource_change_pct"),
                    "outer_rounds": record.get("outer_rounds"),
                    "source": record.get("state_path"),
                }
            )
    return rows


def validate_external_authority(
    authority: Dict[str, Any], method: str, scene: str, path: Path
) -> Dict[str, Any]:
    prefix = f"{method}/{scene}"
    require(
        authority.get("schema_version") == "mysql_external_baseline_scene_authority_v1",
        f"{prefix}: authority schema mismatch",
    )
    require(authority.get("status") == "complete", f"{prefix}: not complete")
    require(authority.get("method") == method, f"{prefix}: method mismatch")
    require(authority.get("scene") == scene, f"{prefix}: scene mismatch")
    require(authority.get("formal_scope") == "mysql_only", f"{prefix}: scope mismatch")
    require(
        authority.get("cross_engine_comparison_prohibited") is True,
        f"{prefix}: cross-engine guard missing",
    )
    active = authority.get("active_parameters") or []
    inactive = authority.get("inactive_optimizer_costs_fixed_to_default") or []
    require(len(active) == 4 and len(set(active)) == 4, f"{prefix}: active costs !=4")
    require(len(inactive) == 4 and len(set(inactive)) == 4, f"{prefix}: fixed costs !=4")
    require(not set(active).intersection(inactive), f"{prefix}: active/fixed overlap")
    require(authority.get("active_parameter_count") == 4, f"{prefix}: active count !=4")
    require(authority.get("baseline_wave_count") == 1, f"{prefix}: baseline wave count !=1")
    require(authority.get("baseline_tuned_or_updated") is False, f"{prefix}: baseline mutated")
    budget = int(authority.get("candidate_budget") or 0)
    observations = authority.get("candidate_observations") or []
    require(budget > 0 and len(observations) == budget, f"{prefix}: candidate budget mismatch")
    require(
        all(
            (row.get("valid") is True and row.get("technical_failure") is False)
            or (row.get("valid") is False and row.get("technical_failure") is True)
            for row in observations
        ),
        f"{prefix}: candidate accounting contains an ambiguous row",
    )
    require(
        any(row.get("valid") is True for row in observations),
        f"{prefix}: no valid search observation",
    )
    require(
        authority.get("search_observations_are_not_final_results") is True,
        f"{prefix}: search/final separation missing",
    )
    require(authority.get("manual_mutation_after_start") is False, f"{prefix}: manual mutation")
    clean_restore = authority.get("clean_restore") or {}
    require(clean_restore.get("status") == "PASS", f"{prefix}: clean restore failed")

    baseline = authority.get("baseline") or {}
    validation = authority.get("final_champion_validation") or {}
    frozen = authority.get("frozen_champion") or {}
    require(baseline.get("valid") is True, f"{prefix}: baseline invalid")
    require(validation.get("valid") is True, f"{prefix}: champion validation invalid")
    baseline_qps = finite_number(baseline.get("db_qps"), f"{prefix} baseline_qps")
    final_qps = finite_number(validation.get("db_qps"), f"{prefix} final_qps")
    stored_gain = finite_number(
        validation.get("relative_qps_improvement_pct"), f"{prefix} gain"
    )
    computed_gain = relative_change_pct(baseline_qps, final_qps)
    require(
        math.isclose(stored_gain, computed_gain, rel_tol=1e-9, abs_tol=1e-9),
        f"{prefix}: stored and computed gain differ",
    )
    params = frozen.get("params") or {}
    if int(frozen.get("trial") or 0) == 0:
        require(bool(params), f"{prefix}: baseline champion params missing")
    else:
        require(set(params) == set(active), f"{prefix}: champion costs differ from active set")
    return {
        "scene": scene,
        "method": EXTERNAL_LABELS[method],
        "method_kind": "external_baseline",
        "baseline_qps": baseline_qps,
        "final_qps": final_qps,
        "relative_qps_improvement_pct": computed_gain,
        "comparison_metric": "method_own_baseline_relative_qps_improvement_pct",
        "absolute_qps_cross_method_comparable": False,
        "baseline_resources": baseline.get("resource_means"),
        "champion_resources": validation.get("resource_means"),
        "active_parameters": active,
        "inactive_optimizer_costs_fixed_to_default": inactive,
        "champion_params": params,
        "candidate_budget": budget,
        "source": str(path),
        "source_sha256": file_sha256(path),
    }


def load_external_rows(external_root: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for method in EXTERNAL_METHODS:
        canonical = external_root / method
        matches = sorted(
            path for path in external_root.glob(f"{method}*") if path.is_dir()
        )
        method_root = canonical if canonical.is_dir() else (
            matches[0] if len(matches) == 1 else canonical
        )
        require(
            method_root.is_dir(),
            f"missing or ambiguous method root for {method}: {matches}",
        )
        root_authority_path = method_root / "authority_manifest.json"
        require(root_authority_path.is_file(), f"missing root authority: {root_authority_path}")
        root_authority = load_json(root_authority_path)
        require(root_authority.get("status") == "complete", f"{method}: root authority incomplete")
        require(root_authority.get("two_workers_per_scene") is True, f"{method}: not two workers/scene")
        require(root_authority.get("four_scenes_run_in_parallel") is True, f"{method}: scenes not parallel")
        require(
            int(root_authority.get("physical_workload_invocations") or 0)
            == int(root_authority.get("valid_replays") or 0)
            + int(root_authority.get("technical_failures") or 0),
            f"{method}: physical accounting mismatch",
        )
        for scene in SCENES:
            path = method_root / scene / "authority_manifest.json"
            require(path.is_file(), f"missing scene authority: {path}")
            rows.append(validate_external_authority(load_json(path), method, scene, path))
    return rows


def markdown_table(rows: Iterable[Dict[str, Any]]) -> str:
    ordered = list(rows)
    lines = [
        "# MySQL Algorithms B/C  Baseline ",
        "",
        " cost baseline  QPS  QPS ",
        "",
        "|  |  | Baseline QPS |  QPS |  |",
        "|---|---|---:|---:|---:|",
    ]
    for row in ordered:
        lines.append(
            "| {scene} | {method} | {baseline:.4f} | {final:.4f} | {gain:+.2f}% |".format(
                scene=row["scene"],
                method=row["method"],
                baseline=row["baseline_qps"],
                final=row["final_qps"],
                gain=row["relative_qps_improvement_pct"],
            )
        )
    lines.extend(
        [
            "",
            " baseline 4 MySQL cost 4 optimizer cost  Docker worker",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--formal-data", type=Path, default=DEFAULT_FORMAL_DATA)
    parser.add_argument("--external-root", type=Path, default=DEFAULT_EXTERNAL_ROOT)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    formal_data = load_json(args.formal_data)
    internal_rows = load_internal_rows(formal_data)
    external_rows = load_external_rows(args.external_root)
    scene_metadata = {
        row["scene"]: {
            "scene_label": row.get("scene_label"),
            "target_resource": row.get("target_resource"),
        }
        for row in internal_rows
    }
    for row in external_rows:
        row.update(scene_metadata[row["scene"]])
    method_order = {
        name: index
        for index, name in enumerate((*INTERNAL_METHODS, "GPTuner", "AQETuner"))
    }
    scene_order = {name: index for index, name in enumerate(SCENES)}
    rows = sorted(
        [*internal_rows, *external_rows],
        key=lambda row: (scene_order[row["scene"]], method_order[row["method"]]),
    )
    result = {
        "contract": "mysql_bc_external_baseline_relative_comparison_v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "comparison_metric": "method_own_baseline_relative_qps_improvement_pct",
        "absolute_qps_cross_method_comparable": False,
        "cross_engine_comparison_prohibited": True,
        "external_root": str(args.external_root),
        "formal_data_source": str(args.formal_data),
        "formal_data_sha256": file_sha256(args.formal_data),
        "rows": rows,
    }
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.markdown_output.write_text(markdown_table(rows), encoding="utf-8")
    print(json.dumps({"status": "PASS", "row_count": len(rows)}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
