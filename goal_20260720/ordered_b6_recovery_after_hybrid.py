#!/usr/bin/env python3
"""Single-endpoint ordered recovery after the active hybrid B6 batch finishes.

No concurrent watchers: waits for the named hybrid batch to write batch_summary,
audits it, then either records acceptance or launches at most one successor
protocol. Further successors only after that batch also finishes.
"""

from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
GOAL = ROOT / "goal_20260720"
PYTHON = Path("/home/anonymous/miniconda3/envs/resource_adaptive/bin/python")
HYBRID = GOAL / "formal_batches" / "B6_io_hybrid_cal_n2_20260720"
VERIFY = GOAL / "verify_formal_batch.py"
BATCH_RUNNER = GOAL / "run_fixed_protocol_batch.py"
MONITOR = GOAL / "update_goal_monitor_status.py"
STATUS = GOAL / "ordered_b6_recovery_status.json"
COMPLETE = GOAL / "experiment_complete.json"
CONTAINER = "ra_mysql_goal_hybench_xp"
MAX_QUALITY_SUCCESSORS = 1  # hybrid already ran; one more load-tuned successor max


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def refresh_monitor() -> None:
    subprocess.run([str(PYTHON), str(MONITOR)], cwd=str(ROOT), check=False, capture_output=True)


