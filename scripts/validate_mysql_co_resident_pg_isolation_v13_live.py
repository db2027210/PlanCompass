#!/usr/bin/env python3
"""Publish the read-only live v65 co-resident isolation validation."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import mysql_co_resident_pg_isolation_v13 as isolation
from scripts import mysql_cross_project_wave_coordination_v2 as gate


OUTPUT = isolation.CONTRACT_PATH.parent / "live_validation.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    isolation.install()
    result = gate.rolling_quiet_gate(
        run_id="v65_read_only_live_validation",
        required_seconds=15.0,
        maximum_wait_seconds=60.0,
        target_cpus={0},
    )
    final = result["samples"][-1]["co_resident_pg_isolation"]
    if (
        result.get("status") != "PASS"
        or result.get("errors") != []
        or final.get("status") != "PASS_ISOLATED_CO_RESIDENT_PG"
        or final.get("passed") is not True
        or (final.get("inactive_sealed_base_container_policy") or {}).get(
            "ignored_count"
        ) != 2
        or (final.get("container_readback") or {}).get("passed") is not True
    ):
        raise RuntimeError("v65 live isolation validation failed closed")
    document = {
        "schema_version": "mysql_co_resident_pg_isolation_v13_live_validation_v1",
        "status": "PASS",
        "validated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "external_mutation": False,
        "coordination_contract_path": str(isolation.CONTRACT_PATH),
        "coordination_contract_sha256": sha(isolation.CONTRACT_PATH),
        "validator_source_path": str(Path(__file__).resolve()),
        "validator_source_sha256": sha(Path(__file__).resolve()),
        "isolation_source_path": str(Path(isolation.__file__).resolve()),
        "isolation_source_sha256": sha(Path(isolation.__file__).resolve()),
        "rolling_gate": result,
    }
    temporary = OUTPUT.with_name(f".{OUTPUT.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, OUTPUT)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii",
    )
    print(json.dumps({"status": "PASS", "path": str(OUTPUT), "sha256": sha(OUTPUT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
