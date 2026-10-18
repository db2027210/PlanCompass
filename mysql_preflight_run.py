#!/usr/bin/env python3
"""Run experience-guided MySQL preflight points with stable measurement gates."""
from __future__ import annotations
import argparse, hashlib, json, os, re, subprocess, time
from pathlib import Path
from mysql_cost_adapter import MySQLCLIClient, MySQLCostAdapter

ROOT = Path(__file__).resolve().parent
WORK = {
  # Boundary/refinement scans select AP-9: 100% AP-9 exposes two structural
  # plans along memory_block_read_cost instead of diluting them in 13 queries.
  "hybench_ap": dict(container="ra_mysql_goal_hybench_tp",port=13365,password="CHANGE_ME",database="hybench_10x",cap=dict(io=100),argv=["mysql_hybench_runner.py","--workload","ap","--ap-clients","2","--ap-query-sequence","9"]),
  # 29c exposes six plans along memory_block_read_cost and remains bounded at
  # about 13 seconds/query in the timing pilot.
  "job": dict(container="ra-mysql-grid-job-w2",port=13412,password="CHANGE_ME",database="imdb_job",cap=dict(io=100),argv=["mysql_job_runner.py","--concurrency","1","--query-timeout-ms","20000","--query-names","29c.sql"]),
  # Q14 switches between lineitem ALL -> part eq_ref and part ALL -> lineitem
  # ref as row_evaluate_cost crosses the refined boundary.
  "tpch": dict(container="ra-mysql-grid-tpch-memory",port=13307,password="CHANGE_ME",database="tpch",cap=dict(io=100),argv=["mysql_benchmark_runner.py","--benchmark","tpch","--terminals","1","--query-ids","14"]),
  # Transaction 17 is the plan-sensitive CPU-heavy HyBench TP path selected by
  # the earlier successful A28 pilots.
  "hybench_tp": dict(container="ra_mysql_gd_hybench_xp_auto6",port=32790,password="CHANGE_ME",database="hybench",cap=dict(cpu=1.5,memory=8,io=100),bounded_warmup=True,argv=["mysql_hybench_runner.py","--workload","tp","--tp-clients","8","--tp-transaction-id","17"]),
  # StockLevel-only is read-only and plan-sensitive.  It prevents database
  # growth across points from being mistaken for a cost-knob effect.
  "tpcc": dict(container="gptuner_mysql_benchbase",port=57652,password="CHANGE_ME",runner_password="password",database="tpcc",cap=dict(io=100),argv=["mysql_benchmark_runner.py","--benchmark","tpcc","--terminals","8","--user","admin","--tpcc-weights","0,0,0,0,100"]),
  # Keep the small buffer pool to force physical reads, but allow enough
  # bandwidth for the analytical query to complete inside a measurement.
  "hybench_xp": dict(container="ra_mysql_preflight_xp_20260721_small",port=14368,password="CHANGE_ME",database="hybench_10x",cap=dict(io=8.0),io_device="8:0",io_bps=8388608,restart_each_point=True,argv=["mysql_hybench_runner.py","--workload","xp","--xap-clients","8","--xtp-clients","8","--ap-query-sequence","9"]),
}
BOTTLENECK_THRESHOLDS = {"memory": 0.70, "cpu": 0.80, "io": 0.75}


def _target_is_dominant(target, normalized):
    """Require the intended constrained resource to dominate CPU/memory peers.

    Non-target IO has no meaningful utilization denominator unless an IO cap is
    enforced, so it is used for dominance only when IO is itself the target.
    """
    target_value = float(normalized.get(target, 0.0))
    peers = [
        float(value)
        for name, value in normalized.items()
        if name in {"cpu", "memory"} and name != target
    ]
    return target_value >= max(peers, default=0.0)


def _bottleneck_relevant_stability(raw, normalized, target):
    """Gate the target and every materially utilized non-target resource.

    A non-target below ten percent of its enforced/reference capacity cannot
    be the active bottleneck.  Its relative CV can be arbitrarily large near
    zero, so it is recorded but does not force a ten-minute rerun.
    """
    metrics = raw.get("stability", {}).get("metrics", {})
    checked = []
    ignored_inactive = []
    for name in ("cpu", "memory", "io"):
        utilization = float(normalized.get(name, 0.0))
        materially_active = name == target or utilization > 0.10
        if materially_active:
            checked.append(name)
            if not bool(metrics.get(name, {}).get("stable", False)):
                return False, checked, ignored_inactive
        else:
            ignored_inactive.append(name)
    return True, checked, ignored_inactive


