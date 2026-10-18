from __future__ import annotations

import json
from pathlib import Path

from shell import build_runtime_tree_portable_manifests_v3 as builder


def test_fresh_publications_convert_to_one_directory_aware_schema() -> None:
    first = {
        label: builder.build_one(label, spec)
        for label, spec in builder.SOURCES.items()
    }
    second = {
        label: builder.build_one(label, spec)
        for label, spec in builder.SOURCES.items()
    }
    assert first == second
    for label, result in first.items():
        path = Path(result["path"])
        document = json.loads(path.read_text(encoding="utf-8"))
        portable = builder.tree.validate_portable_manifest(document)
        assert document["schema_version"] == (
            "mysql_algorithm_c_runtime_tree_manifest_v3"
        )
        assert document["algorithm"] == (
            "sha256_canonical_relative_tree_entries_v3"
        )
        assert portable["tree_content_sha256"] == (
            builder.SOURCES[label]["tree_content_sha256"]
        )
        assert result["directory_count"] > 0
        assert path.with_name(path.name + ".sha256").is_file()
