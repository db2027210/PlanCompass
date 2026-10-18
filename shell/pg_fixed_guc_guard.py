#!/usr/bin/env python3
"""Fail-closed PostgreSQL non-search GUC execution-domain guard.

The formal optimizers are allowed to change only the seven planner cost GUCs
listed in ``binary_plan_resource_tuner.COST_PARAMS``.  This module snapshots
every other ``pg_settings`` row and compares the semantic values across
workers and replay boundaries.  Instance-identity settings whose values must
differ between independent workers (for example ``port`` and
``data_directory``) are retained in the receipt but excluded from the shared
semantic fingerprint.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from binary_plan_resource_tuner import (
    COST_PARAMS,
    pg_conn_from_worker,
    psql_query_with_transient_retry,
)


IDENTITY_GUCS = frozenset(
    {
        "config_file",
        "data_directory",
        "external_pid_file",
        "hba_file",
        "ident_file",
        "port",
    }
)


def canonical_sha256(value: Any) -> str:
    """Return a stable SHA-256 for a JSON-serializable value."""
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def snapshot_non_search_gucs(
    cfg: Mapping[str, Any],
    worker: Mapping[str, Any],
) -> Dict[str, Any]:
    """Capture all PostgreSQL settings except the seven searchable GUCs."""
    pg = pg_conn_from_worker(dict(cfg), dict(worker))
    sql = """
        SELECT name,
               setting,
               COALESCE(unit, ''),
               vartype,
               context,
               source,
               pending_restart::text
        FROM pg_settings
        ORDER BY name
    """
    output = psql_query_with_transient_retry(
        pg,
        sql,
        dbname="postgres",
        attempts=5,
        retry_delay_s=1.0,
        retry_context="fixed_guc_snapshot",
    )
    rows: Dict[str, Dict[str, Any]] = {}
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) != 7:
            raise RuntimeError(f"malformed pg_settings row: {line!r}")
        name = parts[0]
        if name in COST_PARAMS:
            continue
        rows[name] = {
            "setting": parts[1],
            "unit": parts[2],
            "vartype": parts[3],
            "context": parts[4],
            "source": parts[5],
            "pending_restart": parts[6] == "on",
        }
    if not rows:
        raise RuntimeError("non-search pg_settings snapshot is empty")
    semantic = {
        name: value
        for name, value in rows.items()
        if name not in IDENTITY_GUCS
    }
    identity = {
        name: rows[name]
        for name in sorted(IDENTITY_GUCS)
        if name in rows
    }
    return {
        "contract": "pg_non_search_guc_snapshot_v1",
        "worker": str(worker.get("name") or worker.get("container") or ""),
        "search_gucs_excluded": list(COST_PARAMS),
        "identity_gucs_excluded_from_shared_fingerprint": sorted(IDENTITY_GUCS),
        "semantic_settings": semantic,
        "semantic_sha256": canonical_sha256(semantic),
        "identity_settings": identity,
        "setting_count": len(rows),
    }


def audit_fixed_guc_domain(
    cfg: Mapping[str, Any],
    workers: Sequence[Mapping[str, Any]],
    *,
    expected_semantic_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    """Require identical non-search GUCs across workers and replay boundaries."""
    if not workers:
        raise ValueError("fixed-GUC audit requires at least one worker")
    snapshots = [snapshot_non_search_gucs(cfg, worker) for worker in workers]
    hashes = [str(snapshot["semantic_sha256"]) for snapshot in snapshots]
    mismatches: Dict[str, Dict[str, Any]] = {}
    reference = snapshots[0]["semantic_settings"]
    for snapshot in snapshots[1:]:
        current = snapshot["semantic_settings"]
        for name in sorted(set(reference) | set(current)):
            if reference.get(name) != current.get(name):
                mismatches[name] = {
                    "reference": reference.get(name),
                    "observed": current.get(name),
                    "worker": snapshot["worker"],
                }
    errors = []
    if len(set(hashes)) != 1:
        errors.append("non_search_guc_worker_mismatch")
    if expected_semantic_sha256 and hashes[0] != expected_semantic_sha256:
        errors.append("non_search_guc_replay_boundary_drift")
    receipt = {
        "contract": "pg_fixed_non_search_guc_domain_guard_v1",
        "passed": not errors,
        "errors": errors,
        "semantic_sha256": hashes[0],
        "expected_semantic_sha256": expected_semantic_sha256,
        "worker_count": len(workers),
        "snapshots": snapshots,
        "mismatches": mismatches,
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    if errors:
        raise RuntimeError(
            "fixed non-search PostgreSQL GUC domain failed: "
            + json.dumps(receipt, ensure_ascii=False, sort_keys=True)
        )
    return receipt


def write_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    """Persist an atomic, human-auditable fixed-GUC receipt."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def assert_only_search_gucs_changed(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> None:
    """Require the semantic non-search fingerprint to remain unchanged."""
    before_hash = str(before.get("semantic_sha256") or "")
    after_hash = str(after.get("semantic_sha256") or "")
    if not before_hash or before_hash != after_hash:
        raise RuntimeError(
            "non-search PostgreSQL GUCs changed across replay: "
            f"before={before_hash!r} after={after_hash!r}"
        )


def searchable_gucs() -> Iterable[str]:
    """Expose the formal search set for audit/report generation."""
    return tuple(COST_PARAMS)
