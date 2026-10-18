#!/usr/bin/env python3
"""Build an isolated MySQL IO feature cache with independent repeat runs.

The source feature extractor lives in the sibling ``features_cache_M1`` project.
This wrapper never overwrites the original cache or experiment directories.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd


DEFAULT_BASE_CACHE = Path("/home/anonymous/program/features_cache_M1/mysql_data")
DEFAULT_EXTRACTOR = Path("/home/anonymous/program/features_cache_M1/get_mysql_io_feat.py")
DEFAULT_OUTPUT = Path("/home/anonymous/program/features_cache_M1/mysql_io_expanded")
DEFAULT_REPEAT_ROOTS = (
    (
        "fx1",
        Path(
            "/disk3/anonymous/experiment_mysql_result_tpch_m_repeat_fixed1_20260523_190245_r1/low"
        ),
    ),
    (
        "sf23",
        Path(
            "/disk3/anonymous/experiment_mysql_result_tpch_m_repeat_sf23_20260524_131115_r1/low"
        ),
    ),
)
CASE_PATTERN = re.compile(r"^h(?P<number>\d+)$")


def _load_extractor(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("mysql_io_feature_extractor", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import feature extractor: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_repeat(value: str) -> Tuple[str, Path]:
    label, separator, raw_path = value.partition("=")
    if not separator or not re.fullmatch(r"[a-z0-9]+", label):
        raise argparse.ArgumentTypeError("repeat root must be LABEL=/absolute/path")
    path = Path(raw_path)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("repeat root must use an absolute path")
    return label, path


def _required_source_files(case_dir: Path) -> List[Path]:
    return [
        case_dir / "metrics/container_fs_read_mb.csv",
        case_dir / "metrics/container_fs_write_mb.csv",
        case_dir / "plans/plan.jsonl",
        case_dir / "plans/sqllog.csv",
    ]


def _write_parquet_atomic(frame: pd.DataFrame, path: Path) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False)
    temporary.replace(path)


def prepare(
    base_cache: Path,
    output_dir: Path,
    extractor_path: Path,
    repeat_roots: List[Tuple[str, Path]],
    *,
    force: bool,
) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: Dict[str, Any] = {
        "base_cache": str(base_cache),
        "output_dir": str(output_dir),
        "extractor": str(extractor_path),
        "base_files": [],
        "repeat_files": [],
        "skipped": [],
    }

    for source in sorted(base_cache.glob("*mysql_io_*features.parquet")):
        destination = output_dir / source.name
        if force or not destination.exists():
            shutil.copy2(source, destination)
        manifest["base_files"].append(str(destination))

    extractor = _load_extractor(extractor_path)
    for label, root in repeat_roots:
        if not root.is_dir():
            manifest["skipped"].append({"root": str(root), "reason": "missing_root"})
            continue
        builder = extractor.MySQLIODatasetBuilder(str(root))
        for case_dir in sorted(root.glob("h*")):
            match = CASE_PATTERN.match(case_dir.name)
            if match is None:
                continue
            missing = [str(path) for path in _required_source_files(case_dir) if not path.is_file()]
            if missing:
                manifest["skipped"].append(
                    {"root": str(root), "case": case_dir.name, "missing": missing}
                )
                continue
            alias = f"h{match.group('number')}r{label}"
            mode = "concurrent" if label.startswith("sf") else "serial"
            suffix = "5s" if mode == "concurrent" else "serial"
            destination = output_dir / f"{alias}_mysql_io_{suffix}_features.parquet"
            if force or not destination.exists():
                if force:
                    for stale in output_dir.glob(f"{alias}_mysql_io_*_features.parquet"):
                        if stale != destination:
                            stale.unlink()
                frame = builder.build_dataset(case_dir.name, mode=mode, window_s=5)
                if frame.empty:
                    manifest["skipped"].append(
                        {"root": str(root), "case": case_dir.name, "reason": "empty_features"}
                    )
                    continue
                frame = frame.copy()
                frame["case_alias"] = alias
                frame["base_case"] = case_dir.name
                frame["repeat_label"] = label
                _write_parquet_atomic(frame, destination)
            row_count = len(pd.read_parquet(destination, columns=["target_io_mb"]))
            manifest["repeat_files"].append(
                {
                    "alias": alias,
                    "base_case": case_dir.name,
                    "repeat_label": label,
                    "mode": mode,
                    "root": str(root),
                    "path": str(destination),
                    "rows": int(row_count),
                }
            )

    manifest["base_file_count"] = len(manifest["base_files"])
    manifest["repeat_file_count"] = len(manifest["repeat_files"])
    manifest["total_file_count"] = manifest["base_file_count"] + manifest["repeat_file_count"]
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-cache", type=Path, default=DEFAULT_BASE_CACHE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--extractor", type=Path, default=DEFAULT_EXTRACTOR)
    parser.add_argument(
        "--repeat-root",
        action="append",
        type=_parse_repeat,
        help="LABEL=/absolute/result/root/low; repeatable",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    repeats = list(args.repeat_root or DEFAULT_REPEAT_ROOTS)
    report = prepare(
        args.base_cache,
        args.output_dir,
        args.extractor,
        repeats,
        force=args.force,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
