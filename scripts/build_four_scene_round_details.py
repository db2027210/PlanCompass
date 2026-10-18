#!/usr/bin/env python3
"""Build an auditable Markdown index of MySQL four-scene round evidence."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".md"

SCENES = {
    "job_memory": {
        "label": "JOB / memory",
        "short": "job",
        "run": "direct_20260724T203107",
        "target": "memory",
        "state": ROOT / "history/mysql_sgd_env_agents_20260723/manual_controllers/job_memory/direct_campaign_state.json",
    },
    "hybench_xp_io": {
        "label": "HyBench XP / I/O",
        "short": "xp",
        "run": "direct_20260725T101830",
        "target": "io",
        "state": ROOT / "history/mysql_sgd_env_agents_20260723/manual_controllers/hybench_xp_io/direct_campaign_state.json",
    },
    "tpch_memory": {
        "label": "TPC-H / memory",
        "short": "tpch",
        "run": "direct_20260724T203102",
        "target": "memory",
        "state": ROOT / "history/mysql_sgd_env_agents_20260723/manual_controllers/tpch_memory/direct_campaign_state.json",
    },
    "tpcc_cpu": {
        "label": "TPC-C / CPU",
        "short": "tpcc",
        "run": "direct_20260725T101835",
        "target": "cpu",
        "state": ROOT / "history/mysql_sgd_env_agents_20260723/manual_controllers/tpcc_cpu/direct_campaign_state.json",
    },
}

JOB_ALG_C = ROOT / "history/mysql_gradient_debug/job_v6/20260728T085336Z/result_20260729_003014_010055.json"


def load(path: Path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def n(value, digits=6):
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "" if value else ""
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}"
    return str(value).replace("|", "\\|")


def params(value):
    if not value:
        return "—"
    return ", ".join(f"{k}={n(v, 5)}" for k, v in value.items())


def resources(value):
    value = value or {}
    return f"C={n(value.get('cpu'), 4)}, M={n(value.get('memory'), 4)}, I={n(value.get('io'), 4)}"


def rel(path: Path | str):
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


lines: list[str] = []
lines += [
    "# ",
    "",
    "> 2026-08-05`mysql_only` PostgreSQL/MySQL ",
    "",
    "Algorithm C “ →  →  Top-5  → ” direct campaign  incumbent  Top-5 ",
    "",
    "## 1. ",
    "",
    "|  | Algorithm C  |  |  |  direct  |  |",
    "|---|---:|---:|---:|---:|---|",
    "| JOB / memory | 1 | 15 | 5 | 82 | Algorithm C  1  5  |",
    "| HyBench XP / I/O | 0 | 0 | 0 | 15 16 |  16  decision  |",
    "| TPC-H / memory | 0 | 0 | 0 | 55 |  |",
    "| TPC-C / CPU | 0 | 0 | 0 | 12 |  |",
    "",
    " 0  Algorithm C ",
    "",
    "## 2. JOB Algorithm C 1 ",
    "",
]

job = load(JOB_ALG_C)
outer = job["outer_loop"][0]
inner = outer["inner_loop"]
lines += [
    f"- {outer.get('iter')} {inner.get('parameter_subsets_enumerated')}/{inner.get('expected_parameter_subsets')} ",
    f"-  SGD {inner.get('evaluated_total_sgd_iterations')}{inner.get('staged_real_replays')} ",
    f"- `outer_accepted={str(outer.get('outer_accepted')).lower()}`Reason `{outer.get('outer_acceptance_reason')}`",
    f"- `{rel(JOB_ALG_C)}`",
    "",
    "### 2.1  15 ",
    "",
    "|  | subset |  |  score |  CPU |  memory |  I/O |  |  Top-5 |",
    "|---:|---|---|---:|---:|---:|---:|---|---|",
]

queue_ids = {x.get("subset_id") for x in job.get("replay_candidate_queue", [])}
for row in job.get("all_candidate_rankings", []):
    rr = row.get("final_resource") or {}
    lines.append(
        "| {rank} | `{sid}` | {active} | {score} | {cpu} | {mem} | {io} | {point} | {top5} |".format(
            rank=row.get("pre_fingerprint_rank"),
            sid=row.get("subset_id"),
            active="<br>".join(f"`{x}`" for x in row.get("active_subset", [])),
            score=n(row.get("final_score")),
            cpu=n(rr.get("cpu")), mem=n(rr.get("memory")), io=n(rr.get("io")),
            point=params(row.get("optimized_params")),
            top5="" if row.get("subset_id") in queue_ids else "",
        )
    )

lines += [
    "",
    "15  score `score_improvement=0` Top-5  canonical subset  JOB ",
    "",
    "### 2.2  Top-5 ",
    "",
    "| / | subset |  |  C/M/I |  C/M/I |  DB QPS | p95 ms |  |  | Reason |",
    "|---:|---|---|---|---|---:|---:|---|---|---|",
]

for ev in outer["real_replay"]["evaluations"]:
    val = ev.get("candidate_final_validation") or {}
    gate = val.get("outer_gate") or {}
    lines.append(
        "| {ord}/{rank} | `{sid}` | {point} | {pred} | {real} | {qps} | {p95} | {stable} | {eligible} | {reasons} |".format(
            ord=ev.get("replay_ordinal"), rank=ev.get("candidate_rank"), sid=ev.get("candidate_subset_id"),
            point=params(ev.get("candidate_optimized_params")),
            pred=resources(ev.get("candidate_predicted_final_resource")), real=resources(val.get("utilization_pct")),
            qps=n(val.get("db_qps")), p95=n(val.get("p95_ms"), 2), stable=n(val.get("stable")),
            eligible=n(val.get("outer_eligible")), reasons=", ".join(gate.get("reasons") or []) or "—",
        )
    )

lines += [
    "",
    " subset  `p95_lte_incumbent_1_10`  DB QPS  winner 2 ",
    "",
    "## 3.  direct campaign",
    "",
    " candidate  Algorithm C `ΔQPS`  `Δtarget`  candidate − incumbent JOB/TPC-H  memoryXP  I/OTPC-C  CPU `Δtarget` ",
    "",
]

for key, cfg in SCENES.items():
    state = load(cfg["state"])
    direct_dir = ROOT / f"history/mysql_sgd_env_agents_20260723/{cfg['short']}/formal_sgd_direct/{cfg['run']}"
    decisions = sorted(direct_dir.glob("round_*.decision.json"))
    lines += [
        f"### 3.{list(SCENES).index(key)+1} {cfg['label']}",
        "",
        f"`round={state.get('round')}``accepted_shrinkage={state.get('accepted_shrinkage')}``rejected_count={state.get('rejected_count')}``technical_failure_count={state.get('technical_failure_count')}` run  decision  {len(decisions)} ",
        "",
        "|  |  | candidate QPS | candidate C/M/I | incumbent QPS | incumbent C/M/I | ΔQPS | Δtarget | candidate utility | Δutility | / |  |",
        "|---:|---|---:|---|---:|---|---:|---:|---:|---:|---|---|",
    ]
    for path in decisions:
        row = load(path)
        cand = row.get("candidate") or {}
        inc = row.get("incumbent_before") or {}
        cr = cand.get("resource_means") or {}
        ir = inc.get("resource_means") or {}
        failed = [name for name, passed in (row.get("checks") or {}).items() if passed is False]
        reason = (row.get("outer_gate") or {}).get("reason") or ", ".join(failed) or ("accepted" if row.get("accepted") else "rejected")
        round_no = int(path.stem.split("_")[1].split(".")[0])
        point_dir = ROOT / f"history/mysql_sgd_env_agents_20260723/{cfg['short']}/formal_sgd_campaigns/{cfg['run']}_round_{round_no:03d}"
        delta_qps = None
        delta_target = None
        if cand.get("db_qps") is not None and inc.get("db_qps") is not None:
            delta_qps = cand["db_qps"] - inc["db_qps"]
        if cr.get(cfg["target"]) is not None and ir.get(cfg["target"]) is not None:
            delta_target = cr[cfg["target"]] - ir[cfg["target"]]
        lines.append(
            "| {rnd} | {accepted} | {cq} | {cr} | {iq} | {ir} | {dq} | {dt} | {cu} | {du} | {reason} | `{point}` |".format(
                rnd=round_no, accepted=n(row.get("accepted")), cq=n(cand.get("db_qps")), cr=resources(cr),
                iq=n(inc.get("db_qps")), ir=resources(ir),
                dq=n(delta_qps), dt=n(delta_target),
                cu=n(row.get("candidate_utility")), du=n(row.get("utility_delta")), reason=reason,
                point=rel(point_dir),
            )
        )
    if state.get("round", 0) > len(decisions):
        lines += ["", f">  {state.get('round')}  run  {len(decisions)}  decision"]
    lines.append("")

lines += [
    "## 4. ",
    "",
    "1. JOB Algorithm C  15 /",
    "2. XPTPC-HTPC-C  Algorithm C  Top-5  direct ",
    "3. direct / decision ",
    "4.  SSD  IOPSawait`%util`  MiB/s QPS/ SSD",
    "",
    "## 5. ",
    "",
    f"- JOB Algorithm C  JSON`{rel(JOB_ALG_C)}`",
    "-  direct decision`history/mysql_sgd_env_agents_20260723/{job,xp,tpch,tpcc}/formal_sgd_direct/<run>/round_NNN.decision.json`",
    "- `history/mysql_sgd_env_agents_20260723/{job,xp,tpch,tpcc}/formal_sgd_campaigns/<run>_round_NNN/`",
    "- `IO.md`",
    "",
]

OUT.write_text("\n".join(lines), encoding="utf-8")
print(OUT)
