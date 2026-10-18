#!/usr/bin/env python3
"""HyBenchXP Memory v85 Phase B same-vector composition A/B driver.

Sequential same-worker >=3min baseline-only then validate-params on rebuild w1.
Does not modify binary_plan_resource_tuner.py.

Hard requirements (post Codex recovery):
- resolve cgroup path at runtime; prefer docker-limited/<cid>; fail closed
- 3-5s sampler self-test with >=3 numeric rows before any tuner
- align series rows to validation event window (not whole-file averages)
- baseline hard gate: aligned n>=45, mean+p50 present, status/purity/sources/samples
  all valid BEFORE launching a candidate
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

DEFAULT_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}

CTC10_PARAMS = {
    **DEFAULT_PARAMS,
    "cpu_tuple_cost": 10.0,
}


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
            # prefer existing path; docker-limited listed first
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
    """Return list of (ts, ws_pct, ws_gib)."""
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
    """3-5s sampler self-test; require >=3 numeric rows."""
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
            if "1383676" in ln:
                continue
            # only block if this is a memory tuner or uses rebuild port
            if "memory" in ln.lower() or "57433" in ln or "rebuild" in ln:
                raise RuntimeError(f"port/tuner conflict: {ln}")
    log("[preflight] w1 free for memory controller")


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
    """Hard gates: fail any => do NOT launch candidate."""
    aligned = aligned_audit.get("aligned") or {}
    n = int(aligned.get("n") or 0)
    avg = aligned.get("avg")
    p50 = aligned.get("p50")
    mem_src = str(base_m.get("memory_source") or "")
    io_src = str(base_m.get("io_source") or "")
    gates = {
        "status_ok": base_m.get("status") == "ok",
        "aligned_n_ge_45": n >= 45,
        "aligned_mean_present": avg is not None and avg == avg,  # not NaN
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
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--config",
        default=str(
            REPO
            / "config/binary_plan_tuning_hybenchxp_memory_fair_v85_same_vector_comp_w1_20260715.json"
        ),
    )
    ap.add_argument("--stamp", default=time.strftime("%Y%m%d_%H%M%S"))
    ap.add_argument("--params-name", default="ctc10")
    ap.add_argument(
        "--params-json",
        default=json.dumps(CTC10_PARAMS),
        help="seven cost params JSON",
    )
    ap.add_argument("--skip-self-test", action="store_true")
    args = ap.parse_args()
    params = json.loads(args.params_json)
    # ensure only seven cost keys
    for k in list(params.keys()):
        if k not in DEFAULT_PARAMS:
            raise RuntimeError(f"illegal param key (only 7 cost params allowed): {k}")

    assert_w1_free()

    out_root = RUNS / f"hyxp_memory_fair_v85_same_vector_comp_{args.params_name}_{args.stamp}"
    out_root.mkdir(parents=True, exist_ok=True)
    summary: Dict[str, Any] = {
        "type": "v85_phase_b_same_vector_composition_ab",
        "out_root": str(out_root),
        "config": args.config,
        "params_candidate": params,
        "plan_note": (
            "same-vector ctc10 composition: AP6 v80 speed + AP8 nested_hashagg_type mag weights 70/30"
        ),
        "w1_candidate_spent": 0,
        "driver_version": "v85_same_vector_composition",
    }

    # --- sampler self-test before any tuner ---
    if not args.skip_self_test:
        try:
            summary["sampler_self_test"] = sampler_self_test(out_root / "_self_test", duration_s=4.0)
        except Exception as exc:
            summary["classification"] = "error / sampler_self_test_failed"
            summary["error"] = str(exc)
            (out_root / "component_ab_summary.json").write_text(
                json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            log(f"[fatal] sampler self-test failed: {exc}")
            return 10

    # --- baseline ---
    base_out = out_root / "default_baseline"
    base_out.mkdir(parents=True, exist_ok=True)
    sampler = WsSampler(base_out / "ws_series.csv")
    try:
        summary["cgroup_mem_dir"] = str(sampler.start())
    except Exception as exc:
        summary["classification"] = "error / cgroup_resolve_failed"
        summary["error"] = str(exc)
        (out_root / "component_ab_summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        log(f"[fatal] cgroup resolve failed: {exc}")
        return 11

    t0 = time.time()
    rc = run_cmd(
        [
            sys.executable,
            "-u",
            "binary_plan_resource_tuner.py",
            "--config",
            args.config,
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
        (out_root / "component_ab_summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        log(f"[fatal] missing baseline event in {base_jsonl}")
        return 2

    base_m = extract_metrics(base_ev)
    summary["default"] = base_m
    window = extract_window(base_ev)
    aligned_def = align_series(series_def, window)
    summary["default_series_aligned"] = aligned_def
    hard = baseline_hard_gates(base_m, aligned_def)
    summary["baseline_hard_gates"] = hard

    a = aligned_def.get("aligned") or {}
    log(
        f"[default] status={base_m.get('status')} mem%={base_m.get('memory_pct'):.4f} "
        f"cpu%={base_m.get('cpu_pct'):.4f} io={base_m.get('io_mib_s'):.4f} "
        f"db_qps={base_m.get('db_qps')} samples={base_m.get('samples_memory')} "
        f"mem_src={base_m.get('memory_source')} io_src={base_m.get('io_source')} "
        f"aligned_n={a.get('n')} aligned_avg={a.get('avg')} aligned_p50={a.get('p50')}"
    )

    if not hard["ok"]:
        summary["classification"] = "invalid / baseline_hard_gate_failed"
        summary["w1_candidate_spent"] = 0
        summary["candidate_launched"] = False
        (out_root / "component_ab_summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        md = [
            "# v85 Phase B same-vector composition A/B — BASELINE HARD GATE FAILED",
            "",
            f"- out: `{out_root}`",
            f"- failed gates: `{hard['failed']}`",
            f"- candidate launched: **false** (budget not spent)",
            f"- aligned n/avg/p50: {a.get('n')} / {a.get('avg')} / {a.get('p50')}",
            f"- official mem%: {base_m.get('memory_pct')}",
            "",
        ]
        (out_root / "component_ab_summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
        log(f"[STOP] baseline hard gate failed: {hard['failed']} — NOT launching candidate")
        return 20

    # --- candidate only after baseline hard gate passes ---
    assert_w1_free()
    cand_out = out_root / args.params_name
    cand_out.mkdir(parents=True, exist_ok=True)
    params_path = cand_out / "params.json"
    params_path.write_text(json.dumps(params, indent=2) + "\n", encoding="utf-8")
    sampler2 = WsSampler(cand_out / "ws_series.csv")
    sampler2.start()
    t1 = time.time()
    rc2 = run_cmd(
        [
            sys.executable,
            "-u",
            "binary_plan_resource_tuner.py",
            "--config",
            args.config,
            "--output-dir",
            str(cand_out),
            "validate-params",
            "--target",
            "memory",
            "--worker-name",
            "cpu_mem_w1",
            "--label",
            f"memory_v85_comp_{args.params_name}",
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
    summary["w1_candidate_spent"] = 1

    series_c = load_ws_series(cand_out / "ws_series.csv")
    summary["cand_series_full"] = summarize_full(series_c)

    cand_jsonl = cand_out / "memory_single_validation_hybenchxp.jsonl"
    cand_ev = parse_validation_event(cand_jsonl, ("single_candidate_validation",))
    if not cand_ev:
        summary["classification"] = "error / missing_candidate_event"
        (out_root / "component_ab_summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        log(f"[fatal] missing candidate event in {cand_jsonl}")
        return 3

    cand_m = extract_metrics(cand_ev)
    summary["candidate"] = cand_m
    window_c = extract_window(cand_ev)
    aligned_c = align_series(series_c, window_c)
    summary["cand_series_aligned"] = aligned_c
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
    # prefer official metrics for classification; also record aligned drop
    b_al = float((a.get("avg") if a.get("avg") is not None else b_mem) or 0)
    c_al = float((ac.get("avg") if ac.get("avg") is not None else c_mem) or 0)
    aligned_mem_drop = b_al - c_al
    xfer = other_up_intersect_sweet(base_m, cand_m)

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
        "db_gain_ge_15": (db_gain is not None and db_gain >= 15.0),
        "db_gain_ge_20": (db_gain is not None and db_gain >= 20.0),
        "intersect_other_up_sweet": xfer["intersection_nonempty"],
        "real_memory_drop": mem_drop > 0.5,
        "aligned_real_memory_drop": aligned_mem_drop > 0.5,
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

    if not checks["both_status_ok"] or not checks["samples_ge_45_both"] or not checks["aligned_n_ge_45_both"]:
        cls = "invalid"
    elif not checks["real_memory_drop"] and not checks["aligned_real_memory_drop"]:
        cls = "reject / no_memory_drop"
    elif (
        checks["cand_mem_lt_80"]
        and checks["cand_mem_drop_ge_5"]
        and checks["db_gain_ge_15"]
        and checks["intersect_other_up_sweet"]
    ):
        cls = "strict_pass_component"
    elif checks["cand_mem_lt_80"] and checks["cand_mem_drop_ge_5"] and checks["db_gain_ge_15"]:
        cls = "near_miss / no_transfer_sweet"
    elif checks["real_memory_drop"] and (db_gain or 0) > 0:
        cls = "valid_component / partial"
    else:
        cls = "reject"
    summary["classification"] = cls

    (out_root / "component_ab_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    md = [
        "# v85 Phase B same-vector composition A/B",
        "",
        f"- out: `{out_root}`",
        f"- params: `{json.dumps(params)}`",
        f"- classification: **{cls}**",
        f"- cgroup: `{summary.get('cgroup_mem_dir')}`",
        f"- w1_candidate_spent: {summary.get('w1_candidate_spent')}",
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
        f"- checks: `{json.dumps(checks)}`",
        "",
    ]
    (out_root / "component_ab_summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    log(f"[done] {cls} summary={out_root / 'component_ab_summary.json'}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("[interrupt] KeyboardInterrupt")
        sys.exit(130)
    except Exception as exc:
        log(f"[fatal] {exc}")
        sys.exit(99)
