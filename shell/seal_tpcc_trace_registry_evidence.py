#!/usr/bin/env python3
"""Seal checksums after the dedicated TPC-C raw EXPLAIN run."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "history/mysql_sgd_algorithm_c/tpcc_cpu/production_trace_registry"


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> str:
    payload = canonical_bytes(value)
    path.write_bytes(payload)
    sha = hashlib.sha256(payload).hexdigest()
    path.with_name(path.name + ".sha256").write_text(f"{sha}  {path.name}\n", encoding="ascii")
    return sha


def main() -> int:
    inventory_path = OUT / "tpcc.raw_explain_inventory.json"
    feature_path = OUT / "tpcc.feature_source.json"
    registry_path = OUT / "tpcc.strict_registry.json"
    corpus_path = OUT / "tpcc.representative_sql_corpus.json"
    provenance_path = OUT / "tpcc.trace_provenance.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if inventory["explained_count"] + inventory["unsupported_or_error_count"] != len(inventory["entries"]):
        raise ValueError("raw EXPLAIN inventory counts do not reconcile")
    if inventory["dml_executed"] is not False or inventory["transaction_replay_used"] is not False:
        raise ValueError("raw EXPLAIN safety flags are invalid")
    if registry["execution_authorized"] is not False:
        raise ValueError("raw EXPLAIN cannot authorize the registry")
    inventory_sha = digest(inventory_path)
    feature = json.loads(feature_path.read_text(encoding="utf-8"))
    feature["raw_explain_required_before_estimator_use"] = False
    feature["raw_explain_inventory"] = {"path": str(inventory_path), "sha256": inventory_sha}
    feature_sha = write_json(feature_path, feature)
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance.update({
        "raw_explain_status": "COMPLETE",
        "raw_explain_inventory_path": str(inventory_path),
        "raw_explain_inventory_sha256": inventory_sha,
        "feature_source_path": str(feature_path),
        "feature_source_sha256": feature_sha,
        "execution_authorized": False,
        "complete_workload": True,
        "dml_executed": False,
        "transaction_replay_used": False,
    })
    provenance_sha = write_json(provenance_path, provenance)
    # Re-emit the inventory sidecar even when it was already present.
    inventory_path.with_name(inventory_path.name + ".sha256").write_text(
        f"{inventory_sha}  {inventory_path.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "status": "SEALED_FAIL_CLOSED",
        "inventory_sha256": inventory_sha,
        "feature_source_sha256": feature_sha,
        "provenance_sha256": provenance_sha,
        "explained_count": inventory["explained_count"],
        "unsupported_or_error_count": inventory["unsupported_or_error_count"],
        "registry_sha256": digest(registry_path),
        "corpus_sha256": digest(corpus_path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