def _plan_fingerprint(samples):
    """Hash plan structure while ignoring cost values changed by the knobs."""
    volatile = {
        "cost_info", "rows_examined_per_scan", "rows_produced_per_join",
        "filtered", "data_read_per_join",
    }

    def strip_costs(value):
        if isinstance(value, dict):
            return {
                key: strip_costs(item)
                for key, item in value.items()
                if key not in volatile
            }
        if isinstance(value, list):
            return [strip_costs(item) for item in value]
        return value

    canonical = []
    for sample in samples:
        try:
            canonical.append(strip_costs(json.loads(str(sample))))
        except json.JSONDecodeError:
            canonical.append(str(sample))
    if not canonical:
        return None
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

def _set_io_limit(cfg, pid, bps):
    relative = None
    for line in (Path("/proc") / str(pid) / "cgroup").read_text().splitlines():
        _, controllers, path = line.split(":",2)
        if "blkio" in controllers.split(","):
            relative = path
            break
    if not relative:
        raise RuntimeError(f"cannot locate blkio cgroup for {cfg['container']}")
    root = Path("/sys/fs/cgroup/blkio") / relative.lstrip("/")
    value = f"{cfg['io_device']} {int(bps)}\n"
    for direction in ("read","write"):
        subprocess.run(
            ["sudo","tee",str(root / f"blkio.throttle.{direction}_bps_device")],
            input=value,text=True,stdout=subprocess.DEVNULL,check=True,
        )


