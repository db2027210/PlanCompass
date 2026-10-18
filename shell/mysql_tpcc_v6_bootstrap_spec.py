#!/usr/bin/env python3
"""Render a fresh qualification-only handoff for the 544f TPC-C successor.

This wrapper intentionally retains the vetted v5 command construction while
rebinding every executable path and local identity check to the v12 immutable
bundle.  It cannot contact a host, Docker, or MySQL.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mysql_tpcc_v5_bootstrap_spec as prior  # noqa: E402


BUNDLE_ID = "d3772996017a15fca17ce94e71a348c3861c0f3a1ffee4f0ab60a033f3809adc"
INVALIDATED_544F = "544f06d3121968dd23e558bfb7ab3f2163d8baabc7f6cc3d500ac3729c8bf599"

# v5 deliberately centralizes plan construction in its module globals.  Bind
# both levels before any validation/rendering so the inherited local-manifest
# verification and every argv path refer to v12 only.
prior.BUNDLE_ID = BUNDLE_ID
prior.base.BUNDLE_ID = BUNDLE_ID
prior.base.BUNDLE_REMOTE = prior.base.RUNTIME_ROOT + "/bundles/" + BUNDLE_ID
prior.base.SERVICE_REMOTE = (
    prior.base.BUNDLE_REMOTE + "/authority/mysql_tpcc_node04_adopted_service_v2.py"
)
prior.base.MATERIALIZER_REMOTE = (
    prior.base.BUNDLE_REMOTE + "/authority/mysql_tpcc_materialize_phase_command_v2.py"
)
prior.base.CONFIG_MATERIALIZER_REMOTE = (
    prior.base.BUNDLE_REMOTE + "/authority/mysql_tpcc_config_materialize_v2.py"
)
prior.MEASUREMENT_BRIDGE_HELPER_REMOTE = (
    prior.base.BUNDLE_REMOTE + "/authority/mysql_tpcc_measurement_bridge_v1.py"
)

canonical = prior.canonical
sha256 = prior.sha256
load_sidecar = prior.load_sidecar
SpecError = prior.SpecError


def validate(spec, *, now=None):
    value = prior.validate(spec, now=now)
    if value["bundle"]["id"] != BUNDLE_ID:
        raise SpecError("v12 qualification spec bundle differs")
    return value


def make_spec(manifest_path: Path, attempt_id: str, token: str, *, now=None):
    return validate(prior.make_spec(manifest_path, attempt_id, token, now=now), now=now)


def validate_plan_bindings(plan):
    value = prior.validate_plan_bindings(plan)
    payload = canonical(value).decode("ascii")
    if INVALIDATED_544F in payload:
        raise SpecError("544f executable path remains in qualification plan")
    prefix = prior.base.RUNTIME_ROOT + "/bundles/"
    observed = {fragment[:64] for fragment in payload.split(prefix)[1:]}
    if observed != {BUNDLE_ID}:
        raise SpecError("qualification plan does not bind exactly v12")
    return value


def command_plan(spec):
    return validate_plan_bindings(prior.command_plan(validate(spec)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--render", action="store_true")
    group.add_argument("--emit-plan", type=Path)
    parser.add_argument("--bundle-manifest", type=Path)
    parser.add_argument("--attempt-id")
    parser.add_argument("--main-agent-token-file", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.emit_plan:
        plan = command_plan(load_sidecar(args.emit_plan))
        if args.output:
            payload = canonical(plan)
            prior.base.atomic(args.output, payload)
            prior.base.atomic(
                Path(str(args.output) + ".sha256"),
                (sha256(args.output) + "  " + args.output.name + "\n").encode(),
            )
        print(json.dumps({"status": "PASS_EMITTED_V12_QUALIFICATION_ONLY_PLAN", "bundle_id": BUNDLE_ID, "output": str(args.output) if args.output else None}, sort_keys=True))
        return 0
    if not all((args.bundle_manifest, args.attempt_id, args.main_agent_token_file, args.output)):
        raise SpecError("render arguments absent")
    token_path = args.main_agent_token_file
    if token_path.is_symlink() or not token_path.is_file() or token_path.stat().st_mode & 0o077:
        raise SpecError("main-agent token file must be regular 0600")
    token = token_path.read_text(encoding="utf-8").strip()
    spec = make_spec(args.bundle_manifest, args.attempt_id, token)
    payload = canonical(spec)
    prior.base.atomic(args.output, payload)
    prior.base.atomic(
        Path(str(args.output) + ".sha256"),
        (sha256(args.output) + "  " + args.output.name + "\n").encode(),
    )
    print(json.dumps({"status": "PASS_RENDERED_V12_QUALIFICATION_ONLY_SPEC", "bundle_id": BUNDLE_ID, "accepted_round_authorized": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, SpecError) as exc:
        sys.stderr.write("NOT_READY: " + str(exc) + "\n")
        raise SystemExit(2)
