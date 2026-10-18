#!/usr/bin/env python3
"""Create a local, short-lived, qualification-only v12 TPC-C handoff.

The program is offline-only: it renders reviewed argv material but never
contacts the target host, Docker, or either database.  The handoff token is
an attempt identifier (not a database credential); no MySQL user or password
is read, generated, or recorded here.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import tempfile

import mysql_tpcc_v6_bootstrap_spec as spec


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "artifacts/mysql_tpcc_typed_distributed_successor_v12" / spec.BUNDLE_ID
OUT = ROOT / "artifacts/mysql_tpcc_v12_qualification_materials_v1"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path: Path, payload: bytes, mode: int) -> None:
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, mode)


def attempt_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return "tpcc-v4-v12-544f-successor-" + stamp + "-" + secrets.token_hex(12)


def verify_bundle() -> None:
    manifest = json.loads((BUNDLE / "bundle_manifest.json").read_text(encoding="utf-8"))
    if (
        BUNDLE.name != spec.BUNDLE_ID
        or manifest.get("bundle_id") != spec.BUNDLE_ID
        or manifest.get("schema_version") != "mysql_tpcc_typed_distributed_successor_bundle_v12"
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise RuntimeError("v12 immutable qualification input differs")


def build(output_root: Path, selected_attempt: str | None) -> Path:
    verify_bundle()
    identifier = selected_attempt or attempt_id()
    token = identifier
    # Reuse the v6 parser rather than merely trusting the generated format.
    if spec.prior.base.TOKEN.fullmatch(identifier) is None:
        raise RuntimeError("attempt identifier is not safe for a remote argv")
    output_root.mkdir(parents=True, exist_ok=True)
    final = output_root / identifier
    if final.exists():
        raise RuntimeError("qualification material directory already exists: " + str(final))
    stage = Path(tempfile.mkdtemp(prefix=".v12-qualification.", dir=str(output_root)))
    try:
        token_file = stage / "main-agent-handoff-token"
        write_new(token_file, (token + "\n").encode("ascii"), 0o600)
        rendered = spec.make_spec(BUNDLE / "bundle_manifest.json", identifier, token)
        plan = spec.command_plan(rendered)
        write_new(stage / "qualification_spec.json", spec.canonical(rendered), 0o444)
        write_new(stage / "qualification_spec.json.sha256", (digest(stage / "qualification_spec.json") + "  qualification_spec.json\n").encode("ascii"), 0o444)
        write_new(stage / "qualification_plan.json", spec.canonical(plan), 0o444)
        write_new(stage / "qualification_plan.json.sha256", (digest(stage / "qualification_plan.json") + "  qualification_plan.json\n").encode("ascii"), 0o444)
        material = {
            "schema_version": "mysql_tpcc_v12_qualification_materials_v1",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "offline_render_only": True,
            "remote_actions": 0,
            "bundle_id": spec.BUNDLE_ID,
            "superseded_bundle_id": spec.INVALIDATED_544F,
            "attempt_id": identifier,
            "handoff_token_sha256": hashlib.sha256(token.encode("ascii")).hexdigest(),
            "database_credentials_present": False,
            "qualification_only": True,
            "capture_authorized": False,
            "formal_r0_authorized": False,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
            "files": {
                "qualification_spec.json": digest(stage / "qualification_spec.json"),
                "qualification_plan.json": digest(stage / "qualification_plan.json"),
            },
        }
        write_new(stage / "materials_manifest.json", spec.canonical(material), 0o444)
        os.chmod(stage, 0o500)
        os.replace(stage, final)
        return final
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=OUT)
    parser.add_argument("--attempt-id")
    args = parser.parse_args()
    result = build(args.output_root.resolve(), args.attempt_id)
    print(json.dumps({"status": "PASS_RENDERED_QUALIFICATION_ONLY", "bundle_id": spec.BUNDLE_ID, "directory": str(result), "formal_launch_authorized": False, "accepted_round_authorized": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
