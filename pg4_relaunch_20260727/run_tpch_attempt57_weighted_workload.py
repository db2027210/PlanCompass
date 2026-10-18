#!/usr/bin/env python3
"""Run attempt57 TPC-H with transaction-local candidate planner parameters."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
from pathlib import Path
import sys
import threading
from typing import Any, Dict, Mapping, MutableMapping, Optional, Sequence


PARAMETERS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
BASE_RUNNER_NAME = "run_pg_weighted_sql_baseline_seeded.py"


def parse_candidate_args(argv: Sequence[str]) -> tuple[Dict[str, float], list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--candidate-params-json", required=True)
    known, remaining = parser.parse_known_args(list(argv))
    raw = json.loads(known.candidate_params_json)
    if not isinstance(raw, dict) or set(raw) != set(PARAMETERS):
        raise ValueError("candidate params must contain exactly seven planner GUCs")
    params = {name: float(raw[name]) for name in PARAMETERS}
    if any(not math.isfinite(value) or value <= 0.0 for value in params.values()):
        raise ValueError("candidate planner GUC values must be finite and positive")
    return params, remaining


def apply_local_and_readback(
    cursor: Any,
    params: Mapping[str, float],
) -> Dict[str, float]:
    """Apply all candidate GUCs to the current transaction and read them back."""
    for name in PARAMETERS:
        cursor.execute(
            f"SET LOCAL {name} = %s",
            (format(float(params[name]), ".17g"),),
        )
    cursor.execute(
        """
        SELECT name, current_setting(name)::double precision
        FROM unnest(%s::text[]) AS names(name)
        ORDER BY name
        """,
        (list(PARAMETERS),),
    )
    rows = cursor.fetchall()
    actual = {str(name): float(value) for name, value in rows}
    if set(actual) != set(PARAMETERS):
        raise RuntimeError(f"incomplete worker candidate readback: {sorted(actual)}")
    for name in PARAMETERS:
        if not math.isclose(
            actual[name],
            float(params[name]),
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise RuntimeError(
                f"worker candidate readback mismatch: "
                f"{name}={actual[name]} expected={params[name]}"
            )
    return actual


def is_worker_application(application_name: Any) -> bool:
    return str(application_name or "").startswith("resource-adaptive-tpch-worker-")


class WorkerAudit:
    def __init__(self, params: Mapping[str, float]) -> None:
        self.params = dict(params)
        self._lock = threading.Lock()
        self._workers: MutableMapping[str, Dict[str, Any]] = {}

    def record(self, application_name: str, readback: Mapping[str, float]) -> None:
        with self._lock:
            row = self._workers.setdefault(
                application_name,
                {
                    "application_name": application_name,
                    "transactions_applied": 0,
                    "immediate_readback_passed": True,
                    "first_readback": dict(readback),
                },
            )
            if row["first_readback"] != dict(readback):
                raise RuntimeError(
                    f"worker readback changed within point: {application_name}"
                )
            row["transactions_applied"] += 1

    def snapshot(self) -> list[Dict[str, Any]]:
        with self._lock:
            return [dict(self._workers[name]) for name in sorted(self._workers)]


class WorkerCursor:
    def __init__(
        self,
        raw_cursor: Any,
        *,
        raw_connection: Any,
        application_name: str,
        params: Mapping[str, float],
        audit: WorkerAudit,
    ) -> None:
        self._raw = raw_cursor
        self._connection = raw_connection
        self._application_name = application_name
        self._params = params
        self._audit = audit
        self._workload_query = False

    def __enter__(self) -> "WorkerCursor":
        self._raw.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> Any:
        suppressed = self._raw.__exit__(exc_type, exc, traceback)
        if exc_type is None:
            self._connection.commit()
        else:
            self._connection.rollback()
        return suppressed

    def execute(self, sql: Any, params: Optional[Sequence[Any]] = None) -> Any:
        text = str(sql).lstrip().upper()
        setup_statement = (
            text.startswith("SET STATEMENT_TIMEOUT")
            or text.startswith("SELECT SET_CONFIG('WORK_MEM'")
        )
        if setup_statement:
            return self._raw.execute(sql, params)
        if self._workload_query:
            raise RuntimeError("one workload statement per transaction is required")
        readback = apply_local_and_readback(self._raw, self._params)
        self._audit.record(self._application_name, readback)
        self._workload_query = True
        return self._raw.execute(sql, params)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)


class WorkerConnection:
    def __init__(
        self,
        raw_connection: Any,
        *,
        application_name: str,
        params: Mapping[str, float],
        audit: WorkerAudit,
    ) -> None:
        self._raw = raw_connection
        self._application_name = application_name
        self._params = params
        self._audit = audit

    @property
    def autocommit(self) -> bool:
        return False

    @autocommit.setter
    def autocommit(self, requested: bool) -> None:
        if requested is not True:
            raise RuntimeError("attempt57 worker expects the base runner to request autocommit")
        self._raw.autocommit = False

    def cursor(self, *args: Any, **kwargs: Any) -> WorkerCursor:
        return WorkerCursor(
            self._raw.cursor(*args, **kwargs),
            raw_connection=self._raw,
            application_name=self._application_name,
            params=self._params,
            audit=self._audit,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)


def load_base_runner(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("attempt57_weighted_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load weighted workload runner: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def augment_outputs(
    output_dir: Path,
    params: Mapping[str, float],
    audit: WorkerAudit,
    workers_expected: int,
) -> None:
    rows = audit.snapshot()
    if (
        len(rows) != workers_expected
        or any(
            row["transactions_applied"] <= 0
            or row["immediate_readback_passed"] is not True
            or row["first_readback"] != dict(params)
            for row in rows
        )
    ):
        raise RuntimeError(
            f"worker session-local candidate audit failed: "
            f"{len(rows)}/{workers_expected}"
        )
    application = {
        "mode": "transaction_local_set_local",
        "alter_system_used": False,
        "global_settings_changed": False,
        "every_query_transaction_applies_all_parameters": True,
        "immediate_readback_required": True,
        "workers_expected": workers_expected,
        "workers_verified": len(rows),
        "worker_sessions": rows,
    }
    for filename in ("run_manifest.json", "summary.json"):
        path = output_dir / filename
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["effective_parameters"] = dict(params)
        payload["candidate_parameter_application"] = application
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    incoming = list(sys.argv[1:] if argv is None else argv)
    params, base_argv = parse_candidate_args(incoming)
    base_path = Path(__file__).resolve().with_name(BASE_RUNNER_NAME)
    base = load_base_runner(base_path)
    audit = WorkerAudit(params)
    raw_connect = base.psycopg2.connect

    def connect(*args: Any, **kwargs: Any) -> Any:
        application_name = kwargs.get("application_name")
        connection = raw_connect(*args, **kwargs)
        if not is_worker_application(application_name):
            return connection
        return WorkerConnection(
            connection,
            application_name=str(application_name),
            params=params,
            audit=audit,
        )

    base.psycopg2.connect = connect
    original_argv = sys.argv
    try:
        sys.argv = [str(base_path), *base_argv]
        args = base.parse_args()
        result = int(base.main())
    finally:
        sys.argv = original_argv
        base.psycopg2.connect = raw_connect
    if result != 0:
        return result
    augment_outputs(args.output_dir.resolve(), params, audit, args.workers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
