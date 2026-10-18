#!/usr/bin/env python3
"""Create short-lived JOB qualification materials for the v34 successor.

The underlying sealed-material schema is intentionally compatible with v33;
all authority is rebound here to the exact v34 bundle and manifest.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = ROOT / "shell/mysql_job_v33_client_tcp_bootstrap_materials_v1.py"
BUNDLE_ID = "e7d077d8dbc382f36a1dcf5bbbebfe321e4e7e02ab2a7bc92280f8cb72af4ff1"
MANIFEST_SHA256 = "67d2d79d74e79455673b6b3cf9701647fd4a1bb8e74397c4970b278829587c81"
DEFAULT_BUNDLE = (
    ROOT / "artifacts/mysql_job_v34_measurement_bind_successor" / BUNDLE_ID
)
DEFAULT_OUTPUT_ROOT = (
    ROOT / "artifacts/mysql_job_v34_measurement_bind_bootstrap_materials_v1"
)
V33_INVALIDATION = (
    "authority/mysql_job_v33_9fe3_measurement_bind_invalidation_20260731.json"
)


def base_module():
    spec = importlib.util.spec_from_file_location("job_v33_materials", BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("v33 material generator is not importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BUNDLE_ID = BUNDLE_ID
    module.MANIFEST_SHA256 = MANIFEST_SHA256
    module.DEFAULT_BUNDLE = DEFAULT_BUNDLE
    module.DEFAULT_OUTPUT_ROOT = DEFAULT_OUTPUT_ROOT
    return module


def verify_successor(bundle: Path) -> None:
    manifest = json.loads(
        (bundle / "bundle_manifest.json").read_text(encoding="utf-8")
    )
    files = manifest.get("files") or {}
    if (
        manifest.get("bundle_id") != BUNDLE_ID
        or V33_INVALIDATION not in files
        or "qualification/mysql_job_distributed_live_readback_qualification_v2.py"
        not in files
    ):
        raise RuntimeError("v34 measurement-bind closure differs")
    collector = (
        bundle
        / "qualification/mysql_job_distributed_live_readback_qualification_v2.py"
    ).read_text(encoding="utf-8")
    if (
        "JOB_MEASUREMENT_ROOT" not in collector
        or '"measurement_bind": {' not in collector
        or "validate_host_tmpfs_output(" not in collector
    ):
        raise RuntimeError("v34 collector measurement authority differs")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--verify-materials", type=Path)
    args = parser.parse_args()
    base = base_module()
    if args.verify_materials:
        result = base.verify_materials(args.verify_materials)
    else:
        bundle = args.bundle.resolve(strict=True)
        verify_successor(bundle)
        result = base.create(bundle, args.output_root)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
