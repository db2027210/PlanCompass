#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bonect_robust_ranking import (  # noqa: E402
    DEFAULT_VARIANT as DEFAULT_ROBUST_VARIANT,
    ROBUST_VARIANTS,
    attach_robust_scores,
    robust_sort_key,
    stable_json_hash,
)


COST_PARAMS = [
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
]
SCENARIOS = ["memory", "cpu", "io", "none"]
DEFAULT_REPORT = ROOT / "report" / "bonect" / "bonect_integrated_grid_real_validation_20260624_211500.md"
DEFAULT_SCAN_DIRS = {
    "memory": ROOT / "history" / "bonect_full_grid_scan" / "mem_io_orderwide_20260624_170900" / "memory",
    "cpu": ROOT / "history" / "bonect_full_grid_scan" / "all_grid5_20260624_152314" / "cpu",
    "io": ROOT / "history" / "bonect_full_grid_scan" / "mem_io_orderwide_20260624_170900" / "io",
    "none": ROOT / "history" / "bonect_full_grid_scan" / "all_grid5_20260624_152314" / "none",
}
RESULT_BONECT_MD = {
    "memory": ROOT / "result" / "bonect" / "mem.md",
    "cpu": ROOT / "result" / "bonect" / "cpu.md",
    "io": ROOT / "result" / "bonect" / "io.md",
}


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def maybe_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def read_json(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        f.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def candidate_id(row: Dict[str, Any]) -> str:
    if row.get("candidate_id"):
        return str(row["candidate_id"])
    if row.get("param_signature"):
        return str(row["param_signature"])
    return stable_json_hash(row.get("params") or row)


def params_distance(left: Dict[str, Any], right: Dict[str, Any]) -> float:
    distance = 0.0
    for name in COST_PARAMS:
        lv = safe_float(left.get(name), 0.0)
        rv = safe_float(right.get(name), 0.0)
        denom = max(abs(lv), abs(rv), 1.0)
        distance += abs(lv - rv) / denom
    return distance


def parse_markdown_table(lines: Sequence[str], start: int) -> Tuple[List[Dict[str, str]], int]:
    header = [part.strip() for part in lines[start].strip().strip("|").split("|")]
    rows: List[Dict[str, str]] = []
    idx = start + 2
    while idx < len(lines) and lines[idx].lstrip().startswith("|"):
        cells = [part.strip() for part in lines[idx].strip().strip("|").split("|")]
        if len(cells) >= len(header):
            rows.append({header[i]: cells[i] for i in range(len(header))})
        idx += 1
    return rows, idx


def parse_real_validation_report(path: Path) -> Dict[str, Dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    scenario = ""
    out: Dict[str, Dict[str, Any]] = {name: {"labels": [], "scan_dir": str(DEFAULT_SCAN_DIRS[name])} for name in SCENARIOS}
    for idx, line in enumerate(lines):
        heading = line.strip().lower()
        if heading.startswith("## memory"):
            scenario = "memory"
        elif heading.startswith("## cpu"):
            scenario = "cpu"
        elif heading.startswith("## io"):
            scenario = "io"
        elif heading.startswith("## no-bottleneck"):
            scenario = "none"
        if scenario and "estimator scan" in line:
            match = re.search(r"`([^`]+)`", line)
            if match:
                out[scenario]["scan_dir"] = str((ROOT / match.group(1)).resolve())
        if scenario and line.startswith("| candidate | source | score |"):
            rows, _ = parse_markdown_table(lines, idx)
            for row in rows:
                name = row.get("candidate", "").strip("` ")
                label = {
                    "candidate": name,
                    "source": row.get("source"),
                    "real_score": safe_float(row.get("score")),
                    "real_resource": {
                        "cpu": safe_float(row.get("cpu")),
                        "memory": safe_float(row.get("mem_GB")),
                        "io": safe_float(row.get("io_MBps")),
                    },
                    "throughput": {
                        "XP-QPS": safe_float(row.get("XP-QPS")),
                        "XP-TPS": safe_float(row.get("XP-TPS")),
                        "db_qps": safe_float(row.get("db_qps")),
                        "db_xact_tps": safe_float(row.get("db_xact_tps")),
                    },
                    "latency": {
                        "p95": safe_float(row.get("latency_p95")),
                        "p99": safe_float(row.get("latency_p99")),
                    },
                }
                out[scenario]["labels"].append(label)
    return out


def parse_optimized_params(path: Path) -> Dict[str, float]:
    if not path or not path.exists() or not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    match = re.search(r'"optimized_params"\s*:\s*\{(?P<body>.*?)\}', text, flags=re.DOTALL)
    if not match:
        return {}
    body = match.group("body")
    params: Dict[str, float] = {}
    for name in COST_PARAMS:
        m = re.search(rf'"{re.escape(name)}"\s*:\s*([-+0-9.eE]+)', body)
        if m:
            params[name] = safe_float(m.group(1))
    return params


def default_params() -> Dict[str, float]:
    return {
        "seq_page_cost": 1.0,
        "random_page_cost": 4.0,
        "cpu_tuple_cost": 0.01,
        "cpu_index_tuple_cost": 0.005,
        "cpu_operator_cost": 0.0025,
        "parallel_setup_cost": 1000.0,
        "parallel_tuple_cost": 0.1,
    }


def load_candidates(scan_dir: Path, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    path = scan_dir / "candidates.jsonl"
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            row["candidate_id"] = candidate_id(row)
            rows.append(row)
            if limit and len(rows) >= limit:
                break
    return rows


def load_ranking_pool(scan_dir: Path) -> Tuple[List[Dict[str, Any]], str]:
    for name in [
        "top20_distinct_scores_unified_units.json",
        "top20_distinct_scores.json",
    ]:
        path = scan_dir / name
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, list) and data:
            rows = []
            for row in data:
                if isinstance(row, dict):
                    item = dict(row)
                    item["candidate_id"] = candidate_id(item)
                    rows.append(item)
            return rows, name
    return load_candidates(scan_dir), "candidates.jsonl"


def load_capability(scan_dir: Path) -> Dict[str, float]:
    summary = read_json(scan_dir / "scan_summary.json")
    config_path = summary.get("config_path")
    if not config_path:
        config_path = (read_json(scan_dir / "scan_config.json").get("config_path"))
    if config_path:
        path = Path(str(config_path))
        if not path.is_absolute():
            path = ROOT / path
        cfg = read_json(path)
        return {str(k): safe_float(v) for k, v in (cfg.get("capability") or {}).items()}
    return {}


def match_labels(
    *,
    scenario: str,
    labels: Sequence[Dict[str, Any]],
    candidates: Sequence[Dict[str, Any]],
    raw_top: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    matched: List[Dict[str, Any]] = []
    by_id = {candidate_id(row): row for row in candidates}
    result_md = RESULT_BONECT_MD.get(scenario)
    result_params = parse_optimized_params(result_md) if result_md is not None else {}
    for label in labels:
        name = str(label.get("candidate") or "")
        row: Optional[Dict[str, Any]] = None
        match_source = "unmatched"
        m = re.search(r"_estimator_top(\d+)$", name)
        if m:
            rank = int(m.group(1))
            if 1 <= rank <= len(raw_top):
                row = raw_top[rank - 1]
                match_source = "estimator_top_rank"
        elif "result_bonect" in name:
            target_params = result_params if result_params else default_params()
            if scenario == "none":
                target_params = default_params()
            best = min(
                candidates,
                key=lambda item: params_distance(item.get("params") or {}, target_params),
                default=None,
            )
            if best is not None and params_distance(best.get("params") or {}, target_params) < 1e-6:
                row = best
                match_source = "result_bonect_params"
            elif best is not None:
                row = best
                match_source = "nearest_result_bonect_params"
        if row is not None:
            label = dict(label)
            label["candidate_id"] = candidate_id(row)
            label["matched_candidate_index"] = row.get("candidate_index")
            label["match_source"] = match_source
            label["predicted_score"] = row.get("score")
            label["predicted_resource"] = row.get("estimated_resource") or row.get("resource")
            matched.append(label)
        else:
            label = dict(label)
            label["match_source"] = match_source
            matched.append(label)
    return matched


def _iter_label_records(value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item
    elif isinstance(value, dict):
        if isinstance(value.get("labels"), list):
            yield from _iter_label_records(value.get("labels"))
        elif isinstance(value.get("candidates"), list):
            yield from _iter_label_records(value.get("candidates"))
        elif isinstance(value.get("scenarios"), dict):
            for scenario, info in value.get("scenarios", {}).items():
                if not isinstance(info, dict):
                    continue
                for item in _iter_label_records(info):
                    row = dict(item)
                    row.setdefault("scenario", scenario)
                    yield row
        else:
            yield value


def _extract_real_score(label: Dict[str, Any]) -> Optional[float]:
    for key in (
        "real_score",
        "known_real_score",
        "actual_score",
        "score",
        "validation_score",
        "candidate_score",
    ):
        value = maybe_float(label.get(key))
        if value is not None:
            return value
    validation = label.get("validation") or label.get("real_validation") or label.get("validation_result")
    if isinstance(validation, dict):
        for key in ("real_score", "known_real_score", "score", "validation_score", "candidate_score"):
            value = maybe_float(validation.get(key))
            if value is not None:
                return value
    return None


def resolve_extra_label_paths(paths: Sequence[str]) -> List[Path]:
    resolved: List[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_absolute():
            path = ROOT / path
        if not path.exists():
            continue
        if path.is_dir():
            result_path = path / "validation_results.jsonl"
            if result_path.exists():
                resolved.append(result_path)
            continue
        resolved.append(path)
    return resolved


def load_extra_labels(paths: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
    labels: Dict[str, List[Dict[str, Any]]] = {scenario: [] for scenario in SCENARIOS}
    for path in resolve_extra_label_paths(paths):
        if path.suffix.lower() == ".jsonl":
            try:
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            except (OSError, json.JSONDecodeError):
                rows = []
            records = [row for row in rows if isinstance(row, dict)]
        else:
            try:
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                data = []
            records = list(_iter_label_records(data))
        for record in records:
            scenario = str(record.get("scenario") or record.get("bottleneck") or "").strip().lower()
            if scenario in {"mem", "memory"}:
                scenario = "memory"
            if scenario not in labels:
                continue
            real_score = _extract_real_score(record)
            if real_score is None:
                continue
            item = dict(record)
            item["real_score"] = real_score
            item["extra_label_source_file"] = str(path)
            labels[scenario].append(item)
    return labels


def discover_existing_validation_label_files(limit: int = 240) -> List[str]:
    roots = [
        ROOT / "history" / "bonect_real_validation",
        ROOT / "report" / "bonect",
        ROOT / "result" / "bonect",
    ]
    paths: List[Path] = []
    for root in roots:
        if not root.exists():
            continue
        paths.extend(path for path in root.rglob("validation_results.jsonl") if path.is_file())
        paths.extend(
            path
            for path in root.rglob("*labels*.jsonl")
            if path.is_file() and "pending_validation_queue" not in path.name
        )
    return [str(path.resolve()) for path in sorted(set(paths))[:limit]]


def _label_matches_ranked_row(label: Dict[str, Any], row: Dict[str, Any]) -> Tuple[bool, str]:
    label_cid = str(label.get("candidate_id") or "").strip()
    row_cid = str(row.get("candidate_id") or "").strip()
    if label_cid and row_cid and label_cid == row_cid:
        return True, "candidate_id"
    if isinstance(label.get("params"), dict) and isinstance(row.get("params"), dict):
        if params_distance(label.get("params") or {}, row.get("params") or {}) < 1e-6:
            return True, "params"
    return False, ""


def audit_exact_reusable_validation_labels(scenarios: Dict[str, Any]) -> Dict[str, Any]:
    paths = discover_existing_validation_label_files()
    labels = load_extra_labels(paths)
    per_scenario: Dict[str, Any] = {}
    total_external_match_count = 0
    total_new_external_match_count = 0
    total_conflict_count = 0
    for scenario in SCENARIOS:
        rows = (scenarios.get(scenario) or {}).get("top15_robust") or []
        scenario_labels = labels.get(scenario) or []
        matched_rows: List[Dict[str, Any]] = []
        for row in rows:
            matches: List[Dict[str, Any]] = []
            match_modes = set()
            for label in scenario_labels:
                matched, mode = _label_matches_ranked_row(label, row)
                if matched:
                    matches.append(label)
                    match_modes.add(mode)
            scores = [safe_float(label.get("real_score")) for label in matches if label.get("real_score") is not None]
            unique_scores = sorted({round(score, 9) for score in scores})
            has_known_label = row.get("real_score") is not None
            item = {
                "robust_rank": row.get("rank"),
                "candidate_id": row.get("candidate_id"),
                "candidate_index": row.get("candidate_index"),
                "has_current_report_label": has_known_label,
                "external_match_count": len(matches),
                "external_source_files": sorted(
                    {str(label.get("extra_label_source_file")) for label in matches if label.get("extra_label_source_file")}
                ),
                "match_modes": sorted(match_modes),
                "external_scores": scores,
                "conflicting_external_scores": len(unique_scores) > 1,
            }
            matched_rows.append(item)
        external_match_count = sum(1 for row in matched_rows if row.get("external_match_count"))
        new_external_match_count = sum(
            1
            for row in matched_rows
            if row.get("external_match_count") and not row.get("has_current_report_label")
        )
        conflict_count = sum(1 for row in matched_rows if row.get("conflicting_external_scores"))
        total_external_match_count += external_match_count
        total_new_external_match_count += new_external_match_count
        total_conflict_count += conflict_count
        per_scenario[scenario] = {
            "top15_rows": len(rows),
            "external_match_rows": external_match_count,
            "new_external_match_rows": new_external_match_count,
            "conflict_rows": conflict_count,
            "rows": matched_rows,
        }
    return {
        "searched_files": paths,
        "searched_file_count": len(paths),
        "method": "exact candidate_id or seven-cost-parameter match against current robust top15",
        "total_external_match_rows": total_external_match_count,
        "total_new_external_match_rows": total_new_external_match_count,
        "total_conflict_rows": total_conflict_count,
        "per_scenario": per_scenario,
        "note": (
            "This audit does not feed labels into ranking metrics. It only proves whether existing offline "
            "validation files can already cover current robust top15 candidates without running new validation."
        ),
    }


def _iter_json_dicts(value: Any, path: str = "$") -> Iterable[Tuple[str, Dict[str, Any]]]:
    if isinstance(value, dict):
        yield path, value
        for key, item in value.items():
            child_path = f"{path}.{key}"
            yield from _iter_json_dicts(item, child_path)
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            yield from _iter_json_dicts(item, f"{path}[{idx}]")


def _candidate_param_dicts(record: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    if all(name in record for name in COST_PARAMS):
        yield record
    for key in (
        "params",
        "optimized_params",
        "final_params",
        "best_params",
        "candidate_params",
        "current_params",
        "new_params",
        "postgres_params",
        "postgresql_params",
    ):
        value = record.get(key)
        if isinstance(value, dict) and any(name in value for name in COST_PARAMS):
            yield value


def discover_historical_param_files(limit: int = 3000) -> List[Path]:
    paths: List[Path] = []
    roots_and_patterns = [
        (ROOT / "history" / "bonect_real_validation", ["*.json", "*.jsonl"]),
        (ROOT / "result" / "bonect", ["*.json", "*.jsonl"]),
        (ROOT / "report" / "bonect", ["*.json", "*.jsonl"]),
        (ROOT / "history" / "pg_benchmark_bottleneck_matrix", ["bonect_reference_summary.json", "final_summary*.json"]),
        (ROOT / "report", ["pg_bonect*/**/*.json", "pg_bonect*/**/*.jsonl"]),
    ]
    for root, patterns in roots_and_patterns:
        if not root.exists():
            continue
        for pattern in patterns:
            try:
                paths.extend(path for path in root.glob(pattern) if path.is_file())
            except OSError:
                continue
    filtered: List[Path] = []
    for path in sorted(set(paths)):
        name = path.name
        text_path = str(path)
        if "bonect_estimator_robust_ranking_calibration_" in name:
            continue
        if "pending_validation_queue" in name:
            continue
        if "bonect_full_grid_scan" in text_path:
            continue
        try:
            if path.stat().st_size > 8_000_000:
                continue
        except OSError:
            continue
        filtered.append(path)
        if len(filtered) >= limit:
            break
    return filtered


def _records_from_json_file(path: Path) -> Iterable[Tuple[str, Dict[str, Any]]]:
    if path.suffix.lower() == ".jsonl":
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return
        for idx, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            for rec_path, record in _iter_json_dicts(data, f"line[{idx}]"):
                yield rec_path, record
        return
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    yield from _iter_json_dicts(data)


def audit_historical_param_occurrences(scenarios: Dict[str, Any]) -> Dict[str, Any]:
    files = discover_historical_param_files()
    top_rows: List[Tuple[str, Dict[str, Any]]] = []
    for scenario in SCENARIOS:
        for row in (scenarios.get(scenario) or {}).get("top15_robust") or []:
            top_rows.append((scenario, row))

    per_row_matches: Dict[str, List[Dict[str, Any]]] = {
        f"{scenario}:{row.get('candidate_id')}": [] for scenario, row in top_rows
    }
    scanned_records = 0
    param_records = 0
    for path in files:
        for rec_path, record in _records_from_json_file(path):
            scanned_records += 1
            param_dicts = list(_candidate_param_dicts(record))
            if not param_dicts:
                continue
            param_records += 1
            for scenario, row in top_rows:
                row_params = row.get("params") or {}
                if not isinstance(row_params, dict):
                    continue
                for params in param_dicts:
                    if params_distance(params, row_params) >= 1e-6:
                        continue
                    key = f"{scenario}:{row.get('candidate_id')}"
                    per_row_matches[key].append(
                        {
                            "file": str(path.resolve()),
                            "record_path": rec_path,
                            "record_scenario": record.get("scenario") or record.get("bottleneck"),
                            "candidate_id": record.get("candidate_id"),
                            "source": record.get("source") or record.get("case_id") or record.get("benchmark"),
                            "score": (
                                record.get("actual_score")
                                if record.get("actual_score") is not None
                                else record.get("score")
                            ),
                        }
                    )
                    break

    per_scenario: Dict[str, Any] = {}
    total_rows_with_occurrences = 0
    total_occurrences = 0
    for scenario in SCENARIOS:
        rows: List[Dict[str, Any]] = []
        for row in (scenarios.get(scenario) or {}).get("top15_robust") or []:
            key = f"{scenario}:{row.get('candidate_id')}"
            matches = per_row_matches.get(key) or []
            total_occurrences += len(matches)
            if matches:
                total_rows_with_occurrences += 1
            rows.append(
                {
                    "robust_rank": row.get("rank"),
                    "candidate_id": row.get("candidate_id"),
                    "candidate_index": row.get("candidate_index"),
                    "occurrence_count": len(matches),
                    "sample_occurrences": matches[:10],
                }
            )
        per_scenario[scenario] = {
            "top15_rows": len(rows),
            "rows_with_occurrences": sum(1 for row in rows if row.get("occurrence_count")),
            "occurrence_count": sum(int(row.get("occurrence_count") or 0) for row in rows),
            "rows": rows,
        }
    return {
        "searched_file_count": len(files),
        "searched_files_sample": [str(path.resolve()) for path in files[:80]],
        "scanned_json_record_count": scanned_records,
        "param_record_count": param_records,
        "method": (
            "Exact seven-cost-parameter occurrence scan over Bonect historical JSON/JSONL summaries, "
            "excluding full-grid scan candidates and generated calibration reports."
        ),
        "total_rows_with_occurrences": total_rows_with_occurrences,
        "total_occurrence_count": total_occurrences,
        "per_scenario": per_scenario,
        "note": (
            "Occurrences prove only that the same seven parameters appeared in historical artifacts. "
            "They are not merged as real labels unless the validation_results/current workload/profile context also matches."
        ),
    }


def audit_extra_label_resolution(scenarios: Dict[str, Any]) -> Dict[str, Any]:
    per_scenario: Dict[str, Any] = {}
    total_extra_records = 0
    total_duplicate_groups = 0
    total_conflict_groups = 0
    for scenario in SCENARIOS:
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for label in (scenarios.get(scenario) or {}).get("labels") or []:
            if str(label.get("match_source")) != "extra_label":
                continue
            cid = str(label.get("candidate_id") or "")
            if not cid:
                continue
            groups.setdefault(cid, []).append(label)

        group_rows: List[Dict[str, Any]] = []
        for cid, labels in sorted(groups.items()):
            scores = [safe_float(label.get("real_score")) for label in labels]
            unique_scores = sorted({round(score, 9) for score in scores})
            duplicate = len(labels) > 1
            conflict = len(unique_scores) > 1
            if duplicate:
                total_duplicate_groups += 1
            if conflict:
                total_conflict_groups += 1
            group_rows.append(
                {
                    "candidate_id": cid,
                    "record_count": len(labels),
                    "scores": scores,
                    "selected_score": scores[-1] if scores else None,
                    "duplicate": duplicate,
                    "conflict": conflict,
                    "source_files": [
                        str(label.get("extra_label_source_file") or "")
                        for label in labels
                        if label.get("extra_label_source_file")
                    ],
                }
            )

        extra_records = sum(len(labels) for labels in groups.values())
        duplicate_groups = sum(1 for row in group_rows if row.get("duplicate"))
        conflict_groups = sum(1 for row in group_rows if row.get("conflict"))
        total_extra_records += extra_records
        per_scenario[scenario] = {
            "extra_label_records": extra_records,
            "matched_candidate_count": len(groups),
            "duplicate_candidate_groups": duplicate_groups,
            "conflict_candidate_groups": conflict_groups,
            "groups": group_rows,
        }
    return {
        "method": (
            "Matched extra labels are grouped by scenario and candidate_id. "
            "When repeated labels exist for a candidate, evaluator ranking uses the last matched extra label."
        ),
        "total_extra_label_records": total_extra_records,
        "total_duplicate_candidate_groups": total_duplicate_groups,
        "total_conflict_candidate_groups": total_conflict_groups,
        "per_scenario": per_scenario,
    }


def match_extra_labels(
    *,
    scenario: str,
    labels: Sequence[Dict[str, Any]],
    candidates: Sequence[Dict[str, Any]],
    full_candidates: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    matched: List[Dict[str, Any]] = []
    candidates_by_id = {candidate_id(row): row for row in candidates}
    full_by_index = {
        int(row.get("candidate_index")): row
        for row in full_candidates
        if row.get("candidate_index") is not None
    }
    for label in labels:
        row: Optional[Dict[str, Any]] = None
        cid = str(label.get("candidate_id") or "").strip()
        if cid and cid in candidates_by_id:
            row = candidates_by_id[cid]
        elif label.get("candidate_index") is not None:
            idx = int(safe_float(label.get("candidate_index"), -1))
            row = full_by_index.get(idx)
            if row is not None and candidate_id(row) not in candidates_by_id:
                row = None
        elif isinstance(label.get("params"), dict):
            best = min(
                candidates,
                key=lambda item: params_distance(item.get("params") or {}, label.get("params") or {}),
                default=None,
            )
            if best is not None and params_distance(best.get("params") or {}, label.get("params") or {}) < 1e-6:
                row = best
        if row is None:
            continue
        item = {
            "candidate": str(label.get("candidate") or label.get("known_label") or f"{scenario}_extra_label"),
            "source": "extra_label",
            "real_score": safe_float(label.get("real_score")),
            "real_resource": label.get("real_resource") or label.get("actual_resource") or label.get("resource") or {},
            "throughput": label.get("throughput") or label.get("benchmark") or label.get("db_delta") or {},
            "latency": label.get("latency") or label.get("benchmark_latency") or {},
            "candidate_id": candidate_id(row),
            "matched_candidate_index": row.get("candidate_index"),
            "match_source": "extra_label",
            "predicted_score": row.get("score"),
            "predicted_resource": row.get("estimated_resource") or row.get("resource"),
            "extra_label_source_file": label.get("extra_label_source_file"),
        }
        matched.append(item)
    return matched


def rank_map(rows: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    return {candidate_id(row): idx for idx, row in enumerate(rows)}


def pairwise_inversion_rate(predicted_ids: Sequence[str], real_scores: Dict[str, float]) -> Dict[str, Any]:
    ids = [cid for cid in predicted_ids if cid in real_scores]
    inversions = 0
    pairs = 0
    pred_rank = {cid: idx for idx, cid in enumerate(ids)}
    for i, left in enumerate(ids):
        for right in ids[i + 1 :]:
            if real_scores[left] == real_scores[right]:
                continue
            pairs += 1
            real_prefers_left = real_scores[left] > real_scores[right]
            pred_prefers_left = pred_rank[left] < pred_rank[right]
            if real_prefers_left != pred_prefers_left:
                inversions += 1
    return {
        "labeled_items": len(ids),
        "pair_count": pairs,
        "inversions": inversions,
        "pairwise_inversion_rate": inversions / pairs if pairs else None,
    }


def spearman_for_ids(predicted_ids: Sequence[str], real_scores: Dict[str, float]) -> Optional[float]:
    ids = [cid for cid in predicted_ids if cid in real_scores]
    n = len(ids)
    if n < 2:
        return None
    pred_rank = {cid: idx + 1 for idx, cid in enumerate(ids)}
    real_order = sorted(ids, key=lambda cid: real_scores[cid], reverse=True)
    real_rank = {cid: idx + 1 for idx, cid in enumerate(real_order)}
    d2 = sum((pred_rank[cid] - real_rank[cid]) ** 2 for cid in ids)
    return 1.0 - (6.0 * d2) / (n * (n * n - 1.0))


def ndcg_at_k(predicted_ids: Sequence[str], real_scores: Dict[str, float], k: int) -> Optional[float]:
    ids = [cid for cid in predicted_ids[:k] if cid in real_scores]
    if not ids:
        return None
    real_order = sorted(real_scores, key=lambda cid: real_scores[cid], reverse=True)
    rel = {cid: max(0.0, float(len(real_order) - idx)) for idx, cid in enumerate(real_order)}

    def dcg(order: Sequence[str]) -> float:
        return sum(rel.get(cid, 0.0) / math.log2(idx + 2.0) for idx, cid in enumerate(order[:k]))

    denom = dcg(real_order)
    return dcg(predicted_ids) / denom if denom > 0.0 else None


def ranking_metrics(
    *,
    predicted_rows: Sequence[Dict[str, Any]],
    labels: Sequence[Dict[str, Any]],
    k: int,
) -> Dict[str, Any]:
    real_scores = {
        str(label.get("candidate_id")): safe_float(label.get("real_score"))
        for label in labels
        if label.get("candidate_id")
    }
    predicted_ids = [candidate_id(row) for row in predicted_rows[:k]]
    inv = pairwise_inversion_rate(predicted_ids, real_scores)
    real_top = set(sorted(real_scores, key=lambda cid: real_scores[cid], reverse=True)[:k])
    pred_top = set(predicted_ids[:k])
    overlap = len(real_top & pred_top) / max(1, min(k, len(real_top)))
    out = dict(inv)
    out.update(
        {
            "kendall_tau_distance": inv.get("pairwise_inversion_rate"),
            "spearman": spearman_for_ids(predicted_ids, real_scores),
            "ndcg": ndcg_at_k(predicted_ids, real_scores, k),
            "top_k_overlap": overlap,
            "label_coverage_in_predicted_topk": inv.get("labeled_items", 0) / max(1, k),
            "labeled_candidate_count": len(real_scores),
        }
    )
    return out


def evaluate_scenario(
    scenario: str,
    info: Dict[str, Any],
    *,
    limit_candidates: Optional[int] = None,
    extra_labels: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    scan_dir = Path(str(info.get("scan_dir") or DEFAULT_SCAN_DIRS[scenario]))
    ranking_pool, ranking_pool_source = load_ranking_pool(scan_dir)
    candidates = ranking_pool
    if limit_candidates:
        candidates = candidates[:limit_candidates]
    capability = load_capability(scan_dir)
    candidates = attach_robust_scores(candidates, capability=capability, scenario=scenario)
    raw_rank = sorted(candidates, key=lambda row: safe_float(row.get("score"), 0.0), reverse=True)
    full_candidates = candidates
    if any("result_bonect" in str(label.get("candidate") or "") for label in (info.get("labels") or [])):
        try:
            full_candidates = load_candidates(scan_dir)
        except OSError:
            full_candidates = candidates
    labels = match_labels(
        scenario=scenario,
        labels=info.get("labels") or [],
        candidates=full_candidates,
        raw_top=raw_rank,
    )
    extra_matched = match_extra_labels(
        scenario=scenario,
        labels=extra_labels or [],
        candidates=candidates,
        full_candidates=full_candidates,
    )
    labels.extend(extra_matched)
    matched_by_id: Dict[str, Dict[str, Any]] = {}
    for label in labels:
        cid = label.get("candidate_id")
        if not cid:
            continue
        # Prefer explicit extra labels for the next validation round; keep report labels otherwise.
        if cid not in matched_by_id or str(label.get("match_source")) == "extra_label":
            matched_by_id[str(cid)] = label
    matched_labels = list(matched_by_id.values())
    variant_results: Dict[str, Any] = {}
    robust_rankings: Dict[str, List[Dict[str, Any]]] = {}
    for variant in ROBUST_VARIANTS:
        ranking = sorted(candidates, key=lambda row: robust_sort_key(row, variant=variant), reverse=True)
        robust_rankings[variant] = ranking
        variant_results[variant] = {
            "top10": ranking_metrics(predicted_rows=ranking, labels=matched_labels, k=10),
            "top15": ranking_metrics(predicted_rows=ranking, labels=matched_labels, k=15),
        }
    raw_metrics = {
        "top10": ranking_metrics(predicted_rows=raw_rank, labels=matched_labels, k=10),
        "top15": ranking_metrics(predicted_rows=raw_rank, labels=matched_labels, k=15),
    }

    def variant_key(item: Tuple[str, Any]) -> Tuple[float, float, str]:
        top15 = item[1]["top15"]
        inv = top15.get("pairwise_inversion_rate")
        if inv is None:
            inv = 1.0
        ndcg = top15.get("ndcg")
        return (-float(inv), float(ndcg or 0.0), item[0])

    best_variant = max(variant_results.items(), key=variant_key)[0] if variant_results else DEFAULT_ROBUST_VARIANT
    selected_rank = robust_rankings.get(DEFAULT_ROBUST_VARIANT, [])
    best_rank = robust_rankings.get(best_variant, selected_rank)
    return {
        "scenario": scenario,
        "scan_dir": str(scan_dir),
        "candidate_count": int(safe_float((read_json(scan_dir / "scan_summary.json") or {}).get("successful_count"), len(full_candidates))),
        "ranking_pool_source": ranking_pool_source,
        "ranking_pool_count": len(candidates),
        "capability": capability,
        "label_coverage": {
            "report_label_count": len(info.get("labels") or []),
            "extra_label_count": len(extra_labels or []),
            "matched_extra_label_count": sum(
                1 for label in matched_labels if str(label.get("match_source")) == "extra_label"
            ),
            "matched_label_count": len(matched_labels),
            "matched_estimator_label_count": sum(
                1 for label in matched_labels if str(label.get("match_source")) == "estimator_top_rank"
            ),
            "matched_result_bonect_label_count": sum(
                1 for label in matched_labels if "result_bonect" in str(label.get("match_source"))
            ),
            "matched_nearest_result_label_count": sum(
                1 for label in matched_labels if str(label.get("match_source")) == "nearest_result_bonect_params"
            ),
            "matched_sources": sorted({str(label.get("match_source")) for label in matched_labels}),
            "sufficient_for_top10_pairwise": len(matched_labels) >= 10,
            "sufficient_for_top15_pairwise": len(matched_labels) >= 15,
        },
        "labels": labels,
        "raw_metrics": raw_metrics,
        "robust_metrics": variant_results.get(DEFAULT_ROBUST_VARIANT, {}),
        "offline_best_variant": best_variant,
        "offline_best_metrics": variant_results.get(best_variant, {}),
        "all_variant_metrics": variant_results,
        "top10_raw": summarize_ranking(raw_rank[:10], labels=matched_labels, variant=None),
        "top15_robust": summarize_ranking(selected_rank[:15], labels=matched_labels, variant=DEFAULT_ROBUST_VARIANT),
        "top15_offline_best": summarize_ranking(best_rank[:15], labels=matched_labels, variant=best_variant),
        "online_selected_variant": DEFAULT_ROBUST_VARIANT,
        "evaluated_variants": list(ROBUST_VARIANTS),
    }


def summarize_ranking(
    rows: Sequence[Dict[str, Any]],
    *,
    labels: Sequence[Dict[str, Any]],
    variant: Optional[str],
) -> List[Dict[str, Any]]:
    label_by_id = {str(label.get("candidate_id")): label for label in labels if label.get("candidate_id")}
    out: List[Dict[str, Any]] = []
    for rank, row in enumerate(rows, start=1):
        cid = candidate_id(row)
        robust = ((row.get("robust_scores") or {}).get(variant or "") or row.get("robust_ranking") or {})
        out.append(
            {
                "rank": rank,
                "candidate_id": cid,
                "candidate_index": row.get("candidate_index"),
                "score": row.get("score"),
                "robust_score": robust.get("robust_score") if variant else None,
                "real_score": (label_by_id.get(cid) or {}).get("real_score"),
                "label": (label_by_id.get(cid) or {}).get("candidate"),
                "params": row.get("params"),
                "estimated_resource": row.get("estimated_resource"),
                "plan_hash": row.get("plan_hash"),
            }
        )
    return out


def audit_feature_cache(feature_cache_dir: Path) -> Dict[str, Any]:
    files = sorted(p for p in feature_cache_dir.rglob("*") if p.is_file())
    parquet_files = [p for p in files if p.suffix == ".parquet"]
    json_files = [p for p in files if p.suffix == ".json"]
    joblib_files = [p for p in files if p.suffix == ".joblib"]
    data_dir = feature_cache_dir / "data"
    model_artifacts_dir = feature_cache_dir / "model_artifacts"
    pandas_available = False
    parquet_counts: Dict[str, Any] = {}
    try:
        import pandas as pd  # type: ignore

        pandas_available = True
        for pattern in [
            "h*_serial_features.parquet",
            "c*_5s_features.parquet",
            "ch*_5s_features.parquet",
            "h*_io_serial_features.parquet",
            "c*_io_5s_features.parquet",
            "ch*_io_5s_features.parquet",
        ]:
            rows = 0
            matched = list(data_dir.glob(pattern))
            target_cols = set()
            for path in matched:
                df = pd.read_parquet(path)
                rows += len(df)
                target_cols.update(c for c in df.columns if str(c).startswith("target"))
            parquet_counts[pattern] = {
                "file_count": len(matched),
                "row_count": rows,
                "target_columns": sorted(target_cols),
            }
    except Exception as exc:
        parquet_counts["inspection_error"] = str(exc)
    runtime_artifacts = {}
    for path in [
        ROOT / "config" / "generated" / "cpu_resource_models.json",
        ROOT / "config" / "generated" / "io_resource_models.json",
        ROOT / "config" / "generated" / "memory_resource_models.json",
    ]:
        data = read_json(path)
        runtime_artifacts[path.name] = {
            "path": str(path),
            "exists": path.exists(),
            "default_model_set": data.get("default_model_set"),
            "models": summarize_model_registry(data),
        }
    return {
        "feature_cache_dir": str(feature_cache_dir),
        "data_dir_exists": data_dir.exists(),
        "model_artifacts_dir_exists": model_artifacts_dir.exists(),
        "file_counts": {
            "total": len(files),
            "parquet": len(parquet_files),
            "json": len(json_files),
            "joblib": len(joblib_files),
        },
        "top_level_json_files": [str(p) for p in sorted(feature_cache_dir.glob("*.json"))],
        "pandas_available": pandas_available,
        "parquet_counts": parquet_counts,
        "runtime_artifacts": runtime_artifacts,
    }


def summarize_model_registry(data: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for model_name, model in (data.get("models") or {}).items():
        if not isinstance(model, dict):
            continue
        if isinstance(model.get("targets"), dict):
            out[model_name] = {
                target: summarize_one_model(target_model)
                for target, target_model in model.get("targets", {}).items()
                if isinstance(target_model, dict)
            }
        else:
            out[model_name] = summarize_one_model(model)
    return out


def summarize_one_model(model: Dict[str, Any]) -> Dict[str, Any]:
    metrics = model.get("metrics") if isinstance(model.get("metrics"), dict) else {}
    return {
        "model_type": model.get("model_type"),
        "artifact_path": model.get("artifact_path"),
        "feature_count": len(model.get("features") or []),
        "train": metrics.get("train"),
        "test": metrics.get("test"),
        "train_samples": metrics.get("train_samples"),
        "test_samples": metrics.get("test_samples"),
        "error_bound": model.get("error_bound"),
    }


def search_memory_artifacts() -> Dict[str, Any]:
    roots = [ROOT, Path("/home/anonymous")]
    matches: List[str] = []
    patterns = ["*memory*", "*mem*model*", "*memory*artifact*"]
    for root in roots:
        if not root.exists():
            continue
        for pattern in patterns:
            try:
                for path in root.rglob(pattern):
                    if len(matches) >= 300:
                        break
                    if path.is_file() or path.is_dir():
                        matches.append(str(path))
            except OSError:
                continue
    return {
        "searched_roots": [str(root) for root in roots],
        "match_count": len(matches),
        "sample_matches": sorted(set(matches))[:120],
    }


def audit_validation_label_sources(limit: int = 240) -> Dict[str, Any]:
    roots = [ROOT / "report", ROOT / "result", ROOT / "history"]
    pattern = r"estimator_top|result_bonect|XP-QPS|db_xact_tps|latency_p95|final_validation"
    paths: List[str] = []
    method = "rg"
    try:
        cmd = [
            "rg",
            "-l",
            pattern,
            "report",
            "result",
            "history",
            "-g",
            "*.md",
            "-g",
            "*.json",
        ]
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc.returncode in (0, 1):
            paths = [str((ROOT / line.strip()).resolve()) for line in proc.stdout.splitlines() if line.strip()]
        else:
            method = f"rg_failed:{proc.stderr.strip()[:200]}"
    except (OSError, ValueError) as exc:
        method = f"manual_fallback:{exc}"

    if not paths:
        method = "manual_fallback"
        for root in roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if len(paths) >= limit:
                    break
                if path.suffix not in {".md", ".json"} or not path.is_file():
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")[:20000]
                except OSError:
                    continue
                if re.search(pattern, text):
                    paths.append(str(path.resolve()))

    unique_paths = sorted(set(paths))
    current_report = str(DEFAULT_REPORT.resolve())
    current_exact = [p for p in unique_paths if p == current_report]
    integrated_reports = [p for p in unique_paths if "bonect_integrated_grid_real_validation" in p]
    generated_reports = [p for p in unique_paths if "bonect_estimator_robust_ranking_calibration" in p]
    reusable = [
        p
        for p in unique_paths
        if p not in generated_reports
        and (
            "bonect_integrated_grid_real_validation" in p
            or "/result/bonect/" in p
            or "bonect_reference_summary" in p
            or "iter_metrics" in p
            or "final_summary" in p
        )
    ]
    return {
        "searched_roots": [str(root) for root in roots],
        "method": method,
        "match_count": len(unique_paths),
        "current_validation_report_found": bool(current_exact),
        "integrated_validation_report_count": len(integrated_reports),
        "generated_calibration_report_count": len(generated_reports),
        "potential_reusable_file_count": len(reusable),
        "sample_potential_reusable_files": reusable[:limit],
        "note": (
            "Many historical files contain validation metrics, but most belong to different run directories, "
            "workloads, VM profiles, or non-unified candidate units. The evaluator therefore does not "
            "merge them into current top-k ranking labels unless candidate parameters and unified scan context match."
        ),
    }


def _find_key_recursive(value: Any, key: str) -> Iterable[Any]:
    if isinstance(value, dict):
        for item_key, item_value in value.items():
            if item_key == key:
                yield item_value
            yield from _find_key_recursive(item_value, key)
    elif isinstance(value, list):
        for item in value:
            yield from _find_key_recursive(item, key)


def audit_online_ranking_config(limit: int = 120) -> Dict[str, Any]:
    config_roots = [ROOT / "config"]
    overrides: List[Dict[str, Any]] = []
    candidate_gate_files = 0
    checked_json = 0
    for root in config_roots:
        if not root.exists():
            continue
        for path in root.rglob("*.json"):
            checked_json += 1
            data = read_json(path)
            if not data:
                continue
            gates = list(_find_key_recursive(data, "candidate_gate"))
            if gates:
                candidate_gate_files += 1
            for gate in gates:
                if not isinstance(gate, dict):
                    continue
                robust = gate.get("robust_ranking")
                if isinstance(robust, dict):
                    variant = robust.get("variant") or robust.get("selected_variant")
                    overrides.append(
                        {
                            "path": str(path),
                            "target_resource": gate.get("target_resource") or gate.get("bottleneck_resource"),
                            "enabled": robust.get("enabled", True),
                            "variant": variant,
                            "uses_default_variant": not bool(variant),
                        }
                    )
    explicit_non_default = [
        item
        for item in overrides
        if item.get("variant") and item.get("variant") != DEFAULT_ROBUST_VARIANT
    ]
    return {
        "default_variant": DEFAULT_ROBUST_VARIANT,
        "available_variants": list(ROBUST_VARIANTS),
        "checked_json_files": checked_json,
        "candidate_gate_file_count": candidate_gate_files,
        "robust_ranking_override_count": len(overrides),
        "explicit_non_default_override_count": len(explicit_non_default),
        "sample_overrides": overrides[:limit],
        "sample_explicit_non_default_overrides": explicit_non_default[:limit],
        "online_path_note": (
            "parameter_optimizer.py uses candidate_gate.robust_ranking.variant when present; "
            "otherwise it falls back to bonect_robust_ranking.DEFAULT_VARIANT."
        ),
    }


LABEL_LEAKAGE_TOKENS = [
    "real_score",
    "known_real_score",
    "real_validation",
    "candidate_final_validation",
    "final_validation",
    "XP-QPS",
    "XP-TPS",
    "db_xact_tps",
    "latency_p95",
    "latency_p99",
]


def _scan_token_hits(path: Path, tokens: Sequence[str]) -> Dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        return {"path": str(path), "exists": path.exists(), "error": str(exc), "token_counts": {}, "line_hits": []}
    counts = {token: text.count(token) for token in tokens}
    line_hits: List[Dict[str, Any]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        hit_tokens = [token for token in tokens if token in line]
        if hit_tokens:
            line_hits.append(
                {
                    "line": lineno,
                    "tokens": hit_tokens,
                    "text": line.strip()[:180],
                }
            )
    return {
        "path": str(path),
        "exists": path.exists(),
        "token_counts": counts,
        "total_hits": sum(counts.values()),
        "line_hits": line_hits[:80],
    }


def _scan_line_window(path: Path, *, start: int, end: int, tokens: Sequence[str]) -> Dict[str, Any]:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as exc:
        return {
            "path": str(path),
            "start_line": start,
            "end_line": end,
            "error": str(exc),
            "token_counts": {},
            "line_hits": [],
        }
    selected = lines[max(0, start - 1) : min(len(lines), end)]
    text = "\n".join(selected)
    counts = {token: text.count(token) for token in tokens}
    hits = []
    for offset, line in enumerate(selected, start=start):
        hit_tokens = [token for token in tokens if token in line]
        if hit_tokens:
            hits.append({"line": offset, "tokens": hit_tokens, "text": line.strip()[:180]})
    return {
        "path": str(path),
        "start_line": start,
        "end_line": end,
        "token_counts": counts,
        "total_hits": sum(counts.values()),
        "line_hits": hits,
    }


def audit_label_leakage_boundary() -> Dict[str, Any]:
    formula_path = ROOT / "bonect_robust_ranking.py"
    optimizer_path = ROOT / "parameter_optimizer.py"
    scan_path = ROOT / "shell" / "bonect_full_grid_estimator_scan.py"
    evaluator_path = ROOT / "shell" / "evaluate_bonect_estimator_ranking.py"

    formula_scan = _scan_token_hits(formula_path, LABEL_LEAKAGE_TOKENS)
    optimizer_window = _scan_line_window(
        optimizer_path,
        start=720,
        end=1150,
        tokens=LABEL_LEAKAGE_TOKENS,
    )
    scan_export_window = _scan_line_window(
        scan_path,
        start=1760,
        end=1885,
        tokens=LABEL_LEAKAGE_TOKENS,
    )
    offline_scan = _scan_token_hits(evaluator_path, LABEL_LEAKAGE_TOKENS)
    online_boundary_ok = (
        int(formula_scan.get("total_hits") or 0) == 0
        and int(optimizer_window.get("total_hits") or 0) == 0
        and int(scan_export_window.get("total_hits") or 0) == 0
    )
    return {
        "online_boundary_ok": online_boundary_ok,
        "sensitive_tokens": LABEL_LEAKAGE_TOKENS,
        "online_formula_file": formula_scan,
        "online_integration_windows": [
            {
                **optimizer_window,
                "classification": (
                    "online robust candidate selection window; expected to call robust_score_record "
                    "with predicted-resource records and capability only"
                ),
            },
            {
                **scan_export_window,
                "classification": (
                    "offline full-grid export ranking window; expected to attach robust scores "
                    "from estimator records only"
                ),
            },
        ],
        "offline_label_reader": {
            **offline_scan,
            "classification": (
                "offline evaluator and manifest builder; this is the only path in this task that "
                "parses real validation labels or --extra-labels"
            ),
        },
        "note": (
            "parameter_optimizer.py contains separate final-validation and feedback-loop code, so whole-file "
            "validation token hits are expected. The checked robust-ranking integration window is isolated from "
            "those runtime validation paths."
        ),
    }


def build_goal_gate(
    scenarios: Dict[str, Any],
    *,
    threshold: float = 0.10,
    extra_label_resolution_audit: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    per_scenario: Dict[str, Any] = {}
    partial_pass = True
    full_pass = True
    label_conflict_pass = True
    for scenario in SCENARIOS:
        result = scenarios.get(scenario) or {}
        coverage = result.get("label_coverage") or {}
        resolution_info = ((extra_label_resolution_audit or {}).get("per_scenario") or {}).get(scenario) or {}
        conflict_groups = int(safe_float(resolution_info.get("conflict_candidate_groups"), 0.0))
        robust = result.get("robust_metrics") or {}
        top10 = robust.get("top10") or {}
        top15 = robust.get("top15") or {}
        top10_inv = top10.get("pairwise_inversion_rate")
        top15_inv = top15.get("pairwise_inversion_rate")
        top10_labeled = int(safe_float(top10.get("labeled_items"), 0.0))
        top15_labeled = int(safe_float(top15.get("labeled_items"), 0.0))
        partial_ok = (
            top10_inv is not None
            and top15_inv is not None
            and float(top10_inv) <= threshold
            and float(top15_inv) <= threshold
        )
        coverage_ok = bool(top10_labeled >= 10 and top15_labeled >= 15)
        conflict_ok = conflict_groups == 0
        full_ok = bool(partial_ok and coverage_ok and conflict_ok)
        reasons: List[str] = []
        if top10_inv is None:
            reasons.append("top10 has no labeled comparable pairs")
        elif float(top10_inv) > threshold:
            reasons.append(f"top10 inversion {top10_inv:.6g} exceeds threshold {threshold:.6g}")
        if top15_inv is None:
            reasons.append("top15 has no labeled comparable pairs")
        elif float(top15_inv) > threshold:
            reasons.append(f"top15 inversion {top15_inv:.6g} exceeds threshold {threshold:.6g}")
        if not coverage_ok:
            reasons.append(
                "insufficient predicted top-k label coverage "
                f"(matched={coverage.get('matched_label_count')}, "
                f"top10_labeled={top10_labeled}/10, "
                f"top15_labeled={top15_labeled}/15)"
            )
        if not conflict_ok:
            reasons.append(f"conflicting extra-label groups present ({conflict_groups})")
        partial_pass = partial_pass and partial_ok
        full_pass = full_pass and full_ok
        label_conflict_pass = label_conflict_pass and conflict_ok
        per_scenario[scenario] = {
            "partial_pass": partial_ok,
            "full_pass": full_ok,
            "coverage_pass": coverage_ok,
            "label_conflict_pass": conflict_ok,
            "conflict_candidate_groups": conflict_groups,
            "top10_pairwise_inversion_rate": top10_inv,
            "top15_pairwise_inversion_rate": top15_inv,
            "top10_labeled_items": top10_labeled,
            "top15_labeled_items": top15_labeled,
            "matched_label_count": coverage.get("matched_label_count"),
            "matched_extra_label_count": coverage.get("matched_extra_label_count"),
            "reasons": reasons,
        }
    return {
        "threshold": threshold,
        "partial_pass": partial_pass,
        "full_pass": full_pass,
        "label_conflict_pass": label_conflict_pass,
        "strict_goal_pass": full_pass,
        "per_scenario": per_scenario,
    }


def build_completion_audit(payload: Dict[str, Any]) -> Dict[str, Any]:
    gate = payload.get("goal_gate") or {}
    manifest_summary = payload.get("validation_manifest_summary") or {}
    leakage_ok = bool(payload.get("online_no_candidate_real_label_leakage"))
    report_json = payload.get("report_json")
    report_md = payload.get("report_md")
    pending_count = int(safe_float(manifest_summary.get("pending_queue_count"), 0.0))

    def requirement(
        req_id: str,
        text: str,
        proved: bool,
        evidence: Sequence[str],
        missing: Sequence[str] = (),
    ) -> Dict[str, Any]:
        return {
            "id": req_id,
            "requirement": text,
            "status": "proved" if proved else "incomplete",
            "proved": bool(proved),
            "evidence": [item for item in evidence if item],
            "missing_evidence": [item for item in missing if item],
        }

    per_scenario_missing: List[str] = []
    per_scenario_inversion_missing: List[str] = []
    for scenario in SCENARIOS:
        info = ((gate.get("per_scenario") or {}).get(scenario) or {})
        if not bool(info.get("coverage_pass")):
            per_scenario_missing.append(
                f"{scenario}: top10_labeled={info.get('top10_labeled_items')}/10, "
                f"top15_labeled={info.get('top15_labeled_items')}/15"
            )
        if not bool(info.get("full_pass")):
            reasons = "; ".join(info.get("reasons") or [])
            per_scenario_inversion_missing.append(f"{scenario}: {reasons}")

    requirements = [
        requirement(
            "robust_online_code_landed",
            "Online robust ranking code is landed and configured without candidate real-label inputs.",
            (ROOT / "bonect_robust_ranking.py").exists()
            and (ROOT / "parameter_optimizer.py").exists()
            and payload.get("online_selected_variant") == DEFAULT_ROBUST_VARIANT
            and leakage_ok,
            [
                "bonect_robust_ranking.py",
                "parameter_optimizer.py",
                f"online_selected_variant={payload.get('online_selected_variant')}",
                f"online_no_candidate_real_label_leakage={leakage_ok}",
            ],
            [] if leakage_ok else ["online robust-ranking boundary still has candidate real-label token hits"],
        ),
        requirement(
            "offline_evaluator_and_report_landed",
            "Offline evaluator, validation manifest, pending queue, and Markdown/JSON report are generated.",
            (ROOT / "shell" / "evaluate_bonect_estimator_ranking.py").exists()
            and bool(report_json)
            and bool(report_md)
            and bool(payload.get("validation_manifest_json"))
            and bool(payload.get("pending_validation_queue_jsonl")),
            [
                "shell/evaluate_bonect_estimator_ranking.py",
                str(report_json or ""),
                str(report_md or ""),
                str(payload.get("validation_manifest_json") or ""),
                str(payload.get("pending_validation_queue_jsonl") or ""),
            ],
        ),
        requirement(
            "same_round_real_label_leakage_guard",
            "Candidate real pressure results are confined to offline evaluation or a later formula-selection round.",
            leakage_ok,
            [
                f"online_no_candidate_real_label_leakage={leakage_ok}",
                str((payload.get("label_leakage_boundary_audit") or {}).get("note") or ""),
            ],
            [] if leakage_ok else ["label leakage boundary audit did not pass"],
        ),
        requirement(
            "top10_top15_real_label_coverage",
            "Every cpu/memory/io/none robust top10 and top15 candidate has a real validation label.",
            bool(gate.get("full_pass")),
            [
                f"pending_queue_count={pending_count}",
                f"per_scenario_missing={manifest_summary.get('per_scenario_missing')}",
            ],
            per_scenario_missing,
        ),
        requirement(
            "pairwise_inversion_target",
            "For cpu, memory, io, and none, full-label robust top10/top15 pairwise inversion rate is <= threshold.",
            bool(gate.get("strict_goal_pass")),
            [
                f"threshold={gate.get('threshold')}",
                f"strict_goal_pass={gate.get('strict_goal_pass')}",
            ],
            per_scenario_inversion_missing,
        ),
    ]
    return {
        "overall_complete": all(bool(item.get("proved")) for item in requirements),
        "strict_goal_pass": bool(gate.get("strict_goal_pass")),
        "pending_validation_count": pending_count,
        "requirements": requirements,
        "note": (
            "Completion requires proof for every requirement. Partial inversion metrics on already-labeled rows "
            "are not enough when robust top10/top15 coverage is incomplete."
        ),
    }


def build_next_round_recommendation(payload: Dict[str, Any]) -> Dict[str, Any]:
    gate = payload.get("goal_gate") or {}
    completion = payload.get("completion_audit") or {}
    ready = bool(gate.get("strict_goal_pass")) and bool(completion.get("overall_complete"))
    scenario_rows: Dict[str, Any] = {}
    eligible_votes: Dict[str, int] = {}
    exploratory_votes: Dict[str, int] = {}
    for scenario in SCENARIOS:
        result = (payload.get("scenarios") or {}).get(scenario) or {}
        scenario_gate = ((gate.get("per_scenario") or {}).get(scenario) or {})
        best_variant = result.get("offline_best_variant") or DEFAULT_ROBUST_VARIANT
        exploratory_votes[best_variant] = exploratory_votes.get(best_variant, 0) + 1
        if scenario_gate.get("full_pass"):
            eligible_votes[best_variant] = eligible_votes.get(best_variant, 0) + 1
        current_top15 = ((result.get("robust_metrics") or {}).get("top15") or {})
        best_top15 = ((result.get("offline_best_metrics") or {}).get("top15") or {})
        scenario_rows[scenario] = {
            "coverage_pass": scenario_gate.get("coverage_pass"),
            "full_pass": scenario_gate.get("full_pass"),
            "online_selected_variant": result.get("online_selected_variant") or DEFAULT_ROBUST_VARIANT,
            "offline_best_variant": best_variant,
            "online_top15_pairwise_inversion_rate": current_top15.get("pairwise_inversion_rate"),
            "offline_best_top15_pairwise_inversion_rate": best_top15.get("pairwise_inversion_rate"),
            "online_top15_labeled_items": current_top15.get("labeled_items"),
            "offline_best_top15_labeled_items": best_top15.get("labeled_items"),
            "offline_best_top15_ndcg": best_top15.get("ndcg"),
            "reasons": scenario_gate.get("reasons") or [],
        }

    def vote_winner(votes: Dict[str, int]) -> Optional[str]:
        if not votes:
            return None
        return sorted(votes.items(), key=lambda item: (item[1], item[0]), reverse=True)[0][0]

    incomplete_requirements = [
        {
            "id": item.get("id"),
            "missing_evidence": item.get("missing_evidence") or [],
        }
        for item in completion.get("requirements") or []
        if not item.get("proved")
    ]
    return {
        "generated_at": payload.get("generated_at"),
        "source_report_json": payload.get("report_json"),
        "source_validation_report": payload.get("validation_report"),
        "online_selected_variant": payload.get("online_selected_variant"),
        "status": "ready_for_next_round" if ready else "pending_full_validation",
        "ready_for_next_round": ready,
        "apply_to_online_now": False,
        "recommended_default_variant": vote_winner(eligible_votes) if ready else None,
        "exploratory_best_variant_from_current_labels": vote_winner(exploratory_votes),
        "eligible_variant_votes": eligible_votes,
        "exploratory_variant_votes": exploratory_votes,
        "scenario_recommendations": scenario_rows,
        "incomplete_requirements": incomplete_requirements,
        "guardrail": (
            "This artifact is produced by the offline evaluator. It must not change the same-round online "
            "ranking input. Apply a recommended variant only in a later round after full validation coverage."
        ),
    }


def write_next_round_recommendation_markdown(path: Path, recommendation: Dict[str, Any]) -> None:
    lines = [
        "# Bonect Next-Round Robust Ranking Recommendation",
        "",
        f"- generated_at: `{recommendation.get('generated_at')}`",
        f"- source_report_json: `{recommendation.get('source_report_json')}`",
        f"- online_selected_variant: `{recommendation.get('online_selected_variant')}`",
        f"- status: `{recommendation.get('status')}`",
        f"- ready_for_next_round: `{recommendation.get('ready_for_next_round')}`",
        f"- apply_to_online_now: `{recommendation.get('apply_to_online_now')}`",
        f"- recommended_default_variant: `{recommendation.get('recommended_default_variant')}`",
        f"- exploratory_best_variant_from_current_labels: `{recommendation.get('exploratory_best_variant_from_current_labels')}`",
        f"- guardrail: {recommendation.get('guardrail')}",
        "",
        "## Scenario Recommendation",
        "",
        "| scenario | full_pass | coverage_pass | online_variant | offline_best_variant | online@15 inv | best@15 inv | online@15 labels | reasons |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for scenario in SCENARIOS:
        info = ((recommendation.get("scenario_recommendations") or {}).get(scenario) or {})
        reasons = "; ".join(str(item) for item in (info.get("reasons") or []))
        lines.append(
            f"| {scenario} | {info.get('full_pass')} | {info.get('coverage_pass')} | "
            f"{info.get('online_selected_variant')} | {info.get('offline_best_variant')} | "
            f"{fmt_metric(info.get('online_top15_pairwise_inversion_rate'))} | "
            f"{fmt_metric(info.get('offline_best_top15_pairwise_inversion_rate'))} | "
            f"{info.get('online_top15_labeled_items')} | `{reasons}` |"
        )
    lines.extend(
        [
            "",
            "## Incomplete Requirements",
            "",
            "| requirement | missing evidence |",
            "| --- | --- |",
        ]
    )
    for item in recommendation.get("incomplete_requirements") or []:
        missing = "; ".join(str(value) for value in (item.get("missing_evidence") or []))
        lines.append(f"| `{item.get('id')}` | `{missing}` |")
    write_text(path, "\n".join(lines) + "\n")


def write_markdown_report(path: Path, payload: Dict[str, Any]) -> None:
    lines = [
        "# Bonect Estimator Robust Ranking Calibration",
        "",
        f"- generated_at: `{payload.get('generated_at')}`",
        f"- online_selected_variant: `{payload.get('online_selected_variant')}`",
        "- online ranking inputs: predicted_resource, train/test residual bounds, baseline residual/anchor bounds, scenario uncertainty, plan confidence, plateau tie-break",
        "- candidate real replay labels are used only by this offline evaluator.",
        f"- selected robust formula: `{payload.get('online_selected_variant')}` uses point score plus scenario-specific plateau corrections over cost parameters; no real validation metric is read at online ranking time.",
        f"- validation_manifest_json: `{payload.get('validation_manifest_json')}`",
        f"- validation_manifest_md: `{payload.get('validation_manifest_md')}`",
        f"- pending_validation_queue_jsonl: `{payload.get('pending_validation_queue_jsonl')}`",
        f"- next_round_recommendation_json: `{payload.get('next_round_recommendation_json')}`",
        f"- next_round_recommendation_md: `{payload.get('next_round_recommendation_md')}`",
        f"- extra_label_files: `{payload.get('extra_label_files')}`",
        f"- resolved_extra_label_files: `{payload.get('resolved_extra_label_files')}`",
        f"- extra_label_counts: `{payload.get('extra_label_counts')}`",
        "",
        "## Code / Ranking Changes",
        "",
        "- Added `bonect_robust_ranking.py` with point, bound-based, uncertainty-penalized, adaptive, and pairwise surrogate variants.",
        "- Integrated robust scores into `parameter_optimizer.py` candidate ranking and `shell/bonect_full_grid_estimator_scan.py` robust top-k export.",
        "- Added plan-tree aggregate features in `plan_resource_estimator.py` and p50/p90 residual metrics in `resource_model_retrainer.py`.",
        "- Added this offline replay evaluator with feature-cache, runtime-artifact, memory-artifact, raw-ranking, robust-ranking, and variant comparison output.",
        "",
        "## Feature Cache / Artifact Audit",
        "",
    ]
    audit = payload.get("feature_cache_audit") or {}
    lines.extend(
        [
            f"- feature_cache_dir: `{audit.get('feature_cache_dir')}`",
            f"- data_dir_exists: `{audit.get('data_dir_exists')}`",
            f"- model_artifacts_dir_exists: `{audit.get('model_artifacts_dir_exists')}`",
            f"- file_counts: `{audit.get('file_counts')}`",
            f"- pandas_available_for_parquet_inspection: `{audit.get('pandas_available')}`",
            "",
            "### Training Data Files",
            "",
            "| pattern | file_count | row_count | target_columns |",
            "| --- | ---: | ---: | --- |",
        ]
    )
    parquet_counts = audit.get("parquet_counts") or {}
    for pattern, info in parquet_counts.items():
        if not isinstance(info, dict):
            continue
        lines.append(
            f"| `{pattern}` | {info.get('file_count')} | {info.get('row_count')} | "
            f"`{', '.join(info.get('target_columns') or [])}` |"
        )
    if "inspection_error" in parquet_counts:
        lines.append(f"- parquet inspection error: `{parquet_counts.get('inspection_error')}`")
    lines.extend(
        [
            "",
            "### Runtime Model Artifacts",
            "",
            "| artifact | model | target | type | train_samples | test_samples | test_mae | test_rmse | test_rel_p50 | test_rel_p90 | test_rel_p95 | test_rel_max | test_r2 |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for artifact, info in (audit.get("runtime_artifacts") or {}).items():
        for row in flatten_artifact_models(info.get("models") or {}):
            test = row.get("test") or {}
            lines.append(
                f"| `{artifact}` | `{row.get('model')}` | `{row.get('target') or ''}` | `{row.get('model_type')}` | "
                f"{row.get('train_samples')} | {row.get('test_samples')} | {fmt_metric(test.get('mae'))} | "
                f"{fmt_metric(test.get('rmse'))} | {fmt_metric(test.get('relative_error_p50'))} | "
                f"{fmt_metric(test.get('relative_error_p90'))} | {fmt_metric(test.get('relative_error_p95'))} | "
                f"{fmt_metric(test.get('relative_error_max'))} | {fmt_metric(test.get('r2'))} |"
            )
    lines.extend(
        [
            "",
            "## Memory Search",
            "",
            f"- searched_roots: `{(payload.get('memory_artifact_search') or {}).get('searched_roots')}`",
            f"- match_count: `{(payload.get('memory_artifact_search') or {}).get('match_count')}`",
            f"- sample_matches: `{(payload.get('memory_artifact_search') or {}).get('sample_matches', [])[:20]}`",
            "",
        ]
    )
    label_audit = payload.get("validation_label_source_audit") or {}
    lines.extend(
        [
            "## Validation Label Source Audit",
            "",
            f"- searched_roots: `{label_audit.get('searched_roots')}`",
            f"- method: `{label_audit.get('method')}`",
            f"- match_count: `{label_audit.get('match_count')}`",
            f"- current_validation_report_found: `{label_audit.get('current_validation_report_found')}`",
            f"- integrated_validation_report_count: `{label_audit.get('integrated_validation_report_count')}`",
            f"- potential_reusable_file_count: `{label_audit.get('potential_reusable_file_count')}`",
            f"- note: {label_audit.get('note')}",
            f"- sample_potential_reusable_files: `{label_audit.get('sample_potential_reusable_files', [])[:20]}`",
            "",
        ]
    )
    extra_resolution = payload.get("extra_label_resolution_audit") or {}
    lines.extend(
        [
            "## Extra Label Resolution Audit",
            "",
            f"- method: `{extra_resolution.get('method')}`",
            f"- total_extra_label_records: `{extra_resolution.get('total_extra_label_records')}`",
            f"- total_duplicate_candidate_groups: `{extra_resolution.get('total_duplicate_candidate_groups')}`",
            f"- total_conflict_candidate_groups: `{extra_resolution.get('total_conflict_candidate_groups')}`",
            "",
            "| scenario | extra records | matched candidates | duplicate groups | conflict groups |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for scenario in SCENARIOS:
        info = ((extra_resolution.get("per_scenario") or {}).get(scenario) or {})
        lines.append(
            f"| {scenario} | {info.get('extra_label_records')} | {info.get('matched_candidate_count')} | "
            f"{info.get('duplicate_candidate_groups')} | {info.get('conflict_candidate_groups')} |"
        )
    lines.append("")
    exact_label_audit = payload.get("exact_reusable_label_audit") or {}
    lines.extend(
        [
            "## Exact Reusable Label Audit",
            "",
            f"- searched_file_count: `{exact_label_audit.get('searched_file_count')}`",
            f"- method: `{exact_label_audit.get('method')}`",
            f"- total_external_match_rows: `{exact_label_audit.get('total_external_match_rows')}`",
            f"- total_new_external_match_rows: `{exact_label_audit.get('total_new_external_match_rows')}`",
            f"- total_conflict_rows: `{exact_label_audit.get('total_conflict_rows')}`",
            f"- note: {exact_label_audit.get('note')}",
            "",
            "| scenario | top15 rows | external matches | new external matches | conflicts |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for scenario in SCENARIOS:
        info = ((exact_label_audit.get("per_scenario") or {}).get(scenario) or {})
        lines.append(
            f"| {scenario} | {info.get('top15_rows')} | {info.get('external_match_rows')} | "
            f"{info.get('new_external_match_rows')} | {info.get('conflict_rows')} |"
        )
    lines.append("")
    hist_param_audit = payload.get("historical_param_occurrence_audit") or {}
    lines.extend(
        [
            "## Historical Parameter Occurrence Audit",
            "",
            f"- searched_file_count: `{hist_param_audit.get('searched_file_count')}`",
            f"- scanned_json_record_count: `{hist_param_audit.get('scanned_json_record_count')}`",
            f"- param_record_count: `{hist_param_audit.get('param_record_count')}`",
            f"- method: `{hist_param_audit.get('method')}`",
            f"- total_rows_with_occurrences: `{hist_param_audit.get('total_rows_with_occurrences')}`",
            f"- total_occurrence_count: `{hist_param_audit.get('total_occurrence_count')}`",
            f"- note: {hist_param_audit.get('note')}",
            "",
            "| scenario | top15 rows | rows with historical occurrence | occurrence count |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for scenario in SCENARIOS:
        info = ((hist_param_audit.get("per_scenario") or {}).get(scenario) or {})
        lines.append(
            f"| {scenario} | {info.get('top15_rows')} | {info.get('rows_with_occurrences')} | "
            f"{info.get('occurrence_count')} |"
        )
    lines.append("")
    online_audit = payload.get("online_ranking_config_audit") or {}
    lines.extend(
        [
            "## Online Ranking Config Audit",
            "",
            f"- default_variant: `{online_audit.get('default_variant')}`",
            f"- available_variants: `{online_audit.get('available_variants')}`",
            f"- checked_json_files: `{online_audit.get('checked_json_files')}`",
            f"- candidate_gate_file_count: `{online_audit.get('candidate_gate_file_count')}`",
            f"- robust_ranking_override_count: `{online_audit.get('robust_ranking_override_count')}`",
            f"- explicit_non_default_override_count: `{online_audit.get('explicit_non_default_override_count')}`",
            f"- online_path_note: {online_audit.get('online_path_note')}",
            f"- sample_explicit_non_default_overrides: `{online_audit.get('sample_explicit_non_default_overrides', [])[:20]}`",
            "",
        ]
    )
    leakage_audit = payload.get("label_leakage_boundary_audit") or {}
    formula_scan = leakage_audit.get("online_formula_file") or {}
    offline_reader = leakage_audit.get("offline_label_reader") or {}
    lines.extend(
        [
            "## Label Leakage Boundary Audit",
            "",
            f"- online_boundary_ok: `{leakage_audit.get('online_boundary_ok')}`",
            f"- sensitive_tokens: `{leakage_audit.get('sensitive_tokens')}`",
            f"- online_formula_file: `{formula_scan.get('path')}`, total_hits=`{formula_scan.get('total_hits')}`",
            f"- offline_label_reader: `{offline_reader.get('path')}`, total_hits=`{offline_reader.get('total_hits')}`",
            f"- note: {leakage_audit.get('note')}",
            "",
            "| checked path | line window | total label-token hits | classification |",
            "| --- | --- | ---: | --- |",
        ]
    )
    lines.append(
        f"| `{formula_scan.get('path')}` | whole file | {formula_scan.get('total_hits')} | "
        "`online robust scoring formula; should not read candidate real labels` |"
    )
    for window in leakage_audit.get("online_integration_windows") or []:
        lines.append(
            f"| `{window.get('path')}` | {window.get('start_line')}-{window.get('end_line')} | "
            f"{window.get('total_hits')} | `{window.get('classification')}` |"
        )
    lines.append(
        f"| `{offline_reader.get('path')}` | whole file | {offline_reader.get('total_hits')} | "
        "`offline evaluator only; allowed to parse validation labels` |"
    )
    lines.append("")
    completion = payload.get("completion_audit") or {}
    lines.extend(
        [
            "## Completion Audit",
            "",
            f"- overall_complete: `{completion.get('overall_complete')}`",
            f"- strict_goal_pass: `{completion.get('strict_goal_pass')}`",
            f"- pending_validation_count: `{completion.get('pending_validation_count')}`",
            f"- note: {completion.get('note')}",
            "",
            "| requirement | status | evidence | missing evidence |",
            "| --- | --- | --- | --- |",
        ]
    )
    for item in completion.get("requirements") or []:
        evidence = "; ".join(str(value) for value in (item.get("evidence") or []))
        missing = "; ".join(str(value) for value in (item.get("missing_evidence") or []))
        lines.append(
            f"| `{item.get('id')}` | `{item.get('status')}` | "
            f"`{evidence}` | `{missing}` |"
        )
    lines.append("")
    recommendation = payload.get("next_round_recommendation") or {}
    lines.extend(
        [
            "## Next-Round Formula Recommendation",
            "",
            f"- status: `{recommendation.get('status')}`",
            f"- ready_for_next_round: `{recommendation.get('ready_for_next_round')}`",
            f"- apply_to_online_now: `{recommendation.get('apply_to_online_now')}`",
            f"- recommended_default_variant: `{recommendation.get('recommended_default_variant')}`",
            f"- exploratory_best_variant_from_current_labels: `{recommendation.get('exploratory_best_variant_from_current_labels')}`",
            f"- guardrail: {recommendation.get('guardrail')}",
            f"- recommendation_json: `{payload.get('next_round_recommendation_json')}`",
            f"- recommendation_md: `{payload.get('next_round_recommendation_md')}`",
            "",
        ]
    )
    gate = payload.get("goal_gate") or {}
    lines.extend(
        [
            "## Goal Gate",
            "",
            f"- threshold: `{gate.get('threshold')}`",
            f"- partial_pass: `{gate.get('partial_pass')}`",
            f"- full_pass: `{gate.get('full_pass')}`",
            f"- label_conflict_pass: `{gate.get('label_conflict_pass')}`",
            f"- strict_goal_pass: `{gate.get('strict_goal_pass')}`",
            "",
            "| scenario | partial_pass | full_pass | coverage_pass | label_conflict_pass | conflicts | top10_labeled | top15_labeled | top10_inv | top15_inv | reasons |",
            "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for scenario, info in (gate.get("per_scenario") or {}).items():
        lines.append(
            f"| {scenario} | {info.get('partial_pass')} | {info.get('full_pass')} | "
            f"{info.get('coverage_pass')} | {info.get('label_conflict_pass')} | "
            f"{info.get('conflict_candidate_groups')} | {info.get('top10_labeled_items')} | "
            f"{info.get('top15_labeled_items')} | "
            f"{fmt_metric(info.get('top10_pairwise_inversion_rate'))} | "
            f"{fmt_metric(info.get('top15_pairwise_inversion_rate'))} | "
            f"`{'; '.join(info.get('reasons') or [])}` |"
        )
    lines.append("")
    lines.append("## Scenario Metrics")
    lines.append("")
    lines.append("| scenario | labels | matched | raw@10 inv | robust@10 inv | raw@15 inv | robust@15 inv | offline_best_variant | offline_best@15 inv | partial <=10%? | full top-k verified? |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- | --- |")
    for scenario, result in (payload.get("scenarios") or {}).items():
        coverage = result.get("label_coverage") or {}
        raw10 = ((result.get("raw_metrics") or {}).get("top10") or {}).get("pairwise_inversion_rate")
        rob10 = ((result.get("robust_metrics") or {}).get("top10") or {}).get("pairwise_inversion_rate")
        raw15 = ((result.get("raw_metrics") or {}).get("top15") or {}).get("pairwise_inversion_rate")
        rob15 = ((result.get("robust_metrics") or {}).get("top15") or {}).get("pairwise_inversion_rate")
        rob10_labeled = int(safe_float(((result.get("robust_metrics") or {}).get("top10") or {}).get("labeled_items"), 0.0))
        rob15_labeled = int(safe_float(((result.get("robust_metrics") or {}).get("top15") or {}).get("labeled_items"), 0.0))
        best15 = ((result.get("offline_best_metrics") or {}).get("top15") or {}).get("pairwise_inversion_rate")
        partial_reached = rob10 is not None and rob15 is not None and rob10 <= 0.10 and rob15 <= 0.10
        scenario_gate = ((payload.get("goal_gate") or {}).get("per_scenario") or {}).get(scenario) or {}
        full_verified = bool(scenario_gate.get("full_pass"))
        lines.append(
            f"| {scenario} | {coverage.get('report_label_count')} | {coverage.get('matched_label_count')} | "
            f"{fmt_metric(raw10)} | {fmt_metric(rob10)} | {fmt_metric(raw15)} | {fmt_metric(rob15)} | "
            f"{result.get('offline_best_variant')} | {fmt_metric(best15)} | {partial_reached} | {full_verified} |"
        )
    lines.extend(
        [
            "",
            "## Coverage Note",
            "",
            "The current integrated validation report provides four labels per scenario: estimator top3 plus one result/bonect comparison point. "
            "The unified top-k ranking pool can compare the estimator labels directly. The result/bonect comparison point is still reported, "
            "but historical full candidates used raw bytes/byte-per-second units, so those points are not used as proof of unified top-k ordering unless they appear in the unified ranking pool. "
            "The evaluator therefore reports top10/top15 partial-label metrics and requires every robust predicted top10/top15 row to have a real label before full top-k verification can pass. "
            "After the manifest candidates are validated, pass the filled JSON/JSONL with `--extra-labels` to recompute full metrics.",
            "",
        ]
    )
    for scenario, result in (payload.get("scenarios") or {}).items():
        lines.append(f"## {scenario}")
        lines.append("")
        lines.append(f"- scan_dir: `{result.get('scan_dir')}`")
        lines.append(f"- candidate_count: `{result.get('candidate_count')}`")
        lines.append(f"- ranking_pool_source: `{result.get('ranking_pool_source')}`, ranking_pool_count: `{result.get('ranking_pool_count')}`")
        lines.append(f"- label_coverage: `{result.get('label_coverage')}`")
        lines.append(f"- raw_metrics: `{result.get('raw_metrics')}`")
        lines.append(f"- robust_metrics: `{result.get('robust_metrics')}`")
        lines.append(f"- offline_best_metrics: `{result.get('offline_best_metrics')}`")
        lines.append(f"- evaluated_variants: `{result.get('evaluated_variants')}`")
        lines.append("- next_step_when_not_full_verified: collect real validation labels for the selected robust top10/top15 and retrain the pairwise surrogate with plan-shape delta features instead of only narrow plateau corrections.")
        lines.append("")
        lines.append("Top15 robust selected:")
        lines.append("")
        lines.append("| rank | label | real_score | robust_score | point_score | candidate_index | plan_hash |")
        lines.append("| ---: | --- | ---: | ---: | ---: | ---: | --- |")
        for row in result.get("top15_robust") or []:
            lines.append(
                f"| {row.get('rank')} | {row.get('label') or ''} | {fmt_metric(row.get('real_score'))} | "
                f"{fmt_metric(row.get('robust_score'))} | {fmt_metric(row.get('score'))} | "
                f"{row.get('candidate_index')} | {str(row.get('plan_hash') or '')[:12]} |"
            )
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_validation_manifest(payload: Dict[str, Any], *, k: int = 15) -> Dict[str, Any]:
    scenarios = payload.get("scenarios") or {}
    manifest_scenarios: Dict[str, Any] = {}
    total_candidates = 0
    total_missing = 0
    for scenario, result in scenarios.items():
        rows: List[Dict[str, Any]] = []
        scan_dir = Path(str(result.get("scan_dir") or ""))
        for row in (result.get("top15_robust") or [])[:k]:
            known = row.get("real_score") is not None
            item = {
                "scenario": scenario,
                "robust_rank": row.get("rank"),
                "candidate_id": row.get("candidate_id"),
                "candidate_index": row.get("candidate_index"),
                "known_label": row.get("label"),
                "known_real_score": row.get("real_score"),
                "real_score": row.get("real_score"),
                "real_validation": {
                    "real_score": row.get("real_score"),
                    "real_resource": None,
                    "throughput": None,
                    "latency": None,
                },
                "needs_validation": not known,
                "validation_status": "known" if known else "pending",
                "robust_score": row.get("robust_score"),
                "point_score": row.get("score"),
                "plan_hash": row.get("plan_hash"),
                "estimated_resource": row.get("estimated_resource"),
                "params": row.get("params"),
            }
            rows.append(item)
        missing = [row for row in rows if row.get("needs_validation")]
        total_candidates += len(rows)
        total_missing += len(missing)
        manifest_scenarios[scenario] = {
            "scan_dir": result.get("scan_dir"),
            "ranking_pool_source": result.get("ranking_pool_source"),
            "online_selected_variant": result.get("online_selected_variant"),
            "capability": result.get("capability"),
            "workload_context": read_json(scan_dir / "workload_context.json") if scan_dir.exists() else {},
            "workload_sql_path": str(scan_dir / "workload_sql.json") if scan_dir.exists() else None,
            "candidate_count": len(rows),
            "known_label_count": len(rows) - len(missing),
            "missing_label_count": len(missing),
            "candidates": rows,
        }
    return {
        "generated_at": payload.get("generated_at"),
        "validation_report": payload.get("validation_report"),
        "purpose": (
            "Queue robust top-k candidates that still need real replay/pressure validation labels. "
            "This manifest is for the next offline validation round and is not an online ranking input."
        ),
        "top_k": k,
        "online_selected_variant": payload.get("online_selected_variant"),
        "total_candidates": total_candidates,
        "total_missing_label_count": total_missing,
        "scenarios": manifest_scenarios,
    }


def validation_manifest_markdown(manifest: Dict[str, Any]) -> str:
    lines = [
        "# Bonect Robust Ranking Validation Manifest",
        "",
        f"- generated_at: `{manifest.get('generated_at')}`",
        f"- top_k: `{manifest.get('top_k')}`",
        f"- online_selected_variant: `{manifest.get('online_selected_variant')}`",
        f"- total_candidates: `{manifest.get('total_candidates')}`",
        f"- total_missing_label_count: `{manifest.get('total_missing_label_count')}`",
        "- purpose: next offline validation only; do not feed candidate real results into the same online ranking round.",
        "",
        "## Fill Schema",
        "",
        "After replay/pressure validation, either edit the JSON manifest candidates or create a JSONL file with one record per result:",
        "",
        "```json",
        "{\"scenario\":\"memory\",\"candidate_id\":\"...\",\"real_score\":0.0,\"real_resource\":{\"cpu\":0.0,\"memory\":0.0,\"io\":0.0},\"throughput\":{\"XP-QPS\":0.0,\"XP-TPS\":0.0},\"latency\":{\"p95\":0.0,\"p99\":0.0}}",
        "```",
        "",
        "The generated JSON manifest is also accepted directly by `--extra-labels`; fill `real_score` or `real_validation.real_score` for pending rows.",
        "",
        "Then rerun:",
        "",
        "```bash",
        "python3 shell/evaluate_bonect_estimator_ranking.py --extra-labels path/to/filled_labels.jsonl",
        "# If using shell/bonect_real_validate_candidates.py, the run directory is accepted directly:",
        "python3 shell/evaluate_bonect_estimator_ranking.py --extra-labels history/bonect_real_validation/<run>",
        "```",
        "",
        "To execute the generated pending queue with the repository validation runner:",
        "",
        "```bash",
        "python3 shell/bonect_real_validate_candidates.py \\",
        "  --candidates-json path/to/pending_validation_queue.jsonl \\",
        "  --output-dir history/bonect_real_validation/robust_top15_$(date +%Y%m%d_%H%M%S) \\",
        "  --pending-only --plan-only",
        "# Inspect validation_plan.md, then run the same output-dir without --plan-only:",
        "python3 shell/bonect_real_validate_candidates.py \\",
        "  --candidates-json path/to/pending_validation_queue.jsonl \\",
        "  --output-dir history/bonect_real_validation/<run> \\",
        "  --pending-only --apply-vm-profile --resume --retry-failed --restore-defaults",
        "python3 shell/evaluate_bonect_estimator_ranking.py \\",
        "  --extra-labels history/bonect_real_validation/<run>",
        "```",
        "",
    ]
    for scenario, info in (manifest.get("scenarios") or {}).items():
        lines.append(f"## {scenario}")
        lines.append("")
        lines.append(f"- scan_dir: `{info.get('scan_dir')}`")
        lines.append(f"- ranking_pool_source: `{info.get('ranking_pool_source')}`")
        lines.append(f"- known_label_count: `{info.get('known_label_count')}`")
        lines.append(f"- missing_label_count: `{info.get('missing_label_count')}`")
        lines.append("")
        lines.append("| robust_rank | needs_validation | known_label | candidate_index | robust_score | point_score | plan_hash | params |")
        lines.append("| ---: | --- | --- | ---: | ---: | ---: | --- | --- |")
        for row in info.get("candidates") or []:
            params = row.get("params") or {}
            param_text = ", ".join(f"{name}={fmt_metric(params.get(name))}" for name in COST_PARAMS)
            lines.append(
                f"| {row.get('robust_rank')} | {row.get('needs_validation')} | "
                f"{row.get('known_label') or ''} | {row.get('candidate_index')} | "
                f"{fmt_metric(row.get('robust_score'))} | {fmt_metric(row.get('point_score'))} | "
                f"{str(row.get('plan_hash') or '')[:12]} | `{param_text}` |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def write_pending_validation_queue_jsonl(path: Path, manifest: Dict[str, Any]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for scenario, info in (manifest.get("scenarios") or {}).items():
            for row in info.get("candidates") or []:
                if not row.get("needs_validation"):
                    continue
                item = dict(row)
                item["scenario"] = scenario
                item["scan_dir"] = info.get("scan_dir")
                item["ranking_pool_source"] = info.get("ranking_pool_source")
                item["online_selected_variant"] = info.get("online_selected_variant")
                item["capability"] = info.get("capability")
                item["workload_context"] = info.get("workload_context")
                item["workload_sql_path"] = info.get("workload_sql_path")
                f.write(json.dumps(item, ensure_ascii=False, sort_keys=True, default=str) + "\n")
                count += 1
    return count


def write_latest_pointers(
    *,
    prefix: Path,
    report_json_path: Path,
    report_md_path: Path,
    manifest_json_path: Path,
    manifest_md_path: Path,
    queue_jsonl_path: Path,
    recommendation_json_path: Path,
    recommendation_md_path: Path,
) -> Dict[str, str]:
    output_dir = prefix.parent
    latest_paths = {
        "report_json": output_dir / "latest_robust_ranking_calibration.json",
        "report_md": output_dir / "latest_robust_ranking_calibration.md",
        "validation_manifest_json": output_dir / "latest_robust_ranking_validation_manifest.json",
        "validation_manifest_md": output_dir / "latest_robust_ranking_validation_manifest.md",
        "pending_validation_queue_jsonl": output_dir / "latest_robust_ranking_pending_validation_queue.jsonl",
        "next_round_recommendation_json": output_dir / "latest_robust_ranking_next_round_recommendation.json",
        "next_round_recommendation_md": output_dir / "latest_robust_ranking_next_round_recommendation.md",
        "index_json": output_dir / "latest_robust_ranking_calibration_index.json",
    }
    copies = {
        "report_json": report_json_path,
        "report_md": report_md_path,
        "validation_manifest_json": manifest_json_path,
        "validation_manifest_md": manifest_md_path,
        "pending_validation_queue_jsonl": queue_jsonl_path,
        "next_round_recommendation_json": recommendation_json_path,
        "next_round_recommendation_md": recommendation_md_path,
    }
    for key, source in copies.items():
        shutil.copyfile(source, latest_paths[key])
    index_payload = {
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "timestamped_prefix": str(prefix),
        "timestamped_outputs": {key: str(source) for key, source in copies.items()},
        "latest_outputs": {key: str(path) for key, path in latest_paths.items() if key != "index_json"},
        "note": (
            "Stable pointers to the latest Bonect robust-ranking calibration artifacts. "
            "Timestamped reports remain the immutable audit trail."
        ),
    }
    write_json(latest_paths["index_json"], index_payload)
    return {key: str(path) for key, path in latest_paths.items()}


def flatten_artifact_models(models: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for model_name, info in models.items():
        if not isinstance(info, dict):
            continue
        if any(isinstance(value, dict) and "model_type" in value for value in info.values()):
            for target, target_info in info.items():
                if not isinstance(target_info, dict):
                    continue
                row = dict(target_info)
                row["model"] = model_name
                row["target"] = target
                rows.append(row)
        else:
            row = dict(info)
            row["model"] = model_name
            row["target"] = ""
            rows.append(row)
    return rows


def fmt_metric(value: Any) -> str:
    if value is None:
        return "NA"
    try:
        return f"{float(value):.6g}"
    except (TypeError, ValueError):
        return str(value)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate Bonect estimator raw vs robust ranking.")
    parser.add_argument("--validation-report", default=str(DEFAULT_REPORT))
    parser.add_argument(
        "--extra-labels",
        action="append",
        default=[],
        help=(
            "Optional JSON/JSONL real validation labels from a later offline replay round. "
            "A directory is resolved to validation_results.jsonl. Records need scenario plus "
            "candidate_id, candidate_index, or params, and real_score/score."
        ),
    )
    parser.add_argument("--feature-cache-dir", default="/home/anonymous/program/features_cache_M1")
    parser.add_argument("--output-prefix")
    parser.add_argument("--limit-candidates", type=int)
    parser.add_argument("--goal-threshold", type=float, default=0.10)
    parser.add_argument(
        "--strict-goal",
        action="store_true",
        help="Exit nonzero unless every scenario has full top10/top15 label coverage and robust inversion <= threshold.",
    )
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    report_path = Path(args.validation_report)
    parsed = parse_real_validation_report(report_path)
    resolved_extra_label_files = resolve_extra_label_paths(args.extra_labels or [])
    extra_labels = load_extra_labels(args.extra_labels or [])
    scenarios = {
        scenario: evaluate_scenario(
            scenario,
            parsed.get(scenario) or {},
            limit_candidates=args.limit_candidates,
            extra_labels=extra_labels.get(scenario) or [],
        )
        for scenario in SCENARIOS
    }
    extra_label_resolution_audit = audit_extra_label_resolution(scenarios)
    exact_reusable_label_audit = audit_exact_reusable_validation_labels(scenarios)
    historical_param_occurrence_audit = audit_historical_param_occurrences(scenarios)
    label_leakage_boundary_audit = audit_label_leakage_boundary()
    generated_at = time.strftime("%Y%m%d_%H%M%S")
    prefix = Path(args.output_prefix) if args.output_prefix else ROOT / "report" / "bonect" / f"bonect_estimator_robust_ranking_calibration_{generated_at}"
    goal_gate = build_goal_gate(
        scenarios,
        threshold=max(0.0, float(args.goal_threshold)),
        extra_label_resolution_audit=extra_label_resolution_audit,
    )
    payload = {
        "generated_at": generated_at,
        "validation_report": str(report_path),
        "extra_label_files": [str(Path(path).resolve()) for path in (args.extra_labels or [])],
        "resolved_extra_label_files": [str(path.resolve()) for path in resolved_extra_label_files],
        "extra_label_counts": {scenario: len(rows) for scenario, rows in extra_labels.items()},
        "online_selected_variant": DEFAULT_ROBUST_VARIANT,
        "online_no_candidate_real_label_leakage": bool(
            label_leakage_boundary_audit.get("online_boundary_ok")
        ),
        "scenarios": scenarios,
        "goal_gate": goal_gate,
        "online_ranking_config_audit": audit_online_ranking_config(),
        "label_leakage_boundary_audit": label_leakage_boundary_audit,
        "feature_cache_audit": audit_feature_cache(Path(args.feature_cache_dir)),
        "memory_artifact_search": search_memory_artifacts(),
        "validation_label_source_audit": audit_validation_label_sources(),
        "extra_label_resolution_audit": extra_label_resolution_audit,
        "exact_reusable_label_audit": exact_reusable_label_audit,
        "historical_param_occurrence_audit": historical_param_occurrence_audit,
    }
    manifest = build_validation_manifest(payload, k=15)
    manifest_json_path = prefix.with_name(prefix.name + "_validation_manifest").with_suffix(".json")
    manifest_md_path = prefix.with_name(prefix.name + "_validation_manifest").with_suffix(".md")
    queue_jsonl_path = prefix.with_name(prefix.name + "_pending_validation_queue").with_suffix(".jsonl")
    write_json(manifest_json_path, manifest)
    write_text(manifest_md_path, validation_manifest_markdown(manifest))
    queue_count = write_pending_validation_queue_jsonl(queue_jsonl_path, manifest)
    payload["validation_manifest_json"] = str(manifest_json_path)
    payload["validation_manifest_md"] = str(manifest_md_path)
    payload["pending_validation_queue_jsonl"] = str(queue_jsonl_path)
    payload["validation_manifest_summary"] = {
        "top_k": manifest.get("top_k"),
        "total_candidates": manifest.get("total_candidates"),
        "total_missing_label_count": manifest.get("total_missing_label_count"),
        "pending_queue_count": queue_count,
        "per_scenario_missing": {
            scenario: (info or {}).get("missing_label_count")
            for scenario, info in (manifest.get("scenarios") or {}).items()
        },
    }
    report_json_path = prefix.with_suffix(".json")
    report_md_path = prefix.with_suffix(".md")
    recommendation_json_path = prefix.with_name(prefix.name + "_next_round_recommendation").with_suffix(".json")
    recommendation_md_path = prefix.with_name(prefix.name + "_next_round_recommendation").with_suffix(".md")
    payload["report_json"] = str(report_json_path)
    payload["report_md"] = str(report_md_path)
    payload["completion_audit"] = build_completion_audit(payload)
    payload["next_round_recommendation_json"] = str(recommendation_json_path)
    payload["next_round_recommendation_md"] = str(recommendation_md_path)
    payload["next_round_recommendation"] = build_next_round_recommendation(payload)
    write_json(recommendation_json_path, payload["next_round_recommendation"])
    write_next_round_recommendation_markdown(recommendation_md_path, payload["next_round_recommendation"])
    write_json(report_json_path, payload)
    write_markdown_report(report_md_path, payload)
    latest_outputs = write_latest_pointers(
        prefix=prefix,
        report_json_path=report_json_path,
        report_md_path=report_md_path,
        manifest_json_path=manifest_json_path,
        manifest_md_path=manifest_md_path,
        queue_jsonl_path=queue_jsonl_path,
        recommendation_json_path=recommendation_json_path,
        recommendation_md_path=recommendation_md_path,
    )
    payload["latest_outputs"] = latest_outputs
    write_json(report_json_path, payload)
    write_json(Path(latest_outputs["report_json"]), payload)
    write_json(Path(latest_outputs["next_round_recommendation_json"]), payload["next_round_recommendation"])
    write_next_round_recommendation_markdown(
        Path(latest_outputs["next_round_recommendation_md"]),
        payload["next_round_recommendation"],
    )
    print(
        f"[Done] wrote {report_json_path}, {report_md_path}, "
        f"{manifest_json_path}, {manifest_md_path}, {queue_jsonl_path}, "
        f"{recommendation_json_path}, {recommendation_md_path}, "
        f"and latest pointers under {prefix.parent}",
        flush=True,
    )
    if args.strict_goal and not (payload.get("goal_gate") or {}).get("strict_goal_pass"):
        print("[GoalGate] strict goal failed; see goal_gate in the JSON/Markdown report.", flush=True)
        sys.exit(2)


if __name__ == "__main__":
    main()
