"""Execution-role contract for Algorithm C pilot and formal campaigns."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


PILOT_ROLE = "algorithm_c_budgeted_pilot_v1"
FORMAL_ROLE = "algorithm_c_post_pilot_formal_v1"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class AlgorithmCExecutionRole:
    name: str
    phase_prefix: str
    search_batch_kind: str
    finalist_batch_prefix: str
    post_baseline_batch_prefix: str
    result_filename: str
    finished_event: str
    interrupted_resume_allowed: bool
    historical_comparison_provisional: bool

    def finalist_batch_kind(self, repetition: int) -> str:
        if repetition < 1:
            raise ValueError("repetition must be positive")
        return f"{self.finalist_batch_prefix}{repetition}"

    def post_baseline_batch_kind(self, repetition: int) -> str:
        if repetition < 1:
            raise ValueError("repetition must be positive")
        return f"{self.post_baseline_batch_prefix}{repetition}"


PILOT = AlgorithmCExecutionRole(
    name=PILOT_ROLE,
    phase_prefix="budgeted_trial",
    search_batch_kind="trial_search",
    finalist_batch_prefix="trial_finalist_rep",
    post_baseline_batch_prefix="trial_post_baseline_rep",
    result_filename="budgeted_trial_result.json",
    finished_event="algorithm_c_budgeted_trial_finished",
    interrupted_resume_allowed=True,
    historical_comparison_provisional=True,
)

FORMAL = AlgorithmCExecutionRole(
    name=FORMAL_ROLE,
    phase_prefix="budgeted_formal",
    search_batch_kind="formal_search",
    finalist_batch_prefix="formal_finalist_rep",
    post_baseline_batch_prefix="formal_post_baseline_rep",
    result_filename="budgeted_formal_result.json",
    finished_event="algorithm_c_budgeted_formal_finished",
    interrupted_resume_allowed=False,
    historical_comparison_provisional=False,
)


def resolve_execution_role(policy: Mapping[str, Any]) -> AlgorithmCExecutionRole:
    """Resolve role without accepting scene names or comparison candidates."""
    role = policy.get("execution_role")
    if role in (None, "", PILOT_ROLE):
        return PILOT
    if role != FORMAL_ROLE:
        raise ValueError(f"unsupported Algorithm C execution role: {role}")
    revision = str(policy.get("algorithm_revision") or "").strip()
    design_sha256 = str(policy.get("algorithm_design_sha256") or "").strip()
    if not revision:
        raise ValueError("formal Algorithm C requires an algorithm revision")
    if not SHA256_PATTERN.fullmatch(design_sha256):
        raise ValueError("formal Algorithm C requires a sealed algorithm-design SHA-256")
    return FORMAL
