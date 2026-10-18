#!/usr/bin/env python3
"""Unit tests for cross-worker PostgreSQL planner-statistics synchronization."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.sync_pg_planner_statistics import (  # noqa: E402
    remap_statistics_sql,
    split_sql_values,
)


def main() -> int:
    values = "1, 2, false, '{1,2}', '{\"a,b\",c}', NULL"
    assert split_sql_values(values) == [
        "1",
        "2",
        "false",
        "'{1,2}'",
        "'{\"a,b\",c}'",
        "NULL",
    ]

    columns = ", ".join(f"c{index}" for index in range(31))
    row = ["NULL"] * 31
    row[0] = "10001"
    row[1] = "2"
    row[2] = "false"
    row[3] = "0"
    row[4] = "4"
    row[5] = "1"
    row[26] = "'{1,2}'"
    dump_sql = (
        f"INSERT INTO pg_catalog.pg_statistic ({columns}) VALUES ("
        + ", ".join(row)
        + ");\n"
    )
    sql, count = remap_statistics_sql(
        dump_sql,
        {("public", "table_a"): 10001},
        {("public", "table_a"): 20001},
        {(10001, 2): (23, -1)},
        {("public", "table_a"): (10, 100.0, 9)},
    )
    assert count == 1
    assert "WHERE starelid IN (20001)" in sql
    assert "WHERE oid = 20001" in sql
    assert "pg_catalog.array_in('{1,2}'::cstring, 23, -1)" in sql
    assert "VALUES (20001, 2" in sql
    assert sql.startswith("BEGIN;")
    assert sql.endswith("COMMIT;\n")

    print("PASS: PostgreSQL planner-statistics synchronization", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
