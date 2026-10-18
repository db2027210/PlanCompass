#!/usr/bin/env python3
"""Build an ambiguity-aware truth file for manual Grok blind validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping


ROOT = Path(__file__).resolve().parents[1]
RAW_TRUTH = ROOT / "docs/artifacts/grok_resource_direction_blind_truth_20260723.json"
EXPERIENCE = ROOT / "docs/artifacts/pg_resource_estimation_agent_experience_20260723.json"
OUTPUT = ROOT / "docs/artifacts/grok_resource_direction_blind_truth_strict_20260723.json"
RESOURCES = ("cpu", "memory", "io")


def strict_label(effect: Mapping[str, Any]) -> Dict[str, Any]:
    counts = {
        str(name): int(value)
        for name, value in (effect.get("direction_example_counts") or {}).items()
    }
    sample_count = sum(counts.values())
    maximum = max(counts.values(), default=0)
    winners = sorted(name for name, value in counts.items() if value == maximum)
    sign_consistency = float(effect.get("sign_consistency") or 0.0)
    scoreable = bool(sample_count and len(winners) == 1 and sign_consistency > 0.5)
    return {
        "direction": winners[0] if scoreable else "unknown",
        "scoreable": scoreable,
        "label_status": "dominant_observed_direction" if scoreable else "ambiguous_no_majority",
        "sign_consistency": sign_consistency,
        "direction_example_counts": counts,
        "observed_delta_pp_interval": effect.get("observed_delta_pp_interval"),
    }


def main() -> int:
    raw_truth = json.loads(RAW_TRUTH.read_text(encoding="utf-8"))
    experience = json.loads(EXPERIENCE.read_text(encoding="utf-8"))
    by_id = {str(row["experience_id"]): row for row in experience["experiences"]}
    truth = []
    for row in raw_truth["truth"]:
        heldout_id = str(row["heldout_experience_id"])
        heldout = by_id[heldout_id]
        truth.append(
            {
                "case_id": row["case_id"],
                "heldout_experience_id": heldout_id,
                "heldout_scope_id": row["heldout_scope_id"],
                "resource_labels": {
                    resource: strict_label(heldout["resource_effect"][resource])
                    for resource in RESOURCES
                },
            }
        )
    document = {
        "schema_version": 2,
        "case_count": len(truth),
        "label_rule": (
            "A resource direction is scoreable only when one observed direction has a strict "
            "majority (sign_consistency > 0.5); ties and no-majority groups are unknown."
        ),
        "truth": truth,
    }
    OUTPUT.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"truth": str(OUTPUT), "cases": len(truth)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
