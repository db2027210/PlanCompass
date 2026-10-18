#!/usr/bin/env python3
"""Audit repeatability of one XP candidate across independent EXPLAIN sessions."""

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


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--accepted-incumbent", type=Path, required=True)
    parser.add_argument("--query-snapshot", type=Path, required=True)
    parser.add_argument("--formal-root", type=Path, required=True)
    parser.add_argument("--candidate-selection", type=Path, required=True)
    parser.add_argument("--candidate-rank", type=int, default=1)
    parser.add_argument("--sessions", type=int, default=2)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.sessions < 2 or args.repeats < 2 or args.output.exists():
        raise RuntimeError("invalid audit dimensions or existing output")
    bundle = args.bundle_root.resolve()
    sys.path.insert(0, str(bundle))
    import xp_round10_explain_only_entrypoint as generation  # type: ignore
    from xp_round10_runtime_guard import ExplainOnlyAudit  # type: ignore

    state_before = hashlib.sha256(args.state.read_bytes()).hexdigest()
    inputs = generation._load_authoritative_inputs(
        args.config,
        args.selection,
        args.accepted_incumbent,
        args.query_snapshot,
    )
    candidates = load(args.candidate_selection).get("top5") or []
    candidate = next(
        item for item in candidates if int(item.get("rank", -1)) == args.candidate_rank
    )
    params = candidate.get("candidate_params") or candidate.get("optimized_params")
    if not isinstance(params, dict):
        raise RuntimeError("candidate parameters are missing")

    observations = []
    for session_index in range(args.sessions):
        audit = ExplainOnlyAudit()
        session = generation.LocalPgExplainSession(
            "127.0.0.1",
            int((inputs["config"].get("postgres") or {}).get("port") or 5432),
            str((inputs["config"].get("postgres") or {}).get("user") or "postgres"),
            str((inputs["config"].get("postgres") or {}).get("dbname") or "postgres"),
            audit,
        )
        try:
            baseline_check: Dict[str, Any] = {}
            factory = generation._estimator_factory(
                inputs=inputs,
                formal_root=args.formal_root,
                session=session,
                audit=audit,
                baseline_check=baseline_check,
            )
            estimate = factory(
                list(inputs["initial_params"]),
                f"same_point_session_{session_index + 1}",
            )
            for repeat_index in range(args.repeats):
                resource, _stats, metadata = estimate(inputs["queries"], params)
                fingerprint = generation._fresh_three_level_fingerprint(
                    metadata,
                    resource,
                    inputs["config"]["capability"],
                )
                observations.append(
                    {
                        "session": session_index + 1,
                        "repeat": repeat_index + 1,
                        "fingerprint": fingerprint,
                        "resource": resource,
                        "feature_ratio": metadata.get("feature_ratios"),
                    }
                )
        finally:
            session.close()

    decision_digests = [
        digest(
            {
                "fingerprint": item["fingerprint"],
                "resource": item["resource"],
                "feature_ratio": item["feature_ratio"],
            }
        )
        for item in observations
    ]
    state_after = hashlib.sha256(args.state.read_bytes()).hexdigest()
    passed = len(set(decision_digests)) == 1 and state_before == state_after
    result = {
        "contract": "xp_same_point_independent_explain_determinism_v1",
        "passed": passed,
        "sessions": args.sessions,
        "repeats_per_session": args.repeats,
        "decision_digests": decision_digests,
        "unique_decision_digest_count": len(set(decision_digests)),
        "fingerprint_ids": [
            item["fingerprint"].get("id") for item in observations
        ],
        "plan_levels": [
            item["fingerprint"].get("plan_level") for item in observations
        ],
        "formal_state_sha256_before": state_before,
        "formal_state_sha256_after": state_after,
        "formal_state_mutated": state_before != state_after,
        "workload_calls_added": 0,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
