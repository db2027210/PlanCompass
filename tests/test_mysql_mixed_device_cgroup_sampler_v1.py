from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_mysql_four_scene_r0_v5 as four_scene
from scripts import run_mysql_xp_four_worker_authority_v1 as xp_four


@pytest.mark.parametrize("module", [four_scene, xp_four])
def test_sampler_reads_each_workers_declared_device(tmp_path, monkeypatch, module):
    cpu = tmp_path / "cpu"
    memory = tmp_path / "memory"
    blkio = tmp_path / "blkio"
    for path in (cpu, memory, blkio):
        path.mkdir()
    (cpu / "cpuacct.usage").write_text("1000\n")
    (memory / "memory.usage_in_bytes").write_text("4096\n")
    (memory / "memory.stat").write_text("total_inactive_file 1024\n")
    (blkio / "blkio.throttle.io_service_bytes").write_text(
        "259:5 Read 111\n259:5 Write 222\n"
        "259:6 Read 333\n259:6 Write 444\n"
    )
    monkeypatch.setattr(
        module.CgroupSampler,
        "_paths",
        staticmethod(lambda _worker: {"cpu": cpu, "memory": memory, "blkio": blkio}),
    )
    sampler = module.CgroupSampler([], tmp_path / "samples.jsonl")

    disk1 = sampler._sample({
        "worker_id": "xp_io_w1",
        "blkio_authority": {"device_major_minor": "259:5"},
    })
    disk2 = sampler._sample({
        "worker_id": "xp_io_w3",
        "blkio_authority": {"device_major_minor": "259:6"},
    })

    assert (disk1["read_bytes"], disk1["write_bytes"]) == (111, 222)
    assert disk1["device_major_minor"] == "259:5"
    assert (disk2["read_bytes"], disk2["write_bytes"]) == (333, 444)
    assert disk2["device_major_minor"] == "259:6"


@pytest.mark.parametrize("module", [four_scene, xp_four])
def test_sampler_fails_closed_when_declared_device_is_absent(tmp_path, module):
    path = tmp_path / "blkio.throttle.io_service_bytes"
    path.write_text("259:6 Read 333\n259:6 Write 444\n")
    with pytest.raises(RuntimeError, match="lacks worker device 259:5"):
        module.CgroupSampler._io(path, "259:5")


@pytest.mark.parametrize("module", [four_scene, xp_four])
def test_xp_io_zero_active_window_fails_closed(tmp_path, module):
    worker = {
        "worker_id": "xp_io_w1",
        "scene": "hybench_xp_io",
        "cpu_quota_cores": 1.0,
        "memory_bytes": 4096,
        "io_read_bps": 1000,
        "io_write_bps": 1000,
    }
    sampler = module.CgroupSampler([worker], tmp_path / "samples.jsonl")
    sampler.thread = SimpleNamespace(join=lambda timeout: None, is_alive=lambda: False)
    sampler.rows = {
        "xp_io_w1": [
            {
                "captured_monotonic": 1.0,
                "cpu_ns": 1,
                "memory_working_set_bytes": 1024,
                "read_bytes": 100,
                "write_bytes": 200,
                "device_major_minor": "259:5",
            },
            {
                "captured_monotonic": 2.0,
                "cpu_ns": 2,
                "memory_working_set_bytes": 1024,
                "read_bytes": 100,
                "write_bytes": 200,
                "device_major_minor": "259:5",
            },
        ]
    }
    with pytest.raises(RuntimeError, match="zero target-device IO"):
        sampler.finish()
