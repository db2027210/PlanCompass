"""MySQL numeric tuning access with exact snapshot and rollback support."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Dict, List, Mapping, Optional, Protocol


SERVER_COST_KEYS = (
    "disk_temptable_create_cost",
    "disk_temptable_row_cost",
    "key_compare_cost",
    "memory_temptable_create_cost",
    "memory_temptable_row_cost",
    "row_evaluate_cost",
)
ENGINE_COST_KEYS = ("io_block_read_cost", "memory_block_read_cost")
MYSQL_COST_KEYS = SERVER_COST_KEYS + ENGINE_COST_KEYS
SYSTEM_VARIABLE_KEYS = (
    "innodb_adaptive_hash_index",
    "innodb_buffer_pool_size",
    "innodb_flush_log_at_trx_commit",
    "innodb_spin_wait_delay",
    "innodb_spin_wait_pause_multiplier",
    "innodb_sync_spin_loops",
    "innodb_thread_concurrency",
    "innodb_log_spin_cpu_abs_lwm",
    "innodb_log_spin_cpu_pct_hwm",
    "innodb_log_wait_for_flush_spin_hwm",
    "join_buffer_size",
    "sort_buffer_size",
    "read_buffer_size",
    "read_rnd_buffer_size",
    "tmp_table_size",
    "max_heap_table_size",
)
ZERO_ALLOWED_SYSTEM_VARIABLE_KEYS = (
    "innodb_adaptive_hash_index",
    "innodb_thread_concurrency",
)
MYSQL_TUNING_KEYS = MYSQL_COST_KEYS + SYSTEM_VARIABLE_KEYS


class SQLClient(Protocol):
    def execute(self, sql: str) -> str:
        """Execute SQL in a fresh client session and return tab-separated rows."""


@dataclass(frozen=True)
class CostSnapshotItem:
    """One cost row, retaining NULL so rollback can restore compiled defaults."""

    name: str
    table: str
    raw_value: Optional[float]
    default_value: float
    engine_name: Optional[str] = None
    device_type: Optional[int] = None

    @property
    def effective_value(self) -> float:
        return self.default_value if self.raw_value is None else self.raw_value


class MySQLCLIClient:
    """Small dependency-free SQL client using the official ``mysql`` CLI."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.mysql_binary = str(config.get("mysql_binary", "mysql"))
        self.defaults_extra_file = str(config.get("defaults_extra_file", "")).strip()
        self.host = str(config.get("host", "127.0.0.1"))
        self.port = int(config.get("port", 3306))
        self.user = str(config.get("user", "root"))
        self.database = str(config.get("database", "")).strip()
        self.password = str(config.get("password", ""))
        self.timeout_sec = float(config.get("query_timeout_sec", 30.0))

    def _args(self, sql: str) -> List[str]:
        args = [self.mysql_binary]
        # MySQL requires --defaults-extra-file to precede all other options.
        if self.defaults_extra_file:
            args.append(f"--defaults-extra-file={self.defaults_extra_file}")
        args.extend(
            [
                "--batch",
                "--raw",
                "--skip-column-names",
                f"--host={self.host}",
                f"--port={self.port}",
                f"--user={self.user}",
            ]
        )
        if self.database:
            args.append(f"--database={self.database}")
        # A client-side timeout alone disconnects the mysql process while the
        # server can keep executing the SELECT for hours.  Set a slightly
        # shorter server-side guard so timed-out diagnostics cannot become
        # orphan queries.  MySQL applies MAX_EXECUTION_TIME only to SELECTs;
        # administrative snapshot/restore statements remain unaffected.
        guarded_sql = sql
        if self.timeout_sec > 0:
            guard_ms = max(1, int(self.timeout_sec * 900.0))
            guarded_sql = f"SET SESSION MAX_EXECUTION_TIME={guard_ms};{sql}"
        args.extend(["--execute", guarded_sql])
        return args

    def execute(self, sql: str) -> str:
        env = os.environ.copy()
        if self.password:
            env["MYSQL_PWD"] = self.password
        try:
            proc = subprocess.run(
                self._args(sql),
                capture_output=True,
                text=True,
                timeout=self.timeout_sec if self.timeout_sec > 0 else None,
                env=env,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"mysql client not found: {self.mysql_binary}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"mysql query timed out after {self.timeout_sec:.1f}s") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            raise RuntimeError(f"mysql query failed (exit={proc.returncode}): {detail}")
        return proc.stdout


def _parse_nullable_float(value: str) -> Optional[float]:
    normalized = value.strip()
    if normalized.upper() == "NULL" or normalized == r"\N" or not normalized:
        return None
    return float(normalized)


def _sql_number(value: float) -> str:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"MySQL optimizer cost must be finite and > 0, got {value!r}")
    return format(parsed, ".17g")


