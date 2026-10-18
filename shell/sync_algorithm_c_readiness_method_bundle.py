#!/usr/bin/env python3
"""Bind readiness R07 to the complete current Algorithm C method manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FREEZE = ROOT / "config/formal/frozen_mysql_deployment_schedule_20260723_v4_mysql_only"
MANIFEST = FREEZE / "experience_table_sgd_method_manifest.json"
FORMAL_CONFIG = FREEZE / "experience_table_sgd_formal_config.json"
READINESS = FREEZE / "readiness_manifest.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    method = json.loads(MANIFEST.read_text(encoding="utf-8"))
    readiness = json.loads(READINESS.read_text(encoding="utf-8"))
    bundle = readiness["global_requirements"]["method_bundle"]
    bundle["method_manifest"] = {
        "path": str(MANIFEST.relative_to(ROOT)),
        "sha256": sha256(MANIFEST),
    }
    bundle["formal_config"] = {
        "path": str(FORMAL_CONFIG.relative_to(ROOT)),
        "sha256": sha256(FORMAL_CONFIG),
    }
    bundle["registered_sources"] = [
        {"path": entry["path"], "sha256": entry["sha256"]}
        for entry in method["registered_sources"]
    ]
    bundle["ready"] = True
    readiness["status"] = "NOT_READY"
    readiness["launch_authorized"] = False
    temporary = READINESS.with_suffix(READINESS.suffix + ".tmp")
    temporary.write_text(
        json.dumps(readiness, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(READINESS)
    print(
        f"registered_sources={len(bundle['registered_sources'])} "
        f"readiness_sha256={sha256(READINESS)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
