"""Frozen drain/apply/flush/new-connection readback protocol for MySQL costs."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Callable, Mapping, Protocol

from formal_recost_core import MYSQL_COST_NAMES


SERVER_COST_NAMES = {
    "disk_temptable_create_cost",
    "disk_temptable_row_cost",
    "key_compare_cost",
    "memory_temptable_create_cost",
    "memory_temptable_row_cost",
    "row_evaluate_cost",
}
ENGINE_COST_NAMES = {"io_block_read_cost", "memory_block_read_cost"}


class CostProtocolError(RuntimeError):
    """The frozen cost apply/readback protocol did not complete exactly."""


class SQLClient(Protocol):
    def execute(self, sql: str) -> str: ...


ClientFactory = Callable[[], SQLClient]


@dataclass(frozen=True)
class CostApplyEvidence:
    drained_connection_ids: tuple[int, ...]
    params_requested: Mapping[str, float]
    params_readback: Mapping[str, float]
    apply_connection_generation: int
    readback_connection_generation: int
    commit: bool
    flush_statement: str
    new_connection_after_flush: bool


def _number(value: float) -> str:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise CostProtocolError("MySQL cost values must be finite and positive")
    return format(parsed, ".17g")


def load_and_validate_params(search_space_path: Path, params: Mapping[str, float]) -> dict[str, float]:
    document = json.loads(search_space_path.read_text(encoding="utf-8"))
    mysql = document.get("mysql") if isinstance(document, dict) else None
    spaces = mysql.get("parameters") if isinstance(mysql, dict) else None
    if not isinstance(spaces, dict) or set(spaces) != set(MYSQL_COST_NAMES):
        raise CostProtocolError("frozen MySQL search space is malformed")
    if set(params) != set(MYSQL_COST_NAMES):
        raise CostProtocolError("parameter point must contain exactly all eight MySQL costs")
    result: dict[str, float] = {}
    for name in MYSQL_COST_NAMES:
        value = float(params[name])
        item = spaces[name]
        if item.get("type") != "continuous" or item.get("transform") != "log":
            raise CostProtocolError(f"frozen search-space type differs for {name}")
        lower = float(item["min"])
        upper = float(item["max"])
        if not math.isfinite(value) or value < lower or value > upper:
            raise CostProtocolError(f"{name} is outside the frozen bounds")
        result[name] = value
    return result


def cost_apply_sql(params: Mapping[str, float]) -> str:
    if set(params) != set(MYSQL_COST_NAMES):
        raise CostProtocolError("cost apply requires exactly eight values")
    statements = ["START TRANSACTION"]
    for name in MYSQL_COST_NAMES:
        table = "server_cost" if name in SERVER_COST_NAMES else "engine_cost"
        where = f"cost_name='{name}'"
        if table == "engine_cost":
            where += " AND engine_name='default' AND device_type=0"
        statements.append(
            f"UPDATE mysql.{table} SET cost_value={_number(params[name])} WHERE {where}"
        )
    statements.extend(["COMMIT", "FLUSH OPTIMIZER_COSTS"])
    return ";".join(statements) + ";"


def _drain_query() -> str:
    return (
        "SELECT ID FROM information_schema.PROCESSLIST "
        "WHERE ID<>CONNECTION_ID() "
        "AND USER NOT IN ('system user','event_scheduler') "
        "AND COMMAND<>'Daemon' ORDER BY ID;"
    )


def _readback_query() -> str:
    server = ",".join(f"'{name}'" for name in sorted(SERVER_COST_NAMES))
    engine = ",".join(f"'{name}'" for name in sorted(ENGINE_COST_NAMES))
    return (
        "SELECT cost_name,COALESCE(cost_value,default_value) AS effective_value "
        "FROM mysql.server_cost "
        f"WHERE cost_name IN ({server}) "
        "UNION ALL "
        "SELECT cost_name,COALESCE(cost_value,default_value) AS effective_value "
        "FROM mysql.engine_cost WHERE engine_name='default' AND device_type=0 "
        f"AND cost_name IN ({engine}) ORDER BY cost_name;"
    )


def _parse_ids(raw: str) -> tuple[int, ...]:
    ids: list[int] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            value = int(line.strip())
        except ValueError as exc:
            raise CostProtocolError(f"non-numeric processlist ID: {line!r}") from exc
        if value <= 0:
            raise CostProtocolError("processlist IDs must be positive")
        ids.append(value)
    if len(ids) != len(set(ids)):
        raise CostProtocolError("duplicate processlist IDs")
    return tuple(ids)


def _parse_readback(raw: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 2 or fields[0] in values:
            raise CostProtocolError(f"malformed cost readback row: {line!r}")
        value = float(fields[1])
        if not math.isfinite(value) or value <= 0.0:
            raise CostProtocolError(f"invalid cost readback value: {line!r}")
        values[fields[0]] = value
    if set(values) != set(MYSQL_COST_NAMES):
        raise CostProtocolError(
            f"cost readback field mismatch: missing={sorted(set(MYSQL_COST_NAMES) - set(values))} "
            f"extra={sorted(set(values) - set(MYSQL_COST_NAMES))}"
        )
    return values


def drain_apply_flush_readback(
    client_factory: ClientFactory,
    search_space_path: Path,
    params: Mapping[str, float],
) -> CostApplyEvidence:
    requested = load_and_validate_params(search_space_path, params)
    generation = 1
    drain_client = client_factory()
    ids = _parse_ids(drain_client.execute(_drain_query()))
    for connection_id in ids:
        # Each KILL is deliberately issued from a fresh short-lived control
        # connection.  The actual workload has already stopped.
        generation += 1
        client_factory().execute(f"KILL CONNECTION {connection_id};")
    generation += 1
    remaining = _parse_ids(client_factory().execute(_drain_query()))
    if remaining:
        raise CostProtocolError(f"old client connections remain after drain: {remaining}")

    generation += 1
    apply_generation = generation
    client_factory().execute(cost_apply_sql(requested))
    generation += 1
    readback_generation = generation
    readback = _parse_readback(client_factory().execute(_readback_query()))
    for name in MYSQL_COST_NAMES:
        if not math.isclose(readback[name], requested[name], rel_tol=1e-12, abs_tol=1e-15):
            raise CostProtocolError(
                f"cost readback mismatch for {name}: requested={requested[name]} readback={readback[name]}"
            )
    if readback_generation <= apply_generation:
        raise CostProtocolError("readback did not use a new connection generation")
    return CostApplyEvidence(
        drained_connection_ids=ids,
        params_requested=requested,
        params_readback=readback,
        apply_connection_generation=apply_generation,
        readback_connection_generation=readback_generation,
        commit=True,
        flush_statement="FLUSH OPTIMIZER_COSTS",
        new_connection_after_flush=True,
    )