def _kill_workload_sessions(cfg):
    """Terminate benchmark sessions left behind by a hard-stopped JVM."""
    env = dict(os.environ)
    env["MYSQL_PWD"] = cfg["password"]
    base = [
        "mysql", "--protocol=tcp", "--host=127.0.0.1",
        f"--port={cfg['port']}", "--user=root", "--batch",
        "--skip-column-names", cfg["database"],
    ]
    database = str(cfg["database"]).replace("'", "''")
    query = (
        "SELECT ID FROM information_schema.processlist "
        f"WHERE ID <> CONNECTION_ID() AND DB = '{database}'"
    )
    found = subprocess.run(
        [*base, "-e", query], capture_output=True, text=True,
        timeout=15, check=False, env=env,
    )
    if found.returncode != 0:
        raise RuntimeError(f"cannot enumerate workload sessions: {(found.stderr or found.stdout)[-1000:]}")
    ids = [item for item in found.stdout.split() if item.isdigit()]
    for connection_id in ids:
        subprocess.run(
            [*base, "-e", f"KILL CONNECTION {connection_id}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=15, check=False, env=env,
        )
    if ids:
        time.sleep(2)
    return len(ids)

def _runtime_config(cfg):
    inspect = json.loads(subprocess.check_output(["docker","inspect",cfg["container"]], text=True))[0]
    if cfg.get("restart_each_point"):
        # A hard-stopped HyBench JVM can leave its SQL executing inside MySQL.
        # Restarting this dedicated container makes every point independent.
        subprocess.run(["docker", "restart", cfg["container"]], check=True, stdout=subprocess.DEVNULL)
        time.sleep(5)
        inspect = json.loads(subprocess.check_output(["docker","inspect",cfg["container"]], text=True))[0]
    elif inspect["State"]["Status"] != "running":
        subprocess.run(["docker","start",cfg["container"]],check=True,stdout=subprocess.DEVNULL)
        time.sleep(5)
        inspect = json.loads(subprocess.check_output(["docker","inspect",cfg["container"]], text=True))[0]
    host = inspect["HostConfig"]
    cap = dict(cfg["cap"])
    cap["cpu"] = float(host["NanoCpus"]) / 1e9
    cap["memory"] = float(host["Memory"]) / 1024.0**3
    if cap["cpu"] <= 0 or cap["memory"] <= 0:
        raise RuntimeError(f"{cfg['container']} must have enforced CPU and memory limits")
    pid = int(inspect["State"]["Pid"])
    return dict(cfg, pid=pid, cap=cap)

def run(row, outdir, min_duration, max_duration, warmup):
    name = row["benchmark"]; cfg = WORK[name]
    cfg = _runtime_config(cfg)
    client = MySQLCLIClient(dict(host="127.0.0.1",port=cfg["port"],user="root",password=cfg["password"],database=cfg["database"],query_timeout_sec=60))
    ready_error = None
    for _ in range(30):
        try:
            client.execute("SELECT 1")
            ready_error = None
            break
        except RuntimeError as exc:
            ready_error = exc
            time.sleep(2)
    if ready_error is not None:
        raise RuntimeError(f"MySQL did not become ready: {ready_error}")
    adapter = MySQLCostAdapter(client)
    snap = adapter.snapshot()
    try:
        adapter.apply(row["values"])
        attempts = []
        durations = [min_duration] if min_duration >= max_duration else [min_duration, max_duration]
        for duration in durations:
            argv = ["/home/anonymous/miniconda3/envs/resource_adaptive/bin/python", *cfg["argv"], "--port",str(cfg["port"]),"--password",cfg.get("runner_password",cfg["password"]),"--database",cfg["database"],"--mysqld-pid",str(cfg["pid"]),"--warmup-duration-sec",str(warmup),"--duration-sec",str(duration),"--warmup-samples","0"]
            if name in {"hybench_ap","hybench_tp","hybench_xp"}: argv += ["--random-seed",str(row.get("seed", 20260721))]
            if name == "tpch": argv += ["--user", "root"]
            # All benchmark runners require an explicit output directory; append it
            # last so conditional arguments cannot accidentally consume it.
            run_out = (Path(outdir) / name / row["point_id"] / f"measure_{duration}s").resolve()
            run_out.mkdir(parents=True, exist_ok=True)
            argv += ["--output-root", str(run_out)]
            if name in {"hybench_tp", "hybench_xp"}:
                # Bound the actual monitored window, even when an AP query or
                # TP transaction is still draining at the HyBench boundary.
                argv += ["--hard-timeout-sec", str(duration)]
            if __import__("os").environ.get("PREFLIGHT_DEBUG") == "1": print("ARGV=" + repr(argv), flush=True)
            attempt_started = time.time()
            if cfg.get("io_bps") or cfg.get("bounded_warmup"):
                # Warm for one bounded minute under the same IO constraint as
                # the measurement.  Changing the cap at the measurement
                # boundary creates an artificial first-window ramp.
                if cfg.get("io_bps"):
                    _set_io_limit(cfg, cfg["pid"], cfg["io_bps"])
                warmup_argv = list(argv)
                warmup_argv[warmup_argv.index("--warmup-duration-sec") + 1] = "0"
                warmup_argv[warmup_argv.index("--duration-sec") + 1] = str(warmup)
                warmup_argv += ["--hard-timeout-sec", str(warmup + 5)]
                warmup_out = run_out / "prewarm_bounded"
                warmup_out.mkdir(parents=True, exist_ok=True)
                warmup_argv[warmup_argv.index("--output-root") + 1] = str(warmup_out)
                warmup_started = time.time()
                warmup_proc = subprocess.run(
                    warmup_argv, cwd=ROOT, text=True, capture_output=True,
                    timeout=warmup + 300, check=False,
                )
                warmup_elapsed = time.time() - warmup_started
                if warmup_proc.returncode != 0 and warmup_elapsed < warmup:
                    detail = warmup_proc.stdout + "\n" + warmup_proc.stderr
                    raise RuntimeError(f"bounded warmup failed rc={warmup_proc.returncode}: {detail[-1500:]}")
                if warmup_proc.returncode != 0:
                    print(f"WARMUP_HARD_STOP {row['point_id']} after {warmup_elapsed:.1f}s", flush=True)
                _kill_workload_sessions(cfg)
                argv[argv.index("--warmup-duration-sec") + 1] = "0"
            proc = subprocess.run(
                argv, cwd=ROOT, text=True, capture_output=True,
                timeout=warmup + duration + 300, check=False,
            )
            if name in {"hybench_ap", "hybench_tp", "hybench_xp"}:
                _kill_workload_sessions(cfg)
            state = json.loads(subprocess.check_output(
                ["docker","inspect",cfg["container"]], text=True
            ))[0]["State"]
            if state.get("Status") != "running" or int(state.get("Pid") or 0) != cfg["pid"]:
                raise RuntimeError(
                    f"container PID changed during measurement: "
                    f"before={cfg['pid']} after={state.get('Pid')} status={state.get('Status')}"
                )
            output = proc.stdout + "\n" + proc.stderr
            matches = re.findall(r"RESOURCE_METRICS=(\{.*\})", output)
            metrics = json.loads(matches[-1]) if matches else None
            if proc.returncode != 0 or metrics is None:
                raise RuntimeError(f"runner failed rc={proc.returncode}: {output[-1500:]}")
            raw = metrics.get("resource",{})
            norm = {k:float(raw.get(src,0))/float(cfg["cap"][k]) for k,src in (("cpu","cpu"),("memory","memory"),("io","io"))}
            relevant_stable, checked_resources, ignored_inactive = (
                _bottleneck_relevant_stability(raw, norm, row["target_bottleneck"])
            )
            explain_samples = metrics.get("_metadata",{}).get("explain_samples",[]) or []
            plan_fingerprint = _plan_fingerprint(explain_samples)
            attempts.append({
                "duration_sec": duration,
                "elapsed_sec": time.time() - attempt_started,
                "resource_raw": raw,
                "resource_norm": norm,
                "qps": metrics.get("qps"),
                "stable": relevant_stable,
                "stability_checked_resources": checked_resources,
                "stability_ignored_inactive_resources": ignored_inactive,
                "plan_fingerprint": plan_fingerprint,
            })
            if attempts[-1]["stable"]:
                break
            if duration < max_duration:
                print(f"UNSTABLE {row['point_id']} after {duration}s; extending to {max_duration}s", flush=True)
        final = attempts[-1]
        target = row["target_bottleneck"]
        threshold = BOTTLENECK_THRESHOLDS[target]
        workload_valid = bool(float(final.get("qps") or 0.0) > 0.0)
        constructed = bool(
            workload_valid
            and final["stable"]
            and final["resource_norm"].get(target, 0.0) >= threshold
            and _target_is_dominant(target, final["resource_norm"])
        )
        return {
            "point_id":row["point_id"], "benchmark":name,
            "target_bottleneck":row["target_bottleneck"], "values":row["values"],
            "multipliers":row["multipliers"], "resource_raw":final["resource_raw"],
            "resource_norm":final["resource_norm"], "qps":final["qps"],
            "stable":final["stable"],
            "workload_valid":workload_valid,
            "bottleneck_constructed":constructed,
            "target_utilization":final["resource_norm"].get(target),
            "bottleneck_threshold":threshold,
            "early_stopped":final["stable"] and final["duration_sec"] == min_duration and min_duration < max_duration,
            "measurement_duration_sec":final["duration_sec"], "attempts":attempts,
            "plan_fingerprint":final.get("plan_fingerprint"),
            "resource_capacity":cfg["cap"],
            "returncode":0, "elapsed_sec":time.time()-started,
        }
    finally:
        adapter.restore(snap)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--points",type=Path,default=Path("preflight/experience_points_20260721.json")); ap.add_argument("--output",type=Path,default=Path("preflight/experience_measurements_20260721.json")); ap.add_argument("--min-duration",type=int,default=180); ap.add_argument("--duration",type=int,default=600,help="maximum measured duration"); ap.add_argument("--warmup",type=int,default=60); ap.add_argument("--benchmarks",default="hybench_ap,job,tpch,hybench_tp,tpcc,hybench_xp"); ap.add_argument("--all-points",action="store_true"); ap.add_argument("--point-ids",default="",help="comma-separated exact point IDs; overrides the default p1 selection")
    a=ap.parse_args()
    if a.warmup != 60 or a.min_duration < 180 or a.duration > 600 or a.min_duration > a.duration:
        ap.error("protocol requires warmup=60, 180 <= min-duration <= duration <= 600")
    payload=json.loads(a.points.read_text()); selected=[]
    wanted=set(a.benchmarks.split(","))
    wanted_point_ids={item.strip() for item in a.point_ids.split(",") if item.strip()}
    for row in payload["points"]:
        point_selected=(row["point_id"] in wanted_point_ids) if wanted_point_ids else (a.all_points or row["point_id"].endswith("-p1"))
        if row["benchmark"] in wanted and point_selected: selected.append(row)
    out=[]
    if a.output.exists():
        try:
            previous=json.loads(a.output.read_text()).get("points",[])
            out=[item for item in previous if isinstance(item,dict)]
        except (OSError,json.JSONDecodeError):
            out=[]
    completed={item.get("point_id") for item in out if not item.get("error")}
    selected=[row for row in selected if row["point_id"] not in completed]

    def persist():
        a.output.parent.mkdir(parents=True,exist_ok=True)
        payload={"protocol":{"warmup_sec":a.warmup,"min_measure_sec":a.min_duration,"max_measure_sec":a.duration,"early_stop_on_stability":True,"bottleneck_thresholds":BOTTLENECK_THRESHOLDS},"points":out}
        temporary=a.output.with_suffix(a.output.suffix+".tmp")
        temporary.write_text(json.dumps(payload,indent=2)+"\n")
        temporary.replace(a.output)

    for row in selected:
        out=[item for item in out if item.get("point_id") != row["point_id"]]
        global started; started=time.time(); print(f"START {row['point_id']}",flush=True)
        try: out.append(run(row,a.output.parent,a.min_duration,a.duration,a.warmup)); print(json.dumps(out[-1],sort_keys=True),flush=True)
        except Exception as exc: out.append({"point_id":row["point_id"],"benchmark":row["benchmark"],"target_bottleneck":row.get("target_bottleneck"),"error":str(exc)}); print(f"ERROR {row['point_id']}: {exc}",flush=True)
        persist()
    persist()
if __name__ == "__main__": main()
