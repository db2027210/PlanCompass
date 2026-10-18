#!/usr/bin/env python3
"""Create a fresh bundle/seal-bound XP formal-R0-only config."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_sidecar_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise RuntimeError(f"symlink rejected: {path}")
    path = path.resolve(strict=True)
    sidecar = path.with_name(path.name + ".sha256")
    if (
        not sidecar.is_file()
        or sidecar.is_symlink()
        or sidecar.read_text(encoding="ascii")
        != f"{sha256(path)}  {path.name}\n"
    ):
        raise RuntimeError(f"checksum sidecar absent or differs: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("JSON object required")
    return value


def write_fresh(path: Path, value: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"fresh config already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_bytes(payload)
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha256(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--capture-at-seal", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    bundle = args.bundle.resolve(strict=True)
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    if (
        bundle.name != manifest.get("bundle_id")
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("XP bundle identity/scope differs")
    seal_path = args.capture_at_seal.resolve(strict=True)
    seal = load_sidecar_json(seal_path)
    trace = ((seal.get("sources") or {}).get("trace") or {})
    if (
        seal.get("bundle_id") != manifest["bundle_id"]
        or seal.get("formal_r0_launch_authorized") is not True
        or seal.get("outer_execution_authorized") is not False
        or not isinstance(trace.get("sha256"), str)
        or len(trace["sha256"]) != 64
    ):
        raise RuntimeError("XP capture AT seal is not R0-only authority")
    template = json.loads(
        (bundle / "config/hybench_xp_io.algorithm_c.optimizer.template.json")
        .read_text(encoding="utf-8")
    )
    output_root = args.output.resolve().parent / f"xp_formal_r0_{args.run_id}"
    template["profile"] = str(
        (bundle / "profile/hybench_xp_io_formal_v8.json").resolve()
    )
    template["output"]["directory"] = str(output_root)
    template["runtime_bundle_id"] = manifest["bundle_id"]
    template["formal_scope"] = "mysql_only"
    template["cross_engine_comparison_prohibited"] = True
    template["authorization_stage"] = "formal_r0_only"
    template["formal_r0_launch_authorized"] = True
    template["execution_authorized"] = False
    template["outer_execution_authorized"] = False
    template["accepted_round_authorized"] = False
    template["measurement_seconds"] = 600
    template["minimum_measurement_seconds"] = 600
    template["mysql"]["host"] = "127.0.0.1"
    template["mysql"]["port"] = 65304
    template["workload"].update({
        "capture_only": False,
        "formal_r0_only": True,
        "capture_io_envelope_is_not_r0": True,
        "formal_graceful_quiescence_seconds": 120,
        "host_control_cpuset": "184-199,440-455",
    })
    template["formal_r0_identity"] = {
        "schema_version": "mysql_xp_formal_r0_identity_v1",
        "bundle_id": manifest["bundle_id"],
        "capture_at_seal_sha256": sha256(seal_path),
        "capture_trace_sha256": trace["sha256"],
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "resource_envelope": manifest["resource_envelope"],
        "mysql_port": 65304,
        "workload_seed": 4508333,
    }
    template["formal_outer_protocol"].update({
        "formal_real_r0_checkpoint_path": str(
            output_root / "formal_production_real_r0_hybench_xp.json"
        ),
        "xp_formal_r0_authority_path": str(
            output_root / "mysql_xp_formal_r0_authority.json"
        ),
        "outer_execution_authorized": False,
    })
    write_fresh(args.output.resolve(), template)
    print(json.dumps({
        "config": str(args.output.resolve()),
        "output_directory": str(output_root),
        "bundle_id": manifest["bundle_id"],
        "capture_at_seal_sha256": sha256(seal_path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
