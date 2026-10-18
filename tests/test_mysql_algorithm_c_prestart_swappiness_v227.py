import json

import pytest

from scripts import mysql_algorithm_c_reset_affinity_v3 as subject


def test_enforces_both_xp_cpu_workers_before_resource_gate(tmp_path, monkeypatch):
    binding = tmp_path / "prepared_binding.json"
    binding.write_text(json.dumps({
        "workers": [
            {"worker_id": "xp_cpu_w1", "scene": "hybench_xp_cpu"},
            {"worker_id": "xp_cpu_w2", "scene": "hybench_xp_cpu"},
            {"worker_id": "xp_io_w1", "scene": "hybench_xp_io"},
        ]
    }))
    monkeypatch.setattr(
        subject,
        "_enforce_swappiness",
        lambda worker: {
            "worker_id": worker["worker_id"],
            "after": 0,
            "readback_passed": True,
        },
    )

    result = subject.enforce_prestart_swappiness(binding)

    assert result["worker_count"] == 2
    assert result["all_readbacks_passed"] is True
    assert set(result["workers"]) == {"xp_cpu_w1", "xp_cpu_w2"}


def test_prestart_fails_closed_when_worker_authority_is_incomplete(
    tmp_path, monkeypatch
):
    binding = tmp_path / "prepared_binding.json"
    binding.write_text(json.dumps({
        "workers": [{"worker_id": "xp_cpu_w1", "scene": "hybench_xp_cpu"}]
    }))
    monkeypatch.setattr(subject, "_enforce_swappiness", lambda worker: {})

    with pytest.raises(RuntimeError, match="worker authority differs"):
        subject.enforce_prestart_swappiness(binding)