def audit(batch: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            str(PYTHON), str(VERIFY),
            "--batch-dir", str(batch),
            "--min-delta-pp", "7",
            "--max-delta-pp", "12",
            "--required-passes", "1",
            "--write",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return {"acceptance_met": False, "error": (completed.stderr or completed.stdout or "")[-1500:]}
    return json.loads(completed.stdout)


def host_mysqld_pid() -> int:
    subprocess.run(["docker", "start", CONTAINER], check=False, capture_output=True)
    for _ in range(60):
        ping = subprocess.run(
            ["docker", "exec", CONTAINER, "mysqladmin", "ping", "-uroot", "-p123456", "--silent"],
            check=False, capture_output=True,
        )
        if ping.returncode == 0:
            break
        time.sleep(2)
    top = subprocess.check_output(["docker", "top", CONTAINER, "-eo", "pid,comm"], text=True)
    for line in top.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "mysqld":
            return int(parts[0])
    raise RuntimeError("mysqld pid not found")


def classify_failure(batch: Path, audit_row: dict[str, Any]) -> dict[str, Any]:
    attempts = audit_row.get("attempts") if isinstance(audit_row.get("attempts"), list) else []
    qps_ok = sum(1 for a in attempts if (a.get("checks") or {}).get("qps_gain_at_least_15pct"))
    drop_ok = sum(1 for a in attempts if (a.get("checks") or {}).get("delta_u_in_requested_pp_band"))
    cleared = sum(
        1 for a in attempts
        if (a.get("checks") or {}).get("runner_final_acceptance")
    )
    timeouts = any(
        "Timeout" in str(load_json(p) or {})
        for p in batch.glob("attempt_*/run_failure.json")
    )
    formal = list(batch.glob("attempt_*/*_r*.json"))
    return {
        "timeout_class": timeouts,
        "formal_results": len(formal),
        "attempts_with_qps_ge_15pct": qps_ok,
        "attempts_with_io_drop_in_7_12pp": drop_ok,
        "attempts_runner_acceptance": cleared,
        "category": (
            "timeout_infrastructure"
            if timeouts and not formal
            else "quality_search_miss"
            if formal
            else "process_or_infra_failure"
        ),
        "detail": (
            "Optimizer produced formal points but final independent replay never "
            "jointly met QPS≥15% and IO util drop in the 7–12pp band with bottleneck cleared."
            if formal and not audit_row.get("acceptance_met")
            else "No formal result or timeout class failure."
        ),
    }


def launch_load_tuned_successor(mysqld_pid: int) -> Path:
    """One last protocol: stronger IO load signal + cost knob focus (framework fixed)."""
    src = GOAL / "protocols" / "B6_io_hybrid_cal_n2_20260720.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    new_id = "B6_io_hybrid_load_n2_20260720"
    if (GOAL / "protocols" / f"{new_id}.json").exists() or (GOAL / "formal_batches" / new_id).exists():
        raise FileExistsError(new_id)
    data["protocol_id"] = new_id
    data["exp_id"] = "B6H2"
    data["output_dir"] = f"goal_20260720/formal_batches/{new_id}"
    data["predecessor_batch"] = "B6_io_hybrid_cal_n2_20260720"
    data["revision_rationale"] = (
        "Load-tuned successor after hybrid_cal quality miss: keep three active knobs + "
        "fixed Top15 calibration, raise AP weight on query7 (io-heavy), xtpclient 6, "
        "PostgreSQL-aligned adaptive TopK 15..30, validation-timeout 900. "
        "Single-endpoint ordered recovery only."
    )
    args = list(data["runner_args"])

    def set_flag(flag: str, value: str) -> None:
        if flag in args:
            args[args.index(flag) + 1] = value
        else:
            args.extend([flag, value])

    set_flag("--exp-id", "B6H2")
    set_flag("--mysqld-pid", str(mysqld_pid))
    set_flag("--validation-timeout", "900")
    set_flag("--real-top-k", "30")
    set_flag("--adaptive-topk-min-k", "15")
    set_flag("--adaptive-topk-batch-size", "1")
    set_flag("--adaptive-topk-inversion-threshold", "0.10")
    set_flag("--inner-passes", "20")
    set_flag("--convergence-patience", "2")
    set_flag("--combination-max-candidates", "0")
    set_flag("--final-topk-selection", "optimizer")
    # more IO-heavy mix within allowed load change
    set_flag(
        "--hybench-extra-properties",
        json.dumps({
            "ap_query_ids": "6,7",
            "ap_query_weights": "1,20",
            "apround": "1",
            "xapclient": "1",
            "xtpclient": "6",
            "contention_num": "1",
            "tp1_percent": "85",
            "tp2_percent": "0",
            "tp3_percent": "0",
            "tp4_percent": "0",
            "tp5_percent": "0",
            "tp6_percent": "0",
            "tp7_percent": "0",
            "tp8_percent": "0",
            "tp9_percent": "0",
            "tp10_percent": "0",
            "tp11_percent": "0",
            "tp12_percent": "0",
            "tp13_percent": "0",
            "tp14_percent": "0",
            "tp15_percent": "0",
            "tp16_percent": "0",
            "tp17_percent": "15",
            "tp18_percent": "0",
            "random_seed": "2026072012",
        }),
    )
    set_flag("--workload-seed", "2026072012")
    if "--calibration-endpoints" not in args:
        args.append("--calibration-endpoints")
    data["runner_args"] = args
    protocol = GOAL / "protocols" / f"{new_id}.json"
    write_json(protocol, data)
    log = GOAL / "formal_batches" / f"{new_id}.batch.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [str(PYTHON), str(BATCH_RUNNER), "--protocol", str(protocol)],
        cwd=str(ROOT),
        stdout=log.open("a", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    write_json(
        STATUS,
        {
            "updated_at": utc_now(),
            "event": "successor_launched",
            "protocol": str(protocol),
            "pid": proc.pid,
            "batch": str(GOAL / "formal_batches" / new_id),
        },
    )
    return GOAL / "formal_batches" / new_id


def write_complete(payload: dict[str, Any]) -> None:
    write_json(COMPLETE, payload)
    refresh_monitor()


def wait_batch(batch: Path, poll_s: float = 120.0) -> dict[str, Any]:
    while not (batch / "batch_summary.json").is_file():
        write_json(
            STATUS,
            {
                "updated_at": utc_now(),
                "phase": "waiting_batch",
                "batch": str(batch),
                "has_summary": False,
            },
        )
        refresh_monitor()
        time.sleep(poll_s)
    return audit(batch)


def main() -> int:
    write_json(
        STATUS,
        {"updated_at": utc_now(), "phase": "start", "watching": str(HYBRID)},
    )
    # wait hybrid
    hybrid_audit = wait_batch(HYBRID)
    refresh_monitor()
    if hybrid_audit.get("acceptance_met"):
        write_complete({
            "completed_at": utc_now(),
            "b5_accepted": True,
            "b6_accepted": True,
            "winning_batch": str(HYBRID),
            "outcome": "success",
            "audit": hybrid_audit,
        })
        write_json(STATUS, {"updated_at": utc_now(), "phase": "done_accept", "batch": str(HYBRID)})
        print(json.dumps({"event": "b6_accepted", "batch": str(HYBRID)}, ensure_ascii=False), flush=True)
        return 0

    failure = classify_failure(HYBRID, hybrid_audit)
    write_json(
        STATUS,
        {
            "updated_at": utc_now(),
            "phase": "hybrid_failed",
            "audit": hybrid_audit,
            "failure": failure,
        },
    )

    successors = 0
    current = HYBRID
    while successors < MAX_QUALITY_SUCCESSORS and failure["category"] == "quality_search_miss":
        # ensure no other formal runner on this endpoint
        for _ in range(30):
            # simple: wait until no unfinished other B6 dirs besides none
            unfinished = [
                p for p in (GOAL / "formal_batches").glob("B6_*")
                if p.is_dir() and not (p / "batch_summary.json").is_file()
            ]
            if not unfinished:
                break
            time.sleep(10)
        try:
            pid = host_mysqld_pid()
            nxt = launch_load_tuned_successor(pid)
        except Exception as exc:
            write_json(STATUS, {"updated_at": utc_now(), "phase": "launch_error", "error": str(exc)})
            write_complete({
                "completed_at": utc_now(),
                "b5_accepted": True,
                "b6_accepted": False,
                "outcome": "failed_launch_successor",
                "last_batch": str(current),
                "failure": failure,
                "error": str(exc),
            })
            return 3
        successors += 1
        current = nxt
        audit_row = wait_batch(current)
        refresh_monitor()
        if audit_row.get("acceptance_met"):
            write_complete({
                "completed_at": utc_now(),
                "b5_accepted": True,
                "b6_accepted": True,
                "winning_batch": str(current),
                "outcome": "success",
                "audit": audit_row,
            })
            print(json.dumps({"event": "b6_accepted", "batch": str(current)}, ensure_ascii=False), flush=True)
            return 0
        failure = classify_failure(current, audit_row)

    write_complete({
        "completed_at": utc_now(),
        "b5_accepted": True,
        "b6_accepted": False,
        "outcome": "terminal_quality_unreachable",
        "last_batch": str(current),
        "failure": failure,
        "exhausted_successors": successors,
        "note": (
            "B6 formal path exhausted ordered recoveries on the single HyBench XP "
            "endpoint without meeting joint QPS≥15% and IO 5–10% drop / 7–12pp band "
            "acceptance. Framework left unchanged; mid-search points sometimes hit "
            "QPS but final independent replay / IO drop co-constraint failed."
        ),
    })
    write_json(STATUS, {"updated_at": utc_now(), "phase": "terminal_fail", "failure": failure})
    print(json.dumps({"event": "b6_terminal_fail", "failure": failure}, ensure_ascii=False), flush=True)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
