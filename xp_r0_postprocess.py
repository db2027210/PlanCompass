#!/usr/bin/env python3
"""XP-only postprocessor for a sealed real HyBench binding trace.

The process waits for the Java trace seal, captures EXPLAIN FORMAT=JSON for
each distinct observed executable statement while the R0 tunnel is alive, and
publishes an immutable registry only when every gate is satisfied. It never
turns a failed or incomplete capture into execution authorization.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from collections import Counter


ROOT = Path("/home/anonymous/program/resource_adaptive_mysql")
try:
    RUN_ID = sys.argv[sys.argv.index("--run-id") + 1]
except (ValueError, IndexError):
    RUN_ID = "XP_CAPTURE_R0_TRACE_20260726T0615Z"
RUN = ROOT / "history/mysql_sgd_env_agents_20260723/xp/formal_sgd_campaigns" / RUN_ID
TRACE = RUN / "formal_binding_trace.jsonl"
MANIFEST = RUN / "formal_binding_trace.jsonl.manifest.json"
CNF = RUN / "private/mysql.cnf"
OUT = RUN / "postprocessed_evidence"
REGISTRY = OUT / "hybench_xp.immutable_plan_workload_registry_v1.json"
FEATURE = OUT / "hybench_xp.trained_estimator_feature_source.json"
RAW = OUT / "raw_explain"
INVENTORY = OUT / "raw_explain_inventory.json"
AUDIT = OUT / "capture_audit.json"
HEARTBEAT = ROOT / "history/mysql_sgd_env_agents_20260723/manual_controllers/hybench_xp_io/subagent_heartbeat.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def heartbeat(status: str, task: str, evidence: str, next_action: str) -> None:
    payload = {
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": status,
        "current_task": task,
        "mutex_state": "xp_singleton_held",
        "last_command": f"xp_r0_postprocess.py --run-id {RUN_ID}",
        "last_evidence": evidence,
        "next_action": next_action,
    }
    write_json(HEARTBEAT, payload)


def load_events() -> list[dict]:
    rows = []
    for line in TRACE.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("trace event is not an object")
        rows.append(value)
    return rows


def explain(sql: str) -> tuple[bool, str]:
    completed = subprocess.run(
        ["/usr/bin/mysql", f"--defaults-file={CNF}", "--protocol=tcp", "--host=127.0.0.1",
         "--port=65104", "--user=root", "--connect-timeout=3", "--ssl-mode=DISABLED",
         "--get-server-public-key", "--batch", "--raw", "--skip-column-names"],
        input=f"EXPLAIN FORMAT=JSON {sql};\n",
        text=True,
        capture_output=True,
        timeout=15,
    )
    output = completed.stdout.strip()
    error = completed.stderr.strip()
    return completed.returncode == 0 and bool(output), output if output else error


def main() -> int:
    deadline = time.time() + 660
    heartbeat("r0_trace_postprocess_waiting", "wait_for_trace_seal", str(TRACE), "wait for manifest")
    while time.time() < deadline and not MANIFEST.exists():
        time.sleep(10)
    if not MANIFEST.exists():
        heartbeat("fail_closed_trace_not_sealed", "wait_for_trace_seal", str(TRACE), "retain R0 and do not authorize")
        return 2

    heartbeat("r0_trace_sealed_capturing_explain", "capture_observed_raw_explain", str(MANIFEST), "capture every distinct statement")
    events = load_events()
    statements = {}
    for event in events:
        if event.get("event_type") != "statement":
            continue
        sql = event.get("canonical_executable_sql")
        query_id = event.get("query_id")
        if not isinstance(sql, str) or not sql.strip() or not isinstance(query_id, str):
            raise ValueError("statement event lacks executable SQL or query_id")
        key = (query_id, hashlib.sha256(sql.encode("utf-8")).hexdigest())
        statements.setdefault(key, {"query_id": query_id, "sql": sql, "events": []})["events"].append(event)

    RAW.mkdir(parents=True, exist_ok=True)
    rows = []
    unsupported = []
    for index, item in enumerate(sorted(statements.values(), key=lambda x: (x["query_id"], x["sql"])), 1):
        ok, output = explain(item["sql"])
        name = f"{index:04d}_{item['query_id'].replace('-', '_')}.json"
        path = RAW / name
        if ok:
            try:
                parsed = json.loads(output)
            except json.JSONDecodeError:
                ok = False
                parsed = {"unsupported": True, "raw_output": output}
        else:
            parsed = {"unsupported": True, "error": output}
            unsupported.append({"query_id": item["query_id"], "path": str(path), "error": output})
        write_json(path, parsed)
        rows.append({
            "query_id": item["query_id"],
            "sql_sha256": hashlib.sha256(item["sql"].encode("utf-8")).hexdigest(),
            "observed_event_count": len(item["events"]),
            "raw_explain_path": str(path),
            "raw_explain_sha256": sha(path),
            "supported": ok,
        })

    trace_sha = sha(TRACE)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    event_types = Counter(str(event.get("event_type")) for event in events)
    workers = sorted({str(event.get("worker_or_terminal")) for event in events})
    coverage = {
        "event_count": len(events),
        "event_types": dict(sorted(event_types.items())),
        "workers": workers,
        "statement_count": len(statements),
        "query_ids": sorted({row["query_id"] for row in rows}),
        "trace_sha256": trace_sha,
        "manifest_run_id": manifest.get("run_id"),
        "manifest_trace_sha256": manifest.get("trace_sha256") or manifest.get("events_sha256"),
    }
    r0_path = RUN / "r0_result.json"
    r0 = json.loads(r0_path.read_text(encoding="utf-8")) if r0_path.exists() else {}
    r0_stable = bool(r0.get("stability", {}).get("stable", False))
    r0_resources = r0.get("resource_means", {})
    r0_band = (
        0.85 <= float(r0_resources.get("io", -1)) <= 0.95
        and 0.30 <= float(r0_resources.get("cpu", -1)) <= 0.50
        and 0.30 <= float(r0_resources.get("memory", -1)) <= 0.50
    )
    gates = {
        "trace_sealed": manifest.get("status") == "SEALED" or manifest.get("schema_version") == "formal_mysql_binding_trace_manifest_v1",
        "trace_run_id": manifest.get("run_id") == RUN_ID,
        "trace_hash_match": manifest.get("trace_sha256", manifest.get("events_sha256")) == trace_sha,
        "all_workers": all(worker in workers for worker in [*(f"XAP-{i}" for i in range(1, 9)), *(f"XTP-{i}" for i in range(1, 9))]),
        "statement_events": bool(statements),
        "raw_explain_complete": bool(rows) and not unsupported and all(row["supported"] for row in rows),
        "r0_stable": r0_stable,
        "r0_resource_band": r0_band,
    }
    audit = {
        "schema_version": "hybench_xp_r0_capture_audit_v1",
        "run_id": RUN_ID,
        "immutable": True,
        "execution_authorized": bool(all(gates.values())),
        "gates": gates,
        "coverage": coverage,
        "raw_explain": rows,
        "unsupported": unsupported,
        "r0_result_path": str(r0_path),
        "r0_result_sha256": sha(r0_path) if r0_path.exists() else None,
    }
    write_json(INVENTORY, {"schema_version": "hybench_xp_raw_explain_inventory_v1", "immutable": True, "run_id": RUN_ID, "files": rows, "unsupported": unsupported})
    write_json(AUDIT, audit)
    if not all(gates.values()):
        heartbeat("fail_closed_capture_or_r0_gate", "reconcile_xp_capture_evidence", str(AUDIT), "do not authorize Algorithm C; inspect failed gates")
        return 3

    registry = {
        "schema_version": "mysql_hybench_xp_immutable_plan_workload_registry_v1",
        "immutable": True,
        "execution_authorized": True,
        "benchmark": "hybench_xp",
        "scenario": "hybench_xp_io",
        "run_id": RUN_ID,
        "trace": {"path": str(TRACE), "sha256": trace_sha, "manifest": str(MANIFEST), "manifest_sha256": sha(MANIFEST)},
        "raw_explain_inventory": {"path": str(INVENTORY), "sha256": sha(INVENTORY)},
        "queries": rows,
        "coverage": coverage,
        "r0": {"path": str(r0_path), "sha256": sha(r0_path), "resource_means": r0_resources},
    }
    write_json(REGISTRY, registry)
    feature = {
        "schema_version": "mysql_hybench_xp_immutable_feature_source_v1",
        "immutable": True,
        "execution_authorized": True,
        "benchmark": "hybench_xp",
        "scenario": "hybench_xp_io",
        "workload_registry_path": str(REGISTRY),
        "workload_registry_sha256": sha(REGISTRY),
        "registry_query_ids": registry["coverage"]["query_ids"],
        "require_strict_plan_registry": True,
        "raw_explain_inventory_path": str(INVENTORY),
        "raw_explain_inventory_sha256": sha(INVENTORY),
    }
    write_json(FEATURE, feature)
    heartbeat("r0_evidence_ready", "publish_xp_algorithm_c_config", str(AUDIT), "update production config and start one Algorithm C round")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
