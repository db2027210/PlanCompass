from __future__ import annotations

from scripts import mysql_docker_control_resilience_v1 as core
from scripts import mysql_docker_control_resilience_v4 as resilience
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v32 as controller


def test_docker_start_timeout_only(monkeypatch):
    calls = []

    def fake_run(argv, *, timeout):
        calls.append((argv, timeout))
        return timeout

    monkeypatch.setattr(core, "resilient_run", fake_run)
    assert resilience.resilient_run(["docker", "start", "xp_cpu_w1"], timeout=120) == 600
    assert resilience.resilient_run(["docker", "inspect", "xp_cpu_w1"], timeout=120) == 120
    assert resilience.resilient_run(["docker", "run", "-d"], timeout=300) == 300
    assert [timeout for _, timeout in calls] == [600, 120, 300]


def test_v122_contract_seals_parent_and_start_recovery():
    contract = controller.validate_contract()
    assert contract["docker_start_response_timeout_seconds"] == 600
    assert contract["candidate_search_requires_paper_baseline_pass"] is True
