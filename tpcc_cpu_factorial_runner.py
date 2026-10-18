"""Execute the reviewed 2^3 TPCC CPU-contention grid on the owned instance."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping

from tpcc_cpu_factorial_plan import build_plan, load_factorial


VARIABLES = ("innodb_spin_wait_delay", "innodb_sync_spin_loops", "innodb_thread_concurrency")


def _verify_owner(cfg: Mapping[str, Any]) -> None:
    target = cfg["target"]
    completed = subprocess.run(
        ["docker", "inspect", str(target["container"])], text=True, capture_output=True,
        timeout=30, check=False,
    )
    if completed.returncode:
        raise RuntimeError("cannot verify owned TPCC container")
    payload = json.loads(completed.stdout)
    item = payload[0]
    required_key, required_value = str(target["required_label"]).split("=", 1)
    labels = item.get("Config", {}).get("Labels", {}) or {}
    bindings = item.get("HostConfig", {}).get("PortBindings", {}).get("3306/tcp", []) or []
    ports = {int(binding["HostPort"]) for binding in bindings if binding.get("HostPort")}
    if labels.get(required_key) != required_value or int(target["port"]) not in ports:
        raise RuntimeError("TPCC target ownership label or port binding mismatch")


def _mysql(port: int, password: str, sql: str) -> str:
    env = dict(os.environ)
    env["MYSQL_PWD"] = password
    completed = subprocess.run(
        ["mysql", "--connect-timeout=5", "--batch", "--raw", "--skip-column-names",
         "-h", "127.0.0.1", "-P", str(port), "-uroot", "-e", sql],
        text=True, capture_output=True, timeout=30, check=False, env=env,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or "mysql command failed")
    return completed.stdout.strip()


def _snapshot(port: int, password: str) -> dict[str, int]:
    names = ",".join(repr(name) for name in VARIABLES)
    rows = _mysql(port, password, f"SHOW GLOBAL VARIABLES WHERE Variable_name IN ({names})")
    result = {}
    for line in rows.splitlines():
        name, value = line.split("\t", 1)
        result[name] = int(value)
    if set(result) != set(VARIABLES):
        raise RuntimeError(f"incomplete variable snapshot: {result}")
    return result


def _apply(port: int, password: str, values: Mapping[str, int]) -> None:
    for name, value in values.items():
        if name not in VARIABLES:
            raise ValueError(name)
        _mysql(port, password, f"SET GLOBAL {name}={int(value)}")
    actual = _snapshot(port, password)
    expected = {name: int(values[name]) for name in VARIABLES}
    if actual != expected:
        raise RuntimeError(f"variable verification failed: expected={expected}, actual={actual}")


def _last_json(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        if line.startswith("RESOURCE_METRICS="):
            line = line.split("=", 1)[1]
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and {"cpu", "memory", "io", "qps"} <= set(value):
            return value
    raise RuntimeError("workload monitor emitted no complete metrics JSON")


def _run_workload(*, pid: int, port: int, password: str, output: Path,
                  duration: int, terminals: int) -> tuple[dict[str, Any], float]:
    argv = [
        "python", "mysql_resource_monitor.py", "--pid", str(pid), "--interval-sec", "1",
        "--warmup-samples", "5", "--", "python", "mysql_benchmark_command.py",
        "--benchmark", "tpcc", "--host", "127.0.0.1", "--port", str(port),
        "--database", "tpcc", "--user", "root", "--password", password,
        "--output-dir", str(output), "--duration-sec", str(duration),
        "--clients", str(terminals), "--scale-factor", "143",
    ]
    started = time.monotonic()
    completed = subprocess.run(argv, cwd=Path(__file__).resolve().parent, text=True,
                               capture_output=True, timeout=duration + 240, check=False)
    elapsed = time.monotonic() - started
    output.mkdir(parents=True, exist_ok=True)
    (output / "monitor.stdout.log").write_text(completed.stdout or "", encoding="utf-8")
    (output / "monitor.stderr.log").write_text(completed.stderr or "", encoding="utf-8")
    if completed.returncode:
        raise RuntimeError(f"workload failed with {completed.returncode}: {output}")
    return _last_json(completed.stdout), elapsed


def execute(config: Path, output: Path, *, pid: int, password: str,
            duration: int | None = None, terminals: int | None = None,
            container: str | None = None, port_override: int | None = None,
            required_label: str | None = None, scenario: str = "cpu") -> dict[str, Any]:
    cfg = load_factorial(config)
    if container is not None or port_override is not None or required_label is not None:
        target = dict(cfg["target"])
        if container is not None: target["container"] = container
        if port_override is not None: target["port"] = int(port_override)
        if required_label is not None: target["required_label"] = required_label
        cfg = {**cfg, "target": target}
    _verify_owner(cfg)
    plan = build_plan(cfg)
    port = int(cfg["target"]["port"])
    measure = int(duration or cfg["benchmark"]["measure_seconds"])
    clients = int(terminals or cfg["benchmark"]["terminals"])
    output.mkdir(parents=True, exist_ok=False)
    original = _snapshot(port, password)
    baseline_values = {
        name: int(cfg.get("baseline_values", original)[name]) for name in VARIABLES
    }
    (output / "runtime_snapshot.json").write_text(json.dumps(original, indent=2) + "\n")
    started = time.monotonic()
    rows: list[dict[str, Any]] = []
    try:
        _apply(port, password, baseline_values)
        baseline, _ = _run_workload(pid=pid, port=port, password=password,
                                    output=output / "workloads" / "baseline",
                                    duration=measure, terminals=clients)
        (output / "baseline.json").write_text(json.dumps({"params": baseline_values, "resource": baseline,
                                                           "score": 0.0}, indent=2) + "\n")
        for index, point in enumerate(plan["points"]):
            values = {name: int(value) for name, value in point["values"].items()}
            try:
                _apply(port, password, values)
                metrics, elapsed = _run_workload(
                    pid=pid, port=port, password=password,
                    output=output / "workloads" / f"point-{index:02d}",
                    duration=measure, terminals=clients,
                )
                row = {
                    "candidate_id": f"tpcc_cpu_contention_{index}", "candidate_index": index,
                    "status": "ok", "params": values, "actual_resource": metrics,
                    "actual_score": float(metrics["qps"]), "elapsed_sec": elapsed,
                    "completed_at": datetime.now().isoformat(timespec="seconds"),
                    "source": "mysql_system_variable_full_factorial_real_replay",
                }
            finally:
                _apply(port, password, original)
            rows.append(row)
            with (output / "full_replay_results.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
    finally:
        _apply(port, password, original)
    summary = {
        "status": "completed", "full_grid_candidate_count": len(plan["points"]),
        "unique_success_count": len(rows), "attempted_this_run": len(rows),
        "successes_this_run": len(rows), "failures_this_run": 0,
        "elapsed_sec": time.monotonic() - started, "scenario": scenario, "benchmark": "tpcc",
        "endpoint": f"127.0.0.1:{port}",
    }
    (output / "full_replay_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/tpcc_cpu_contention_factorial.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--password", default='CHANGE_ME')
    parser.add_argument("--duration", type=int)
    parser.add_argument("--terminals", type=int)
    parser.add_argument("--container")
    parser.add_argument("--port", type=int)
    parser.add_argument("--required-label")
    parser.add_argument("--scenario", choices=("cpu", "none"), default="cpu")
    args = parser.parse_args()
    print(json.dumps(execute(args.config, args.output, pid=args.pid, password=args.password,
                             duration=args.duration, terminals=args.terminals,
                             container=args.container, port_override=args.port,
                             required_label=args.required_label, scenario=args.scenario), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
