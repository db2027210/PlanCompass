#!/usr/bin/env python3
"""Create a content-addressed *local seal plan* from already real TPC-C evidence.

This never launches a workload.  It invokes the immutable registry sealer's
evidence validator and records only the accepted local input hashes.  A plan
is not a registry and never authorizes R0, replay, or accepted rounds.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import secrets
import shutil
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEALER_NAME = "mysql_tpcc_typed_trace_registry_sealer_v2.py"


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sealer_from(bundle: Path):
    bundle = bundle.resolve(strict=True)
    manifest_path = bundle / "bundle_manifest.json"
    manifest = load(manifest_path)
    if manifest.get("schema_version") not in {"mysql_tpcc_typed_registry_sealer_successor_bundle_v1", "mysql_tpcc_typed_registry_sealer_successor_bundle_v2"} or manifest.get("formal_scope") != "mysql_only" or manifest.get("formal_launch_authorized") is not False:
        raise RuntimeError("registry sealer bundle authority differs")
    source = bundle / "registry" / SEALER_NAME
    if manifest.get("files", {}).get("registry/" + SEALER_NAME) != sha(source):
        raise RuntimeError("registry sealer source hash differs")
    spec = importlib.util.spec_from_file_location("immutable_tpcc_registry_sealer", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("immutable registry sealer is not importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return bundle, manifest, source, module


def parse() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-sealer-bundle", type=Path, required=True)
    parser.add_argument("--plan-root", type=Path, required=True)
    for option in ("--v13-bundle", "--bundle-manifest", "--trace", "--trace-lock", "--collector-output", "--production-command", "--dynamic-gate", "--dynamic-gate-raw", "--dynamic-gate-a-solo", "--dynamic-gate-a-paired", "--dynamic-gate-b-solo", "--dynamic-gate-b-paired", "--db-live-readback", "--client-live-readback", "--provenance", "--owner-trace-authority", "--qualification-ledger", "--qualification-command", "--qualification-receipt", "--typed-command", "--typed-command-sidecar", "--capture-result", "--capture-completion", "--performance-schema-authority", "--sensitive-events", "--coverage", "--resource-readback", "--effective-resource-readback", "--runtime-process-gate", "--workload-process-evidence"):
        parser.add_argument(option, type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse()
    bundle, manifest, source, module = sealer_from(args.registry_sealer_bundle)
    checked, paths = module.evidence(args)
    input_hashes = {name: sha(path) for name, path in sorted(paths.items())}
    plan = {
        "schema_version": "mysql_tpcc_typed_trace_registry_seal_plan_v1",
        "status": "PASS_REAL_EVIDENCE_PREVALIDATED_NOT_PUBLISHED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "registry_sealer_bundle_id": manifest["bundle_id"],
        "registry_sealer_manifest_sha256": sha(bundle / "bundle_manifest.json"),
        "registry_sealer_source_sha256": sha(source),
        "attempt_id": checked["attempt"],
        "input_hashes": input_hashes,
        "registry_publish_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }
    plan_id = hashlib.sha256(canonical(plan)).hexdigest()
    plan["plan_id"] = plan_id
    root = args.plan_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / plan_id
    if target.exists() or target.is_symlink():
        if load(target / "seal_plan.json") != plan:
            raise RuntimeError("existing seal plan content differs")
        print(json.dumps({"status": "PASS_IDEMPOTENT_EXISTING_REAL_EVIDENCE_SEAL_PLAN", "plan": str(target), "plan_id": plan_id}, sort_keys=True))
        return 0
    stage = root / (".stage-" + plan_id + "-" + secrets.token_hex(8))
    stage.mkdir(mode=0o700)
    try:
        raw = canonical(plan)
        (stage / "seal_plan.json").write_bytes(raw)
        (stage / "seal_plan.json.sha256").write_text(hashlib.sha256(raw).hexdigest() + "  seal_plan.json\n", encoding="ascii")
        os.rename(stage, target)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    print(json.dumps({"status": "PASS_REAL_EVIDENCE_PREVALIDATED_NOT_PUBLISHED", "plan": str(target), "plan_id": plan_id, "formal_launch_authorized": False, "accepted_round_authorized": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
