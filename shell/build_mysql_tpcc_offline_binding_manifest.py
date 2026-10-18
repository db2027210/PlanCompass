#!/usr/bin/env python3
"""Build the 34-statement TPC-C collector manifest from typed production trace."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT
    / "history/mysql_sgd_algorithm_c/tpcc_cpu/typed_v2_registry/"
    "7622cfac4ea2-58f1d761fa97-77af321d5551/"
    "tpcc.typed_v2.strict_registry.json"
)
OUTPUT_ROOT = ROOT / "artifacts/mysql_tpcc_typed_manifest"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: object) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("utf-8")


def main() -> int:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    if (
        source.get("formal_scope") != "mysql_only"
        or source.get("cross_engine_comparison_prohibited") is not True
        or source.get("immutable") is not True
        or source.get("statement_template_count") != 34
        or len(source.get("statements", [])) != 34
    ):
        raise RuntimeError("TPC-C typed-v2 source registry is not frozen/complete")
    statements = []
    for row in source["statements"]:
        bindings = row.get("representative_bindings")
        if not isinstance(bindings, list) or not bindings:
            raise RuntimeError("TPC-C statement lacks typed representative bindings")
        typed = bindings[0].get("typed_bindings")
        if not isinstance(typed, list):
            raise RuntimeError("TPC-C statement lacks typed parameter positions")
        positions = [int(binding["position"]) for binding in typed]
        if positions != list(range(1, len(typed) + 1)):
            raise RuntimeError("TPC-C parameter positions are not contiguous")
        transaction_type = str(row["transaction"])
        statements.append({
            "binding_id": (
                f"{int(row['order']):02d}:"
                f"{transaction_type}:{row['statement_id']}"
            ),
            "transaction_type": transaction_type,
            "statement_id": str(row["statement_id"]),
            "order": int(row["order"]),
            "parameter_count": len(positions),
            "parameter_positions": positions,
            "template_sql_sha256": str(
                row["canonical_template_sql_sha256"]
            ),
            "source_query_id": str(row["query_id"]),
        })
    if [row["order"] for row in statements] != list(range(1, 35)):
        raise RuntimeError("TPC-C statement order is not exactly 1..34")
    manifest = {
        "schema_version": "mysql_tpcc_jdbc_typed_binding_manifest_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "capture_only": True,
        "outer_execution_authorized": False,
        "binding_count": 34,
        "transaction_types": [
            "Delivery", "NewOrder", "OrderStatus", "Payment", "StockLevel"
        ],
        "transaction_aliases": {
            "SensitiveStockLevel": "StockLevel",
        },
        "source_registry": str(SOURCE),
        "source_registry_sha256": digest(SOURCE),
        "statements": statements,
    }
    payload = canonical(manifest)
    bundle_id = hashlib.sha256(payload).hexdigest()
    destination = OUTPUT_ROOT / bundle_id
    manifest_path = destination / "tpcc_offline_binding_manifest.json"
    if destination.exists():
        if digest(manifest_path) != bundle_id:
            raise RuntimeError("existing TPC-C typed manifest bundle differs")
        print(f"TPC_C_TYPED_MANIFEST_ALREADY_PUBLISHED {manifest_path}")
        return 0
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{bundle_id}.", dir=str(OUTPUT_ROOT))
    )
    try:
        (staging / manifest_path.name).write_bytes(payload)
        os.chmod(staging / manifest_path.name, 0o444)
        os.chmod(staging, 0o555)
        try:
            os.rename(staging, destination)
        except FileExistsError:
            if digest(manifest_path) != bundle_id:
                raise RuntimeError("racing TPC-C manifest publish differs")
        if digest(manifest_path) != bundle_id:
            raise RuntimeError("published TPC-C typed manifest digest differs")
    finally:
        if staging.exists():
            os.chmod(staging, 0o755)
            shutil.rmtree(staging)
    print(f"TPC_C_TYPED_MANIFEST_PUBLISHED {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
