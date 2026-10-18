#!/usr/bin/env python3
"""HyBenchXP Memory v75 floor-only 2min A/B driver (default vs spc=2/rpc=4).

Does NOT modify binary_plan_resource_tuner.py. Uses existing baseline-only +
validate-params path on rebuild w1 only. Sidecar samples cgroup working set.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


REPO = Path("/home/anonymous/program/resource_adaptive")
RUNS = Path("/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs")
HOST = "127.0.0.1"
PORT = 57433
USER = "postgres"
DB = "hybench_sf10x"
CONTAINER = "binary_plan_tuning_hybenchxp_rebuild_20260703_180311_cpu_mem_w1"
CAP_GIB = 6.4
CAP_BYTES = CAP_GIB * (1024 ** 3)

DEFAULT_PARAMS = {
    "seq_page_cost": 1,
    "random_page_cost": 4,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000,
    "parallel_tuple_cost": 0.1,
}
SPC2_PARAMS = {
    **DEFAULT_PARAMS,
    "seq_page_cost": 2,
    "random_page_cost": 4,
}

FLOORS = {
    "loan_applicant_hash": {
        "sql_file": "shell/hybenchxp_memory_floor_loan_applicant_hash_v75.sql",
        "config": "config/binary_plan_tuning_hybenchxp_memory_fair_v75_floor_loan_applicant_hash_w1_20260714.json",
        "label_prefix": "memory_v75_floor_loan_applicant",
    },
    "transfer_amount_bucket": {
        "sql_file": "shell/hybenchxp_memory_floor_transfer_amount_bucket_v75.sql",
        "config": "config/binary_plan_tuning_hybenchxp_memory_fair_v75_floor_transfer_amount_bucket_w1_20260714.json",
        "label_prefix": "memory_v75_floor_transfer_bucket",
    },
    "saving_hash_sort": {
        "sql_file": "shell/hybenchxp_memory_floor_saving_hash_sort_v75.sql",
        "config": "config/binary_plan_tuning_hybenchxp_memory_fair_v75_floor_saving_hash_sort_w1_20260714.json",
        "label_prefix": "memory_v75_floor_saving_hash_sort",
    },
}


def log(msg: str) -> None:
    print(msg, flush=True)


def read_sql(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def plan_sig(plan_node: dict) -> str:
    parts: List[str] = []

    def walk(n: dict) -> None:
        if not isinstance(n, dict):
            return
        node = n.get("Node Type", "?")
        strat = n.get("Strategy")
        rel = n.get("Relation Name")
        workers = n.get("Workers Planned")
        label = node
        if strat:
            label += f":{strat}"
        if rel:
            label += f":{rel}"
        if workers:
            label += f":W{workers}"
        parts.append(label)
        for ch in n.get("Plans") or []:
            walk(ch)

    walk(plan_node)
    return " > ".join(parts)


def explain_sig(sql: str, costs: Dict[str, float]) -> Tuple[str, str]:
    env = {**os.environ, "PGPASSWORD": "postgres"}
    # strip SQL comments
    body_sql = "\n".join(
        ln for ln in sql.splitlines() if not ln.strip().startswith("--")
    ).strip().rstrip(";")
    sets = [f"SET {k} = {v};" for k, v in costs.items()]
    sets.append("SET max_parallel_workers_per_gather = 4;")
    body = "\n".join(sets) + "\nEXPLAIN (FORMAT JSON)\n" + body_sql + ";\n"
    r = subprocess.run(
        [
            "psql",
            "-h",
            HOST,
            "-p",
            str(PORT),
            "-U",
            USER,
            "-d",
            DB,
            "-X",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-At",
        ],
        input=body,
        text=True,
        capture_output=True,
        env=env,
        cwd=str(REPO),
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout)[:800])
    text = r.stdout.strip()
    idx = text.find("[")
    if idx < 0:
        raise RuntimeError(f"no json in EXPLAIN: {text[:200]!r}")
    data = json.loads(text[idx:])
    plan = data[0]["Plan"]
    sig = plan_sig(plan)
    h = hashlib.sha256(sig.encode("utf-8")).hexdigest()[:16]
    return sig, h


def container_id() -> str:
    r = subprocess.run(
        ["docker", "inspect", "-f", "{{.Id}}", CONTAINER],
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.strip()


def find_mem_dir(cid: str) -> Path:
    candidates = [
        Path(f"/sys/fs/cgroup/memory/docker/{cid}"),
        Path(f"/sys/fs/cgroup/memory/docker-limited/{cid}"),
        Path(f"/sys/fs/cgroup/system.slice/docker-{cid}.scope"),
        Path(f"/sys/fs/cgroup/memory/system.slice/docker-{cid}.scope"),
    ]
    for p in candidates:
        if (p / "memory.usage_in_bytes").exists() or (p / "memory.current").exists():
            return p
    raise RuntimeError(f"cgroup mem dir not found for {cid}")


def read_working_set_bytes(mem_dir: Path) -> float:
    usage_path = mem_dir / "memory.usage_in_bytes"
    if not usage_path.exists():
        usage_path = mem_dir / "memory.current"
    usage = float(usage_path.read_text().strip())
    inactive = 0.0
    stat_path = mem_dir / "memory.stat"
    if stat_path.exists():
        for line in stat_path.read_text().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] in ("inactive_file", "total_inactive_file"):
                inactive = float(parts[1])
                if parts[0] == "total_inactive_file":
                    break
    return max(0.0, usage - inactive)


def percentiles(vals: List[float], ps: List[float]) -> Dict[str, float]:
    if not vals:
        return {f"p{int(p)}": float("nan") for p in ps}
    s = sorted(vals)
    n = len(s)
    out: Dict[str, float] = {}
    for p in ps:
        if n == 1:
            out[f"p{int(p)}"] = s[0]
            continue
        k = (p / 100.0) * (n - 1)
        f = int(k)
        c = min(f + 1, n - 1)
        frac = k - f
        out[f"p{int(p)}"] = s[f] * (1 - frac) + s[c] * frac
    return out


class WsSampler:
    def __init__(self, out_csv: Path, interval_s: float = 1.0) -> None:
        self.out_csv = out_csv
        self.interval_s = interval_s
        self._stop = False
        self.proc: Optional[subprocess.Popen] = None
        self.samples: List[float] = []

    def start(self) -> None:
        cid = container_id()
        mem_dir = find_mem_dir(cid)
        self.out_csv.parent.mkdir(parents=True, exist_ok=True)
        # small helper process so it survives parent brief waits
        script = f"""
