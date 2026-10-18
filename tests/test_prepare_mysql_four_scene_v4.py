from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/prepare_mysql_four_scene_v4.py"


def _load():
    spec = importlib.util.spec_from_file_location("prepare_mysql_four_scene_v4", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_defaults_are_private_tcp_credentials() -> None:
    module = _load()
    rendered = module._defaults("secret")
    assert "password=secret" in rendered
    assert "protocol=tcp" in rendered
    assert "ssl-mode=DISABLED" in rendered


def test_v4_has_eight_unique_workers_ports_and_containers() -> None:
    freeze = json.loads(module_path().read_text(encoding="utf-8"))
    workers = freeze["workers"]
    assert len(workers) == 8
    assert len({row["worker_id"] for row in workers}) == 8
    assert len({row["host_port"] for row in workers}) == 8
    assert len({row["container"] for row in workers}) == 8


def test_preparer_binds_cpu_and_enforces_kernel_blkio() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert '"--cpuset-cpus", str(worker["database_cpuset"])' in source
    assert '"--cpuset-mems", str(worker["database_cpuset_mems"])' in source
    assert '"--cpus", str(worker["cpu_quota_cores"])' in source
    assert "blkio.throttle.read_bps_device" in source
    assert "blkio.throttle.write_bps_device" in source
    assert "_kernel_resource_readback(inspect, worker)" in source
    assert "_enforce_blkio(inspect, freeze, worker)" in source


def module_path() -> Path:
    return (
        ROOT
        / "config/formal/frozen_mysql_disk2_two_worker_20260809_v4"
        / "four_scene_resource_successor.json"
    )
