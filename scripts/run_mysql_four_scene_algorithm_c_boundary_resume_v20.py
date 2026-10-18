#!/usr/bin/env python3
"""Resume Algorithm C with bounded post-target stability confirmation.

The formal accepted-round target remains exactly 10.  A scene that reaches
10/10 with only one stable accepted interval still needs one real Top-5
confirmation.  Such a confirmation updates the incumbent, interval, and
stability streak exactly like an accepted replay, but it does not create an
11th accepted-round credit.
"""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v19 as v19


alg = v19.alg
ORIGINAL_STATE_DOCUMENT = v19.state_document
ORIGINAL_ATOMIC = alg.atomic
TARGET_ACCEPTED_ROUNDS = 10
MAX_POST_TARGET_ACCEPTED_CONFIRMATIONS = 2
ACTIVE_ACCEPTED_ROUNDS: "ConfirmationAcceptedRounds | None" = None


class ConfirmationCount(int):
    """An integer that keeps the 10/10 target open for bounded confirmation."""

    def __new__(cls, value: int, confirmation_open: bool):
        instance = int.__new__(cls, value)
        instance.confirmation_open = confirmation_open
        return instance

    def __ge__(self, other: object) -> bool:
        if (
            self.confirmation_open
            and int(self) == TARGET_ACCEPTED_ROUNDS
            and int(other) == TARGET_ACCEPTED_ROUNDS
        ):
            return False
        return int.__ge__(self, other)


class ConfirmationAcceptedRounds(dict[str, int]):
    """Cap formal credit at 10 while recording post-target acceptances."""

    def __init__(self, values: Mapping[str, Any]):
        normalized = {str(key): int(value) for key, value in values.items()}
        if any(value < 0 or value > TARGET_ACCEPTED_ROUNDS for value in normalized.values()):
            raise RuntimeError("accepted-round checkpoint exceeds the formal 10-round target")
        super().__init__(normalized)
        self.confirmation_acceptances = {key: 0 for key in normalized}
        self.last_update: dict[str, dict[str, Any]] = {}

    def __getitem__(self, key: str) -> int:
        value = int(super().__getitem__(key))
        confirmation_open = (
            value == TARGET_ACCEPTED_ROUNDS
            and self.confirmation_acceptances.get(key, 0)
            < MAX_POST_TARGET_ACCEPTED_CONFIRMATIONS
        )
        return ConfirmationCount(value, confirmation_open)

    def __setitem__(self, key: str, value: int) -> None:
        previous = int(super().__getitem__(key)) if key in self else 0
        requested = int(value)
        if previous == TARGET_ACCEPTED_ROUNDS and requested > previous:
            ordinal = self.confirmation_acceptances.get(key, 0) + 1
            self.confirmation_acceptances[key] = ordinal
            super().__setitem__(key, TARGET_ACCEPTED_ROUNDS)
            self.last_update[key] = {
                "kind": "post_target_stability_confirmation",
                "accepted_state_credit_increment": 0,
                "accepted_rounds_before": previous,
                "accepted_rounds_after": TARGET_ACCEPTED_ROUNDS,
                "confirmation_ordinal": ordinal,
            }
            return
        if requested > TARGET_ACCEPTED_ROUNDS:
            raise RuntimeError("accepted-round mutation would exceed the formal target")
        super().__setitem__(key, requested)
        self.last_update[key] = {
            "kind": "accepted_round_credit" if requested > previous else "no_credit_change",
            "accepted_state_credit_increment": max(0, requested - previous),
            "accepted_rounds_before": previous,
            "accepted_rounds_after": requested,
            "confirmation_ordinal": None,
        }

    def __deepcopy__(self, memo: dict[int, Any]) -> "ConfirmationAcceptedRounds":
        duplicate = type(self)({key: int(dict.__getitem__(self, key)) for key in self})
        duplicate.confirmation_acceptances = copy.deepcopy(
            self.confirmation_acceptances, memo
        )
        duplicate.last_update = copy.deepcopy(self.last_update, memo)
        return duplicate


def state_document(**kwargs: Any) -> dict[str, Any]:
    global ACTIVE_ACCEPTED_ROUNDS
    document = ORIGINAL_STATE_DOCUMENT(**kwargs)
    accepted = ConfirmationAcceptedRounds(document["accepted_rounds"])
    ACTIVE_ACCEPTED_ROUNDS = accepted
    document["accepted_rounds"] = accepted
    document["post_target_stability_confirmation_release"] = {
        "release_version": "v20",
        "source_path": str(Path(__file__).resolve()),
        "source_sha256": alg.sha(Path(__file__).resolve()),
        "formal_accepted_round_target": TARGET_ACCEPTED_ROUNDS,
        "accepted_round_credit_capped_at_target": True,
        "post_target_confirmation_uses_real_top5": True,
        "post_target_confirmation_updates_incumbent_and_interval": True,
        "post_target_confirmation_increments_accepted_rounds": False,
        "maximum_post_target_accepted_confirmations": (
            MAX_POST_TARGET_ACCEPTED_CONFIRMATIONS
        ),
        "rejected_confirmation_resets_stability": True,
        "workload_scoring_acceptance_and_interval_rules_changed": False,
    }
    document["attempt_budget"]["post_target_stability_confirmation"] = {
        "enabled": True,
        "formal_accepted_round_credit_increment": 0,
        "maximum_accepted_confirmations": MAX_POST_TARGET_ACCEPTED_CONFIRMATIONS,
    }
    return document


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    document: Mapping[str, Any] = value
    scene = str(value.get("scene") or "")
    if (
        path.name == f"{scene}.decision.json"
        and value.get("accepted") is True
        and ACTIVE_ACCEPTED_ROUNDS is not None
    ):
        update = ACTIVE_ACCEPTED_ROUNDS.last_update.get(scene) or {}
        if update.get("kind") == "post_target_stability_confirmation":
            amended = copy.deepcopy(dict(value))
            amended["schema_version"] = (
                "mysql_four_scene_algorithm_c_outer_decision_boundary_resume_v20"
            )
            amended["accepted_state_credit_increment"] = 0
            amended["post_target_stability_confirmation"] = copy.deepcopy(update)
            document = amended
    ORIGINAL_ATOMIC(path, document)


def main() -> int:
    alg.atomic = atomic
    v19.state_document = state_document
    return v19.main()


if __name__ == "__main__":
    raise SystemExit(main())
