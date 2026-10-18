from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import launch_mysql_sgd_claude_agents as launcher


SCRIPT = Path(__file__).with_name("launch_mysql_sgd_claude_agents.py")


def fake_wrapper(tmp_path: Path) -> tuple[Path, Path, Path]:
    wrapper = tmp_path / "fake_claude.py"
    calls = tmp_path / "calls.jsonl"
    agents = tmp_path / "agents.json"
    agents.write_text("[]\n", encoding="utf-8")
    wrapper.write_text(
        """#!/usr/bin/env python3
import json, os, pathlib, sys
calls=pathlib.Path(os.environ['FAKE_CALLS'])
with calls.open('a',encoding='utf-8') as h: h.write(json.dumps(sys.argv[1:])+"\\n")
if sys.argv[1:2] == ['-p']:
 print(os.environ.get('FAKE_HEALTH','HEALTH_OK'))
 raise SystemExit(int(os.environ.get('FAKE_HEALTH_RC','0')))
if sys.argv[1:3] == ['agents','--json']:
 print(pathlib.Path(os.environ['FAKE_AGENTS']).read_text(),end='')
 raise SystemExit(0)
if sys.argv[1:2] == ['--bg']:
 if os.environ.get('FAKE_REGISTER_BG','1') == '1':
  agents=pathlib.Path(os.environ['FAKE_AGENTS'])
  payload=json.loads(agents.read_text())
  payload.append({'name':sys.argv[sys.argv.index('--name')+1], 'pid':int(os.environ['FAKE_LIVE_PID'])})
  agents.write_text(json.dumps(payload))
 print('{"accepted":true}')
 raise SystemExit(0)
raise SystemExit(91)
""",
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    return wrapper, calls, agents


def run_launcher(wrapper: Path, calls: Path, agents: Path, *extra: str, health="HEALTH_OK"):
    environment = dict(os.environ)
    environment.update(
        FAKE_CALLS=str(calls), FAKE_AGENTS=str(agents), FAKE_HEALTH=health,
        FAKE_LIVE_PID=str(os.getpid()),
    )
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--wrapper", str(wrapper), *extra],
        cwd=launcher.ROOT, env=environment, text=True, capture_output=True,
    )


def read_calls(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_dry_run_validates_all_prompts_without_invoking_wrapper(tmp_path: Path) -> None:
    absent = tmp_path / "must_not_run"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--wrapper", str(absent), "--dry-run"],
        cwd=launcher.ROOT, text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "DRY_RUN" in result.stdout
    assert result.stdout.count("WOULD_LAUNCH name=mysql_sgd_") == 5


def test_health_failure_is_first_and_starts_zero_agents(tmp_path: Path) -> None:
    wrapper, calls, agents = fake_wrapper(tmp_path)
    result = run_launcher(wrapper, calls, agents, health="BALANCE_DOWN")
    assert result.returncode == 2
    observed = read_calls(calls)
    assert len(observed) == 1
    assert observed[0][0] == "-p"
    assert observed[0][observed[0].index("--model") + 1] == "deepseek-v4-pro[1m]"
    assert not any("--bg" in call for call in observed)


def test_health_402_stdout_is_visible_and_starts_zero_agents(tmp_path: Path) -> None:
    wrapper, calls, agents = fake_wrapper(tmp_path)
    result = run_launcher(
        wrapper, calls, agents, health="API Error: 402 Insufficient Balance"
    )
    assert result.returncode == 2
    assert "API Error: 402 Insufficient Balance" in result.stderr
    observed = read_calls(calls)
    assert len(observed) == 1
    assert observed[0][0] == "-p"
    assert not any("--bg" in call for call in observed)


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        ("API_KEY=should-not-leak", "potential secret detected"),
        ("bad\x1b[31mresponse", "control characters detected"),
    ],
)
def test_health_diagnostic_rejects_unsafe_content(stdout: str, expected: str) -> None:
    detail = launcher._safe_failure_detail(stdout, "")
    assert expected in detail
    assert stdout not in detail


