#!/usr/bin/env python3
"""Run XP candidate generation twice without executing a workload.

The audit calls only ``generate_selection``.  It proves that the formal state
is byte-identical before/after, checks the fixed Top-5 schema, and compares a
canonical decision digest across two independent EXPLAIN-only generations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Mapping


DIAGNOSTIC_FIELDS = (
    "feature_ratio",
    "predicted_resource",
    "utilization",
    "dScore/dU",
    "T",
    "confidence",
    "g_j",
    "eta",
    "delta_param",
    "candidate_score",
)


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def decision_payload(selection: Mapping[str, Any]) -> list[Dict[str, Any]]:
    payload = []
    for item in selection.get("top5") or []:
        missing = [field for field in DIAGNOSTIC_FIELDS if field not in item]
        if missing:
            raise RuntimeError(f"candidate lacks diagnostic fields: {missing}")
        fingerprint = item.get("three_level_fingerprint") or {}
        identifier = str(fingerprint.get("id") or "")
        if not identifier:
            raise RuntimeError("candidate lacks three-level fingerprint")
        if (
            item.get("boundary_corner") is True
            or item.get("probe_kind") == "parameter_boundary_corner"
            or "corner" in str(item.get("candidate_origin") or "")
        ):
            raise RuntimeError(f"Top-5 contains a corner: {identifier}")
        payload.append(
            {
                "fingerprint": fingerprint,
                "candidate_params": item.get("candidate_params")
                or item.get("optimized_params"),
                "selection_role": item.get("selection_role"),
                "formal_range_eligible": item.get(
                    "formal_range_eligible", True
                ),
                **{field: item[field] for field in DIAGNOSTIC_FIELDS},
            }
        )
    if len(payload) != 5:
        raise RuntimeError(f"selection must contain exactly five points: {len(payload)}")
    identifiers = [str(item["fingerprint"]["id"]) for item in payload]
    if len(set(identifiers)) != 5:
        raise RuntimeError("selection fingerprints are not distinct")
    return payload


def digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
    parser.add_argument("--batch-id", default="cont026")
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    bundle = args.bundle_root.resolve()
    campaign = args.campaign_root.resolve()
    output_root = args.output_root.resolve()
    if output_root.exists():
        raise RuntimeError(f"output root already exists: {output_root}")
    sys.path.insert(0, str(bundle))
    from xp_unattended_adapters import FormalPaths, generate_selection  # type: ignore

    state_path = campaign / "state.json"
    state = load(state_path)
    state_before = sha256(state_path)
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
        replay_audit=(campaign / "batches/seed000/replay_summary.json").resolve(),
        rank1_evidence=(campaign / "batches/seed000/selection.json").resolve(),
        restore_script=args.restore_script.resolve(),
    )
    paths.validate()

    decisions = []
    runs = []
    for label in ("a", "b"):
        run_dir = output_root / label
        run_dir.mkdir(parents=True, exist_ok=False)
        selection = generate_selection(paths, args.batch_id, state, run_dir)
        decisions.append(decision_payload(selection))
        generation = load(run_dir / "explain_only/explain_only_generation.json")
        if generation.get("workload_executed") is not False:
            raise RuntimeError("generation audit does not prove workload_executed=false")
        if generation.get("benchmark_executed") is not False:
            raise RuntimeError("generation audit does not prove benchmark_executed=false")
        runs.append(
            {
                "label": label,
                "selection_path": str(run_dir / "diagnostic_bundle/selection.json"),
                "selection_sha256": sha256(
                    run_dir / "diagnostic_bundle/selection.json"
                ),
                "decision_digest": digest(decisions[-1]),
                "candidate_count": len(
                    generation.get("all_candidate_rankings") or []
                ),
                "predicted_safe_novel_fingerprint_count": generation.get(
                    "predicted_safe_novel_fingerprint_count"
                ),
            }
        )

    state_after = sha256(state_path)
    if state_before != state_after:
        raise RuntimeError("formal state changed during generation-only audit")
    if decisions[0] != decisions[1]:
        raise RuntimeError("independent generation decision payloads differ")

    audit = {
        "contract": "xp_explain_only_generation_determinism_audit_v1",
        "passed": True,
        "batch_id": args.batch_id,
        "bundle_root": str(bundle),
        "formal_state_sha256_before": state_before,
        "formal_state_sha256_after": state_after,
        "formal_state_mutated": False,
        "workload_calls_added": 0,
        "decision_payload_byte_identical": True,
        "decision_digest": digest(decisions[0]),
        "runs": runs,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    audit_path = output_root / "audit.json"
    audit_path.write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(audit, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
