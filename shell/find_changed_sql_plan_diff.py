#!/usr/bin/env python3

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


JSONDict = Dict[str, Any]


@dataclass
class PlanChangeRecord:
    """PlanChangeRecord."""

    log_line: int
    timestamp: str
    queryid: str
    query: str
    plan_mode: str
    compare_mode: str
    previous_plan_hash: str
    current_plan_hash: str
    previous_plan_summary: str
    current_plan_summary: str
    detail_file: Optional[Path]
    plan_capture_file: Optional[Path]


class Tee:
    def __init__(self, *streams: Any) -> None:
        self.streams = streams

    def write(self, data: str) -> int:
        for stream in self.streams:
            stream.write(data)
        return len(data)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=" SQL"
    )
    parser.add_argument(
        "log_file",
        help=" JSONL  history/optimization_history_2026-03-23_151623.jsonl",
    )
    parser.add_argument(
        "--queryid",
        action="append",
        default=[],
        help=" queryid ",
    )
    parser.add_argument(
        "--contains",
        action="append",
        default=[],
        help=" SQL ",
    )
    parser.add_argument(
        "--latest-only",
        action="store_true",
        help=" queryid + plan_mode ",
    )
    parser.add_argument(
        "--show-plan-tree",
        action="store_true",
        help="",
    )
    parser.add_argument(
        "--node-type-only",
        action="store_true",
        help=" Node Type ",
    )
    parser.add_argument(
        "--max-diffs",
        type=int,
        default=20,
        help=" 20",
    )
    parser.add_argument(
        "--output-text",
        default="",
        help="",
    )
    parser.add_argument(
        "--summary-output-text",
        default="",
        help="",
    )
    return parser.parse_args()


