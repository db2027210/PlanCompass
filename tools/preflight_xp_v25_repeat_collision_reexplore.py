#!/usr/bin/env python3
"""Run the XP v25 collision recovery through selection generation only."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--formal-manifest", type=Path, required=True)
    parser.add_argument("--authority-counter", type=Path, required=True)
    parser.add_argument("--authority-history", type=Path, required=True)
    parser.add_argument("--canonical-state", type=Path, required=True)
    parser.add_argument("--canonical-manifest", type=Path, required=True)
    parser.add_argument("--acceptance-manifest", type=Path, required=True)
    parser.add_argument("--query-snapshot", type=Path, required=True)
    parser.add_argument("--restore-script", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    bundle = args.bundle_root.resolve()
    campaign = args.campaign_root.resolve()
    state_path = campaign / "state.json"
    seed_dir = campaign / "batches/seed000"
    output = args.output_root.resolve()
    if output.exists():
        raise RuntimeError(f"preflight output already exists: {output}")
    output.mkdir(parents=True, exist_ok=False)

    sys.path.insert(0, str(bundle))
    from xp_unattended_adapters import FormalPaths, generate_selection
    from xp_unattended_continuation import normalize_selection

    paths = FormalPaths(
        root=bundle,
        state=state_path,
        batch_root=campaign / "batches",
        formal_manifest=args.formal_manifest.resolve(),
        authority_counter=args.authority_counter.resolve(),
        authority_history=args.authority_history.resolve(),
        canonical_state=args.canonical_state.resolve(),
        canonical_manifest=args.canonical_manifest.resolve(),
        acceptance_manifest=args.acceptance_manifest.resolve(),
        query_snapshot=args.query_snapshot.resolve(),
        replay_audit=(seed_dir / "replay_summary.json").resolve(),
        rank1_evidence=(seed_dir / "selection.json").resolve(),
        restore_script=args.restore_script.resolve(),
    )
    paths.validate()
    state_before = sha256(state_path)
    counter_before = sha256(args.authority_counter.resolve())
    selection = generate_selection(
        paths,
        "preflight_cont051",
        load(state_path),
        output,
    )
    collision_audit_path = output / "exact_plan_repeat/automatic_reexploration.json"
    if not collision_audit_path.is_file():
        raise RuntimeError("collision recovery audit was not written")
    collision_audit = load(collision_audit_path)
    if not (
        collision_audit.get("contract")
        == "exact_repeat_global_fingerprint_collision_routes_to_novel_explain_discovery_v1"
        and int(collision_audit.get("collision_count", -1)) > 0
        and collision_audit.get("replay_previous_fingerprints") is False
        and collision_audit.get("state_mutation") is False
        and int(collision_audit.get("new_workload_calls", -1)) == 0
        and collision_audit.get("manual_candidate_injection") is False
    ):
        raise RuntimeError("collision recovery audit contract mismatch")
    status, normalized = normalize_selection(
        selection,
        replayed_fingerprints=load(state_path)["replayed_fingerprints"],
    )
    if status != "ready" or normalized is None:
        raise RuntimeError(f"reexploration selection is not replay-ready: {status}")
    identifiers = [
        str((item.get("three_level_fingerprint") or {}).get("id") or "")
        for item in normalized["top5"]
    ]
    replayed = set(load(state_path)["replayed_fingerprints"])
    if len(set(identifiers)) != 5 or "" in identifiers or replayed & set(identifiers):
        raise RuntimeError("reexploration did not produce five novel fingerprints")
    if sha256(state_path) != state_before:
        raise RuntimeError("preflight mutated XP formal continuation state")
    if sha256(args.authority_counter.resolve()) != counter_before:
        raise RuntimeError("preflight mutated XP accepted authority counter")

    result = {
        "status": "passed",
        "selection_mode": normalized["batch_mode"],
        "selection_roles": [item["selection_role"] for item in normalized["top5"]],
        "selection_fingerprint_ids": identifiers,
        "collision_fingerprint_ids": collision_audit["collision_fingerprint_ids"],
        "physical_workload_calls_added": 0,
        "state_mutated": False,
        "manual_candidate_injection": False,
        "state_sha256": state_before,
        "authority_counter_sha256": counter_before,
    }
    (output / "preflight_result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