def _sql_system_number(value: float, *, allow_zero: bool = False) -> str:
    parsed = float(value)
    lower_bound_violated = parsed < 0 if allow_zero else parsed <= 0
    if not math.isfinite(parsed) or lower_bound_violated:
        comparator = ">= 0" if allow_zero else "> 0"
        raise ValueError(
            f"MySQL numeric system variable must be finite and {comparator}, got {value!r}"
        )
    return str(int(round(parsed)))


class MySQLCostAdapter:
    """Read, apply, and restore cost constants plus allowlisted numeric globals."""

    def __init__(
        self,
        client: SQLClient,
        *,
        engine_name: str = "default",
        device_type: int = 0,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_]+", engine_name):
            raise ValueError(f"unsafe engine_name: {engine_name!r}")
        if int(device_type) != 0:
            raise ValueError("MySQL currently permits only device_type=0")
        self.client = client
        self.engine_name = engine_name
        self.device_type = int(device_type)

    @staticmethod
    def validate_names(names: Mapping[str, float]) -> None:
        unknown = sorted(set(names) - set(MYSQL_TUNING_KEYS))
        if unknown:
            raise ValueError(f"unsupported MySQL numeric tuning parameters: {unknown}")

    def snapshot(self) -> List[CostSnapshotItem]:
        server_names = ",".join(f"'{name}'" for name in SERVER_COST_KEYS)
        server_sql = (
            "SELECT cost_name,cost_value,default_value FROM mysql.server_cost "
            f"WHERE cost_name IN ({server_names}) ORDER BY cost_name"
        )
        engine_names = ",".join(f"'{name}'" for name in ENGINE_COST_KEYS)
        engine_sql = (
            "SELECT cost_name,cost_value,default_value,engine_name,device_type "
            "FROM mysql.engine_cost "
            f"WHERE engine_name='{self.engine_name}' AND device_type={self.device_type} "
            f"AND cost_name IN ({engine_names}) ORDER BY cost_name"
        )
        items: List[CostSnapshotItem] = []
        for line in self.client.execute(server_sql).splitlines():
            columns = line.split("\t")
            if len(columns) != 3:
                raise RuntimeError(f"unexpected mysql.server_cost row: {line!r}")
            raw_value = _parse_nullable_float(columns[1])
            default_value = _parse_nullable_float(columns[2])
            if default_value is None:
                raise RuntimeError(f"missing compiled default for server cost {columns[0]}")
            items.append(CostSnapshotItem(columns[0], "server_cost", raw_value, default_value))
        for line in self.client.execute(engine_sql).splitlines():
            columns = line.split("\t")
            if len(columns) != 5:
                raise RuntimeError(f"unexpected mysql.engine_cost row: {line!r}")
            raw_value = _parse_nullable_float(columns[1])
            default_value = _parse_nullable_float(columns[2])
            if default_value is None:
                raise RuntimeError(f"missing compiled default for engine cost {columns[0]}")
            items.append(
                CostSnapshotItem(
                    columns[0], "engine_cost", raw_value, default_value, columns[3], int(columns[4])
                )
            )
        global_sql = "SELECT " + ",".join(f"@@GLOBAL.{name}" for name in SYSTEM_VARIABLE_KEYS)
        global_lines = self.client.execute(global_sql).splitlines()
        if len(global_lines) != 1:
            raise RuntimeError(f"unexpected global variable snapshot: {global_lines!r}")
        global_values = global_lines[0].split("\t")
        if len(global_values) != len(SYSTEM_VARIABLE_KEYS):
            raise RuntimeError(f"incomplete global variable snapshot: {global_lines[0]!r}")
        for name, value in zip(SYSTEM_VARIABLE_KEYS, global_values):
            parsed = float(value)
            items.append(CostSnapshotItem(name, "global_variable", parsed, parsed))
        found = {item.name for item in items}
        missing = sorted(set(MYSQL_TUNING_KEYS) - found)
        if missing:
            raise RuntimeError(
                f"MySQL cost catalog is incomplete for engine={self.engine_name}: missing={missing}"
            )
        return items

    def effective_values(self) -> Dict[str, float]:
        return {item.name: item.effective_value for item in self.snapshot()}

    def _wait_for_buffer_pool_size(
        self,
        requested: float,
        *,
        timeout_sec: float = 180.0,
        poll_interval_sec: float = 1.0,
    ) -> None:
        """Wait until MySQL's asynchronous buffer-pool resize is visible."""
        target = int(round(float(requested)))
        actual = -1
        deadline = time.monotonic() + timeout_sec
        while True:
            fields = self.client.execute(
                "SELECT @@GLOBAL.innodb_buffer_pool_size"
            ).strip().split()
            if fields:
                actual = int(float(fields[0]))
                if actual == target:
                    return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    f"buffer-pool resize timed out: requested={target}, actual={actual}"
                )
            time.sleep(min(poll_interval_sec, remaining))

    def apply(self, values: Mapping[str, float]) -> None:
        self.validate_names(values)
        cost_values = {name: value for name, value in values.items() if name in MYSQL_COST_KEYS}
        statements = ["START TRANSACTION"]
        for name, value in cost_values.items():
            table = "server_cost" if name in SERVER_COST_KEYS else "engine_cost"
            where = f"cost_name='{name}'"
            if table == "engine_cost":
                where += f" AND engine_name='{self.engine_name}' AND device_type={self.device_type}"
            statements.append(
                f"UPDATE mysql.{table} SET cost_value={_sql_number(float(value))} WHERE {where}"
            )
        statements.extend(["COMMIT", "FLUSH OPTIMIZER_COSTS"])
        for name, value in values.items():
            if name in SYSTEM_VARIABLE_KEYS:
                statements.append(
                    f"SET GLOBAL {name}="
                    f"{_sql_system_number(float(value), allow_zero=name in ZERO_ALLOWED_SYSTEM_VARIABLE_KEYS)}"
                )
        self.client.execute(";".join(statements) + ";")
        if "innodb_buffer_pool_size" in values:
            self._wait_for_buffer_pool_size(values["innodb_buffer_pool_size"])

    def restore(self, snapshot: List[CostSnapshotItem]) -> None:
        statements = ["START TRANSACTION"]
        for item in snapshot:
            if item.table == "global_variable":
                continue
            value_sql = "NULL" if item.raw_value is None else _sql_number(item.raw_value)
            where = f"cost_name='{item.name}'"
            if item.table == "engine_cost":
                where += (
                    f" AND engine_name='{item.engine_name}' AND device_type={int(item.device_type or 0)}"
                )
            statements.append(f"UPDATE mysql.{item.table} SET cost_value={value_sql} WHERE {where}")
        statements.extend(["COMMIT", "FLUSH OPTIMIZER_COSTS"])
        for item in snapshot:
            if item.table == "global_variable":
                statements.append(
                    f"SET GLOBAL {item.name}="
                    f"{_sql_system_number(item.effective_value, allow_zero=item.name in ZERO_ALLOWED_SYSTEM_VARIABLE_KEYS)}"
                )
        self.client.execute(";".join(statements) + ";")
        buffer_pool = next(
            (item.effective_value for item in snapshot if item.name == "innodb_buffer_pool_size"),
            None,
        )
        if buffer_pool is not None:
            self._wait_for_buffer_pool_size(buffer_pool)

    @staticmethod
    def save_snapshot(path: Path, snapshot: List[CostSnapshotItem]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([asdict(item) for item in snapshot], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def load_snapshot(path: Path) -> List[CostSnapshotItem]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("snapshot must contain a JSON list")
        return [CostSnapshotItem(**item) for item in payload]
