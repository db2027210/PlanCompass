#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
            if isinstance(row, dict):
                yield row


def load_candidate_metadata(path: Path, wanted_indices: Set[int]) -> Dict[int, Dict[str, Any]]:
    metadata: Dict[int, Dict[str, Any]] = {}
    for row in iter_jsonl(path):
        candidate_index = row.get("candidate_index")
        if candidate_index is None:
            continue
        index = int(candidate_index)
        if index in wanted_indices:
            metadata[index] = row
            if len(metadata) == len(wanted_indices):
                break
    missing = sorted(wanted_indices - set(metadata))
    if missing:
        raise ValueError(f"candidate metadata not found for indices: {missing}")
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge real Bonect replay labels with estimator candidate metadata."
    )
    parser.add_argument("--base-labels-jsonl", required=True, type=Path)
    parser.add_argument("--new-labels-jsonl", required=True, type=Path)
    parser.add_argument("--candidate-metadata-jsonl", required=True, type=Path)
    parser.add_argument("--output-jsonl", required=True, type=Path)
    parser.add_argument(
        "--exclude-candidate-index",
        action="append",
        default=[],
        help="Candidate index to exclude from the new batch; may be repeated.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_rows = list(iter_jsonl(args.base_labels_jsonl))
    excluded = {int(value) for value in args.exclude_candidate_index}
    new_rows = [
        row
        for row in iter_jsonl(args.new_labels_jsonl)
        if row.get("status") == "ok"
        and row.get("candidate_index") is not None
        and int(row["candidate_index"]) not in excluded
    ]
    indices = {int(row["candidate_index"]) for row in new_rows}
    metadata = load_candidate_metadata(args.candidate_metadata_jsonl, indices)

    merged_rows: List[Dict[str, Any]] = list(base_rows)
    seen_indices = {
        int(row["candidate_index"])
        for row in base_rows
        if row.get("candidate_index") is not None
    }
    for real_row in new_rows:
        index = int(real_row["candidate_index"])
        if index in seen_indices:
            continue
        merged = dict(metadata[index])
        merged.update(real_row)
        merged["candidate_metadata_source"] = str(args.candidate_metadata_jsonl)
        merged["replay_label_source"] = str(args.new_labels_jsonl)
        merged_rows.append(merged)
        seen_indices.add(index)

    args.output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with args.output_jsonl.open("w", encoding="utf-8") as handle:
        for row in merged_rows:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "base_row_count": len(base_rows),
                "new_row_count": len(merged_rows) - len(base_rows),
                "output_row_count": len(merged_rows),
                "output_jsonl": str(args.output_jsonl),
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