def load_json_file(path: Path) -> JSONDict:
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def scalar_repr(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def is_plan_node(value: Any) -> bool:
    return isinstance(value, dict) and "Node Type" in value


def format_plan_node_brief(node: Any) -> str:
    if not is_plan_node(node):
        return scalar_repr(node)

    parts = [f"Node Type={scalar_repr(node.get('Node Type'))}"]
    for key in [
        "Parent Relationship",
        "Relation Name",
        "Alias",
        "Index Name",
        "Join Type",
        "Index Cond",
        "Hash Cond",
        "Merge Cond",
        "Filter",
        "Sort Key",
        "Group Key",
        "Strategy",
        "CTE Name",
        "Subplan Name",
    ]:
        if key in node:
            parts.append(f"{key}={scalar_repr(node[key])}")
    return ", ".join(parts)


def build_plan_node_anchor(node: JSONDict) -> Tuple[str, ...]:
    parts: List[str] = []
    for key in [
        "Parent Relationship",
        "Relation Name",
        "Alias",
        "Index Name",
        "Join Type",
        "Index Cond",
        "Hash Cond",
        "Merge Cond",
        "Filter",
        "Sort Key",
        "Group Key",
        "Strategy",
        "CTE Name",
        "Subplan Name",
    ]:
        if key in node:
            parts.append(f"{key}={scalar_repr(node[key])}")

    # Empty anchors are too weak; only use them when Node Type also matches.
    if not parts:
        parts.append(f"Node Type={scalar_repr(node.get('Node Type'))}")
    return tuple(parts)


def get_single_plan_child(node: Any) -> Optional[JSONDict]:
    if not is_plan_node(node):
        return None
    children = node.get("Plans", []) or []
    if len(children) != 1:
        return None
    child = children[0]
    if not is_plan_node(child):
        return None
    return child


def build_plan_node_compare_key(node: Any) -> Optional[Tuple[str, ...]]:
    if not is_plan_node(node):
        return None

    parts: List[str] = []
    parent_relationship = node.get("Parent Relationship")
    if parent_relationship is not None:
        parts.append(f"Parent Relationship={scalar_repr(parent_relationship)}")

    if "Relation Name" in node or "Alias" in node or "CTE Name" in node or "Subplan Name" in node:
        for key in ["Relation Name", "Alias", "CTE Name", "Subplan Name"]:
            if key in node:
                parts.append(f"{key}={scalar_repr(node[key])}")
        return tuple(parts)

    if "Join Type" in node or "Hash Cond" in node or "Merge Cond" in node or "Join Filter" in node:
        for key in ["Hash Cond", "Merge Cond", "Join Filter"]:
            if key in node:
                parts.append(f"{key}={scalar_repr(node[key])}")
        if len(parts) > 1:
            return tuple(parts)
        return None

    if "Group Key" in node or "Sort Key" in node:
        for key in ["Group Key", "Sort Key"]:
            if key in node:
                parts.append(f"{key}={scalar_repr(node[key])}")
        return tuple(parts)

    return None


def walk_plan_children_diffs(
    previous: Sequence[Any],
    current: Sequence[Any],
    path: str,
) -> List[str]:
    diffs: List[str] = []
    matched_current: Set[int] = set()

    current_anchor_map: Dict[Tuple[str, ...], List[int]] = {}
    for index, child in enumerate(current):
        if not is_plan_node(child):
            continue
        anchor = build_plan_node_anchor(child)
        current_anchor_map.setdefault(anchor, []).append(index)

    matched_pairs: List[Tuple[int, int]] = []
    unmatched_previous: List[int] = []
    for prev_index, prev_child in enumerate(previous):
        if not is_plan_node(prev_child):
            unmatched_previous.append(prev_index)
            continue
        anchor = build_plan_node_anchor(prev_child)
        candidates = current_anchor_map.get(anchor, [])
        curr_index = next((idx for idx in candidates if idx not in matched_current), None)
        if curr_index is None:
            unmatched_previous.append(prev_index)
            continue
        matched_current.add(curr_index)
        matched_pairs.append((prev_index, curr_index))

    for prev_index, curr_index in matched_pairs:
        diffs.extend(
            walk_diffs(previous[prev_index], current[curr_index], f"{path}[{prev_index}]")
        )

    wrapped_previous: Set[int] = set()
    wrapped_current: Set[int] = set()
    still_unmatched_previous: List[int] = []
    for prev_index in unmatched_previous:
        prev_child = previous[prev_index]
        matched = False
        for curr_index, curr_child in enumerate(current):
            if curr_index in matched_current or curr_index in wrapped_current:
                continue
            curr_single_child = get_single_plan_child(curr_child)
            if curr_single_child is not None and is_plan_node(prev_child):
                if build_plan_node_anchor(prev_child) == build_plan_node_anchor(curr_single_child):
                    diffs.append(
                        f"{path}[{curr_index}]: added ({format_plan_node_brief(curr_child)})"
                    )
                    diffs.extend(walk_diffs(prev_child, curr_single_child, f"{path}[{prev_index}]"))
                    wrapped_previous.add(prev_index)
                    wrapped_current.add(curr_index)
                    matched = True
                    break

            prev_single_child = get_single_plan_child(prev_child)
            if prev_single_child is not None and is_plan_node(curr_child):
                if build_plan_node_anchor(prev_single_child) == build_plan_node_anchor(curr_child):
                    diffs.append(
                        f"{path}[{prev_index}]: removed ({format_plan_node_brief(prev_child)})"
                    )
                    diffs.extend(walk_diffs(prev_single_child, curr_child, f"{path}[{prev_index}]"))
                    wrapped_previous.add(prev_index)
                    wrapped_current.add(curr_index)
                    matched = True
                    break

        if not matched:
            still_unmatched_previous.append(prev_index)

    replacement_previous: Set[int] = set()
    replacement_current: Set[int] = set()
    for prev_index in still_unmatched_previous:
        prev_child = previous[prev_index]
        prev_key = build_plan_node_compare_key(prev_child)
        if prev_key is None:
            continue
        for curr_index, curr_child in enumerate(current):
            if curr_index in matched_current or curr_index in wrapped_current or curr_index in replacement_current:
                continue
            curr_key = build_plan_node_compare_key(curr_child)
            if curr_key != prev_key:
                continue
            diffs.extend(walk_diffs(prev_child, curr_child, f"{path}[{prev_index}]"))
            replacement_previous.add(prev_index)
            replacement_current.add(curr_index)
            break

    for prev_index in still_unmatched_previous:
        if prev_index in replacement_previous:
            continue
        diffs.append(
            f"{path}[{prev_index}]: removed ({format_plan_node_brief(previous[prev_index])})"
        )

    for curr_index, curr_child in enumerate(current):
        if curr_index in matched_current or curr_index in wrapped_current or curr_index in replacement_current:
            continue
        diffs.append(
            f"{path}[{curr_index}]: added ({format_plan_node_brief(curr_child)})"
        )

    return diffs


def walk_diffs(previous: Any, current: Any, path: str = "$") -> List[str]:
    diffs: List[str] = []

    if type(previous) is not type(current):
        diffs.append(
            f"{path}: type {type(previous).__name__} -> {type(current).__name__}"
        )
        return diffs

    if isinstance(previous, dict):
        prev_keys = set(previous.keys())
        curr_keys = set(current.keys())

        for key in sorted(prev_keys - curr_keys):
            diffs.append(f"{path}.{key}: removed ({scalar_repr(previous[key])})")
        for key in sorted(curr_keys - prev_keys):
            diffs.append(f"{path}.{key}: added ({scalar_repr(current[key])})")
        for key in sorted(prev_keys & curr_keys):
            diffs.extend(walk_diffs(previous[key], current[key], f"{path}.{key}"))
        return diffs

    if isinstance(previous, list):
        if path.endswith(".Plans") and all(is_plan_node(item) for item in previous + current):
            return walk_plan_children_diffs(previous, current, path)
        min_len = min(len(previous), len(current))
        for index in range(min_len):
            diffs.extend(walk_diffs(previous[index], current[index], f"{path}[{index}]"))
        for index in range(min_len, len(previous)):
            diffs.append(
                f"{path}[{index}]: removed ({scalar_repr(previous[index])})"
            )
        for index in range(min_len, len(current)):
            diffs.append(f"{path}[{index}]: added ({scalar_repr(current[index])})")
        return diffs

    if previous != current:
        diffs.append(f"{path}: {scalar_repr(previous)} -> {scalar_repr(current)}")
    return diffs


def format_plan_tree(
    plan: Any,
    indent: int = 0,
    *,
    node_type_only: bool = False,
) -> List[str]:
    if not isinstance(plan, dict):
        return [" " * indent + scalar_repr(plan)]

    node_type = str(plan.get("Node Type", "<unknown>"))
    details: List[str] = []
    if not node_type_only:
        for key in [
            "Relation Name",
            "Alias",
            "Index Name",
            "Join Type",
            "Strategy",
            "Index Cond",
            "Hash Cond",
            "Merge Cond",
            "Filter",
            "Sort Key",
            "Group Key",
        ]:
            if key in plan:
                details.append(f"{key}={scalar_repr(plan[key])}")

    line = " " * indent + f"- {node_type}"
    if details:
        line += " | " + ", ".join(details)

    lines = [line]
    for child in plan.get("Plans", []) or []:
        lines.extend(format_plan_tree(child, indent + 2, node_type_only=node_type_only))
    return lines


def extract_node_type_change_depths(diffs: Sequence[str]) -> List[int]:
    depths: List[int] = []
    for diff in diffs:
        if ".Node Type:" not in diff:
            continue
        path = diff.split(":", 1)[0]
        parent_path = path[: -len('.Node Type')]
        depths.append(parent_path.count('.Plans['))
    return depths


def annotate_tree_with_depths(lines: Sequence[str], changed_depths: Sequence[int]) -> List[str]:
    if not changed_depths:
        return list(lines)
    remaining = {depth: 0 for depth in changed_depths}
    out: List[str] = []
    for line in lines:
        stripped = line.lstrip()
        if not stripped.startswith('- '):
            out.append(line)
            continue
        indent = len(line) - len(stripped)
        depth = indent // 2
        marker = ' *' if depth in remaining and remaining[depth] == 0 else ''
        out.append(line + marker)
        if depth in remaining:
            remaining[depth] += 1
    return out


def extract_node_type_transitions(diffs: Sequence[str]) -> List[Tuple[str, str, str]]:
    transitions: List[Tuple[str, str, str]] = []
    for diff in diffs:
        if ".Node Type:" not in diff:
            continue
        path_part, value_part = diff.split(":", 1)
        if "->" not in value_part:
            continue
        old_value, new_value = value_part.split("->", 1)
        transitions.append((
            path_part.strip(),
            old_value.strip(),
            new_value.strip(),
        ))
    return transitions


def extract_structural_node_type_changes(diffs: Sequence[str]) -> List[Tuple[str, str, str]]:
    changes: List[Tuple[str, str, str]] = []
    for diff in diffs:
        if ": added (" in diff:
            path_part, value_part = diff.split(": added (", 1)
            if not value_part.startswith("Node Type="):
                continue
            node_type = value_part[len("Node Type="):].split(",", 1)[0].rstrip(")")
            changes.append((path_part.strip(), "added", node_type.strip()))
            continue
        if ": removed (" in diff:
            path_part, value_part = diff.split(": removed (", 1)
            if not value_part.startswith("Node Type="):
                continue
            node_type = value_part[len("Node Type="):].split(",", 1)[0].rstrip(")")
            changes.append((path_part.strip(), "removed", node_type.strip()))
    return changes


def is_node_type_diff_line(diff: str) -> bool:
    if ".Node Type:" in diff:
        return True
    if ": added (Node Type=" in diff:
        return True
    if ": removed (Node Type=" in diff:
        return True
    return False


def format_summary_table(
    title: str,
    rows: Sequence[Tuple[str, Dict[str, Any]]],
) -> List[str]:
    lines: List[str] = []
    lines.append("")
    lines.append("=" * 140)
    lines.append(title)
    lines.append("=" * 140)
    if not rows:
        lines.append("(empty)")
        return lines

    header = f"{'#':<3} {'transition':<45} {'count':>7} {'sqls':>6}  {'queryids':<40}  representative_sql"
    lines.append(header)
    lines.append(f"{'-'*3} {'-'*45} {'-'*7} {'-'*6}  {'-'*40}  {'-'*30}")
    for idx, (transition_text, item) in enumerate(rows, start=1):
        display_text = transition_text
        if len(display_text) > 45:
            display_text = display_text[:42] + "..."
        queryids = ", ".join(item["queryids"][:3])
        if len(queryids) > 40:
            queryids = queryids[:37] + "..."
        representative_sql = item["queries"][0] if item["queries"] else ""
        representative_sql = representative_sql.replace("\n", " ").strip()
        if len(representative_sql) > 80:
            representative_sql = representative_sql[:77] + "..."
        lines.append(
            f"{idx:<3} {display_text:<45} {int(item['count']):>7} {len(item['queries']):>6}  {queryids:<40}  {representative_sql}"
        )
    return lines


def build_node_type_transition_summary_lines(
    records: Sequence[PlanChangeRecord],
    *,
    node_type_only: bool,
) -> List[str]:
    summary: Dict[Tuple[str, str], Dict[str, Any]] = {}
    structural_summary: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for record in records:
        if not record.detail_file or not record.detail_file.exists():
            continue
        detail = load_json_file(record.detail_file)
        previous_plan = detail.get("previous_normalized_plan")
        current_plan = detail.get("current_normalized_plan")
        diffs = walk_diffs(previous_plan, current_plan)
        transitions = extract_node_type_transitions(diffs)
        structural_changes = extract_structural_node_type_changes(diffs)
        seen_in_record: Set[Tuple[str, str]] = set()
        for _path, old_type, new_type in transitions:
            key = (old_type, new_type)
            item = summary.setdefault(
                key,
                {
                    "count": 0,
                    "queryids": [],
                    "queries": [],
                },
            )
            item["count"] += 1
            if key in seen_in_record:
                continue
            seen_in_record.add(key)
            if record.queryid not in item["queryids"]:
                item["queryids"].append(record.queryid)
            query_text = record.query.strip().replace("\n", " ")
            if query_text and query_text not in item["queries"]:
                item["queries"].append(query_text)

        seen_structural_in_record: Set[Tuple[str, str]] = set()
        for _path, action, node_type in structural_changes:
            key = (action, node_type)
            item = structural_summary.setdefault(
                key,
                {
                    "count": 0,
                    "queryids": [],
                    "queries": [],
                },
            )
            item["count"] += 1
            if key in seen_structural_in_record:
                continue
            seen_structural_in_record.add(key)
            if record.queryid not in item["queryids"]:
                item["queryids"].append(record.queryid)
            query_text = record.query.strip().replace("\n", " ")
            if query_text and query_text not in item["queries"]:
                item["queries"].append(query_text)

    lines: List[str] = []
    ranked = sorted(summary.items(), key=lambda kv: (-int(kv[1]["count"]), kv[0][0], kv[0][1]))
    structural_ranked = sorted(
        structural_summary.items(),
        key=lambda kv: (-int(kv[1]["count"]), kv[0][0], kv[0][1]),
    )
    lines.extend(
        format_summary_table(
            "node_type_transition_summary",
            [(f"{old_type} -> {new_type}", item) for (old_type, new_type), item in ranked],
        )
    )
    if node_type_only:
        lines.extend(
            format_summary_table(
                "structural_node_type_change_summary",
                [(f"{action} {node_type}", item) for (action, node_type), item in structural_ranked],
            )
        )
    return lines


def summarize_node_type_transitions(
    records: Sequence[PlanChangeRecord],
    *,
    node_type_only: bool,
) -> None:
    for line in build_node_type_transition_summary_lines(records, node_type_only=node_type_only):
        print(line, flush=True)


def resolve_detail_file(
    base_dir: Path,
    detail_files: Sequence[JSONDict],
    queryid: str,
    previous_plan_hash: str,
    current_plan_hash: str,
) -> Optional[Path]:
    for item in detail_files:
        if str(item.get("queryid")) != queryid:
            continue
        if str(item.get("previous_plan_hash")) != previous_plan_hash:
            continue
        if str(item.get("current_plan_hash")) != current_plan_hash:
            continue
        file_value = item.get("file")
        if file_value:
            return (base_dir / str(file_value)).resolve()

    for item in detail_files:
        if str(item.get("queryid")) != queryid:
            continue
        file_value = item.get("file")
        if file_value:
            return (base_dir / str(file_value)).resolve()
    return None


def iter_plan_changes(log_path: Path) -> Iterable[PlanChangeRecord]:
    base_dir = log_path.parent.parent if log_path.parent.name == "history" else log_path.parent

    with log_path.open("r", encoding="utf-8") as fp:
        for line_no, line in enumerate(fp, start=1):
            line = line.strip()
            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            if record.get("event") != "iteration_plan_changes":
                continue

            detail_files = record.get("detail_files", []) or []
            plan_capture_value = record.get("plan_capture_file")
            plan_capture_file = None
            if plan_capture_value:
                plan_capture_file = (base_dir / str(plan_capture_value)).resolve()

            for change in record.get("changes", []) or []:
                queryid = str(change.get("queryid"))
                detail_file = resolve_detail_file(
                    base_dir=base_dir,
                    detail_files=detail_files,
                    queryid=queryid,
                    previous_plan_hash=str(change.get("previous_plan_hash", "")),
                    current_plan_hash=str(change.get("current_plan_hash", "")),
                )
                yield PlanChangeRecord(
                    log_line=line_no,
                    timestamp=str(record.get("timestamp", "")),
                    queryid=queryid,
                    query=str(change.get("query", "")),
                    plan_mode=str(change.get("plan_mode", "")),
                    compare_mode=str(change.get("compare_mode", "")),
                    previous_plan_hash=str(change.get("previous_plan_hash", "")),
                    current_plan_hash=str(change.get("current_plan_hash", "")),
                    previous_plan_summary=str(change.get("previous_plan_summary", "")),
                    current_plan_summary=str(change.get("current_plan_summary", "")),
                    detail_file=detail_file,
                    plan_capture_file=plan_capture_file,
                )


def apply_filters(
    records: Sequence[PlanChangeRecord],
    queryids: Sequence[str],
    contains_list: Sequence[str],
    latest_only: bool,
) -> List[PlanChangeRecord]:
    filtered = list(records)

    if queryids:
        queryid_set = {str(item) for item in queryids}
        filtered = [item for item in filtered if item.queryid in queryid_set]

    if contains_list:
        needles = [item.lower() for item in contains_list]
        filtered = [
            item
            for item in filtered
            if all(needle in item.query.lower() for needle in needles)
        ]

    if latest_only:
        latest: Dict[Tuple[str, str], PlanChangeRecord] = {}
        for item in filtered:
            latest[(item.queryid, item.plan_mode)] = item
        filtered = list(latest.values())

    return filtered


def print_record(
    record: PlanChangeRecord,
    max_diffs: int,
    show_plan_tree: bool,
    node_type_only: bool,
) -> None:
    print("=" * 100, flush=True)
    print(
        f"timestamp={record.timestamp} line={record.log_line} queryid={record.queryid} plan_mode={record.plan_mode}",
        flush=True,
    )
    print(
        f"summary={record.previous_plan_summary} -> {record.current_plan_summary} "
        f"hash={record.previous_plan_hash} -> {record.current_plan_hash}",
        flush=True,
    )
    print(f"sql={record.query}", flush=True)
    print(
        f"detail_file={record.detail_file if record.detail_file else '<missing>'}",
        flush=True,
    )
    print(
        f"plan_capture_file={record.plan_capture_file if record.plan_capture_file else '<missing>'}",
        flush=True,
    )

    if not record.detail_file or not record.detail_file.exists():
        print("diffs=<detail file missing>", flush=True)
        return

    detail = load_json_file(record.detail_file)
    previous_plan = detail.get("previous_normalized_plan")
    current_plan = detail.get("current_normalized_plan")
    diffs = walk_diffs(previous_plan, current_plan)
    if node_type_only:
        diffs = [diff for diff in diffs if is_node_type_diff_line(diff)]

    print(f"diff_count={len(diffs)}", flush=True)
    for diff in diffs[:max_diffs]:
        print(f"  - {diff}", flush=True)
    if len(diffs) > max_diffs:
        print(f"  ... truncated, remaining={len(diffs) - max_diffs}", flush=True)

    if show_plan_tree:
        changed_depths = extract_node_type_change_depths(diffs)
        print("previous_plan_tree:", flush=True)
        prev_lines = format_plan_tree(previous_plan, node_type_only=node_type_only)
        if node_type_only:
            prev_lines = annotate_tree_with_depths(prev_lines, changed_depths)
        for line in prev_lines:
            print(line, flush=True)
        print("current_plan_tree:", flush=True)
        curr_lines = format_plan_tree(current_plan, node_type_only=node_type_only)
        if node_type_only:
            curr_lines = annotate_tree_with_depths(curr_lines, changed_depths)
        for line in curr_lines:
            print(line, flush=True)


def write_summary_output(args: argparse.Namespace, records: Sequence[PlanChangeRecord]) -> None:
    if not args.summary_output_text:
        return
    output_path = Path(args.summary_output_text).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = build_node_type_transition_summary_lines(records, node_type_only=args.node_type_only)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    log_path = Path(args.log_file).resolve()

    if not log_path.exists():
        raise FileNotFoundError(f"log file not found: {log_path}")

    records = list(iter_plan_changes(log_path))
    records = apply_filters(
        records=records,
        queryids=args.queryid,
        contains_list=args.contains,
        latest_only=args.latest_only,
    )

    if not records:
        print("No matching plan-change SQL found.", flush=True)
        write_summary_output(args, records)
        return

    write_summary_output(args, records)

    print(f"log_file={log_path}", flush=True)
    print(f"matched_records={len(records)}", flush=True)
    print(flush=True)

    for record in records:
        print_record(
            record=record,
            max_diffs=args.max_diffs,
            show_plan_tree=args.show_plan_tree,
            node_type_only=args.node_type_only,
        )

    summarize_node_type_transitions(
        records,
        node_type_only=args.node_type_only,
    )


def main() -> None:
    args = parse_args()
    if args.output_text:
        output_path = Path(args.output_text).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as fp:
            with contextlib.redirect_stdout(Tee(sys.stdout, fp)):
                run(args)
    else:
        run(args)


if __name__ == "__main__":
    main()
