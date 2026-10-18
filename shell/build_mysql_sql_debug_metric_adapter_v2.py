#!/usr/bin/env python3
"""Build the immutable schema-stable native SQL debug metric adapter v2."""

import hashlib
import json
from pathlib import Path
import shutil
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "shell/mysql_sql_debug_metric_adapter_v1.py"
FAMILY = ROOT / "artifacts/mysql_sql_debug_metric_adapter_v2"


def canonical(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    FAMILY.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sql-adapter-v2-", dir=str(FAMILY)) as raw:
        stage = Path(raw)
        adapter = stage / "qualification/mysql_sql_debug_metric_adapter_v2.py"
        adapter.parent.mkdir()
        shutil.copyfile(str(SOURCE), str(adapter))
        manifest = {
            "schema_version": "mysql_sql_debug_metric_adapter_bundle_v2",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "debug_qualification_only": True,
            "native_sql_metric_shape": "stable_query_duration_ms",
            "transactional_percentile_shape_prohibited": True,
            "workload_launch_authorized": False,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
            "files": {str(adapter.relative_to(stage)): sha256(adapter)},
        }
        bundle_id = hashlib.sha256(canonical(manifest)).hexdigest()
        manifest["bundle_id"] = bundle_id
        manifest_path = stage / "bundle_manifest.json"
        manifest_path.write_bytes(canonical(manifest))
        (stage / "bundle_manifest.json.sha256").write_text(
            "%s  bundle_manifest.json\n" % sha256(manifest_path), encoding="ascii"
        )
        destination = FAMILY / bundle_id
        if destination.exists():
            if json.loads((destination / "bundle_manifest.json").read_text()) != manifest:
                raise RuntimeError("existing SQL adapter bundle differs")
        else:
            stage.rename(destination)
        print(str(destination))


if __name__ == "__main__":
    main()
