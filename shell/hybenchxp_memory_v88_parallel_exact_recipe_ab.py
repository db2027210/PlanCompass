#!/usr/bin/env python3
"""Run four independent same-worker Memory A/B pairs in parallel."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict


REPO = Path("/home/anonymous/program/resource_adaptive")
RUN_ROOT = Path(
    "/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs/"
    "hyxp_memory_fair_v88_parallel_exact_recipe_20260715"
)
BASE_CONFIG = REPO / "config/binary_plan_tuning_hybenchxp_memory_fair_v87_normalized_io_w1_20260715.json"
REPORT_JSON = REPO / "report/bonect/hybenchxp_memory_fair_v88_parallel_exact_recipe_20260715.json"
REPORT_MD = REPO / "report/bonect/hybenchxp_memory_fair_v88_parallel_exact_recipe_20260715.md"

WORKER_IDS = {1: 1, 2: 4, 3: 5, 4: 6}
CPUSETS = {1: "112-121", 4: "148-157", 5: "160-169", 6: "172-181"}
DEFAULT = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}
CASES = {
    1: ("exact_ctc10", {**DEFAULT, "cpu_tuple_cost": 10.0}),
    2: ("ctc5", {**DEFAULT, "cpu_tuple_cost": 5.0}),
    3: ("ctc20", {**DEFAULT, "cpu_tuple_cost": 20.0}),
    4: ("ctc10_ptc3_boundary", {**DEFAULT, "cpu_tuple_cost": 10.0, "parallel_tuple_cost": 3.0}),
}
IO_CAPACITY_MIB_S = 200
XAP_BY_CASE = {1: 3, 2: 3, 3: 3, 4: 3}


def worker(index: int) -> Dict[str, Any]:
    worker_id = WORKER_IDS[index]
    return {
        "name": f"cpu_mem_w{worker_id}",
        "targets": ["memory"],
        "container": f"binary_plan_tuning_hybenchxp_rebuild_20260703_180311_cpu_mem_w{worker_id}",
        "host": "127.0.0.1",
        "port": 57432 + worker_id,
        "dbname": "hybench_sf10x",
        "user": "postgres",
        "password": "postgres",
        "data_dir": (
            "/disk2/resource_adaptive/binary_plan_tuning_hybenchxp_rebuild_20260703_180311/"
            f"workers/cpu_mem_w{worker_id}/pgdata"
        ),
        "disk_root": "/disk2",
        "cpuset_cpus": CPUSETS[worker_id],
    }


def config_for(index: int) -> Path:
    data = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    tuning = data["binary_plan_tuning"]
    validation = tuning["validation"]
    validation["docker_runtime"]["container"] = worker(index)["container"]
    validation["docker_workers"]["workers"] = [worker(index)]
    override = validation["hybench_xp"]["target_overrides"]["memory"]
    override["xtpclient"] = 1
    override["xapclient"] = XAP_BY_CASE[index]
    override["ap_query_weights"] = "65,35"
    override["ap_query_sequence"] = "6,6,6,6,6,6,8,8,8,8"
    tuning["workload_context"]["version"] = "v88_exact_v85_apheavy_65_35_xtp1_xap3"
    capacity = {"memory_gib": 6.4, "cpu_cores": 10, "io_mib_s": IO_CAPACITY_MIB_S}
    tuning["acceptance_policy"]["resource_capacity"] = copy.deepcopy(capacity)
    tuning["acceptance_policy"]["target_overrides"]["memory"]["resource_capacity"] = copy.deepcopy(capacity)
    out = REPO / f"config/binary_plan_tuning_hybenchxp_memory_fair_v88_exact_recipe_w{index}_20260715.json"
    out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return out


def run(cmd: list[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as stream:
        return subprocess.run(cmd, cwd=REPO, stdout=stream, stderr=subprocess.STDOUT).returncode


def last_event(path: Path, names: tuple[str, ...]) -> Dict[str, Any]:
    found: Dict[str, Any] = {}
    if not path.exists():
        return found
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") in names:
            found = event
    return found


def baseline(index: int, cfg: Path) -> Dict[str, Any]:
    label, _ = CASES[index]
    out = RUN_ROOT / f"w{index}_{label}" / "baseline"
    rc = run([
        sys.executable, "-u", "binary_plan_resource_tuner.py", "--config", str(cfg),
        "--output-dir", str(out), "binary-search", "--target", "memory", "--baseline-only",
    ], out / "controller.log")
    event = last_event(out / "memory_binary_search_hybenchxp.jsonl", ("baseline_validation",))
    memory_pct = 100.0 * float((event.get("resources") or {}).get("memory") or 0.0) / 6.4
    samples = event.get("resource_samples") or {}
    ok = (
        rc == 0 and event.get("status") == "ok" and 85.0 <= memory_pct <= 95.0
        and min(int(samples.get(k) or 0) for k in ("memory_bytes", "cpu_cores", "io_bps")) >= 45
    )
    return {"index": index, "config": str(cfg), "output": str(out), "rc": rc, "event": event,
            "memory_pct": memory_pct, "hard_gate_ok": ok}


def candidate(base: Dict[str, Any]) -> Dict[str, Any]:
    index = int(base["index"])
    label, params = CASES[index]
    out = RUN_ROOT / f"w{index}_{label}" / "candidate"
    rc = run([
        sys.executable, "-u", "binary_plan_resource_tuner.py", "--config", base["config"],
        "--output-dir", str(out), "validate-params", "--target", "memory",
        "--worker-name", worker(index)["name"], "--label", f"memory_v88_w{WORKER_IDS[index]}_{label}",
        "--baseline-output-dir", base["output"], "--params-json", json.dumps(params),
    ], out / "controller.log")
    summary_path = out / f"memory_v88_w{WORKER_IDS[index]}_{label}_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    return {**base, "candidate_rc": rc, "candidate_output": str(out), "params": params,
            "summary_path": str(summary_path), "summary": summary}


def compact(item: Dict[str, Any]) -> Dict[str, Any]:
    base = item.get("event") or {}
    summary = item.get("summary") or {}
    validation = summary.get("validation") or {}
    decision = summary.get("decision") or {}
    return {
        "worker": f"w{WORKER_IDS[item['index']]}", "case": CASES[item["index"]][0],
        "baseline_hard_gate_ok": item.get("hard_gate_ok"), "baseline_db_qps": base.get("db_qps"),
        "baseline_resources": base.get("resources"), "baseline_memory_pct": item.get("memory_pct"),
        "candidate_status": validation.get("status"), "candidate_db_qps": validation.get("db_qps"),
        "candidate_resources": validation.get("resources"), "accepted": summary.get("accepted"),
        "reason": summary.get("accept_reject_reason"), "decision": decision,
        "params": item.get("params"), "evidence": item.get("summary_path"),
    }


def main() -> int:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    configs = {index: config_for(index) for index in CASES}
    with ThreadPoolExecutor(max_workers=4) as pool:
        baselines = list(pool.map(lambda i: baseline(i, configs[i]), CASES))
    eligible = [item for item in baselines if item["hard_gate_ok"]]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(candidate, item) for item in eligible]
        completed = [future.result() for future in as_completed(futures)]
    by_index = {item["index"]: item for item in completed}
    rows = [compact(by_index.get(i, baselines[i - 1])) for i in sorted(CASES)]
    report = {
        "run_root": str(RUN_ROOT), "method": "four parallel independent same-worker real A/B pairs",
        "fixed_recipe": {"xtpclient": 1, "xapclient_by_case": XAP_BY_CASE, "weights": "65,35",
                         "sequence": "6,6,6,6,6,6,8,8,8,8", "seed": 4508333,
                         "memory_gib": 6.4, "cpu_cores": 10, "io_mib_s": IO_CAPACITY_MIB_S},
        "pairs": rows, "fallback_db_qps": 114.12708344892893,
    }
    REPORT_JSON.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    lines = ["# Memory v88 parallel exact-recipe A/B", "", "| worker | case | baseline mem% | baseline DB QPS | candidate DB QPS | accepted | reason | evidence |", "| --- | --- | ---: | ---: | ---: | --- | --- | --- |"]
    for row in rows:
        lines.append(f"| {row['worker']} | {row['case']} | {row.get('baseline_memory_pct')} | {row.get('baseline_db_qps')} | {row.get('candidate_db_qps')} | {row.get('accepted')} | {row.get('reason')} | `{row.get('evidence')}` |")
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