def test_health_diagnostic_combines_streams_and_limits_to_last_500_chars() -> None:
    detail = launcher._safe_failure_detail("x" * 600, "API Error: 402 Insufficient Balance")
    assert len(detail) == 500
    assert detail.endswith("API Error: 402 Insufficient Balance")


def test_only_live_pid_is_active_stale_session_is_relaunched(tmp_path: Path) -> None:
    wrapper, calls, agents = fake_wrapper(tmp_path)
    agents.write_text(json.dumps([
        {"name": "mysql_sgd_ap_20260723", "pid": os.getpid(), "status": "running"},
        {"name": "mysql_sgd_job_20260723", "pid": 99999999, "status": "running"},
        {"name": "mysql_sgd_xp_20260723", "status": "running"},
    ]), encoding="utf-8")
    result = run_launcher(wrapper, calls, agents)
    assert result.returncode == 0, result.stderr
    observed = read_calls(calls)
    assert observed[0][0] == "-p"
    assert observed[1] == ["agents", "--json"]
    launches = [call for call in observed if call[0] == "--bg"]
    assert len(launches) == 4
    assert all("--dangerously-skip-permissions" in call for call in launches)
    assert all("--name" in call and "--model" in call for call in launches)
    assert all(call[call.index("--model") + 1] == "deepseek-v4-pro[1m]" for call in launches)
    assert observed[-1] == ["agents", "--json"]
    names = {call[call.index("--name") + 1] for call in launches}
    assert names == set(launcher.SCENARIOS.values()) - {"mysql_sgd_ap_20260723"}
    assert "ACTIVE_SKIP name=mysql_sgd_ap_20260723 reason=live_pid" in result.stdout
    assert result.stdout.count("verified=live_pid") == 4


def test_background_acceptance_without_live_pid_is_failure(tmp_path: Path) -> None:
    wrapper, calls, agents = fake_wrapper(tmp_path)
    environment = dict(os.environ)
    environment.update(
        FAKE_CALLS=str(calls), FAKE_AGENTS=str(agents), FAKE_HEALTH="HEALTH_OK",
        FAKE_LIVE_PID=str(os.getpid()), FAKE_REGISTER_BG="0",
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--wrapper", str(wrapper), "--only", "ap"],
        cwd=launcher.ROOT, env=environment, text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert read_calls(calls)[-1] == ["agents", "--json"]
    assert "LAUNCH_VERIFICATION_FAILED" in result.stderr
    assert "LAUNCHED name=" not in result.stdout


def test_invalid_agents_json_starts_zero_agents(tmp_path: Path) -> None:
    wrapper, calls, agents = fake_wrapper(tmp_path)
    agents.write_text("not-json\n", encoding="utf-8")
    result = run_launcher(wrapper, calls, agents)
    assert result.returncode == 2
    assert not any("--bg" in call for call in read_calls(calls))


def test_prompt_set_and_fixed_names_are_exact() -> None:
    assert launcher.MODEL == "deepseek-v4-pro[1m]"
    assert set(launcher.SCENARIOS.values()) == {
        "mysql_sgd_ap_20260723", "mysql_sgd_job_20260723", "mysql_sgd_xp_20260723",
        "mysql_sgd_tpch_20260723", "mysql_sgd_tpcc_20260723",
    }
    for name in launcher.SCENARIOS.values():
        path, text, digest = launcher._prompt(name)
        assert path.name == name + ".txt"
        assert len(digest) == 64
        assert "launch_authorized=true" in text or "launch_authorized=true" in text.replace(" ", "")
        assert "mysql_sgd_pre_tuning_gate_v1.json" in text
        assert "R0 and SGD require a passed bottleneck check" in text


def test_failed_agent_with_live_pid_is_not_active() -> None:
    payload = [
        {
            "name": "mysql_sgd_ap_20260723",
            "pid": os.getpid(),
            "status": "idle",
            "state": "failed",
        },
        {
            "name": "mysql_sgd_xp_20260723",
            "pid": os.getpid(),
            "status": "idle",
            "state": "working",
        },
    ]
    assert launcher.active_named_agents(payload) == set()
