#!/usr/bin/env python3
"""Build a deterministic cross-scope blind pack for Grok direction validation."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/artifacts/pg_resource_estimation_agent_experience_20260723.json"
INPUTS = ROOT / "docs/artifacts/grok_resource_direction_blind_inputs_20260723.json"
TRUTH = ROOT / "docs/artifacts/grok_resource_direction_blind_truth_20260723.json"
PROMPT = ROOT / ".agents/grok_resource_direction_blind_validation_20260723.md"
WEB_PROMPT = ROOT / ".agents/grok_resource_direction_blind_validation_web_20260723.md"


def token(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def group_key(row: Mapping[str, Any]) -> Tuple[str, str, str]:
    isolation = row["isolation_key"]
    return (
        str(isolation["workload"]),
        str(isolation["target_resource"]),
        token(isolation["capability"]),
    )


def vector(trigger: Mapping[str, Any]) -> Dict[str, float]:
    return {
        str(name): float(values["p50"])
        for name, values in trigger["parameter_log_delta_range"].items()
    }


def distance(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    names = set(left) | set(right)
    return math.sqrt(sum((left.get(name, 0.0) - right.get(name, 0.0)) ** 2 for name in names))


def support_view(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "experience_id": row["experience_id"],
        "trigger": row["trigger"],
        "resource_effect": {
            resource: {
                "direction": row["resource_effect"][resource]["direction"],
                "delta_p50_pp": row["resource_effect"][resource][
                    "observed_delta_pp_interval"
                ]["p50"],
                "sign_consistency": row["resource_effect"][resource]["sign_consistency"],
            }
            for resource in ("cpu", "memory", "io")
        },
        "sample_count": row["sample_count"],
        "confidence": row["confidence"]["heuristic_0_to_1"],
        "causal_interpretation": row["causal_interpretation"],
    }


def main() -> int:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    experiences: Sequence[Mapping[str, Any]] = document["experiences"]
    grouped: Dict[Tuple[str, str, str], List[Mapping[str, Any]]] = {}
    for row in experiences:
        grouped.setdefault(group_key(row), []).append(row)
    eligible = [
        (key, rows)
        for key, rows in grouped.items()
        if len({row["isolation_key"]["incumbent_scope_id"] for row in rows}) >= 5
    ]
    eligible.sort(key=lambda item: (-len(item[1]), item[0]))
    cases: List[Dict[str, Any]] = []
    truth: List[Dict[str, Any]] = []
    for group_index, (key, rows) in enumerate(eligible[:2], start=1):
        scopes = sorted({str(row["isolation_key"]["incumbent_scope_id"]) for row in rows})
        heldout_scope = scopes[-1]
        queries = [
            row for row in rows
            if str(row["isolation_key"]["incumbent_scope_id"]) == heldout_scope
        ]
        queries.sort(key=lambda row: (-int(row["sample_count"]), str(row["experience_id"])))
        training = [
            row for row in rows
            if str(row["isolation_key"]["incumbent_scope_id"]) != heldout_scope
        ]
        for query_index, query in enumerate(queries[:3], start=1):
            query_vector = vector(query["trigger"])
            nearest = sorted(
                training,
                key=lambda row: (
                    distance(query_vector, vector(row["trigger"])),
                    -int(row["sample_count"]),
                    str(row["experience_id"]),
                ),
            )[:5]
            case_id = f"g{group_index:02d}_q{query_index:02d}"
            cases.append(
                {
                    "case_id": case_id,
                    "context": {
                        "workload": key[0],
                        "target_resource": key[1],
                        "capability": json.loads(key[2]),
                        "stability_class": query["isolation_key"]["stability_class"],
                    },
                    "candidate_trigger": query["trigger"],
                    "support_experiences": [support_view(row) for row in nearest],
                    "rules": {
                        "predict_resources_only": ["cpu", "memory", "io"],
                        "qps_forbidden": True,
                        "multi_parameter_is_joint_only": True,
                        "unknown_or_conflict_must_fail_open": True,
                    },
                }
            )
            truth.append(
                {
                    "case_id": case_id,
                    "heldout_experience_id": query["experience_id"],
                    "heldout_scope_id": heldout_scope,
                    "direction": {
                        resource: query["resource_effect"][resource]["direction"]
                        for resource in ("cpu", "memory", "io")
                    },
                    "delta_p50_pp": {
                        resource: query["resource_effect"][resource]["observed_delta_pp_interval"]["p50"]
                        for resource in ("cpu", "memory", "io")
                    },
                }
            )
    inputs_document = {
        "schema_version": 1,
        "source_artifact": str(SOURCE),
        "split": "exact workload/target/capability; one whole incumbent scope held out per group",
        "case_count": len(cases),
        "cases": cases,
    }
    truth_document = {
        "schema_version": 1,
        "case_count": len(truth),
        "truth": truth,
    }
    INPUTS.write_text(json.dumps(inputs_document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    TRUTH.write_text(json.dumps(truth_document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    prompt = f"""You are validating whether an LLM can conservatively predict PostgreSQL resource directions.

