#!/usr/bin/env python3
"""Seal terminal Algorithm C with the bounded three-confirmation recovery."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re

from scripts import build_mysql_algorithm_c_outer_checkpoint_v7 as v7


def build_checkpoint(run_root: Path):
    old_limit = v7.confirmation.MAX_POST_TARGET_ACCEPTED_CONFIRMATIONS
    v7.confirmation.MAX_POST_TARGET_ACCEPTED_CONFIRMATIONS = 3
    try:
        document = v7.build_checkpoint(run_root)
    finally:
        v7.confirmation.MAX_POST_TARGET_ACCEPTED_CONFIRMATIONS = old_limit
    document["terminal_convergence_audit"].update({
        "confirmation_budget_recovery_maximum": 3,
        "accepted_target_unchanged": 10,
        "stability_requirement_unchanged": 2,
    })
    builder = Path(__file__).resolve()
    document["builder_source_path"] = str(builder)
    document["builder_source_sha256"] = v7.base.alg.sha(builder)
    payload = copy.deepcopy(document)
    payload.pop("checkpoint_sha256", None)
    document["checkpoint_sha256"] = v7.base._canonical_sha256(payload)
    return document


def verify_checkpoint(path: Path):
    return v7.verify_checkpoint(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.output_dir.name) is None:
        parser.error("unsafe output directory name")
    if args.output_dir.exists():
        raise RuntimeError(f"fresh checkpoint output exists: {args.output_dir}")
    document = build_checkpoint(args.run_root.resolve())
    args.output_dir.mkdir(parents=True, mode=0o700)
    path = args.output_dir / "prepared_binding.json"
    v7.base.alg.atomic(path, document)
    verify_checkpoint(path)
    print(json.dumps({
        "status": "PREPARED",
        "checkpoint": str(path),
        "completed_outer_attempts": document["completed_outer_attempts"],
        "post_target_stability_confirmation_counts": document[
            "reconstructed_state"
        ]["post_target_stability_confirmation_counts"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
