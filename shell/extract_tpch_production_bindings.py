#!/usr/bin/env python3
"""Extract exact, executed TPC-H SQL bindings from a MySQL general log."""

import argparse
import hashlib
import json
import re
from pathlib import Path


HEADER = re.compile(
    r"^(?P<timestamp>\d{4}-\d\d-\d\dT\S+)\s+(?P<thread>\d+)\s+"
    r"(?P<command>\S+)\s*(?P<sql>.*)$"
)


def normalized(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.replace("`", "")).strip().lower()


def classify(sql: str) -> str | None:
    s = normalized(sql)
    if s.startswith("create view ") and "total_revenue" in s:
        return "Q15_CREATE_VIEW"
    if s.startswith("drop view ") and "revenue" in s:
        return "Q15_DROP_VIEW"
    if not s.startswith("select"):
        return None
    if "supp_nation" in s and "cust_nation" in s and "l_year" in s:
        return "Q7"
    if "promo_revenue" in s:
        return "Q14"
    if "high_line_count" in s and "low_line_count" in s:
        return "Q12"
    if "supplier_cnt" in s and "p_brand" in s and "p_size" in s:
        return "Q16"
    if "avg_yearly" in s:
        return "Q17"
    if "market_share" in s and "o_year" in s:
        return "Q8"
    if "numwait" in s and "not exists" in s:
        return "Q21"
    if "cntrycode" in s and "numcust" in s and "totacctbal" in s:
        return "Q22"
    if "custdist" in s and "c_count" in s and "left outer join" in s:
        return "Q13"
    if "sum(ps_supplycost * ps_availqty)" in s and "ps_partkey" in s:
        return "Q11"
    if "min(ps_supplycost)" in s and "s_acctbal" in s and "p_mfgr" in s:
        return "Q2"
    if "sum(amount)" in s and "o_year" in s and "nation" in s:
        return "Q9"
    if "c_mktsegment" in s and "revenue" in s and "l_orderkey" in s:
        return "Q3"
    if "o_orderpriority" in s and "order_count" in s and "l_commitdate < l_receiptdate" in s:
        return "Q4"
    if "l_returnflag" in s and "l_linestatus" in s and "avg_qty" in s:
        return "Q1"
    if "c_custkey" in s and "c_acctbal" in s and "l_returnflag" in s:
        return "Q10"
    if "n_name" in s and "r_name" in s and "sum(l_extendedprice * (1 - l_discount)) as revenue" in s:
        return "Q5"
    if "sum(l_extendedprice * l_discount) as revenue" in s and "l_quantity" in s:
        return "Q6"
    if "o_totalprice" in s and "having sum(l_quantity)" in s and "c_name" in s:
        return "Q18"
    if "sum(l_extendedprice * (1 - l_discount)) as revenue" in s and s.count("p_brand") >= 3:
        return "Q19"
    if "s_address" in s and "ps_availqty" in s and "0.5 * sum" in s:
        return "Q20"
    if "s_name" in s and "total_revenue" in s and "max(total_revenue)" in s:
        return "Q15_SELECT"
    return None


def records(path: Path):
    current = None
    for line in path.read_text(errors="replace").splitlines():
        match = HEADER.match(line)
        if match:
            if current:
                yield current
            current = match.groupdict()
        elif current is not None:
            current["sql"] += "\n" + line
    if current:
        yield current


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("general_log", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    found: dict[str, list[dict]] = {}
    for record in records(args.general_log):
        if record["command"] not in {"Execute", "Query"}:
            continue
        query = classify(record["sql"])
        if query is None:
            continue
        item = {
            "timestamp": record["timestamp"],
            "thread_id": int(record["thread"]),
            "command": record["command"],
            "sql_sha256": hashlib.sha256(record["sql"].encode()).hexdigest(),
            "sql": record["sql"],
        }
        found.setdefault(query, []).append(item)

    selected = {}
    for number in range(1, 23):
        query = f"Q{number}"
        if query == "Q15":
            parts = ["Q15_CREATE_VIEW", "Q15_SELECT", "Q15_DROP_VIEW"]
            if all(found.get(part) for part in parts):
                selected[query] = [found[part][0] for part in parts]
                sql = "\n\n".join(item["sql"].rstrip("; \n") + ";" for item in selected[query])
                (args.output_dir / "Q15.sql").write_text(sql + "\n")
                for suffix, item in zip(("setup", "select", "teardown"), selected[query]):
                    part_sql = item["sql"].rstrip("; \n") + ";\n"
                    (args.output_dir / f"Q15_{suffix}.sql").write_text(part_sql)
            continue
        if found.get(query):
            selected[query] = [found[query][0]]
            sql = found[query][0]["sql"].rstrip("; \n") + ";\n"
            (args.output_dir / f"{query}.sql").write_text(sql)

    missing = [f"Q{number}" for number in range(1, 23) if f"Q{number}" not in selected]
    manifest = {
        "schema_version": "tpch-production-binding-extraction-v1",
        "source_general_log": str(args.general_log),
        "source_sha256": hashlib.sha256(args.general_log.read_bytes()).hexdigest(),
        "coverage_complete": not missing,
        "covered_queries": sorted(selected, key=lambda q: int(q[1:])),
        "missing_queries": missing,
        "selected": selected,
        "classified_record_counts": {key: len(value) for key, value in sorted(found.items())},
        "fail_open_if_missing": True,
        "generic_custom_plan_available": False,
    }
    destination = args.output_dir / "binding_manifest.json"
    destination.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"coverage_complete": not missing, "covered": len(selected), "missing": missing}))
    return 0 if not missing else 2


if __name__ == "__main__":
    raise SystemExit(main())
