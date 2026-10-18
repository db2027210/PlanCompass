from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "shell/build_mysql_tpcc_v13_qualification_capture_materials_v1.py"
)


def module():
    spec = importlib.util.spec_from_file_location("tpcc_v13_materials", SCRIPT)
    assert spec is not None and spec.loader is not None
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="ascii"))


def test_fresh_v13_materials_are_bound_sealed_and_self_verified(
    tmp_path: Path,
) -> None:
    builder = module()
    created = datetime.now(timezone.utc)
    attempt = "tpcc-v4-v13-test-0123456789abcdef01234567"
    directory = builder.build(
        tmp_path, attempt, now=created
    )
    verified = builder.verify_materials(
        directory, now=created + timedelta(minutes=29)
    )
    assert verified["bundle_id"] == builder.BUNDLE_ID
    assert verified["bundle_manifest_sha256"] == (
        builder.BUNDLE_MANIFEST_SHA256
    )
    assert verified["invalidated_predecessor_bundle_id"] == (
        builder.INVALIDATED_D377
    )
    assert verified["ttl_seconds"] == 1800
    assert verified["remote_actions"] == 0
    assert verified["capture_launch_authorized"] is False
    assert verified["formal_launch_authorized"] is False
    assert verified["accepted_round_authorized"] is False

    plan = load(directory / "qualification_plan.json")
    payload = json.dumps(plan, sort_keys=True)
    assert plan["execution_sequence"] == list(builder.EXECUTION_SEQUENCE)
    assert plan["typed_capture_authority"] == builder.TYPED_AUTHORITY
    assert builder.INVALIDATED_D377 not in payload
    assert builder.BUNDLE_ID in payload
    assert (
        plan["typed_capture_authority"]["qps_resource_input_prohibited"]
        is True
    )


def test_v13_materials_expire_fail_closed(tmp_path: Path) -> None:
    builder = module()
    created = datetime.now(timezone.utc)
    directory = builder.build(
        tmp_path,
        "tpcc-v4-v13-expiry-0123456789abcdef01234567",
        now=created,
    )
    with pytest.raises((builder.MaterialsError, builder.spec.SpecError)):
        builder.verify_materials(
            directory, now=created + timedelta(minutes=30)
        )
