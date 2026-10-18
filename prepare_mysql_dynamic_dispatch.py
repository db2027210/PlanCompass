"""Build runtime-bound tasks for the global MySQL worker scheduler.

This command is read-only with respect to Docker.  It includes only owned,
running and pingable workers, and leaves endpoint/PID placeholders for the
scheduler to bind after an idle worker claims a compatible task.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping, Sequence

from experiment_matrix_mysql import audit_history, expand_runs, load_manifest


FAMILY_FOR_BENCHMARK = {
    "hybench_ap": "hybench", "hybench_tp": "hybench", "hybench_xp": "hybench",
    "job": "job", "tpch": "tpch", "tpcc": "tpcc",
}
DATABASES = {"hybench": "hybench_sf10x", "job": "imdb_job", "tpch": "tpch", "tpcc": "tpcc"}
SCALE_FACTORS = {"hybench": 10, "job": 1, "tpch": 10, "tpcc": 143}


def _run(argv: Sequence[str], *, env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), text=True, capture_output=True, check=False,
                          timeout=30, env=dict(env) if env is not None else None)


def _memory_gb(value: str) -> float:
    text = value.strip().lower()
    if text.endswith("g"):
        return float(text[:-1])
    if text.endswith("m"):
        return float(text[:-1]) / 1024.0
    return float(text) / (1024.0 ** 3)


def discover_workers(pool_cfg: Mapping[str, Any], manifest: Mapping[str, Any],
                     selected_families: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    capabilities = {row["worker_id"]: row for row in manifest["worker_pool"]}
    ready: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in pool_cfg["workers"]:
        family = str(row["family"])
        if selected_families and family not in selected_families:
            continue
        inspected = _run(["docker", "inspect", row["name"]])
        if inspected.returncode:
            rejected.append({"worker_id": row["worker_id"], "reason": "container_absent"})
            continue
        item = json.loads(inspected.stdout)[0]
        labels = item.get("Config", {}).get("Labels", {}) or {}
        state = item.get("State", {})
        if labels.get(pool_cfg["label_key"]) != pool_cfg["worker_label_value"]:
            rejected.append({"worker_id": row["worker_id"], "reason": "ownership_label_mismatch"})
            continue
        pid = int(state.get("Pid", 0))
        if not state.get("Running") or pid <= 0:
            rejected.append({"worker_id": row["worker_id"], "reason": "not_running"})
            continue
        env = dict(os.environ); env["MYSQL_PWD"] = pool_cfg["root_password"]
        ping = _run(["mysqladmin", "--connect-timeout=5", "-h127.0.0.1", "-P", str(row["port"]),
                     "-uroot", "ping"], env=env)
        if ping.returncode:
            rejected.append({"worker_id": row["worker_id"], "reason": "mysql_not_ready",
                             "stderr": ping.stderr[-500:]})
            continue
        ready.append({
            "worker_id": row["worker_id"],
            "benchmarks": capabilities[row["worker_id"]]["benchmarks"],
            "bottlenecks": capabilities[row["worker_id"]].get(
                "bottlenecks", ["cpu", "memory", "io", "none"]),
            "command_context": {
                "port": int(row["port"]), "pid": pid, "database": DATABASES[family],
                "container": row["name"],
                "cpu_cores": float(row["cpus"]), "mem_gb": _memory_gb(row["memory"]),
                "disk_bw_mb": float(row.get("disk_bw_mb", 100.0)),
                "scale_factor": SCALE_FACTORS[family],
            },
        })
    return ready, rejected


def _next_output(root: Path, run_id: str) -> Path:
    candidate = root / run_id
    suffix = 1
    while candidate.exists():
        suffix += 1
        candidate = root / f"{run_id}-attempt-{suffix}"
    return candidate


def build_tasks(manifest: Mapping[str, Any], missing: Sequence[Mapping[str, Any]],
                workers: Sequence[Mapping[str, Any]], results_root: Path) -> list[dict[str, Any]]:
    script = Path(__file__).resolve().parent / "run_mysql_logical_grid.py"
    tasks = []
    for run in missing:
        benchmark = str(run["benchmark"]).lower()
        if not any(
            benchmark in {str(item).lower() for item in worker["benchmarks"]}
            and str(run["bottleneck"]).lower() in {
                str(item).lower() for item in worker.get("bottlenecks", ["cpu", "memory", "io", "none"])
            }
            for worker in workers
        ):
            continue
        destination = _next_output(results_root, str(run["run_id"]))
        command = [
            sys.executable, str(script), "--exp-id", str(run["exp_id"]),
            "--repeat-id", str(run["repeat_id"]), "--benchmark", benchmark,
            "--bottleneck", str(run["bottleneck"]), "--port", "{port}", "--pid", "{pid}",
            "--database", "{database}", "--cpu-cores", "{cpu_cores}", "--mem-gb", "{mem_gb}",
            "--disk-bw-mb", "{disk_bw_mb}", "--scale-factor", "{scale_factor}",
            "--container", "{container}",
            "--required-label", "resource-adaptive-mysql.owner=mysql-grid-20260718-worker",
            "--output", str(destination),
        ]
        tasks.append({**dict(run), "cwd": str(script.parent), "command": command,
                      "output": str(destination)})
    return tasks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("config/mysql_experiment_manifest.json"))
    parser.add_argument("--pool", type=Path, default=Path("config/mysql_dynamic_worker_pool.json"))
    parser.add_argument("--history", type=Path, action="append", default=[])
    parser.add_argument("--families", default="", help="optional comma-separated families")
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    pool_cfg = json.loads(args.pool.read_text())
    selected = {item.strip() for item in args.families.split(",") if item.strip()}
    unknown = selected - set(pool_cfg["sources"])
    if unknown:
        raise SystemExit(f"unknown families: {sorted(unknown)}")
    workers, rejected = discover_workers(pool_cfg, manifest, selected)
    audit = audit_history(manifest, [*args.history, args.results_root])
    args.results_root.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    tasks = build_tasks(manifest, audit["missing"], workers, args.results_root.resolve())
    (args.output_dir / "workers.json").write_text(json.dumps(workers, indent=2) + "\n")
    (args.output_dir / "tasks.json").write_text(json.dumps(tasks, indent=2) + "\n")
    summary = {
        "manifest_runs": len(expand_runs(manifest)), "history_reused": audit["reused_count"],
        "history_missing": audit["missing_count"], "ready_workers": len(workers),
        "rejected_workers": rejected, "dispatchable_tasks": len(tasks),
        "policy": "idle_worker_claims_oldest_compatible_global_task",
    }
    (args.output_dir / "preparation_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0 if workers and tasks else 2


if __name__ == "__main__":
    raise SystemExit(main())
