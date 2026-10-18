#!/usr/bin/env python3
"""Restore attempt10's originally sealed v2 artifact from its revocation overlay."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
from typing import Any, Dict


PROJECT = Path("/home/anonymous/program/resource_adaptive")
ATTEMPT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt10_live_target_relative_20260723"
)
CONFIG = ATTEMPT / "config/live.json"
GLOBAL_ARTIFACT = (
    PROJECT
    / "config/formal/pg5_common_outer_bgallowed_20260722_v1/"
    "experience_table_T_tpcc_cpu_v2.json"
)
SEALED_ARTIFACT = (
    ATTEMPT
    / "artifacts/experience_table_T_tpcc_cpu_v2.formal_a04a80abcdc321a8.json"
)
AUDIT = ATTEMPT / "experience_table_artifact_repair_audit.json"
EXPECTED_FILE_SHA = "a04a80abcdc321a8323ab9a35a45c7f32f14e1e19121bedf60a0eef52e726cbe"
EXPECTED_CANONICAL_SHA = "74f83706a5f5cbd3df93411e468d66f686276dea9ff308840ea47c9ba4f732ad"
REVOKED_FILE_SHA = "e7a2001f717105d0a70c6060f025d25c8cd27cc77e455988a0deb01d0d5182d1"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_table_sha(artifact: Dict[str, Any]) -> str:
    encoded = json.dumps(
        artifact["table"],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return digest(encoded)


def main() -> int:
    config: Dict[str, Any] = json.loads(CONFIG.read_text(encoding="utf-8"))
    contract = config.get("experience_table_contract") or {}
    if contract.get("artifact_sha256") != EXPECTED_FILE_SHA:
        raise RuntimeError("attempt10 expected artifact SHA is not the sealed v2 SHA")
    if contract.get("table_sha256") != EXPECTED_CANONICAL_SHA:
        raise RuntimeError("attempt10 canonical table SHA is not the sealed v2 SHA")

    revoked_bytes = GLOBAL_ARTIFACT.read_bytes()
    if digest(revoked_bytes) != REVOKED_FILE_SHA:
        raise RuntimeError("global v2 artifact differs from the audited revocation overlay")
    revoked = json.loads(revoked_bytes.decode("utf-8"))
    admission = revoked.get("formal_admission") or {}
    if admission.get("ready") is not False:
        raise RuntimeError("global v2 artifact is not the expected revoked overlay")

    restored_text, replacement_count = re.subn(
        r'  "formal_admission": \{.*?\n  \},\n',
        "",
        revoked_bytes.decode("utf-8"),
        count=1,
        flags=re.S,
    )
    if replacement_count != 1:
        raise RuntimeError("could not isolate the single revocation metadata block")
    restored_bytes = restored_text.encode("utf-8")
    if digest(restored_bytes) != EXPECTED_FILE_SHA:
        raise RuntimeError("restored bytes do not match the originally sealed artifact SHA")
    restored = json.loads(restored_text)
    if canonical_table_sha(restored) != EXPECTED_CANONICAL_SHA:
        raise RuntimeError("restored artifact canonical table SHA mismatch")
    if restored.get("table") != config.get("sensitivity_table"):
        raise RuntimeError("restored artifact table differs from runtime sensitivity_table")

    SEALED_ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    artifact_tmp = SEALED_ARTIFACT.with_suffix(".json.tmp")
    artifact_tmp.write_bytes(restored_bytes)
    with artifact_tmp.open("rb") as handle:
        os.fsync(handle.fileno())
    artifact_tmp.replace(SEALED_ARTIFACT)

    before_config_sha = digest(CONFIG.read_bytes())
    backup = CONFIG.with_name(f"live.json.before_artifact_repair_{int(time.time())}.bak")
    shutil.copy2(CONFIG, backup)
    contract["artifact_path"] = str(SEALED_ARTIFACT)
    config["experience_table_contract"] = contract
    config_tmp = CONFIG.with_suffix(".json.tmp")
    config_tmp.write_text(
        json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with config_tmp.open("r+", encoding="utf-8") as handle:
        handle.flush()
        os.fsync(handle.fileno())
    config_tmp.replace(CONFIG)

    audit = {
        "schema_version": 1,
        "status": "repaired",
        "repair_epoch_seconds": time.time(),
        "finding": (
            "The config expected SHA was not stale. The global artifact was modified "
            "later by inserting one formal_admission revocation block."
        ),
        "global_revoked_artifact": {
            "path": str(GLOBAL_ARTIFACT),
            "sha256": REVOKED_FILE_SHA,
            "formal_admission": admission,
        },
        "attempt_sealed_artifact": {
            "path": str(SEALED_ARTIFACT),
            "sha256": digest(SEALED_ARTIFACT.read_bytes()),
            "canonical_table_sha256": canonical_table_sha(restored),
            "table_id": restored.get("table_id"),
            "version": restored.get("version"),
        },
        "config": {
            "path": str(CONFIG),
            "before_sha256": before_config_sha,
            "after_sha256": digest(CONFIG.read_bytes()),
            "backup": str(backup),
            "artifact_sha_gate_preserved": True,
            "expected_artifact_sha256": contract["artifact_sha256"],
            "expected_table_sha256": contract["table_sha256"],
        },
        "transformation": (
            "Removed exactly the later-added revocation metadata block; no table, "
            "direction, magnitude, table ID, version, or configured hash was changed."
        ),
    }
    audit_tmp = AUDIT.with_suffix(".json.tmp")
    audit_tmp.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with audit_tmp.open("r+", encoding="utf-8") as handle:
        handle.flush()
        os.fsync(handle.fileno())
    audit_tmp.replace(AUDIT)
    print(
        f"sealed_artifact={SEALED_ARTIFACT} audit={AUDIT} "
        f"config_sha256={audit['config']['after_sha256']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
