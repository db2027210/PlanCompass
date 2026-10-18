#!/usr/bin/env python3
"""Audit whether external accepted HyBenchXP points are covered by Bonect anchors."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "shell") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "shell"))

import run_pg_benchmark_bottleneck_matrix as matrix  # noqa: E402


DEFAULT_STATUS_JSON = PROJECT_ROOT / "report" / "bonect" / "phase2_external_hyxp_status_latest.json"
DEFAULT_JSON_OUTPUT = PROJECT_ROOT / "report" / "bonect" / "phase2_external_anchor_coverage_latest.json"
DEFAULT_MD_OUTPUT = PROJECT_ROOT / "report" / "bonect" / "phase2_external_anchor_coverage_latest.md"
ACCEPT_REASONS = {
    "accepted_actual_target_drop_and_db_qps_gain",
    "accepted",
}


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        loaded = json.load(f)
    return loaded if isinstance(loaded, dict) else {}


def _jsonl_path_from_status(status: Dict[str, Any]) -> Optional[Path]:
    external = status.get("external")
    if not isinstance(external, dict):
        return None
    raw_path = external.get("jsonl")
    if not raw_path:
        return None
    return Path(str(raw_path))


def _as_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _cost_params(raw: Any) -> Dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    params: Dict[str, float] = {}
    for name in matrix.COST_PARAM_KEYS:
        parsed = _as_float(raw.get(name))
        if parsed is not None:
            params[name] = parsed
    return params


def _accepted_rows(jsonl_path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not jsonl_path.exists():
        return rows
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            if item.get("event") != "candidate_validation":
                continue
            reason = str(
                item.get("accept_reject_reason")
                or item.get("reject_reason")
                or item.get("accept_reason")
                or ""
            )
            if item.get("accepted") is not True and reason not in ACCEPT_REASONS:
                continue
            decision = item.get("decision") if isinstance(item.get("decision"), dict) else {}
            params = _cost_params(item.get("params") or item.get("effective_params"))
            rows.append(
                {
                    "round": item.get("round"),
                    "candidate_index": item.get("candidate_index"),
                    "label": str(item.get("label") or ""),
                    "accepted": bool(item.get("accepted")),
                    "accept_reject_reason": reason,
                    "db_qps": item.get("db_qps"),
                    "db_qps_delta_pct": item.get("db_qps_delta_pct")
                    if item.get("db_qps_delta_pct") is not None
                    else decision.get("db_qps_delta_pct"),
                    "target_delta_pct": item.get("target_delta_pct")
                    if item.get("target_delta_pct") is not None
                    else decision.get("target_delta_pct"),
                    "xp_qps_delta_pct": item.get("xp_qps_delta_pct")
                    if item.get("xp_qps_delta_pct") is not None
                    else decision.get("hybench_xp_qps_delta_pct"),
                    "xp_tps_delta_pct": item.get("xp_tps_delta_pct")
                    if item.get("xp_tps_delta_pct") is not None
                    else decision.get("hybench_xp_tps_delta_pct"),
                    "params": params,
                    "exactly_7_cost_params": (
                        len(params) == 7 and set(params) == set(matrix.COST_PARAM_KEYS)
                    ),
                }
            )
    rows.sort(
        key=lambda row: (
            int(row["round"]) if row.get("round") is not None else 10**9,
            int(row["candidate_index"]) if row.get("candidate_index") is not None else 10**9,
            str(row.get("label") or ""),
        )
    )
    return rows


def _anchor_rows(bottleneck: str, benchmark: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in matrix.bonect_reference_path_anchors(bottleneck, benchmark):
        if not isinstance(item, dict):
            continue
        params = _cost_params(item.get("params"))
        rows.append(
            {
                "name": str(item.get("name") or ""),
                "source": str(item.get("phase2_known_real_source") or ""),
                "known_real": bool(item.get("phase2_known_real_direction_seed")),
                "target": str(item.get("phase2_known_real_target_resource") or ""),
                "db_qps_delta_pct": item.get("phase2_known_real_db_qps_delta_pct"),
                "params": params,
                "exactly_7_cost_params": (
                    len(params) == 7 and set(params) == set(matrix.COST_PARAM_KEYS)
                ),
            }
        )
    return rows


def _label_tokens(label: str) -> List[str]:
    tokens = [label] if label else []
    parts = label.split("_")
    if len(parts) >= 3:
        tokens.append("_".join(parts[1:3]))
    return [item for item in tokens if item]


def _param_close(left: Dict[str, float], right: Dict[str, float]) -> Tuple[bool, float]:
    if set(left) != set(matrix.COST_PARAM_KEYS) or set(right) != set(matrix.COST_PARAM_KEYS):
        return False, math.inf
    max_abs_diff = 0.0
    for name in matrix.COST_PARAM_KEYS:
        diff = abs(float(left[name]) - float(right[name]))
        max_abs_diff = max(max_abs_diff, diff)
        if not math.isclose(float(left[name]), float(right[name]), rel_tol=1e-5, abs_tol=1e-6):
            return False, max_abs_diff
    return True, max_abs_diff


def _match_anchor(
    accepted: Dict[str, Any],
    anchors: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    label = str(accepted.get("label") or "")
    label_tokens = _label_tokens(label)
    params = accepted.get("params") if isinstance(accepted.get("params"), dict) else {}

    best_param_match: Optional[Dict[str, Any]] = None
    best_param_diff = math.inf
    for anchor in anchors:
        name = str(anchor.get("name") or "")
        source = str(anchor.get("source") or "")
        known_real = bool(anchor.get("known_real"))
        if known_real and any(token and (token in name or token in source) for token in label_tokens):
            return {
                "covered": True,
                "match_type": "known_real_label_or_source",
                "anchor_name": name,
                "anchor_known_real": known_real,
                "anchor_target": anchor.get("target"),
                "max_abs_param_diff": None,
            }
        close, diff = _param_close(params, anchor.get("params") or {})
        if close and diff < best_param_diff:
            best_param_match = anchor
            best_param_diff = diff

    if best_param_match:
        return {
            "covered": bool(best_param_match.get("known_real")),
            "match_type": (
                "known_real_param_match"
                if bool(best_param_match.get("known_real"))
                else "non_known_real_param_match"
            ),
            "anchor_name": best_param_match.get("name"),
            "anchor_known_real": bool(best_param_match.get("known_real")),
            "anchor_target": best_param_match.get("target"),
            "max_abs_param_diff": best_param_diff,
        }
    return {
        "covered": False,
        "match_type": "missing",
        "anchor_name": None,
        "anchor_known_real": False,
        "anchor_target": None,
        "max_abs_param_diff": None,
    }


def build_report(status_json: Path, jsonl_path: Optional[Path]) -> Tuple[Dict[str, Any], str]:
    status = _load_json(status_json)
    source_jsonl = jsonl_path or _jsonl_path_from_status(status)
    accepted = _accepted_rows(source_jsonl) if source_jsonl else []
    anchors = _anchor_rows("cpu", "hybench_xp")
    known_real_anchors = [item for item in anchors if item.get("known_real")]

    accepted_with_match: List[Dict[str, Any]] = []
    for row in accepted:
        match = _match_anchor(row, anchors)
        accepted_with_match.append({**row, "anchor_match": match})

    missing = [
        row for row in accepted_with_match
        if not row.get("anchor_match", {}).get("covered")
    ]
    checks = {
        "status_json_exists": status_json.exists(),
        "external_jsonl_exists": bool(source_jsonl and source_jsonl.exists()),
        "accepted_rows_found": len(accepted_with_match) > 0,
        "all_accepted_have_exactly_7_cost_params": all(
            bool(row.get("exactly_7_cost_params")) for row in accepted_with_match
        ),
        "all_accepted_covered_by_known_real_anchor": len(missing) == 0,
    }
    result = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status_json": str(status_json),
        "external_jsonl": str(source_jsonl) if source_jsonl else None,
        "accepted_count": len(accepted_with_match),
        "anchor_count": len(anchors),
        "known_real_anchor_count": len(known_real_anchors),
        "checks": checks,
        "pass": all(checks.values()),
        "missing_accepted_labels": [row.get("label") for row in missing],
        "accepted": accepted_with_match,
        "known_real_anchor_names": [item.get("name") for item in known_real_anchors],
    }

    lines = [
        "# Bonect External Accepted Anchor Coverage Audit",
        "",
        f"- generated_at: `{result['generated_at']}`",
        f"- status_json: `{status_json}`",
        f"- external_jsonl: `{result['external_jsonl']}`",
        f"- accepted_count: `{result['accepted_count']}`",
        f"- CPU anchor_count: `{result['anchor_count']}`",
        f"- CPU known-real anchor_count: `{result['known_real_anchor_count']}`",
        f"- overall pass: `{result['pass']}`",
        "",
        "## Required Checks",
    ]
    for name, ok in checks.items():
        lines.append(f"- `{name}`: `{ok}`")
    lines.extend(
        [
            "",
            "## Accepted Coverage",
            "",
            "| Label | DB QPS delta | CPU delta | 7 params | Covered | Match type | Anchor |",
            "|---|---:|---:|---:|---:|---|---|",
        ]
    )
    for row in sorted(
        accepted_with_match,
        key=lambda item: (
            -float(item.get("db_qps_delta_pct") or 0.0),
            str(item.get("label") or ""),
        ),
    ):
        match = row.get("anchor_match") or {}
        lines.append(
            "| {label} | {db:.4f}% | {target:.4f}pp | `{exact}` | `{covered}` | `{match_type}` | {anchor} |".format(
                label=row.get("label") or "",
                db=float(row.get("db_qps_delta_pct") or 0.0),
                target=float(row.get("target_delta_pct") or 0.0),
                exact=bool(row.get("exactly_7_cost_params")),
                covered=bool(match.get("covered")),
                match_type=match.get("match_type") or "",
                anchor=match.get("anchor_name") or "",
            )
        )
    if missing:
        lines.extend(["", "## Missing Accepted Labels"])
        for row in missing:
            lines.append(f"- `{row.get('label')}`")
    return result, "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status-json", type=Path, default=DEFAULT_STATUS_JSON)
    parser.add_argument("--external-jsonl", type=Path)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--md-output", type=Path, default=DEFAULT_MD_OUTPUT)
    args = parser.parse_args()

    result, md = build_report(args.status_json, args.external_jsonl)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.md_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.md_output.write_text(md, encoding="utf-8")
    print(f"WROTE {args.json_output}")
    print(f"WROTE {args.md_output}")
    print(f"PASS {result['pass']}")
    if result.get("missing_accepted_labels"):
        print("MISSING " + ", ".join(str(x) for x in result["missing_accepted_labels"]))
        return 2
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
