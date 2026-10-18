#!/usr/bin/env python3
"""XP v34: restore and verify the PostgreSQL baseline before the R0 builder."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

try:
    import resume_xp_fresh0_fixed_contract_target10_v33 as v33
except ModuleNotFoundError:
    from tools import resume_xp_fresh0_fixed_contract_target10_v33 as v33


EXPECTED_CPUSET = frozenset((*range(32, 52), *range(288, 308)))
EXPECTED_CPUSET_TEXT = "32-51,288-307"
EXPECTED_BASELINE = {
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "cpu_tuple_cost": 0.01,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
    "random_page_cost": 4.0,
    "seq_page_cost": 1.0,
}
RESTORE_TIMEOUT_SECONDS = 900
_BASE_BOOTSTRAP = v33.run_fresh_runtime_baseline_bootstrap


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _authority_paths() -> list[Path]:
    args = v33.args_global
    values = (
        args.canonical_state,
        args.canonical_manifest,
        args.formal_manifest,
        args.authority_counter,
        args.authority_history,
        args.acceptance_manifest,
    )
    paths = []
    for value in values:
        path = Path(value).resolve()
        if path not in paths:
            paths.append(path)
    return paths


def _sealed_hashes(paths: Sequence[Path]) -> Dict[str, str]:
    output = {}
    for path in paths:
        if not path.is_file():
            raise RuntimeError(f"XP v34 authority file is missing: {path}")
        output[str(path)] = v33.sha256(path)
    return output


def _assert_affinity() -> None:
    observed = frozenset(os.sched_getaffinity(0))
    if observed != EXPECTED_CPUSET:
        raise RuntimeError(
            "XP v34 controller affinity mismatch: "
            f"expected={EXPECTED_CPUSET_TEXT} observed={sorted(observed)}"
        )


def _verify_postgres(config: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        import psycopg2  # type: ignore
    except ImportError as exc:
        raise RuntimeError("XP v34 requires psycopg2 for sealed PG checks") from exc
    pg = config.get("postgres") or {}
    expected_database = str(pg.get("dbname") or "")
    if expected_database != "hybench_sf10x":
        raise RuntimeError("XP v34 config database is not hybench_sf10x")
    connection_args = {
        "host": str(pg.get("host") or "127.0.0.1"),
        "port": int(pg.get("port") or 59630),
        "user": str(pg.get("user") or "postgres"),
        "password": os.environ.get("PGPASSWORD", "postgres"),
        "connect_timeout": 10,
    }
    with psycopg2.connect(dbname="postgres", **connection_args) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT datname FROM pg_database WHERE datname = %s",
                (expected_database,),
            )
            if cursor.fetchone() != (expected_database,):
                raise RuntimeError("XP v34 restored database is missing")
    with psycopg2.connect(
        dbname=expected_database,
        **connection_args,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT name, setting FROM pg_settings "
                "WHERE name = ANY(%s) ORDER BY name",
                (list(EXPECTED_BASELINE),),
            )
            observed = {name: float(value) for name, value in cursor.fetchall()}
    if observed != EXPECTED_BASELINE:
        raise RuntimeError(
            f"XP v34 seven-parameter baseline mismatch: {observed}"
        )
    return {
        "database": expected_database,
        "host": connection_args["host"],
        "port": connection_args["port"],
        "seven_parameter_baseline": observed,
    }


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def restore_and_verify_before_r0_builder(
    *,
    campaign: Path,
    state_store: Any,
    config: Path,
    restore_script: Path,
) -> Dict[str, Any]:
    """Perform the sealed zero-workload restore exactly before R0 EXPLAIN."""
    _assert_affinity()
    if not restore_script.is_file():
        raise RuntimeError("XP v34 restore script is missing")
    authority_before = _sealed_hashes(_authority_paths())
    state_before = copy.deepcopy(state_store.load())
    state_digest_before = _canonical_sha256(state_before)
    audit_path = campaign / "v34_pre_r0_restore_audit.json"
    if audit_path.exists():
        audit = v33.load(audit_path)
        if (
            audit.get("status") != "complete"
            or audit.get("restore_script_sha256") != v33.sha256(restore_script)
            or audit.get("authority_sha256_before") != authority_before
            or audit.get("authority_sha256_after") != authority_before
            or audit.get("campaign_state_sha256_before") != state_digest_before
            or audit.get("campaign_state_sha256_after") != state_digest_before
        ):
            raise RuntimeError("XP v34 existing restore audit is not sealed")
        _assert_affinity()
        _verify_postgres(v33.load(config))
        return audit

    completed = subprocess.run(
        [str(restore_script)],
        check=False,
        capture_output=True,
        text=True,
        timeout=RESTORE_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "XP v34 sealed restore failed before R0 builder: "
            f"returncode={completed.returncode} stderr={completed.stderr[-1000:]}"
        )
    _assert_affinity()
    postgres = _verify_postgres(v33.load(config))
    authority_after = _sealed_hashes(_authority_paths())
    state_after = state_store.load()
    state_digest_after = _canonical_sha256(state_after)
    if authority_after != authority_before:
        raise RuntimeError("XP v34 restore mutated formal/canonical authority")
    if state_after != state_before or state_digest_after != state_digest_before:
        raise RuntimeError("XP v34 restore mutated fresh campaign state")
    audit = {
        "schema_version": "xp_v34_pre_r0_restore_audit_v1",
        "status": "complete",
        "physical_workload_calls_added": 0,
        "restore_script": str(restore_script),
        "restore_script_sha256": v33.sha256(restore_script),
        "restore_timeout_seconds": RESTORE_TIMEOUT_SECONDS,
        "cpuset": EXPECTED_CPUSET_TEXT,
        "postgres": postgres,
        "authority_sha256_before": authority_before,
        "authority_sha256_after": authority_after,
        "campaign_state_sha256_before": state_digest_before,
        "campaign_state_sha256_after": state_digest_after,
        "next_action": "sealed_explain_builder_then_fresh_r0_replay",
        "completed_at_epoch": time.time(),
    }
    _atomic_json(audit_path, audit)
    return audit


def run_fresh_runtime_baseline_bootstrap_v34(**kwargs: Any) -> Dict[str, Any]:
    state_store = kwargs["state_store"]
    state = state_store.load()
    existing = state.get("fresh_runtime_baseline_revalidation") or {}
    if existing.get("status") == "complete":
        return _BASE_BOOTSTRAP(**kwargs)
    restore_and_verify_before_r0_builder(
        campaign=Path(kwargs["campaign"]).resolve(),
        state_store=state_store,
        config=Path(kwargs["config"]).resolve(),
        restore_script=Path(kwargs["restore_script"]).resolve(),
    )
    return _BASE_BOOTSTRAP(**kwargs)


def main() -> int:
    v33.run_fresh_runtime_baseline_bootstrap = (
        run_fresh_runtime_baseline_bootstrap_v34
    )
    return v33.main()


if __name__ == "__main__":
    raise SystemExit(main())