The JSON below contains blind held-out cases. The actual held-out CPU/Memory/IO labels are not included. Do not read files, call tools, use QPS, or calculate Utility. Use only the embedded support experiences. A multi-parameter vector is joint evidence and must never be decomposed into single-parameter causality.

For each case predict candidate-minus-incumbent direction for cpu, memory, and io. Allowed values: decrease, near_zero, increase, unknown. Prefer unknown when support conflicts or is too distant. `pruning_safe` may be true only when all three directions have confidence >=0.70 and compatible support is consistent. Return all {len(cases)} cases. `direction` must be an object whose cpu/memory/io values are direction strings. `confidence` must be an object whose cpu/memory/io values are numbers in [0,1]. Do not swap these fields. Do not include prose outside JSON.

BLIND_CASES_JSON:
{json.dumps(inputs_document, ensure_ascii=False, separators=(",", ":"))}
"""
    PROMPT.parent.mkdir(parents=True, exist_ok=True)
    PROMPT.write_text(prompt, encoding="utf-8")
    web_prompt = f"""You are validating whether web-assisted reasoning can conservatively predict PostgreSQL resource directions.

The JSON below contains blind held-out cases. The actual held-out CPU/Memory/IO labels are not included. Do not read local files or search for this experiment, its case IDs, artifacts, reports, logs, or held-out answers. You must use web search for independent external PostgreSQL knowledge before predicting. Prefer official PostgreSQL documentation and peer-reviewed papers; use blogs/forums only as weak evidence. Record the URLs actually used.

External evidence is a semantic direction prior only. PostgreSQL planner costs are relative and machine-specific, so do not transfer an external numeric percentage into this machine. Infer candidate-minus-incumbent cpu, memory, and io from the embedded local support experiences, using external sources only to interpret parameter and plan semantics. A multi-parameter vector is joint evidence and must never be decomposed into single-parameter causality. Do not use QPS or calculate Utility.

Allowed directions: decrease, near_zero, increase, unknown. Prefer unknown when local support conflicts, is too distant, or external semantics cannot resolve the joint vector. Confidence must reflect local transferability, not merely confidence in PostgreSQL documentation. `pruning_safe` may be true only when all three directions have confidence >=0.70 and compatible local support is consistent; web evidence alone can never make it true. Return all {len(cases)} cases and no prose outside JSON.

BLIND_CASES_JSON:
{json.dumps(inputs_document, ensure_ascii=False, separators=(",", ":"))}
"""
    WEB_PROMPT.write_text(web_prompt, encoding="utf-8")
    print(
        json.dumps(
            {
                "inputs": str(INPUTS),
                "truth": str(TRUTH),
                "prompt": str(PROMPT),
                "web_prompt": str(WEB_PROMPT),
                "cases": len(cases),
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
