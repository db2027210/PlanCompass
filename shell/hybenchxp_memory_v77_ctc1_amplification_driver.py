#!/usr/bin/env python3
"""HyBenchXP Memory v77 ctc1-amplification component A/B driver.

Owns rebuild pool w1 (port 57433) only for Phase A/B.
Amplifies the v76 fullscan ctc1 parallel-preserving transition by raising fixed
identical AP concurrency xapclient 1→2. Does not modify binary_plan_resource_tuner.py.

Hard requirements (inherited + v77):
- resolve cgroup path at runtime; prefer docker-limited/<cid>; fail closed
- 3-5s sampler self-test with >=3 numeric rows before any tuner
- align series rows to validation event window
- baseline hard gate written BEFORE launching a candidate
- seven cost params only; workload concurrency is construction, not an 8th param
- phases: baseline | candidate | full
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


REPO = Path("/home/anonymous/program/resource_adaptive")
RUNS = Path("/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs")
HOST = "127.0.0.1"
PORT = 57433
USER = "postgres"
DB = "hybench_sf10x"
CONTAINER = "binary_plan_tuning_hybenchxp_rebuild_20260703_180311_cpu_mem_w1"
CAP_GIB = 6.4
CAP_BYTES = CAP_GIB * (1024**3)
CPU_CORES = 10.0
SQL_FILE = REPO / "shell/hybenchxp_memory_ap6_transfer_groupagg_switch_v12_fullscan.sql"
PROTECTED_EXTERNAL_PIDS = {1383676, 3549340}

DEFAULT_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}

CTC1_PARAMS = {
    **DEFAULT_PARAMS,
    "cpu_tuple_cost": 1.0,
}

SEVEN_KEYS = tuple(DEFAULT_PARAMS.keys())


def log(msg: str) -> None:
    print(msg, flush=True)


def container_id() -> str:
    r = subprocess.run(
        ["docker", "inspect", "-f", "{{.Id}}", CONTAINER],
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.strip()


def find_mem_dir(cid: str) -> Path:
    """Resolve working-set cgroup path. Prefer docker-limited; fail closed."""
    candidates = [
        Path(f"/sys/fs/cgroup/memory/docker-limited/{cid}"),
        Path(f"/sys/fs/cgroup/memory/docker/{cid}"),
        Path(f"/sys/fs/cgroup/system.slice/docker-{cid}.scope"),
        Path(f"/sys/fs/cgroup/memory/system.slice/docker-{cid}.scope"),
    ]
    for p in candidates:
        if (p / "memory.usage_in_bytes").exists() or (p / "memory.current").exists():
            return p
    raise RuntimeError(
        f"cgroup mem dir not found for {cid}; tried: "
        + ", ".join(str(c) for c in candidates)
    )


def percentiles(vals: Sequence[float], ps: Sequence[float]) -> Dict[str, float]:
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
    """Sidecar cgroup working-set series sampler with runtime path resolution."""

    def __init__(self, out_csv: Path, interval_s: float = 1.0) -> None:
        self.out_csv = out_csv
        self.interval_s = interval_s
        self.proc: Optional[subprocess.Popen] = None
        self.mem_dir: Optional[Path] = None

    def start(self) -> Path:
        cid = container_id()
        mem_dir = find_mem_dir(cid)
        self.mem_dir = mem_dir
        self.out_csv.parent.mkdir(parents=True, exist_ok=True)
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
        (self.out_csv.parent / "sampler.pid").write_text(str(self.proc.pid), encoding="utf-8")
        log(f"[sampler] pid={self.proc.pid} mem_dir={mem_dir} csv={self.out_csv}")
        return mem_dir

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except Exception:
                try:
                    self.proc.terminate()
                except Exception:
                    pass
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except Exception:
                    pass


def load_ws_series(csv_path: Path) -> List[Tuple[float, float, float]]:
    rows: List[Tuple[float, float, float]] = []
    if not csv_path.exists():
        return rows
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ts = float(row["ts"])
                if "ws_pct_cap" in row and row["ws_pct_cap"] not in (None, ""):
                    pct = float(row["ws_pct_cap"])
                    gib = float(row.get("ws_gib") or (pct / 100.0 * CAP_GIB))
                elif "ws_pct" in row and row["ws_pct"] not in (None, ""):
                    pct = float(row["ws_pct"])
                    gib = pct / 100.0 * CAP_GIB
                else:
                    continue
            except (KeyError, ValueError):
                continue
            rows.append((ts, pct, gib))
    return rows


def summarize_full(series: List[Tuple[float, float, float]]) -> Dict[str, Any]:
    pcts = [p for _, p, _ in series]
    if not pcts:
        return {"n": 0, "avg": None, "p50": None}
    return {
        "n": len(pcts),
        "avg": sum(pcts) / len(pcts),
        "p50": statistics.median(pcts),
        "min": min(pcts),
        "max": max(pcts),
        **percentiles(pcts, [50, 90, 95, 99]),
    }


def extract_window(ev: dict) -> Optional[Tuple[float, float]]:
    w = ev.get("window")
    if isinstance(w, (list, tuple)) and len(w) == 2:
        return float(w[0]), float(w[1])
    stats = ev.get("resource_stats") or {}
    for key in ("window", "sample_window", "stable_window"):
        w2 = stats.get(key)
        if isinstance(w2, (list, tuple)) and len(w2) == 2:
            return float(w2[0]), float(w2[1])
    return None


def align_series(
    series: List[Tuple[float, float, float]],
    window: Optional[Tuple[float, float]],
) -> Dict[str, Any]:
    full = summarize_full(series)
    if not window:
        return {
            "window": None,
            "aligned": None,
            "full_series": full,
            "note": "no_window_in_jsonl",
            "valid": False,
        }
    a, b = window
    aligned = [(ts, p, g) for ts, p, g in series if a <= ts <= b]
    pct = [p for _, p, _ in aligned]
    if not pct:
        return {
            "window": [a, b],
            "window_duration_s": b - a,
            "aligned": {"n": 0, "avg": None, "p50": None},
            "full_series": full,
            "valid": False,
            "note": "zero_aligned_rows",
        }
    astats = {
        "n": len(pct),
        "avg": sum(pct) / len(pct),
        "p50": statistics.median(pct),
        "min": min(pct),
        "max": max(pct),
        **percentiles(pct, [50, 90, 95, 99]),
    }
    return {
        "window": [a, b],
        "window_duration_s": b - a,
        "aligned": astats,
        "full_series": full,
        "valid": int(astats["n"]) >= 45 and astats["avg"] is not None and astats["p50"] is not None,
    }


def sampler_self_test(out_dir: Path, duration_s: float = 4.0) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "sampler_self_test.csv"
    if csv_path.exists():
        csv_path.unlink()
    cid = container_id()
    mem_dir = find_mem_dir(cid)
    log(f"[self-test] cid={cid[:12]}... mem_dir={mem_dir}")
    sampler = WsSampler(csv_path, interval_s=0.8)
    sampler.start()
    time.sleep(duration_s)
    sampler.stop()
    series = load_ws_series(csv_path)
    n = len(series)
    result = {
        "ok": n >= 3,
        "n": n,
        "duration_s": duration_s,
        "mem_dir": str(mem_dir),
        "csv": str(csv_path),
        "sample_pcts": [round(p, 3) for _, p, _ in series[:8]],
        "require_n": 3,
    }
    if not result["ok"]:
        raise RuntimeError(
            f"sampler self-test failed: n={n} < 3; mem_dir={mem_dir}; csv={csv_path}"
        )
    log(f"[self-test] PASS n={n} pcts={result['sample_pcts']}")
    return result


def parse_validation_event(jsonl: Path, event_types: Tuple[str, ...]) -> Optional[dict]:
    if not jsonl.exists():
        return None
    last = None
    with jsonl.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            et = ev.get("event") or ev.get("type")
            if et in event_types and (
                "resources" in ev or "db_qps" in ev or ev.get("status") in ("ok", "failed", "error")
            ):
                last = ev
    return last


def extract_metrics(ev: dict) -> Dict[str, Any]:
    res = ev.get("resources") or {}
    stats = ev.get("resource_stats") or {}
    samples = ev.get("resource_samples") or {}
    sample_counts = stats.get("stable_sample_count") or {}
    mem_gib = float(res.get("memory") or 0.0)
    mem_pct = 100.0 * mem_gib / CAP_GIB
    cpu_cores = float(res.get("cpu") or 0.0)
    cpu_pct = 100.0 * cpu_cores / CPU_CORES
    io_mibs = float(res.get("io") or 0.0)
    return {
        "status": ev.get("status"),
        "db_qps": ev.get("db_qps"),
        "p95_ms": ev.get("p95_ms"),
        "p99_ms": ev.get("p99_ms"),
        "xp_qps": ev.get("hybench_xp_qps") or ev.get("xp_qps"),
        "xp_tps": ev.get("hybench_xp_tps") or ev.get("xp_tps"),
        "memory_gib": mem_gib,
        "memory_pct": mem_pct,
        "cpu_cores": cpu_cores,
        "cpu_pct": cpu_pct,
        "io_mib_s": io_mibs,
        "memory_source": stats.get("memory_source"),
        "io_source": stats.get("io_source"),
        "samples_memory": samples.get("memory_bytes") or sample_counts.get("memory_bytes"),
        "samples_cpu": samples.get("cpu_cores") or sample_counts.get("cpu_cores"),
        "samples_io": samples.get("io_bps") or sample_counts.get("io_bps"),
        "window": ev.get("window"),
        "accepted": ev.get("accepted"),
        "decision": ev.get("decision"),
        "label": ev.get("label"),
        "params": ev.get("params") or ev.get("effective_params"),
    }


def run_cmd(cmd: List[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log(f"[run] {' '.join(cmd)}")
    with log_path.open("w") as lf:
        p = subprocess.Popen(
            cmd,
            cwd=str(REPO),
            stdout=lf,
            stderr=subprocess.STDOUT,
            env=os.environ.copy(),
            start_new_session=True,
        )
        (log_path.parent / "tuner.pid").write_text(str(p.pid), encoding="utf-8")
        rc = p.wait()
    log(f"[run] exit={rc} pid was {(log_path.parent / 'tuner.pid').read_text().strip()} log={log_path}")
    return rc


def assert_w1_free() -> None:
    env = {**os.environ, "PGPASSWORD": "postgres"}
    r = subprocess.run(
        ["psql", "-h", HOST, "-p", str(PORT), "-U", USER, "-d", DB, "-Atc", "SELECT 1"],
        capture_output=True,
        text=True,
        env=env,
    )
    if r.returncode != 0 or r.stdout.strip() != "1":
        raise RuntimeError(f"w1 not connectable: {r.stderr or r.stdout}")
    r2 = subprocess.run(
        ["bash", "-lc", "ps -eo pid,cmd | rg 'binary_plan_resource_tuner.py' | rg -v rg || true"],
        capture_output=True,
        text=True,
    )
    for ln in r2.stdout.splitlines():
        if "io_fair" in ln or "cpu_fair" in ln:
            continue
        if "binary_plan_resource_tuner.py" in ln:
            if any(str(pid) in ln for pid in PROTECTED_EXTERNAL_PIDS):
                continue
            if "memory" in ln.lower() or "57433" in ln or "rebuild" in ln:
                raise RuntimeError(f"port/tuner conflict: {ln}")
    # prove no other Memory tuner owns 57433
    r3 = subprocess.run(
        ["bash", "-lc", "ps -eo pid,cmd | rg '57433' | rg -v 'rg |ss |docker-proxy|postgres:' || true"],
        capture_output=True,
        text=True,
    )
    for ln in r3.stdout.splitlines():
        if "binary_plan_resource_tuner" in ln or "hybench" in ln.lower() or "runxp" in ln:
            if any(str(pid) in ln for pid in PROTECTED_EXTERNAL_PIDS):
                continue
            # only fail if another controller/tuner appears to own the port
            if "binary_plan_resource_tuner" in ln:
                raise RuntimeError(f"57433 ownership conflict: {ln}")
    log("[preflight] w1 free for memory v77 controller")


def other_up_intersect_sweet(base: Dict[str, Any], cand: Dict[str, Any]) -> Dict[str, Any]:
    other_up = []
    if float(cand["cpu_pct"]) > float(base["cpu_pct"]) + 0.01:
        other_up.append("cpu")
    if float(cand["io_mib_s"]) > float(base["io_mib_s"]) + 0.01:
        other_up.append("io")
    sweet = []
    c_cpu = float(cand["cpu_pct"])
    c_io = float(cand["io_mib_s"])
    if 65.0 <= c_cpu <= 80.0:
        sweet.append("cpu")
    if 50.0 <= c_io <= 75.0:
        sweet.append("io")
    inter = sorted(set(other_up) & set(sweet))
    return {
        "other_up_resources": other_up,
        "other_sweet_spot_passed": sweet,
        "intersection": inter,
        "intersection_nonempty": bool(inter),
    }


def baseline_hard_gates(
    base_m: Dict[str, Any],
    aligned_audit: Dict[str, Any],
) -> Dict[str, Any]:
    aligned = aligned_audit.get("aligned") or {}
    n = int(aligned.get("n") or 0)
    avg = aligned.get("avg")
    p50 = aligned.get("p50")
    mem_src = str(base_m.get("memory_source") or "")
    io_src = str(base_m.get("io_source") or "")
    gates = {
        "status_ok": base_m.get("status") == "ok",
        "aligned_n_ge_45": n >= 45,
        "aligned_mean_present": avg is not None and avg == avg,
        "aligned_p50_present": p50 is not None and p50 == p50,
        "official_samples_mem_ge_45": int(base_m.get("samples_memory") or 0) >= 45,
        "official_samples_cpu_ge_45": int(base_m.get("samples_cpu") or 0) >= 45,
        "official_samples_io_ge_45": int(base_m.get("samples_io") or 0) >= 45,
        "working_set_source": "working_set" in mem_src,
        "io_source_docker_cgroup_v1_blkio": io_src == "docker_cgroup_v1_blkio",
        "mem_band_85_95": 85.0 <= float(base_m.get("memory_pct") or 0) <= 95.0,
        "cpu_lt_80": float(base_m.get("cpu_pct") or 0) < 80.0,
        "io_lt_75": float(base_m.get("io_mib_s") or 0) < 75.0,
        "window_present": aligned_audit.get("window") is not None,
    }
    failed = [k for k, v in gates.items() if not v]
    return {
        "gates": gates,
        "failed": failed,
        "ok": len(failed) == 0,
        "aligned_n": n,
        "aligned_avg": avg,
        "aligned_p50": p50,
        "prefer_mem_88_92": 88.0 <= float(base_m.get("memory_pct") or 0) <= 92.0,
        "impure_mem_gt_95": float(base_m.get("memory_pct") or 0) > 95.0,
        "mem_lt_85": float(base_m.get("memory_pct") or 0) < 85.0,
    }


def capture_plan(params: Dict[str, float], out_path: Path) -> Dict[str, Any]:
    """EXPLAIN (FORMAT TEXT) plan signature under given seven cost params."""
    sql = SQL_FILE.read_text(encoding="utf-8").strip().rstrip(";")
    set_lines = [f"SET {k} = {float(v)};" for k, v in params.items() if k in SEVEN_KEYS]
    set_lines += [
        "SET max_parallel_workers_per_gather = 4;",
        "SET enable_parallel_hash = on;",
    ]
    script = "\n".join(set_lines) + f"\nEXPLAIN (COSTS OFF, VERBOSE OFF) {sql};\n"
    env = {**os.environ, "PGPASSWORD": "postgres"}
    r = subprocess.run(
        ["psql", "-h", HOST, "-p", str(PORT), "-U", USER, "-d", DB, "-v", "ON_ERROR_STOP=1", "-At"],
        input=script,
        capture_output=True,
        text=True,
        env=env,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
    out_path.write_text(text, encoding="utf-8")
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    sig_bits = []
    workers = None
    for ln in lines:
        s = ln.strip()
        low = s.lower()
        if "workers planned" in low or "workers launched" in low:
            try:
                workers = int(s.split(":")[-1].strip())
            except ValueError:
                pass
        for token in (
            "HashAggregate",
            "GroupAggregate",
            "Aggregate",
            "Gather Merge",
            "Gather",
            "Sort",
            "Seq Scan",
            "Index Only Scan",
            "Index Scan",
            "Bitmap Heap Scan",
            "Parallel",
        ):
            if token.lower() in low and token not in sig_bits:
                # keep order of first appearance
                if any(t.lower() in low for t in [token]):
                    # only add if this line is primarily that node
                    if s.startswith("->") or s[0].isalpha() or s.startswith(" "):
                        node = token
                        if node not in sig_bits:
                            # map line start node more carefully
                            pass
        # extract leading node name
        node_line = s[2:].strip() if s.startswith("->") else s
        node_name = node_line.split(" on ")[0].split("  ")[0].strip()
        if node_name and node_name[0].isalpha() and node_name not in sig_bits:
            # filter noise
            if any(
                k in node_name
                for k in (
                    "HashAggregate",
                    "GroupAggregate",
                    "Aggregate",
                    "Gather",
                    "Sort",
                    "Seq Scan",
                    "Index",
                    "Bitmap",
                    "Hash",
                    "Nested Loop",
                    "Merge Join",
                    "Materialize",
                )
            ):
                sig_bits.append(node_name)
    # parallel-preserving partial transition heuristics
    text_join = "\n".join(lines)
    has_gather = "Gather" in text_join
    has_seq = "Seq Scan" in text_join
    has_index_only = "Index Only Scan" in text_join
    has_sort = "Sort" in text_join
    has_hashagg = "HashAggregate" in text_join
    has_groupagg = "GroupAggregate" in text_join or ("Aggregate" in text_join and "HashAggregate" not in text_join)
    has_gm = "Gather Merge" in text_join
    return {
        "rc": r.returncode,
        "params": params,
        "plan_text_path": str(out_path),
        "plan_lines": lines,
        "signature": " > ".join(sig_bits) if sig_bits else None,
        "workers_hint": workers,
        "flags": {
            "has_gather_or_gm": has_gather,
            "has_gather_merge": has_gm,
            "has_seq_scan": has_seq,
            "has_index_only": has_index_only,
            "has_sort": has_sort,
            "has_hashagg": has_hashagg,
            "has_groupagg_like": has_groupagg,
        },
        "parallel_preserving_partial_like": bool(
            has_gather and has_seq and (has_sort or has_gm) and not has_index_only
        ),
        "ctc2_collapse_like": bool(has_index_only and not has_seq),
    }


def write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_baseline(out_root: Path, config: str, summary: Dict[str, Any]) -> int:
    base_out = out_root / "default_baseline"
    base_out.mkdir(parents=True, exist_ok=True)
    sampler = WsSampler(base_out / "ws_series.csv")
    try:
        summary["cgroup_mem_dir"] = str(sampler.start())
    except Exception as exc:
        summary["classification"] = "error / cgroup_resolve_failed"
        summary["error"] = str(exc)
        write_json(out_root / "component_ab_summary.json", summary)
        log(f"[fatal] cgroup resolve failed: {exc}")
        return 11

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
    sampler.stop()
    summary["default_rc"] = rc
    summary["default_elapsed_s"] = time.time() - t0

    series_def = load_ws_series(base_out / "ws_series.csv")
    summary["default_series_full"] = summarize_full(series_def)

    base_jsonl = base_out / "memory_binary_search_hybenchxp.jsonl"
    base_ev = parse_validation_event(base_jsonl, ("baseline_validation", "baseline_only_complete"))
    if not base_ev:
        summary["classification"] = "error / missing_baseline_event"
        write_json(out_root / "component_ab_summary.json", summary)
        log(f"[fatal] missing baseline event in {base_jsonl}")
        return 2

    base_m = extract_metrics(base_ev)
    summary["default"] = base_m
    window = extract_window(base_ev)
    aligned_def = align_series(series_def, window)
    summary["default_series_aligned"] = aligned_def
    hard = baseline_hard_gates(base_m, aligned_def)
    summary["baseline_hard_gates"] = hard
    summary["candidate_launched"] = False
    summary["w1_candidate_spent"] = 0

    # plan under default params (baseline)
    try:
        summary["plan_default"] = capture_plan(DEFAULT_PARAMS, base_out / "explain_default.txt")
    except Exception as exc:
        summary["plan_default_error"] = str(exc)

    # durable hard-gate decision BEFORE any candidate
    decision = {
        "phase": "baseline_hard_gate",
        "ok": hard["ok"],
        "failed": hard["failed"],
        "gates": hard["gates"],
        "prefer_mem_88_92": hard.get("prefer_mem_88_92"),
        "impure_mem_gt_95": hard.get("impure_mem_gt_95"),
        "mem_lt_85": hard.get("mem_lt_85"),
        "official": {
            "status": base_m.get("status"),
            "memory_pct": base_m.get("memory_pct"),
            "cpu_pct": base_m.get("cpu_pct"),
            "io_mib_s": base_m.get("io_mib_s"),
            "db_qps": base_m.get("db_qps"),
            "samples_memory": base_m.get("samples_memory"),
            "samples_cpu": base_m.get("samples_cpu"),
            "samples_io": base_m.get("samples_io"),
            "memory_source": base_m.get("memory_source"),
            "io_source": base_m.get("io_source"),
        },
        "aligned": aligned_def.get("aligned"),
        "candidate_allowed": bool(hard["ok"]),
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    summary["baseline_decision"] = decision
    write_json(out_root / "baseline_hard_gate_decision.json", decision)
    write_json(out_root / "component_ab_summary.json", summary)

    a = aligned_def.get("aligned") or {}
    log(
        f"[default] status={base_m.get('status')} mem%={base_m.get('memory_pct'):.4f} "
        f"cpu%={base_m.get('cpu_pct'):.4f} io={base_m.get('io_mib_s'):.4f} "
        f"db_qps={base_m.get('db_qps')} samples={base_m.get('samples_memory')} "
        f"mem_src={base_m.get('memory_source')} io_src={base_m.get('io_source')} "
        f"aligned_n={a.get('n')} aligned_avg={a.get('avg')} aligned_p50={a.get('p50')}"
    )
    log(f"[baseline_gate] ok={hard['ok']} failed={hard['failed']}")

    if not hard["ok"]:
        summary["classification"] = "invalid / baseline_hard_gate_failed"
        write_json(out_root / "component_ab_summary.json", summary)
        md = [
            "# v77 Phase A baseline — HARD GATE FAILED",
            "",
            f"- out: `{out_root}`",
            f"- failed gates: `{hard['failed']}`",
            f"- candidate launched: **false** (budget not spent)",
            f"- official mem%/cpu/io/db: {base_m.get('memory_pct')} / {base_m.get('cpu_pct')} / {base_m.get('io_mib_s')} / {base_m.get('db_qps')}",
            f"- aligned n/avg/p50: {a.get('n')} / {a.get('avg')} / {a.get('p50')}",
            f"- impure>95: {hard.get('impure_mem_gt_95')} mem_lt_85: {hard.get('mem_lt_85')}",
            "",
        ]
        (out_root / "component_ab_summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
        log(f"[STOP] baseline hard gate failed: {hard['failed']} — NOT launching candidate")
        return 20

    summary["classification"] = "baseline_valid / candidate_not_run"
    write_json(out_root / "component_ab_summary.json", summary)
    (out_root / "component_ab_summary.md").write_text(
        "\n".join(
            [
                "# v77 Phase A baseline — VALID",
                "",
                f"- out: `{out_root}`",
                f"- official mem%/cpu/io/db: {base_m.get('memory_pct')} / {base_m.get('cpu_pct')} / {base_m.get('io_mib_s')} / {base_m.get('db_qps')}",
                f"- aligned n/avg/p50: {a.get('n')} / {a.get('avg')} / {a.get('p50')}",
                f"- prefer 88-92: {hard.get('prefer_mem_88_92')}",
                f"- candidate_allowed: true",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


def run_candidate(
    out_root: Path,
    config: str,
    params: Dict[str, float],
    params_name: str,
    summary: Dict[str, Any],
) -> int:
    # require existing baseline decision
    decision_path = out_root / "baseline_hard_gate_decision.json"
    if not decision_path.exists():
        summary["classification"] = "error / missing_baseline_decision"
        write_json(out_root / "component_ab_summary.json", summary)
        log("[fatal] baseline_hard_gate_decision.json missing; refuse candidate")
        return 21
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    if not decision.get("ok"):
        summary["classification"] = "error / candidate_refused_baseline_gate"
        write_json(out_root / "component_ab_summary.json", summary)
        log(f"[fatal] baseline gate not ok: {decision.get('failed')}")
        return 22

    base_out = out_root / "default_baseline"
    if not (base_out / "memory_binary_search_hybenchxp.jsonl").exists():
        summary["classification"] = "error / missing_baseline_artifacts"
        write_json(out_root / "component_ab_summary.json", summary)
        return 23

    # reload baseline metrics into summary if needed
    if "default" not in summary:
        base_ev = parse_validation_event(
            base_out / "memory_binary_search_hybenchxp.jsonl",
            ("baseline_validation", "baseline_only_complete"),
        )
        if not base_ev:
            summary["classification"] = "error / missing_baseline_event"
            write_json(out_root / "component_ab_summary.json", summary)
            return 2
        base_m = extract_metrics(base_ev)
        summary["default"] = base_m
        series_def = load_ws_series(base_out / "ws_series.csv")
        aligned_def = align_series(series_def, extract_window(base_ev))
        summary["default_series_aligned"] = aligned_def
        summary["default_series_full"] = summarize_full(series_def)
        summary["baseline_hard_gates"] = baseline_hard_gates(base_m, aligned_def)
        summary["baseline_decision"] = decision
    else:
        base_m = summary["default"]
        aligned_def = summary.get("default_series_aligned") or {}

    assert_w1_free()
    cand_out = out_root / params_name
    cand_out.mkdir(parents=True, exist_ok=True)
    (cand_out / "params.json").write_text(json.dumps(params, indent=2) + "\n", encoding="utf-8")

    # plan under candidate params before load
    try:
        summary["plan_candidate"] = capture_plan(params, cand_out / "explain_candidate.txt")
    except Exception as exc:
        summary["plan_candidate_error"] = str(exc)

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
            f"memory_v77_xap2_{params_name}",
            "--baseline-output-dir",
            str(base_out),
            "--params-json",
            json.dumps(params),
        ],
        cand_out / "tuner_stdout.log",
    )
    sampler2.stop()
    summary["cand_rc"] = rc2
    summary["cand_elapsed_s"] = time.time() - t1
    summary["candidate_launched"] = True
    summary["w1_candidate_spent"] = int(summary.get("w1_candidate_spent") or 0) + 1
    summary["params_candidate"] = params

    series_c = load_ws_series(cand_out / "ws_series.csv")
    summary["cand_series_full"] = summarize_full(series_c)

    cand_jsonl = cand_out / "memory_single_validation_hybenchxp.jsonl"
    cand_ev = parse_validation_event(cand_jsonl, ("single_candidate_validation",))
    if not cand_ev:
        summary["classification"] = "error / missing_candidate_event"
        write_json(out_root / "component_ab_summary.json", summary)
        log(f"[fatal] missing candidate event in {cand_jsonl}")
        return 3

    cand_m = extract_metrics(cand_ev)
    summary["candidate"] = cand_m
    window_c = extract_window(cand_ev)
    aligned_c = align_series(series_c, window_c)
    summary["cand_series_aligned"] = aligned_c
    a = (aligned_def.get("aligned") or {}) if isinstance(aligned_def, dict) else {}
    ac = aligned_c.get("aligned") or {}
    log(
        f"[cand] status={cand_m.get('status')} mem%={cand_m.get('memory_pct'):.4f} "
        f"cpu%={cand_m.get('cpu_pct'):.4f} io={cand_m.get('io_mib_s'):.4f} "
        f"db_qps={cand_m.get('db_qps')} samples={cand_m.get('samples_memory')} "
        f"mem_src={cand_m.get('memory_source')} io_src={cand_m.get('io_source')} "
        f"aligned_n={ac.get('n')} aligned_avg={ac.get('avg')} aligned_p50={ac.get('p50')}"
    )

    b_mem = float(base_m.get("memory_pct") or 0)
    c_mem = float(cand_m.get("memory_pct") or 0)
    b_qps = float(base_m.get("db_qps") or 0)
    c_qps = float(cand_m.get("db_qps") or 0)
    db_gain = ((c_qps - b_qps) / b_qps * 100.0) if b_qps > 0 else None
    mem_drop = b_mem - c_mem
    b_al = float((a.get("avg") if a.get("avg") is not None else b_mem) or 0)
    c_al = float((ac.get("avg") if ac.get("avg") is not None else c_mem) or 0)
    aligned_mem_drop = b_al - c_al
    xfer = other_up_intersect_sweet(base_m, cand_m)

    plan_c = summary.get("plan_candidate") or {}
    plan_flags = plan_c.get("flags") or {}
    checks = {
        "both_status_ok": base_m.get("status") == "ok" and cand_m.get("status") == "ok",
        "samples_ge_45_both": int(base_m.get("samples_memory") or 0) >= 45
        and int(cand_m.get("samples_memory") or 0) >= 45
        and int(base_m.get("samples_cpu") or 0) >= 45
        and int(cand_m.get("samples_cpu") or 0) >= 45
        and int(base_m.get("samples_io") or 0) >= 45
        and int(cand_m.get("samples_io") or 0) >= 45,
        "aligned_n_ge_45_both": int(a.get("n") or 0) >= 45 and int(ac.get("n") or 0) >= 45,
        "working_set_both": "working_set" in str(base_m.get("memory_source") or "")
        and "working_set" in str(cand_m.get("memory_source") or ""),
        "blkio_both": str(base_m.get("io_source") or "") == "docker_cgroup_v1_blkio"
        and str(cand_m.get("io_source") or "") == "docker_cgroup_v1_blkio",
        "baseline_mem_85_95": 85.0 <= b_mem <= 95.0,
        "baseline_cpu_lt_80": float(base_m.get("cpu_pct") or 0) < 80.0,
        "baseline_io_lt_75": float(base_m.get("io_mib_s") or 0) < 75.0,
        "cand_mem_lt_80": c_mem < 80.0,
        "cand_mem_drop_ge_5": mem_drop >= 5.0,
        "cand_mem_not_collapse_below_70": c_mem >= 70.0,
        "prefer_cand_mem_75_80": 75.0 <= c_mem < 80.0,
        "prefer_mem_drop_5_10": 5.0 <= mem_drop <= 10.0,
        "db_gain_ge_15": (db_gain is not None and db_gain >= 15.0),
        "db_gain_ge_20": (db_gain is not None and db_gain >= 20.0),
        "intersect_other_up_sweet": xfer["intersection_nonempty"],
        "real_memory_drop": mem_drop > 0.5,
        "aligned_real_memory_drop": aligned_mem_drop > 0.5,
        "parallel_preserving_partial_plan": bool(plan_c.get("parallel_preserving_partial_like")),
        "not_ctc2_collapse_plan": not bool(plan_c.get("ctc2_collapse_like")),
    }
    summary["delta"] = {
        "mem_drop_pp": mem_drop,
        "aligned_mem_drop_pp": aligned_mem_drop,
        "db_gain_pct": db_gain,
        "cpu_base": base_m.get("cpu_pct"),
        "cpu_cand": cand_m.get("cpu_pct"),
        "io_base": base_m.get("io_mib_s"),
        "io_cand": cand_m.get("io_mib_s"),
    }
    summary["transfer"] = xfer
    summary["checks"] = checks
    summary["plan_flags"] = plan_flags

    strict = (
        checks["both_status_ok"]
        and checks["samples_ge_45_both"]
        and checks["aligned_n_ge_45_both"]
        and checks["working_set_both"]
        and checks["blkio_both"]
        and checks["baseline_mem_85_95"]
        and checks["baseline_cpu_lt_80"]
        and checks["baseline_io_lt_75"]
        and checks["cand_mem_lt_80"]
        and checks["cand_mem_drop_ge_5"]
        and checks["db_gain_ge_15"]
        and checks["intersect_other_up_sweet"]
        and checks["parallel_preserving_partial_plan"]
        and checks["not_ctc2_collapse_plan"]
    )

    if not checks["both_status_ok"] or not checks["samples_ge_45_both"] or not checks["aligned_n_ge_45_both"]:
        cls = "invalid"
    elif not checks["real_memory_drop"] and not checks["aligned_real_memory_drop"]:
        cls = "reject / no_memory_drop"
    elif strict:
        cls = "strict_pass_component"
    elif checks["cand_mem_lt_80"] and checks["cand_mem_drop_ge_5"] and checks["db_gain_ge_15"]:
        cls = "near_miss / residual_clauses"
    elif checks["real_memory_drop"] and (db_gain or 0) > 0:
        cls = "valid_component / partial"
    else:
        cls = "reject"
    summary["classification"] = cls
    summary["strict_pass"] = bool(strict)
    failed_clauses = [k for k, v in checks.items() if not v]
    summary["failed_clauses"] = failed_clauses

    write_json(out_root / "component_ab_summary.json", summary)
    md = [
        "# v77 Phase B ctc1 amplification A/B",
        "",
        f"- out: `{out_root}`",
        f"- params: `{json.dumps(params)}`",
        f"- classification: **{cls}**",
        f"- strict_pass: **{strict}**",
        f"- cgroup: `{summary.get('cgroup_mem_dir')}`",
        f"- w1_candidate_spent: {summary.get('w1_candidate_spent')}",
        f"- plan_cand: `{plan_c.get('signature')}`",
        f"- parallel_preserving_partial: {plan_c.get('parallel_preserving_partial_like')}",
        f"- ctc2_collapse_like: {plan_c.get('ctc2_collapse_like')}",
        "",
        "## Metrics (official validation)",
        "| side | mem% | cpu% | io MiB/s | DB QPS | samples |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        (
            f"| default | {b_mem:.2f} | {float(base_m.get('cpu_pct') or 0):.2f} | "
            f"{float(base_m.get('io_mib_s') or 0):.2f} | {b_qps:.2f} | {base_m.get('samples_memory')} |"
        ),
        (
            f"| cand | {c_mem:.2f} | {float(cand_m.get('cpu_pct') or 0):.2f} | "
            f"{float(cand_m.get('io_mib_s') or 0):.2f} | {c_qps:.2f} | {cand_m.get('samples_memory')} |"
        ),
        "",
        "## Aligned sidecar WS series",
        f"- default: n={a.get('n')} avg={a.get('avg')} p50={a.get('p50')}",
        f"- cand: n={ac.get('n')} avg={ac.get('avg')} p50={ac.get('p50')}",
        f"- aligned mem drop pp: {aligned_mem_drop:.2f}",
        "",
        f"- official mem drop pp: {mem_drop:.2f}",
        f"- db gain %: {db_gain}",
        f"- transfer: `{json.dumps(xfer)}`",
        f"- failed_clauses: `{failed_clauses}`",
        "",
    ]
    (out_root / "component_ab_summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    log(f"[done] {cls} strict={strict} summary={out_root / 'component_ab_summary.json'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--config",
        default=str(
            REPO
            / "config/binary_plan_tuning_hybenchxp_memory_fair_v77_fullscan_ctc1_xap2_w1_20260714.json"
        ),
    )
    ap.add_argument("--stamp", default=time.strftime("%Y%m%d_%H%M%S"))
    ap.add_argument("--params-name", default="ctc1")
    ap.add_argument("--params-json", default=json.dumps(CTC1_PARAMS))
    ap.add_argument(
        "--phase",
        choices=("baseline", "candidate", "full"),
        default="baseline",
        help="baseline-only first is the default; candidate requires prior hard-gate decision",
    )
    ap.add_argument(
        "--out-root",
        default="",
        help="existing out root for candidate phase or resume",
    )
    ap.add_argument("--skip-self-test", action="store_true")
    args = ap.parse_args()
    params = json.loads(args.params_json)
    for k in list(params.keys()):
        if k not in DEFAULT_PARAMS:
            raise RuntimeError(f"illegal param key (only 7 cost params allowed): {k}")
    for k in SEVEN_KEYS:
        if k not in params:
            raise RuntimeError(f"missing required cost param: {k}")

    assert_w1_free()

    if args.out_root:
        out_root = Path(args.out_root)
        out_root.mkdir(parents=True, exist_ok=True)
        summary_path = out_root / "component_ab_summary.json"
        summary: Dict[str, Any] = {}
        if summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
    else:
        out_root = RUNS / f"hyxp_memory_fair_v77_xap2_{args.params_name}_{args.stamp}"
        out_root.mkdir(parents=True, exist_ok=True)
        summary = {}

    summary.update(
        {
            "type": "v77_ctc1_xap2_amplification",
            "out_root": str(out_root),
            "config": args.config,
            "params_candidate": params,
            "workload_construction": {
                "sql": "shell/hybenchxp_memory_ap6_transfer_groupagg_switch_v12_fullscan.sql",
                "xtpclient": 3,
                "xapclient": 2,
                "note": "only xap 1→2 vs v76; not an 8th tuned param",
            },
            "plan_note": (
                "amplify v76 ctc1: default HashAgg+Gather+Seq vs ctc1 Sorted+GM+Sort+partial Hash+Seq "
                "via xapclient=2 fixed concurrency"
            ),
            "driver_version": "v77_ctc1_amplification_cgroup_window_hardgate",
            "phase_requested": args.phase,
        }
    )
    if "w1_candidate_spent" not in summary:
        summary["w1_candidate_spent"] = 0

    if not args.skip_self_test and args.phase in ("baseline", "full"):
        try:
            summary["sampler_self_test"] = sampler_self_test(out_root / "_self_test", duration_s=4.0)
        except Exception as exc:
            summary["classification"] = "error / sampler_self_test_failed"
            summary["error"] = str(exc)
            write_json(out_root / "component_ab_summary.json", summary)
            log(f"[fatal] sampler self-test failed: {exc}")
            return 10

    write_json(out_root / "component_ab_summary.json", summary)
    log(f"[v77] out_root={out_root} phase={args.phase}")

    if args.phase in ("baseline", "full"):
        rc = run_baseline(out_root, args.config, summary)
        if rc != 0:
            return rc
        if args.phase == "baseline":
            log(f"[v77] baseline phase complete; decision at {out_root / 'baseline_hard_gate_decision.json'}")
            return 0

    # candidate / full after baseline ok
    summary = json.loads((out_root / "component_ab_summary.json").read_text(encoding="utf-8"))
    return run_candidate(out_root, args.config, params, args.params_name, summary)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("[interrupt] KeyboardInterrupt")
        sys.exit(130)
    except Exception as exc:
        log(f"[fatal] {exc}")
        sys.exit(99)
