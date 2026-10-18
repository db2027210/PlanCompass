#!/usr/bin/env python3
"""Collect a MySQL experience table from two-sided physical-plan effects.

This is the fail-open fallback used when no same-envelope historical real
replays exist and a workload-specific trained resource model is unavailable.
It changes one optimizer cost at a time on a session-owned MySQL work copy,
captures raw ``EXPLAIN FORMAT=JSON`` for the complete supplied SQL registry,
and records the signed full-range change of three deterministic plan proxies.
It never manufactures QPS or a model-error envelope.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
IMAGE = "sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
SPACE = (
    ROOT
    / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v3/"
    "effective_parameter_spaces.json"
)
SERVER_COSTS = {
    "disk_temptable_create_cost",
    "disk_temptable_row_cost",
    "key_compare_cost",
    "memory_temptable_create_cost",
    "memory_temptable_row_cost",
    "row_evaluate_cost",
}
ENGINE_COSTS = {"io_block_read_cost", "memory_block_read_cost"}
ALL_COSTS = SERVER_COSTS | ENGINE_COSTS
SAFE = re.compile(r"[A-Za-z0-9_.-]+\Z")


class CollectionError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> str:
    payload = canonical(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    path.with_name(path.name + ".sha256").write_text(
        f"{digest}  {path.name}\n", encoding="ascii"
    )
    return digest


def _number(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if math.isfinite(parsed) and parsed >= 0.0 else 0.0


def _decode(raw: str) -> Mapping[str, Any]:
    value: Any = json.loads(raw.strip())
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, Mapping):
        raise CollectionError("EXPLAIN output is not a JSON object")
    return value


def _walk(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def plan_proxy(plan: Mapping[str, Any]) -> dict[str, float]:
    """Return deterministic plan-only CPU/memory/IO proxy values.

    Numeric optimizer estimates are deliberately retained here because they
    are the observed object whose change is being calibrated.  They are not
    used in the strict physical-plan cluster identity.
    """

    cpu = 0.0
    memory = 0.0
    io = 0.0
    for node in _walk(plan):
        rows_scan = _number(node.get("rows_examined_per_scan"))
        rows_join = _number(node.get("rows_produced_per_join"))
        cpu += rows_scan + rows_join
        cost = node.get("cost_info")
        if isinstance(cost, Mapping):
            io += _number(cost.get("read_cost"))
        memory += float(
            bool(node.get("using_temporary_table"))
            or "materialized_from_subquery" in node
            or "grouping_operation" in node
            or "duplicates_removal" in node
        )
    # Keep magnitudes numerically manageable without changing sign/order.
    return {"cpu": cpu / 1_000_000.0, "memory": memory, "io": io / 1_000_000.0}


def endpoint_points(benchmark: str) -> tuple[list[str], dict[str, Any], list[dict[str, Any]]]:
    space = json.loads(SPACE.read_text(encoding="utf-8"))
    definitions = space["all_parameter_definitions"]
    active = list(space["benchmark_candidate_parameters"][benchmark])
    anchor = {name: float(definitions[name]["default"]) for name in ALL_COSTS}
    points: list[dict[str, Any]] = [
        {"endpoint_id": "anchor", "changed_param": None, "side": "anchor", "params": anchor}
    ]
    for name in active:
        for side in ("lower", "upper"):
            params = dict(anchor)
            params[name] = float(definitions[name][side])
            points.append(
                {
                    "endpoint_id": f"{name}_{side}",
                    "changed_param": name,
                    "side": side,
                    "params": params,
                }
            )
    ranges = {
        name: {
            "lower": float(definitions[name]["lower"]),
            "upper": float(definitions[name]["upper"]),
        }
        for name in active
    }
    return active, ranges, points


REMOTE = r'''
set -Eeuo pipefail
umask 077
project="$1"; source_data="$2"; lock="$3"; run="$4"; database="$5"
cpus="$6"; mems="$7"; cpu_quota="$8"; memory="$9"; memswap="${10}"
pids="${11}"; device="${12}"; read_bps="${13}"; write_bps="${14}"; image="${15}"
remote_input="${16}"
work_root="$project/plan_effect_t_work"; work="$work_root/$run"; data="$work/datadir"
evidence="$project/plan_effect_t_sessions/$run"; name="ra_mysql_plan_t_${run}"
cid=""; success=0
cleanup(){
 rc=$?; trap - EXIT HUP INT TERM
 if test -n "$cid" && docker inspect "$cid" >/dev/null 2>&1; then
  docker stop --time 120 "$cid" >"$evidence/container_stop.txt" 2>&1 || true
  docker logs "$cid" >"$evidence/mysql.log" 2>&1 || true
  docker inspect "$cid" >"$evidence/container_inspect_stopped.json" 2>/dev/null || true
  docker rm -f "$cid" >"$evidence/container_remove.txt" 2>&1 || true
 fi
 cid=""
 if test -d "$work"; then
  docker run --rm --network none --cpus 0.5 --cpuset-cpus "$cpus" --cpuset-mems "$mems" \
   --memory 268435456 --memory-swap 268435456 --pids-limit 64 \
   -v "$work:/cleanup:rw" --entrypoint /bin/sh "$image" \
   -c 'chmod -R u+rwX /cleanup 2>/dev/null || true; find /cleanup -mindepth 1 -delete' \
   >/dev/null 2>&1 || true
  rmdir "$work" 2>/dev/null || true
 fi
 printf '{"event":"cleanup","rc":%s,"container_absent":true,"work_copy_absent":%s}\n' \
  "$rc" "$(test ! -e "$work" && echo true || echo false)" >>"$evidence/events.jsonl" || true
 if test "$success" = 1; then
  (cd "$evidence" && find . -type f ! -name checksums.sha256 -print0 | sort -z | xargs -0 sha256sum >checksums.sha256) || rc=1
 fi
 exit "$rc"
}
mkdir -p "$work_root" "$project/plan_effect_t_sessions"; chmod 0700 "$work_root"
test ! -e "$work"; test ! -e "$evidence"; mkdir "$work" "$evidence"; chmod 0700 "$work" "$evidence"
trap cleanup EXIT HUP INT TERM
exec 9>>"$lock"; flock -n 9
test -d "$source_data"; test "$(docker image inspect "$image" --format '{{.Id}}')" = "$image"
test -z "$(docker ps --format '{{.Names}}' | grep -E '^ra_mysql_' || true)"
! pgrep -x java >/dev/null; ! pgrep -x sysbench >/dev/null; ! pgrep -x mysqlslap >/dev/null
mkdir "$data"; cp -a --reflink=auto "$source_data/." "$data/"
docker run --rm --network none --cpus 0.5 --cpuset-cpus "$cpus" --cpuset-mems "$mems" \
 --memory 268435456 --memory-swap 268435456 --pids-limit 64 \
 -v "$data:/copy:rw" --entrypoint /bin/sh "$image" \
 -c 'rm -f /copy/mysql.sock /copy/mysql.sock.lock; chmod -R u+rwX /copy'
cid=$(docker run -d --name "$name" --network none --cpuset-cpus "$cpus" --cpuset-mems "$mems" \
 --cpus "$cpu_quota" --memory "$memory" --memory-swap "$memswap" --pids-limit "$pids" \
 --device-read-bps "$device:$read_bps" --device-write-bps "$device:$write_bps" \
 -v "$data:/var/lib/mysql:rw" "$image" --skip-grant-tables --skip-networking \
 --innodb-flush-method=O_DIRECT --skip-log-bin --max-connections=32)
docker inspect "$cid" >"$evidence/container_inspect_running.json"
ready=0; for _ in $(seq 1 180); do
 if docker exec "$cid" mysql -N -B -e 'SELECT 1' >/dev/null 2>&1; then ready=1; break; fi
 sleep 1
done; test "$ready" = 1
docker exec "$cid" mysql -N -B -e 'SELECT @@version,COUNT(*) FROM information_schema.tables' >"$evidence/mysql_identity.tsv"
cp "$remote_input/endpoint_manifest.json" "$evidence/endpoint_manifest.json"
mkdir "$evidence/raw_explain"
python3 - "$cid" "$database" "$remote_input" "$evidence" <<'PY'
import json,pathlib,subprocess,sys
cid,database,source,evidence=sys.argv[1:]; source=pathlib.Path(source); evidence=pathlib.Path(evidence)
manifest=json.loads((source/'endpoint_manifest.json').read_text())
server={'disk_temptable_create_cost','disk_temptable_row_cost','key_compare_cost','memory_temptable_create_cost','memory_temptable_row_cost','row_evaluate_cost'}
engine={'io_block_read_cost','memory_block_read_cost'}
def mysql(sql,database_name=None):
 cmd=['docker','exec','-i',cid,'mysql','--batch','--raw','--skip-column-names']
 if database_name: cmd.append(database_name)
 p=subprocess.run(
  cmd, input=sql, universal_newlines=True,
  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
 )
 if p.returncode: raise RuntimeError(p.stderr[-2000:])
 return p.stdout
for endpoint in manifest['points']:
 params=endpoint['params']; statements=['START TRANSACTION']
 for name,value in sorted(params.items()):
  table='server_cost' if name in server else 'engine_cost'
  where=f"cost_name='{name}'"
  if name in engine: where+=" AND engine_name='default' AND device_type=0"
  statements.append(f"UPDATE mysql.{table} SET cost_value={float(value):.17g} WHERE {where}")
 statements += ['COMMIT','FLUSH OPTIMIZER_COSTS']
 mysql(';'.join(statements)+';')
 endpoint_dir=evidence/'raw_explain'/endpoint['endpoint_id']; endpoint_dir.mkdir()
 for item in manifest['queries']:
  sql=(source/'sql'/item['file']).read_text().strip().rstrip(';')
  raw=mysql('EXPLAIN FORMAT=JSON '+sql+';\n',database)
  (endpoint_dir/(item['query_id']+'.json')).write_text(raw)
 rows=mysql("SELECT cost_name,COALESCE(cost_value,default_value) FROM mysql.server_cost WHERE cost_name IN ('disk_temptable_create_cost','disk_temptable_row_cost','key_compare_cost','memory_temptable_create_cost','memory_temptable_row_cost','row_evaluate_cost') UNION ALL SELECT cost_name,COALESCE(cost_value,default_value) FROM mysql.engine_cost WHERE engine_name='default' AND device_type=0 AND cost_name IN ('io_block_read_cost','memory_block_read_cost') ORDER BY cost_name")
 (endpoint_dir/'fresh_cost_readback.tsv').write_text(rows)
mysql("UPDATE mysql.server_cost SET cost_value=NULL WHERE cost_name IN ('disk_temptable_create_cost','disk_temptable_row_cost','key_compare_cost','memory_temptable_create_cost','memory_temptable_row_cost','row_evaluate_cost');UPDATE mysql.engine_cost SET cost_value=NULL WHERE engine_name='default' AND device_type=0 AND cost_name IN ('io_block_read_cost','memory_block_read_cost');FLUSH OPTIMIZER_COSTS;")
PY
docker stop --time 120 "$cid" >"$evidence/container_stop.txt"; docker inspect "$cid" >"$evidence/container_inspect_stopped.json"
docker logs "$cid" >"$evidence/mysql.log" 2>&1; docker rm "$cid" >"$evidence/container_remove.txt"; cid=""
printf '{"event":"collection_complete","r0":false,"real_replay":false,"qps_collected":false}\n' >"$evidence/events.jsonl"
success=1
'''


def run_command(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    if result.returncode:
        raise CollectionError(
            f"command failed ({result.returncode}): {' '.join(args)}\n{result.stderr[-4000:]}"
        )
    return result


def load_query_weights(registry: Path | None) -> dict[str, float]:
    if registry is None:
        return {}
    value = json.loads(registry.read_text(encoding="utf-8"))
    rows = value.get("queries")
    if not isinstance(rows, list):
        raise CollectionError("registry queries are absent")
    result = {}
    for row in rows:
        query_id = str(row.get("query_id") or "")
        if not query_id or query_id in result:
            raise CollectionError("registry query ids are invalid")
        result[query_id] = float(row.get("weight", 1.0))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--benchmark", choices=("job", "hybench_xp", "tpch"), required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--source-datadir", required=True)
    parser.add_argument("--lock", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--sql-dir", type=Path, required=True)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--cpus", required=True)
    parser.add_argument("--mems", required=True)
    parser.add_argument("--cpu-quota", type=float, required=True)
    parser.add_argument("--memory-bytes", type=int, required=True)
    parser.add_argument("--memory-swap-bytes", type=int, required=True)
    parser.add_argument("--pids", type=int, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--read-bps", type=int, required=True)
    parser.add_argument("--write-bps", type=int, required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    for value in (args.scenario, args.benchmark):
        if SAFE.fullmatch(value) is None:
            raise CollectionError(f"unsafe identifier: {value}")
    run = args.run_id or datetime.now(timezone.utc).strftime(
        f"{args.benchmark}_plan_effect_t_%Y%m%dT%H%M%SZ"
    )
    if SAFE.fullmatch(run) is None:
        raise CollectionError("unsafe run id")
    sql_paths = sorted(args.sql_dir.glob("*.sql"))
    if not sql_paths:
        raise CollectionError("SQL directory is empty")
    weights = load_query_weights(args.registry)
    queries = []
    for path in sql_paths:
        query_id = path.stem
        if weights and query_id not in weights:
            # JOB capture files use an ordinal prefix; recover the registry id.
            matches = [name for name in weights if path.stem.endswith(name)]
            if len(matches) != 1:
                raise CollectionError(f"SQL file has no unique registry id: {path.name}")
            query_id = matches[0]
        queries.append(
            {
                "query_id": query_id,
                "file": path.name,
                "weight": float(weights.get(query_id, 1.0)),
                "sql_sha256": sha(path),
            }
        )
    if weights and set(weights) != {item["query_id"] for item in queries}:
        raise CollectionError("SQL directory does not exactly cover registry")

    active, ranges, points = endpoint_points(args.benchmark)
    manifest = {
        "schema_version": "mysql_plan_effect_endpoint_manifest_v1",
        "scenario": args.scenario,
        "benchmark": args.benchmark,
        "database": args.database,
        "active_params": active,
        "ranges": ranges,
        "queries": queries,
        "points": points,
        "qps_model_present": False,
        "optimistic_pruning_error_model_present": False,
    }
    with tempfile.TemporaryDirectory(prefix="mysql-plan-effect-t-") as raw_temp:
        staging = Path(raw_temp)
        (staging / "sql").mkdir()
        for path in sql_paths:
            (staging / "sql" / path.name).write_bytes(path.read_bytes())
        (staging / "endpoint_manifest.json").write_bytes(canonical(manifest))
        remote_input = f"{args.project}/plan_effect_t_inputs/{run}"
        run_command(["ssh", "-o", "BatchMode=yes", args.host, "mkdir", "-p", remote_input])
        run_command(["rsync", "-a", "--", str(staging) + "/", f"{args.host}:{remote_input}/"])
        command = [
            "ssh", "-o", "BatchMode=yes", "--", args.host, "bash", "-s", "--",
            args.project, args.source_datadir, args.lock, run, args.database,
            args.cpus, args.mems, str(args.cpu_quota), str(args.memory_bytes),
            str(args.memory_swap_bytes), str(args.pids), args.device,
            str(args.read_bps), str(args.write_bps), IMAGE, remote_input,
        ]
        remote = subprocess.run(
            command, input=REMOTE, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        if remote.returncode:
            raise CollectionError(
                f"remote collection failed ({remote.returncode}): {remote.stderr[-5000:]}"
            )
        args.output.mkdir(parents=True, exist_ok=False)
        run_command(
            [
                "rsync", "-a", "--",
                f"{args.host}:{args.project}/plan_effect_t_sessions/{run}/",
                str(args.output) + "/",
            ]
        )

    query_by_id = {row["query_id"]: row for row in queries}
    predictions: dict[str, dict[str, float]] = {}
    endpoint_refs: dict[str, Any] = {}
    for point in points:
        endpoint_id = point["endpoint_id"]
        totals = {"cpu": 0.0, "memory": 0.0, "io": 0.0}
        refs = []
        for query_id, row in query_by_id.items():
            path = args.output / "raw_explain" / endpoint_id / f"{query_id}.json"
            proxy = plan_proxy(_decode(path.read_text(encoding="utf-8")))
            for resource in totals:
                totals[resource] += proxy[resource] * float(row["weight"])
            refs.append({"query_id": query_id, "path": str(path), "sha256": sha(path)})
        predictions[endpoint_id] = totals
        endpoint_refs[endpoint_id] = {
            "params": point["params"], "plan_proxy": totals, "raw_explain": refs,
            "fresh_cost_readback": {
                "path": str(args.output / "raw_explain" / endpoint_id / "fresh_cost_readback.tsv"),
                "sha256": sha(args.output / "raw_explain" / endpoint_id / "fresh_cost_readback.tsv"),
            },
        }

    table: dict[str, dict[str, float]] = {}
    effects: dict[str, Any] = {}
    for name in active:
        lower = predictions[f"{name}_lower"]
        upper = predictions[f"{name}_upper"]
        table[name] = {resource: upper[resource] - lower[resource] for resource in totals}
        effects[name] = {
            resource: {
                "direction": (
                    "increase" if table[name][resource] > 0 else
                    "decrease" if table[name][resource] < 0 else "neutral"
                ),
                "signed_effect_full_range": table[name][resource],
                "evidence_status": (
                    "DETECTED_PLAN_EFFECT" if table[name][resource] else "NO_DETECTABLE_PLAN_EFFECT"
                ),
                "confidence": {
                    "source": "physical_plan_effect_experience",
                    "real_measurement": False,
                    "query_coverage_ratio": 1.0,
                    "decision": "SUPPORTED" if table[name][resource] else "NO_DETECTABLE_EFFECT",
                },
            }
            for resource in totals
        }

    artifact = {
        "schema_version": "mysql_experience_table_t_v1",
        "database": "mysql",
        "scenario": args.scenario,
        "benchmark": args.benchmark,
        "benchmark_version": "frozen_mysql_workloads_and_parameters_20260723_v3",
        "version": f"mysql_{args.benchmark}_physical_plan_effect_t_v1",
        "resource_columns": ["cpu", "memory", "io"],
        "parameter_space": {"active_params": active, "ranges": ranges},
        "table": table,
        "effect_evidence": effects,
        "calibration": {
            "method": "two_sided_oat_physical_plan_proxy_v1",
            "source": "physical_plan_effect_experience",
            "real_measurement": False,
            "query_coverage_ratio": 1.0,
        },
        "provenance": {
            "source": "physical_plan_effect_experience",
            "real_measurement": False,
            "cross_engine_prior_used": False,
            "qps_model_present": False,
            "joint_resource_error_coverage_present": False,
            "optimistic_pruning_decision": "FAIL_OPEN_RETAIN_REAL_REPLAY",
            "endpoint_manifest": {
                "path": str(args.output / "endpoint_manifest.json"),
                "sha256": sha(args.output / "endpoint_manifest.json"),
            },
            "endpoint_artifacts": endpoint_refs,
            "plan_proxy_formula": {
                "cpu": "sum(rows_examined_per_scan+rows_produced_per_join)/1e6",
                "memory": "count(temporary/materialized/grouping/duplicate-removal plan nodes)",
                "io": "sum(cost_info.read_cost)/1e6",
            },
        },
    }
    t_path = args.output / f"T.mysql_{args.benchmark}_physical_plan_effect_v1.json"
    digest = write_json(t_path, artifact)
    write_json(
        args.output / "summary.json",
        {
            "status": "COMPLETE",
            "scenario": args.scenario,
            "benchmark": args.benchmark,
            "endpoint_count": len(points),
            "query_count": len(queries),
            "query_coverage_ratio": 1.0,
            "t_path": str(t_path),
            "t_sha256": digest,
            "qps_error_constructed": False,
            "optimistic_pruning": "FAIL_OPEN",
        },
    )
    print(json.dumps({"status": "COMPLETE", "t": str(t_path), "sha256": digest}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
