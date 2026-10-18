#!/usr/bin/env python3
"""Screen reproducible 8-D MySQL optimizer-cost points with real workloads.

The sampler is log-space Latin hypercube rather than unconstrained uniform random:
every parameter covers every stratum once, while the seed makes the points exactly
replayable.  Cost tables are restored to their exact original state on every exit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import subprocess
import time
from typing import Any, Mapping

from mysql_cost_adapter import MYSQL_COST_KEYS, MySQLCLIClient, MySQLCostAdapter


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def latin_hypercube_multipliers(
    names: list[str], count: int, seed: int, lower: float, upper: float
) -> list[dict[str, float]]:
    if count < 1:
        raise ValueError("count must be positive")
    if not (0 < lower < upper):
        raise ValueError("expected 0 < lower < upper")
    rng = random.Random(seed)
    log_lower, log_upper = math.log(lower), math.log(upper)
    columns: dict[str, list[float]] = {}
    for name in names:
        strata = list(range(count))
        rng.shuffle(strata)
        columns[name] = [
            math.exp(log_lower + ((stratum + rng.random()) / count) * (log_upper - log_lower))
            for stratum in strata
        ]
    return [{name: columns[name][row] for name in names} for row in range(count)]


def _strip_costs(value: Any) -> Any:
    """Remove numeric cost annotations while retaining physical plan structure."""
    if isinstance(value, list):
        return [_strip_costs(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _strip_costs(item)
            for key, item in value.items()
            if key not in {"cost_info", "query_cost", "read_cost", "eval_cost", "prefix_cost"}
        }
    return value


def plan_fingerprints(samples: Any) -> list[str]:
    fingerprints: list[str] = []
    for raw in samples if isinstance(samples, list) else []:
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            canonical = json.dumps(_strip_costs(parsed), sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError, json.JSONDecodeError):
            canonical = str(raw)
        fingerprints.append(hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16])
    return sorted(set(fingerprints))


def parse_metrics(stdout: str) -> dict[str, Any]:
    prefix = "RESOURCE_METRICS="
    for line in reversed(stdout.splitlines()):
        if line.startswith(prefix):
            payload = json.loads(line[len(prefix):])
            if not isinstance(payload, dict):
                break
            return payload
    raise RuntimeError("runner did not emit RESOURCE_METRICS")


def expanded_command(template: list[Any], output_dir: Path) -> list[str]:
    return [str(item).replace("{output_dir}", str(output_dir)) for item in template]


def normalize_resource(metrics: Mapping[str, Any], capability: Mapping[str, Any]) -> dict[str, float]:
    resource = metrics.get("resource")
    if not isinstance(resource, Mapping):
        raise RuntimeError("measurement has no resource object")
    return {
        name: float(resource[name]) / float(capability[name])
        for name in ("cpu", "memory", "io")
    }


def run_workload(
    spec: Mapping[str, Any], *, point_count: int, seed: int, lower: float,
    upper: float, output_root: Path,
) -> dict[str, Any]:
    output_root = output_root.resolve()
    name = str(spec["name"])
    mysql_cfg = dict(spec["mysql"])
    capability = dict(spec["capability"])
    target = str(spec["target"])
    target_key = "memory" if target == "memory" else target
    controller = MySQLCostAdapter(MySQLCLIClient(mysql_cfg))
    snapshot = controller.snapshot()
    defaults = {
        item.name: float(item.default_value)
        for item in snapshot if item.name in MYSQL_COST_KEYS
    }
    missing = sorted(set(MYSQL_COST_KEYS) - set(defaults))
    if missing:
        raise RuntimeError(f"missing compiled defaults: {missing}")
    names = list(MYSQL_COST_KEYS)
    multiplier_points = latin_hypercube_multipliers(names, point_count, seed, lower, upper)
    points: list[tuple[str, dict[str, float], dict[str, float]]] = [
        ("default", dict(defaults), {key: 1.0 for key in names})
    ]
    for index, multipliers in enumerate(multiplier_points, 1):
        values = {key: defaults[key] * multipliers[key] for key in names}
        points.append((f"lhs_{index:02d}", values, multipliers))

    workload_root = output_root / name
    summary_path = workload_root / "summary.json"
    result: dict[str, Any] = {
        "name": name,
        "target": target,
        "seed": seed,
        "range_multipliers": [lower, upper],
        "compiled_defaults": defaults,
        "rows": [],
        "complete": False,
    }
    write_json(summary_path, result)
    try:
        for index, (label, values, multipliers) in enumerate(points):
            controller.apply(values)
            point_dir = workload_root / f"{index:02d}_{label}"
            command = expanded_command(list(spec["command"]), point_dir)
            started = time.time()
            proc = subprocess.run(
                command,
                cwd=str(spec.get("cwd") or Path(__file__).resolve().parent),
                capture_output=True,
                text=True,
                timeout=float(spec.get("timeout_sec", 300)),
                check=False,
            )
            (point_dir / "runner.stdout.log").parent.mkdir(parents=True, exist_ok=True)
            (point_dir / "runner.stdout.log").write_text(proc.stdout, encoding="utf-8")
            (point_dir / "runner.stderr.log").write_text(proc.stderr, encoding="utf-8")
            row: dict[str, Any] = {
                "label": label,
                "params": values,
                "multipliers": multipliers,
                "command": command,
                "elapsed_sec": time.time() - started,
                "returncode": proc.returncode,
            }
            if proc.returncode == 0:
                metrics = parse_metrics(proc.stdout)
                normalized = normalize_resource(metrics, capability)
                metadata = metrics.get("_metadata") if isinstance(metrics.get("_metadata"), Mapping) else {}
                row.update({
                    "qps": float(metrics.get("qps", 0.0)),
                    "resource_raw": metrics.get("resource"),
                    "resource_normalized": normalized,
                    "target_util": normalized[target_key],
                    "target_bottleneck": normalized[target_key] >= 0.90,
                    "plan_fingerprints": plan_fingerprints(metadata.get("explain_samples")),
                    "measurement_summary": metadata.get("summary_path"),
                })
            else:
                row["error"] = (proc.stderr or proc.stdout)[-4000:]
            result["rows"].append(row)
            write_json(summary_path, result)
    finally:
        controller.restore(snapshot)

    successful = [row for row in result["rows"] if row.get("returncode") == 0]
    plans = {fingerprint for row in successful for fingerprint in row.get("plan_fingerprints", [])}
    bottleneck_rows = [row for row in successful if row.get("target_bottleneck")]
    result.update({
        "complete": True,
        "successful_points": len(successful),
        "distinct_plan_fingerprints": len(plans),
        "plan_sensitive": len(plans) > 1,
        "bottleneck_points": [row["label"] for row in bottleneck_rows],
        "bottleneck_constructed": bool(bottleneck_rows),
    })
    write_json(summary_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--workload", action="append", default=[])
    parser.add_argument("--points", type=int, default=3, help="random points, in addition to default")
    parser.add_argument("--seed", type=int, default=20260721)
    parser.add_argument("--lower-multiplier", type=float, default=0.25)
    parser.add_argument("--upper-multiplier", type=float, default=4.0)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    specs = manifest.get("workloads")
    if not isinstance(specs, list) or not specs:
        raise ValueError("manifest.workloads must be a non-empty list")
    selected = set(args.workload)
    summaries = []
    for offset, spec in enumerate(specs):
        if selected and spec.get("name") not in selected:
            continue
        summaries.append(run_workload(
            spec,
            point_count=args.points,
            seed=args.seed + offset * 1009,
            lower=args.lower_multiplier,
            upper=args.upper_multiplier,
            output_root=args.output_root,
        ))
    if not summaries:
        raise ValueError("no workload matched --workload")
    aggregate = {
        "manifest": str(args.manifest.resolve()),
        "output_root": str(args.output_root.resolve()),
        "workloads": [{
            key: summary.get(key) for key in (
                "name", "target", "successful_points", "distinct_plan_fingerprints",
                "plan_sensitive", "bottleneck_points", "bottleneck_constructed",
            )
        } for summary in summaries],
    }
    write_json(args.output_root / "aggregate.json", aggregate)
    print(json.dumps(aggregate, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
