#!/usr/bin/env python3
"""Fail-closed static audit of JOB v57 diagnostic-to-formal transitions.

This checker is deliberately local-only.  It reads the sealed controller and
runner sources, verifies their SHA-256 pins, and proves the policy properties
that must hold before a completed repeat clean5 may enter formal replay.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Dict, Mapping


TOOLS_DIR = Path(__file__).resolve().parent
EXPECTED_SHA256 = {
    "job_fresh10_target10_autonomous_v49.py": "3688f00b0a32c490f5dd3781b26642f48361c3ba541256f9855730284016035c",
    "job_fresh10_target10_autonomous_v50.py": "2cf4ecd0e1816c6e46013ef2c093b7b3caf7ca5d945cd107ab0bd97696da9804",
    "job_fresh10_target10_autonomous_v55.py": "cc9b19977c140a2d42d0c33abf11250e607afc65fbff0f89eb5d3d6c8a357dd0",
    "job_fresh10_target10_autonomous_v57.py": "ebc884eefe3cc5663ec4ae5738d46ff564afe78e4b56c68ef4b440996198285b",
    "bootstrap_job_v57_fresh0.py": "ed9b5964a49b4e70f8f615f6928a67e5f747e83f989772e72b159442f8e45002",
    "run_job_round8_strict_serial_robust_v10_resource_median.py": "e4738c9eb95e123a38e31410bb067bf817bb8b54e234acbb2f5bc14ed89f2b03",
    "run_job_round8_strict_serial_robust_v9_state_guard.py": "e717449a109669d6ed274e29d5aaf9f4285af8b37c586aeb0ad768970ba02693",
    "audit_job_v57_first_batch.py": "13f43f26d8feed98d85f9a588899c88f42d2660a3e822b8f1b0428bb2759201a",
}


class AuditFailure(RuntimeError):
    """A sealed transition-policy requirement is absent or changed."""


def _require(source: str, needle: str, message: str) -> None:
    if needle not in source:
        raise AuditFailure(message)


def _require_order(source: str, needles: tuple[str, ...], message: str) -> None:
    position = -1
    for needle in needles:
        next_position = source.find(needle, position + 1)
        if next_position < 0:
            raise AuditFailure(message)
        position = next_position


def audit_source_texts(sources: Mapping[str, str]) -> Dict[str, object]:
    """Audit source text; exposed so tests can prove tamper detection."""
    v49 = sources["job_fresh10_target10_autonomous_v49.py"]
    v50 = sources["job_fresh10_target10_autonomous_v50.py"]
    v55 = sources["job_fresh10_target10_autonomous_v55.py"]
    v57 = sources["job_fresh10_target10_autonomous_v57.py"]
    bootstrap = sources["bootstrap_job_v57_fresh0.py"]
    robust_v10 = sources[
        "run_job_round8_strict_serial_robust_v10_resource_median.py"
    ]
    robust_v9 = sources["run_job_round8_strict_serial_robust_v9_state_guard.py"]

    combined_controllers = "\n".join((v49, v50, v55, v57))
    if "input(" in combined_controllers:
        raise AuditFailure("interactive input path exists in the autonomous controller")
    if '"manual_candidate_injection": True' in combined_controllers:
        raise AuditFailure("manual candidate injection is enabled")

    # A clean repeat is gate-checked, then packaged and executed in the same
    # controller path.  The exact selection is double-recomputed and SHA-bound.
    _require_order(
        v50,
        (
            "obtain_passed_repeat_v50(",
            'phase="formal"',
            "prepare_formal_v50(",
            "v49.execute_formal(formal, state_sha_before)",
        ),
        "repeat gate does not automatically lead to sealed formal execution",
    )
    _require(v50, "output.read_bytes() != second.read_bytes()", "gate is not double-recomputed")
    _require(v50, '"selection_sha256":', "gate lacks selection SHA binding")
    _require(v50, '"summary_sha256":', "gate lacks summary SHA binding")
    _require(v50, "all(value is True for value in checks.values())", "gate does not require every check")
    _require(v49, "cannot seal a failed entry gate", "failed gate can be sealed")
    _require(v49, "formal candidates differ from novel discovery points", "formal parameters are not exact-bound")
    _require(v49, 'roles=("trusted", "trusted", "trusted", "probe", "probe")', "formal role contract is absent")

    # v57 starts from its own accepted0 state.  Historical evidence may train
    # ranking/calibration, but cannot seed accepted/incumbent/range state.
    _require(v57, 'autonomous_target10_v57_fresh0', "v57 does not use a distinct fresh0 root")
    _require(v57, "implementation.v49.STATE = implementation.FRESH_STATE", "v57 formal state is not redirected")
    _require(bootstrap, '"accepted_count": 0', "bootstrap does not reset accepted to zero")
    _require(bootstrap, '"gradient_observation_count": 0', "bootstrap inherits gradient state")
    _require(bootstrap, '"historical_incumbent_or_range_inherited": False', "bootstrap inherits incumbent/range")
    _require(bootstrap, '"retry_allowed": False', "v55 failed formal fingerprint is retryable")
    _require(v50, '"historical_training_only"] = True', "historical evidence is not training-only")
    _require(v50, '"historical_incumbent_or_range_inherited"] = False', "historical incumbent/range can leak")
    _require(v50, '"accepted_count": 0', "rebased authority is not accepted0")

    # Diagnostic, probe, rejected and technical rows are state-neutral.  Only a
    # trusted, gate-eligible, improving formal observation may mutate state.
    _require(
        robust_v10,
        'if diagnostic_only or candidate.get("selection_role") == "probe":',
        "probe/diagnostic does not bypass formal median acceptance",
    )
    _require(robust_v10, 'candidate.get("selection_role") == "trusted"', "formal acceptance is not trusted-only")
    _require(v50, 'summary.get("initial_state") != summary.get("final_state")', "diagnostic state-neutrality is not checked")
    _require(v50, 'summary.get("formal_update_flags") != NEUTRAL_FLAGS', "diagnostic update flags are not checked")
    _require(robust_v9, "rejected rank {rank} mutated gradient/incumbent/range state", "rejected state mutation is not rejected")
    _require(robust_v9, "accepted rank {rank} is not a trusted incumbent/range update", "probe acceptance is not rejected")
    _require(robust_v9, "technical rank {rank} has a formal update flag", "technical state update is not rejected")

    # A technical diagnostic advances to new fingerprints.  A nontechnical
    # failed gate may only do another exact-point validation, bounded to five.
    # A partial formal replay is sealed and can never be invoked again.
    _require(v50, "MAX_DIAGNOSTIC_BATCHES = 5", "diagnostic retry bound is not five")
    _require(v50, "raise RepeatTechnicalFailure(attempt, target)", "technical repeat is not sealed")
    _require(v50, 'phase="fresh_discovery_after_repeat_technical"', "technical repeat does not force fresh discovery")
    _require(v50, "fresh discovery repeated a prior diagnostic fingerprint", "fresh diagnostic no-retry guard is absent")
    _require(v50, "source = target", "bounded exact-point gate validation is absent")
    _require(v49, "partial formal replay cannot be repeated", "partial formal replay can be retried")
    _require(v49, "paths.update(B.rglob(pattern))", "global prior-fingerprint exclusion is absent")

    return {
        "status": "passed",
        "decision": "fail_closed_policy_verified",
        "automatic_transition": True,
        "manual_intervention_path": False,
        "formal_accepted_origin": 0,
        "old_accepted_inherited": False,
        "probe_or_rejected_state_update": False,
        "technical_fingerprint_retry": False,
        "partial_formal_retry": False,
        "nontechnical_gate_repeat": "exact_same_points_only_bounded_5",
        "workload_calls": 0,
        "remote_actions": 0,
    }


def main() -> int:
    sources: Dict[str, str] = {}
    observed: Dict[str, str] = {}
    for name, expected in EXPECTED_SHA256.items():
        path = TOOLS_DIR / name
        if not path.is_file():
            raise AuditFailure(f"required sealed source is absent: {path}")
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected:
            raise AuditFailure(f"sealed source SHA mismatch: {name}: {digest}")
        observed[name] = digest
        sources[name] = data.decode("utf-8")
    result = audit_source_texts(sources)
    result["source_sha256"] = observed
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AuditFailure, KeyError, UnicodeDecodeError) as error:
        print(
            json.dumps(
                {
                    "status": "fail_closed",
                    "error": str(error),
                    "workload_calls": 0,
                    "remote_actions": 0,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(2)
