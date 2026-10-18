from __future__ import annotations

from datetime import datetime
import importlib.util
import json
from pathlib import Path
import sys
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load(path: Path, stem: str):
    spec = importlib.util.spec_from_file_location(stem + uuid.uuid4().hex, path)
    module = importlib.util.module_from_spec(spec)
    old = list(sys.path)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = old
    return module


@pytest.fixture(scope="session")
def bundle():
    if (ROOT / "bundle_manifest.json").is_file():
        return ROOT
    builder = load(
        ROOT / "shell/build_mysql_xp_node02_ttl_successor_v22.py",
        "xp_v22_builder",
    )
    return builder.build()


@pytest.fixture()
def materials(bundle):
    return load(
        bundle / "test_support/mysql_xp_qualification_bootstrap_materials_v5.py",
        "xp_v22_materials",
    )


def test_exact_60_minute_materials_preserve_all_gates(tmp_path, bundle, materials):
    result = materials.create_materials(bundle, tmp_path, 60)
    output = Path(result["materials_directory"])
    manifest = json.loads((output / "materials_manifest.json").read_text())
    spec = json.loads((output / "bootstrap_spec.json").read_text())
    plan = json.loads((output / "command_plan.json").read_text())
    created = datetime.fromisoformat(manifest["created_at_utc"])
    expires = datetime.fromisoformat(manifest["expires_at_utc"])
    assert (expires - created).total_seconds() == 3600
    assert result["bundle_id"] == bundle.name
    assert result["formal_r0_authorized"] is False
    assert result["accepted_round_authorized"] is False
    assert spec["formal_scope"] == "mysql_only"
    assert spec["cross_engine_comparison_prohibited"] is True
    assert spec["postgresql_mutation_prohibited"] is True
    gate = spec["formal_gate_binding"]
    assert gate["required_nonempty_subsets"] == 127
    assert gate["fixed_real_replay_top_k"] == 5
    assert gate["database_cpu_quota_cores"] == 5.3
    assert gate["database_io_read_bps"] == 29360128
    assert gate["database_io_write_bps"] == 524288
    for poll in plan["controller_poll_after_each_systemd_run"].values():
        empty = poll["systemd219_empty_control_group_terminal_success"]
        assert empty["journal_and_output_content_must_rehash_to_declared_sha256"] is True
        assert empty["missing_or_tampered_evidence_policy"] == "FAIL_CLOSED_ABANDON_ATTEMPT"


def test_ttl_bounds_remain_fail_closed(tmp_path, bundle, materials):
    for minutes in (9, 61):
        with pytest.raises(materials.MaterialError, match="10..60"):
            materials.create_materials(bundle, tmp_path / str(minutes), minutes)
