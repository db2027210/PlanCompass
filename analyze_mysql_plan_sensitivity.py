"""Find SQL queries whose MySQL plans change across real GD cost candidates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from mysql_cost_adapter import MYSQL_COST_KEYS, MySQLCLIClient, MySQLCostAdapter


def plan_signature(value: Any) -> tuple[tuple[str, str, str], ...]:
    rows: list[tuple[str, str, str]] = []

    def visit(node: Any) -> None:
        if isinstance(node, Mapping):
            table = node.get("table")
            if isinstance(table, Mapping):
                rows.append((
                    str(table.get("table_name", "")),
                    str(table.get("access_type", "")),
                    str(table.get("key", "")),
                ))
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return tuple(rows)


def load_profiles(paths: list[Path], baseline: Mapping[str, float]) -> list[dict[str, float]]:
    profiles = [{name: float(baseline[name]) for name in MYSQL_COST_KEYS}]
    seen = {tuple(sorted(profiles[0].items()))}
    for path in paths:
        document = json.loads(path.read_text(encoding="utf-8"))
        for row in document.get("outer_loop", []):
            params = row.get("params")
            if not isinstance(params, Mapping) or not set(MYSQL_COST_KEYS) <= set(params):
                continue
            profile = {name: float(params[name]) for name in MYSQL_COST_KEYS}
            signature = tuple(sorted(profile.items()))
            if signature not in seen:
                seen.add(signature)
                profiles.append(profile)
    return profiles


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--query-dir", type=Path, required=True)
    parser.add_argument("--query-glob", default="*.sql")
    parser.add_argument("--formal-result", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    client = MySQLCLIClient({
        "mysql_binary": "/usr/bin/mysql", "host": "127.0.0.1", "port": args.port,
        "user": "root", "password": "CHANGE_ME", "database": args.database,
        "query_timeout_sec": 30,
    })
    adapter = MySQLCostAdapter(client)
    snapshot = adapter.snapshot()
    profiles = load_profiles(args.formal_result, {item.name: item.effective_value for item in snapshot})
    queries = sorted(args.query_dir.glob(args.query_glob))
    if not queries:
        raise ValueError(f"no queries matched {args.query_dir / args.query_glob}")
    signatures: dict[str, list[tuple[tuple[str, str, str], ...] | None]] = {
        path.name: [] for path in queries
    }
    errors: dict[str, list[dict[str, Any]]] = {}
    try:
        for profile_index, profile in enumerate(profiles):
            adapter.apply(profile)
            for path in queries:
                sql = path.read_text(encoding="utf-8").strip().rstrip(";")
                try:
                    plan = json.loads(client.execute("EXPLAIN FORMAT=JSON " + sql).strip())
                    signatures[path.name].append(plan_signature(plan))
                except Exception as exc:
                    signatures[path.name].append(None)
                    errors.setdefault(path.name, []).append({
                        "profile_index": profile_index, "error": str(exc),
                    })
    finally:
        adapter.restore(snapshot)

    changed = []
    for name, values in signatures.items():
        unique = []
        for value in values:
            if value is not None and value not in unique:
                unique.append(value)
        if len(unique) > 1:
            changed.append({
                "query": name, "variant_count": len(unique),
                "variants": [[{"table": a, "access": b, "key": c} for a, b, c in value] for value in unique],
            })
    payload = {
        "port": args.port, "database": args.database, "query_count": len(queries),
        "profile_count": len(profiles), "changed_query_count": len(changed),
        "changed_queries": changed, "errors": errors,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output), "query_count": len(queries),
        "profile_count": len(profiles), "changed_query_count": len(changed),
        "changed_queries": [row["query"] for row in changed], "error_query_count": len(errors),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
