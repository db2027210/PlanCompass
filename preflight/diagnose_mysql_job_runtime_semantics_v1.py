#!/usr/bin/env python3
"""Read-only diagnosis for JOB runtime-manifest semantic equality."""

from __future__ import print_function

import argparse
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authority", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    args = parser.parse_args()
    module_spec = importlib.util.spec_from_file_location(
        "sealed_job_runtime_diagnostic", str(args.authority)
    )
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    spec = module.load_with_sidecar(args.spec, "bootstrap spec")
    binding = spec["python_runtime"]
    manifest_path = Path(spec["bundle"]["path"]) / binding["manifest_relative"]
    manifest = module.load_object(manifest_path, "Python runtime manifest")
    checks = {
        "schema_version": manifest.get("schema_version")
        == "mysql_algorithm_c_runtime_tree_manifest_v3",
        "algorithm": manifest.get("algorithm")
        == "sha256_canonical_relative_tree_entries_v3",
        "tree_content_sha256": manifest.get("tree_content_sha256")
        == binding["tree_content_sha256"],
        "executable_sha256": (manifest.get("artifacts") or {}).get(
            binding["executable_relative"]
        )
        == binding["executable_sha256"],
    }
    manifest_tree = manifest.get("tree_content_sha256")
    binding_tree = binding["tree_content_sha256"]
    print(
        json.dumps(
            {
                "checks": checks,
                "binding": binding,
                "manifest_projection": {
                    "schema_version": manifest.get("schema_version"),
                    "algorithm": manifest.get("algorithm"),
                    "tree_content_sha256": manifest.get("tree_content_sha256"),
                    "artifacts": manifest.get("artifacts"),
                },
                "tree_debug": {
                    "manifest_type": type(manifest_tree).__name__,
                    "binding_type": type(binding_tree).__name__,
                    "manifest_length": len(manifest_tree),
                    "binding_length": len(binding_tree),
                    "manifest_codepoints": [ord(value) for value in manifest_tree],
                    "binding_codepoints": [ord(value) for value in binding_tree],
                },
            },
            sort_keys=True,
        )
    )
    return 0 if all(checks.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