import time
from pathlib import Path
mem_dir = Path({str(mem_dir)!r})
out = Path({str(self.out_csv)!r})
interval = {self.interval_s}
cap = {CAP_BYTES}
with out.open('w') as f:
    f.write('ts,ws_bytes,ws_gib,ws_pct_cap\\n')
    f.flush()
    while True:
        usage_path = mem_dir / 'memory.usage_in_bytes'
        if not usage_path.exists():
            usage_path = mem_dir / 'memory.current'
        usage = float(usage_path.read_text().strip())
        inactive = 0.0
        stat = mem_dir / 'memory.stat'
        if stat.exists():
            for line in stat.read_text().splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[0] in ('inactive_file', 'total_inactive_file'):
                    inactive = float(parts[1])
                    if parts[0] == 'total_inactive_file':
                        break
        ws = max(0.0, usage - inactive)
        ts = time.time()
        f.write(f'{{ts}},{{ws}},{{ws/1024**3}},{{100.0*ws/cap}}\\n')
        f.flush()
        time.sleep(interval)
"""
        self.proc = subprocess.Popen(
            [sys.executable, "-u", "-c", script],
            cwd=str(REPO),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        log(f"[sampler] pid={self.proc.pid} csv={self.out_csv}")

    def stop(self) -> Dict[str, Any]:
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        vals: List[float] = []
        if self.out_csv.exists():
            for i, line in enumerate(self.out_csv.read_text().splitlines()):
                if i == 0 or not line.strip():
                    continue
                parts = line.split(",")
                if len(parts) >= 4:
                    vals.append(float(parts[3]))  # pct of cap
        self.samples = vals
        if not vals:
            return {"n": 0}
        ps = percentiles(vals, [50, 90, 95, 99])
        return {
            "n": len(vals),
            "avg": sum(vals) / len(vals),
            "peak": max(vals),
            "min": min(vals),
            **ps,
            "csv": str(self.out_csv),
        }


def parse_validation_event(jsonl: Path, event_names: Tuple[str, ...]) -> Optional[dict]:
    if not jsonl.exists():
        return None
    last = None
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        o = json.loads(line)
        if o.get("event") in event_names and o.get("status") in (None, "ok", "error", "failed"):
            # keep last matching event with metrics if possible
            if "resources" in o or "db_qps" in o or o.get("status"):
                last = o
    return last


def extract_metrics(ev: dict) -> Dict[str, Any]:
    res = ev.get("resources") or {}
    stats = ev.get("resource_stats") or {}
    samples = ev.get("resource_samples") or {}
    mem_gib = float(res.get("memory") or 0.0)
    mem_pct = 100.0 * mem_gib / CAP_GIB
    cpu_cores = float(res.get("cpu") or 0.0)
    cpu_pct = 100.0 * cpu_cores / 10.0  # worker cpuset 10 cores
    io_mibs = float(res.get("io") or 0.0)
    mem_bytes_stats = stats.get("memory_bytes") or {}
    return {
        "status": ev.get("status"),
        "db_qps": ev.get("db_qps"),
        "p95_ms": ev.get("p95_ms"),
        "p99_ms": ev.get("p99_ms"),
        "memory_gib": mem_gib,
        "memory_pct": mem_pct,
        "cpu_cores": cpu_cores,
        "cpu_pct": cpu_pct,
        "io_mib_s": io_mibs,
        "memory_source": stats.get("memory_source"),
        "io_source": stats.get("io_source"),
        "samples_memory": samples.get("memory_bytes") or stats.get("stable_sample_count", {}).get("memory_bytes"),
        "samples_cpu": samples.get("cpu_cores") or stats.get("stable_sample_count", {}).get("cpu_cores"),
        "samples_io": samples.get("io_bps") or stats.get("stable_sample_count", {}).get("io_bps"),
        "memory_bytes_stats": mem_bytes_stats,
        "accepted": ev.get("accepted"),
        "decision": ev.get("decision"),
        "label": ev.get("label"),
        "params": ev.get("params") or ev.get("effective_params"),
    }


def run_cmd(cmd: List[str], log_path: Path, env: Optional[dict] = None) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log(f"[run] {' '.join(cmd)}")
    with log_path.open("w") as lf:
        p = subprocess.Popen(
            cmd,
            cwd=str(REPO),
            stdout=lf,
            stderr=subprocess.STDOUT,
            env=env or os.environ.copy(),
            start_new_session=True,
        )
        (log_path.parent / "tuner.pid").write_text(str(p.pid), encoding="utf-8")
        rc = p.wait()
    log(f"[run] exit={rc} log={log_path}")
    return rc


def assert_w1_free() -> None:
    env = {**os.environ, "PGPASSWORD": "postgres"}
    r = subprocess.run(
        [
            "psql",
            "-h",
            HOST,
            "-p",
            str(PORT),
            "-U",
            USER,
            "-d",
            DB,
            "-Atc",
            "SELECT 1",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if r.returncode != 0 or r.stdout.strip() != "1":
        raise RuntimeError(f"w1 not connectable: {r.stderr or r.stdout}")
    # no concurrent memory tuner on rebuild w1
    r2 = subprocess.run(
        ["bash", "-lc", "ps -eo pid,cmd | rg 'binary_plan_resource_tuner.py' | rg -v rg || true"],
        capture_output=True,
        text=True,
    )
    lines = [
        ln
        for ln in r2.stdout.splitlines()
        if "memory" in ln.lower() or "hyxp_memory" in ln or "v75_floor" in ln
    ]
    # allow only ourselves later; at start should be empty of memory fair
    for ln in lines:
        if "io_fair" in ln:
            continue
        if "cpu_fair" in ln:
            continue
        log(f"[warn] existing tuner line: {ln}")


def evaluate_side(metrics: Dict[str, Any], series: Dict[str, Any], side: str) -> Dict[str, Any]:
    samples = int(metrics.get("samples_memory") or 0)
    mem = float(metrics.get("memory_pct") or 0.0)
    cpu = float(metrics.get("cpu_pct") or 0.0)
    io = float(metrics.get("io_mib_s") or 0.0)
    status = metrics.get("status")
    io_src = str(metrics.get("io_source") or "")
    mem_src = str(metrics.get("memory_source") or "")
    checks = {
        "status_ok": status == "ok",
        "samples_ge_45": samples >= 45,
        "physical_blkio": "blkio" in io_src or "cgroup" in io_src,
        "memory_working_set": "working_set" in mem_src,
        "mem_ge_75": mem >= 75.0,
        "cpu_lt_80": cpu < 80.0,
        "io_lt_75": io < 75.0,
        "series_n_ge_45": int(series.get("n") or 0) >= 45,
        "series_avg_ge_75": float(series.get("avg") or 0.0) >= 75.0,
    }
    ok = all(
        [
            checks["status_ok"],
            checks["samples_ge_45"],
            checks["physical_blkio"],
            checks["mem_ge_75"],
            checks["cpu_lt_80"],
            checks["io_lt_75"],
        ]
    )
    return {
        "side": side,
        "ok": ok,
        "checks": checks,
        "metrics": metrics,
        "series": series,
    }


def run_floor(floor_id: str, stamp: str) -> Dict[str, Any]:
    meta = FLOORS[floor_id]
    config = str(REPO / meta["config"])
    sql = read_sql(meta["sql_file"])
    out_root = RUNS / f"hyxp_memory_fair_v75_floor_ab_{floor_id}_{stamp}"
    out_root.mkdir(parents=True, exist_ok=True)
    result: Dict[str, Any] = {
        "floor_id": floor_id,
        "out_root": str(out_root),
        "config": config,
        "sql_file": meta["sql_file"],
    }

    # 1) EXPLAIN structure hash A/B
    log(f"=== EXPLAIN {floor_id} ===")
    sig_def, hash_def = explain_sig(sql, DEFAULT_PARAMS)
    sig_spc2, hash_spc2 = explain_sig(sql, SPC2_PARAMS)
    result["explain"] = {
        "default_sig": sig_def,
        "default_hash": hash_def,
        "spc2_sig": sig_spc2,
        "spc2_hash": hash_spc2,
        "structure_same": sig_def == sig_spc2,
    }
    log(f"default: {sig_def}")
    log(f"spc2:    {sig_spc2}")
    log(f"same={sig_def == sig_spc2} hash={hash_def}/{hash_spc2}")
    if sig_def != sig_spc2:
        result["classification"] = "reject / plan-structure-flip"
        result["pass"] = False
        (out_root / "floor_ab_summary.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return result

    # 2) default baseline-only 2min
    base_out = out_root / "default_baseline"
    base_out.mkdir(parents=True, exist_ok=True)
    sampler = WsSampler(base_out / "ws_series.csv")
    sampler.start()
    t0 = time.time()
    rc = run_cmd(
        [
            sys.executable,
            "-u",
            "binary_plan_resource_tuner.py",
            "--config",
            config,
            "--output-dir",
            str(base_out),
            "binary-search",
            "--target",
            "memory",
            "--baseline-only",
        ],
        base_out / "tuner_stdout.log",
    )
    series_def = sampler.stop()
    result["default_elapsed_s"] = time.time() - t0
    result["default_rc"] = rc
    base_jsonl = base_out / "memory_binary_search_hybenchxp.jsonl"
    base_ev = parse_validation_event(base_jsonl, ("baseline_validation",))
    if not base_ev:
        result["classification"] = "error / missing_baseline_event"
        result["pass"] = False
        result["default_series"] = series_def
        (out_root / "floor_ab_summary.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return result
    base_metrics = extract_metrics(base_ev)
    result["default"] = evaluate_side(base_metrics, series_def, "default")
    log(
        f"[default] status={base_metrics.get('status')} mem%={base_metrics.get('memory_pct'):.4f} "
        f"cpu%={base_metrics.get('cpu_pct'):.4f} io={base_metrics.get('io_mib_s'):.4f} "
        f"samples={base_metrics.get('samples_memory')} series_avg={series_def.get('avg')}"
    )

    # 3) spc2 validate-params 2min (paired baseline)
    cand_out = out_root / "spc2_rpc4"
    cand_out.mkdir(parents=True, exist_ok=True)
    label = f"{meta['label_prefix']}_spc2_rpc4"
    sampler2 = WsSampler(cand_out / "ws_series.csv")
    sampler2.start()
    t1 = time.time()
    rc2 = run_cmd(
        [
            sys.executable,
            "-u",
            "binary_plan_resource_tuner.py",
            "--config",
            config,
            "--output-dir",
            str(cand_out),
            "validate-params",
            "--target",
            "memory",
            "--worker-name",
            "cpu_mem_w1",
            "--label",
            label,
            "--baseline-output-dir",
            str(base_out),
            "--params-json",
            json.dumps(SPC2_PARAMS),
        ],
        cand_out / "tuner_stdout.log",
    )
    series_spc2 = sampler2.stop()
    result["spc2_elapsed_s"] = time.time() - t1
    result["spc2_rc"] = rc2
    cand_jsonl = cand_out / "memory_single_validation_hybenchxp.jsonl"
    cand_ev = parse_validation_event(cand_jsonl, ("single_candidate_validation",))
    if not cand_ev:
        result["classification"] = "error / missing_candidate_event"
        result["pass"] = False
        result["spc2_series"] = series_spc2
        (out_root / "floor_ab_summary.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return result
    cand_metrics = extract_metrics(cand_ev)
    result["spc2"] = evaluate_side(cand_metrics, series_spc2, "spc2")
    log(
        f"[spc2] status={cand_metrics.get('status')} mem%={cand_metrics.get('memory_pct'):.4f} "
        f"cpu%={cand_metrics.get('cpu_pct'):.4f} io={cand_metrics.get('io_mib_s'):.4f} "
        f"samples={cand_metrics.get('samples_memory')} series_avg={series_spc2.get('avg')}"
    )

    # 4) joint gates
    d_mem = float(base_metrics.get("memory_pct") or 0.0)
    c_mem = float(cand_metrics.get("memory_pct") or 0.0)
    delta_pp = abs(d_mem - c_mem)
    # prefer series avg if available
    d_series = float(series_def.get("avg") or d_mem)
    c_series = float(series_spc2.get("avg") or c_mem)
    series_delta = abs(d_series - c_series)

    joint = {
        "structure_same": True,
        "both_status_ok": base_metrics.get("status") == "ok" and cand_metrics.get("status") == "ok",
        "both_samples_ge_45": int(base_metrics.get("samples_memory") or 0) >= 45
        and int(cand_metrics.get("samples_memory") or 0) >= 45,
        "both_mem_ge_75_official": d_mem >= 75.0 and c_mem >= 75.0,
        "both_mem_ge_75_series_avg": d_series >= 75.0 and c_series >= 75.0,
        "delta_pp_official": delta_pp,
        "delta_pp_series_avg": series_delta,
        "delta_le_3pp_official": delta_pp <= 3.0,
        "delta_le_3pp_series": series_delta <= 3.0,
        "both_cpu_lt_80": float(base_metrics.get("cpu_pct") or 0) < 80
        and float(cand_metrics.get("cpu_pct") or 0) < 80,
        "both_io_lt_75": float(base_metrics.get("io_mib_s") or 0) < 75
        and float(cand_metrics.get("io_mib_s") or 0) < 75,
    }
    # Sustained floor pass: structure same + both avg Mem>=75 (official working set) + infra + cpu/io
    # delta<=3pp is soft preference
    hard_pass = (
        joint["structure_same"]
        and joint["both_status_ok"]
        and joint["both_samples_ge_45"]
        and joint["both_mem_ge_75_official"]
        and joint["both_cpu_lt_80"]
        and joint["both_io_lt_75"]
    )
    result["joint"] = joint
    result["pass"] = hard_pass
    if hard_pass and joint["delta_le_3pp_official"]:
        result["classification"] = "pass / sustained-floor"
    elif hard_pass:
        result["classification"] = "pass_soft_delta / sustained-floor-mem-ok-delta-gt-3pp"
    else:
        fails = [k for k, v in joint.items() if k.startswith("both_") and not v]
        if not joint["both_mem_ge_75_official"]:
            result["classification"] = "reject / sustained-floor-failed"
        else:
            result["classification"] = "reject / " + ",".join(fails or ["joint_fail"])

    (out_root / "floor_ab_summary.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )
    log(f"=== RESULT {floor_id}: pass={result['pass']} class={result['classification']} ===")
    log(
        f"mem default={d_mem:.4f}% spc2={c_mem:.4f}% delta={delta_pp:.4f}pp | "
        f"series avg default={d_series:.4f} spc2={c_series:.4f}"
    )
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--floors",
        default="loan_applicant_hash,transfer_amount_bucket,saving_hash_sort",
        help="comma-separated floor ids",
    )
    ap.add_argument("--stamp", default=time.strftime("%Y%m%d_%H%M%S"))
    args = ap.parse_args()
    floors = [x.strip() for x in args.floors.split(",") if x.strip()]
    for f in floors:
        if f not in FLOORS:
            raise SystemExit(f"unknown floor {f}; known={list(FLOORS)}")

    os.chdir(REPO)
    assert_w1_free()
    summary_all: Dict[str, Any] = {
        "stamp": args.stamp,
        "floors": [],
        "any_pass": False,
    }
    master_dir = RUNS / f"hyxp_memory_fair_v75_floor_ab_master_{args.stamp}"
    master_dir.mkdir(parents=True, exist_ok=True)

    for floor_id in floors:
        log(f"\n######## FLOOR A/B START {floor_id} ########")
        # ensure no leftover memory tuner before each
        assert_w1_free()
        try:
            res = run_floor(floor_id, args.stamp)
        except Exception as exc:
            log(f"[error] floor {floor_id}: {exc}")
            res = {
                "floor_id": floor_id,
                "pass": False,
                "classification": f"error / {type(exc).__name__}",
                "error": str(exc)[:1000],
            }
        summary_all["floors"].append(res)
        summary_all["any_pass"] = summary_all["any_pass"] or bool(res.get("pass"))
        (master_dir / "floor_ab_master_summary.json").write_text(
            json.dumps(summary_all, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        # small settle between floors
        time.sleep(3)

    log(f"\n######## ALL DONE any_pass={summary_all['any_pass']} ########")
    print(json.dumps(summary_all, indent=2, ensure_ascii=False, default=str), flush=True)
    return 0 if summary_all["any_pass"] else 2


if __name__ == "__main__":
    sys.exit(main())
