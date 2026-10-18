#!/usr/bin/env python3
"""Freeze a fresh-token XP capture-only optimizer config."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


# This helper is copied into ``<bundle>/python/shell``.  Resolving parents from
# that location would incorrectly place the campaign under the immutable
# bundle's read-only ``python/history`` tree.  The host binding is already
# frozen to compute00, so bind the writable project root explicitly.
ROOT = Path("/home/anonymous/program/resource_adaptive_mysql")
_BUNDLE_ROOT = Path(__file__).resolve().parents[2]
_BUNDLED_BASE = _BUNDLE_ROOT / "capture/xp_capture_v8_config_template.json"
_PROJECT_BASE = (
    ROOT
    / "history/mysql_gradient_debug/readiness_mysql_early_stop_v1/"
    "xp_capture_v8_config_20260729T105254P08.json"
)
BASE = _BUNDLED_BASE if _BUNDLED_BASE.is_file() else _PROJECT_BASE


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--runtime-bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    bundle = args.runtime_bundle.resolve(strict=True)
    manifest = json.loads((bundle / "manifest.json").read_text())
    if (
        bundle.name != str(manifest.get("bundle_id") or "")
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("XP runtime bundle identity differs")
    value = json.loads(BASE.read_text())
    value["output"]["directory"] = str(
        ROOT / "history/mysql_gradient_debug" / (
            "xp_capture_v8_campaign_" + args.run_id.removeprefix(
                "XP_CAPTURE_V8_PREFLIGHT_"
            )
        )
    )
    value["profile"] = str(
        bundle / "profile/hybench_xp_io_formal_v8.json"
    )
    value["formal_scope"] = "mysql_only"
    value["cross_engine_comparison_prohibited"] = True
    value["runtime_bundle_id"] = manifest["bundle_id"]
    value["authorization_stage"] = "capture_only"
    value["formal_r0_launch_authorized"] = False
    value["outer_execution_authorized"] = False
    value["accepted_round_authorized"] = False
    value["mysql"]["host"] = "127.0.0.1"
    value["mysql"]["port"] = 65204
    value["workload"]["capture_only"] = True
    value["workload"]["capture_io_envelope_is_not_r0"] = True
    data = (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    output = args.output.resolve()
    if output.exists():
        if output.read_bytes() != data:
            raise RuntimeError("existing XP capture config differs")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.{os.getpid()}.partial")
        temporary.write_bytes(data)
        os.replace(temporary, output)
    sidecar = output.with_name(output.name + ".sha256")
    expected = f"{hashlib.sha256(data).hexdigest()}  {output.name}\n"
    if sidecar.exists() and sidecar.read_text() != expected:
        raise RuntimeError("existing XP capture config sidecar differs")
    if not sidecar.exists():
        sidecar.write_text(expected, encoding="ascii")
    print(json.dumps({
        "config": str(output),
        "campaign_directory": value["output"]["directory"],
        "runtime_bundle": str(bundle),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
