#!/usr/bin/env python3
"""Apply MySQL-side CPU pins WITHOUT conflicting with resource_adaptive PG map.

Source of truth for PG / SGD / bruteforce / fullgrid / grid+estimate:
  /home/anonymous/program/resource_adaptive/shell/apply_pg_cpu_pins.py
  /home/anonymous/program/resource_adaptive/config/pg_cpu_pin_map_20260720.json

This script only:
  1. Optionally invokes the PG pin script (default: yes)
  2. Pins live MySQL formal GD dockers to mysql_reserved (96-127 banks)
  3. Pins host MySQL drivers + PG host tuners (binary_plan / grid optimizers)
     into host_control zone 0-15,256-271
  4. Writes a small compatibility plan that mirrors PG zones

It does NOT reassign ra_fullgrid_* / SGD VMs / matrix slots.
MySQL multi-worker grid containers on phys 16-95 are mutually exclusive with
fullgrid (same bank); they are not re-pinned here unless --pin-mysql-grid-exclusive.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RA_ROOT = Path("/home/anonymous/program/resource_adaptive")
PG_MAP = RA_ROOT / "config" / "pg_cpu_pin_map_20260720.json"
PG_APPLY = RA_ROOT / "shell" / "apply_pg_cpu_pins.py"
SMT = 256


def expand(cs: str) -> set[int]:
    out: set[int] = set()
    for part in (cs or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = map(int, part.split("-", 1))
            out.update(range(a, b + 1))
        else:
            out.add(int(part))
    return out


def full_phys(start: int, n: int) -> str:
    a = list(range(start, start + n))
    b = [x + SMT for x in a]

    def rng(xs: list[int]) -> str:
        return f"{xs[0]}-{xs[-1]}" if len(xs) > 1 else str(xs[0])

    return f"{rng(a)},{rng(b)}"


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=False)


def docker_update_cpuset(name: str, cpuset: str, cpus: int | None = None) -> tuple[bool, str]:
    if not run(["docker", "inspect", name]).returncode == 0:
        return False, "missing"
    cmd = ["docker", "update", f"--cpuset-cpus={cpuset}"]
    if cpus is not None:
        cmd += [f"--cpus={cpus}"]
    cmd.append(name)
    completed = run(cmd)
    inspect = run(
        ["docker", "inspect", "-f", "{{.Name}} {{.State.Status}} cpuset={{.HostConfig.CpusetCpus}}", name]
    )
    actual = inspect.stdout.strip()
    ok = completed.returncode == 0 and expand(
        actual.split("cpuset=")[-1] if "cpuset=" in actual else ""
    ) == expand(cpuset)
    if not ok and completed.returncode != 0:
        return False, (completed.stderr or completed.stdout or actual).strip()
    return ok, actual


def taskset_pid(pid: int | str, cpuset: str) -> tuple[bool, str]:
    before = affinity(pid)
    # -a: all threads of the process (important for multi-thread tuners / QEMU)
    for cmd in (
        ["taskset", "-a", "-cp", cpuset, str(pid)],
        ["sudo", "-n", "taskset", "-a", "-cp", cpuset, str(pid)],
        ["sudo", "taskset", "-a", "-cp", cpuset, str(pid)],
        ["taskset", "-cp", cpuset, str(pid)],
        ["sudo", "-n", "taskset", "-cp", cpuset, str(pid)],
    ):
        run(cmd)
        after = affinity(pid)
        if expand(after) == expand(cpuset):
            return True, f"ok {before} -> {after}"
    return False, f"fail {before} want={cpuset} after={affinity(pid)}"


def affinity(pid: int | str) -> str:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("Cpus_allowed_list:"):
                return line.split(":", 1)[1].strip()
    except (OSError, FileNotFoundError):
        pass
    return "?"


def load_pg_map() -> dict:
    return json.loads(PG_MAP.read_text(encoding="utf-8"))


def apply_pg_script() -> dict:
    if not PG_APPLY.exists():
        return {"status": "missing_script", "path": str(PG_APPLY)}
    completed = run([sys.executable, str(PG_APPLY)])
    return {
        "status": "ok" if completed.returncode == 0 else "error",
        "returncode": completed.returncode,
        "stdout_tail": "\n".join(completed.stdout.splitlines()[-40:]),
    }


def mysql_formal_from_map(pg_map: dict) -> list[dict]:
    rows = []
    for row in pg_map.get("mysql_live_do_not_touch") or []:
        name = row["name"]
        cpuset = row["cpuset_cpus"]
        n = len(expand(cpuset))
        rows.append({"container": name, "cpuset": cpuset, "cpus": n})
    if rows:
        return rows
    # fallback
    return [
        {"container": "ra_mysql_gd_hybench_xp_auto6", "cpuset": full_phys(96, 8), "cpus": 16},
        {"container": "ra-mysql-d1-job-w2", "cpuset": full_phys(104, 8), "cpus": 16},
        {"container": "gptuner_mysql_benchbase", "cpuset": full_phys(112, 16), "cpus": 32},
    ]


def pin_host_control_processes() -> list[dict]:
    """Pin host tuners/clients into 0-15,256-271 (PG host_control_and_estimator)."""
    ps = run(["ps", "-eo", "pid,cmd"]).stdout.splitlines()
    # estimator / BF host first (binary_plan, grid optimizer, sgd host)
    estimator_cmds = (
        "binary_plan_resource_tuner.py",
        "double_loop_grid_optimizer.py",
        "mysql_double_loop_grid_runner.py",
        "double_loop_sgd_optimizer.py",
        "run_double_loop_sgd",
    )
    client_cmds = (
        "run_mysql_gd_matrix.py",
        "mysql_experiment_runner.py",
        "apply_pg_cpu_pins.py",
    )
    est_pids: list[int] = []
    client_pids: list[int] = []
    seen: set[int] = set()
    for ln in ps:
        if "build_and_apply_cpu_pins" in ln:
            continue
        parts = ln.strip().split(None, 1)
        if not parts or not parts[0].isdigit():
            continue
        pid = int(parts[0])
        if pid in seen:
            continue
        if any(x in ln for x in estimator_cmds):
            seen.add(pid)
            est_pids.append(pid)
        elif any(x in ln for x in client_cmds):
            seen.add(pid)
            client_pids.append(pid)

    results = []
    # 4-core slices inside phys 0-15 for estimators (prefer 0-11 for BF, leave 12-15 for OS)
    # slots: 0-3, 4-7, 8-11  (3 slots × 4 phys = 12); overflow pack into 0-7
    est_slots = [(0, 4), (4, 4), (8, 4)]
    for i, pid in enumerate(est_pids):
        start, n = est_slots[i % len(est_slots)]
        cs = full_phys(start, n)
        ok, detail = taskset_pid(pid, cs)
        results.append(
            {
                "role": "host_estimator",
                "pid": pid,
                "cpuset": cs,
                "status": "ok" if ok else "error",
                "detail": detail,
                "cmd": next((ln for ln in ps if ln.strip().startswith(str(pid))), "")[:140],
            }
        )

    # schedulers / drivers on 12-15
    for i, pid in enumerate(client_pids):
        cs = full_phys(12 + (i % 2) * 2, 2)  # 12-13 or 14-15
        ok, detail = taskset_pid(pid, cs)
        results.append(
            {
                "role": "host_client",
                "pid": pid,
                "cpuset": cs,
                "status": "ok" if ok else "error",
                "detail": detail,
            }
        )
    return results


def write_compat_plan(pg_map: dict, mysql_rows: list[dict], host_rows: list[dict], pg_result: dict) -> Path:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    plan = {
        "updated_at": now,
        "authority": {
            "pg_sgd_bruteforce_grid": str(PG_MAP),
            "pg_apply": str(PG_APPLY),
            "mysql_formal_only": str(ROOT / "scripts" / "build_and_apply_cpu_pins.py"),
        },
        "policy": (
            "PG map is source of truth for SGD VMs, bruteforce/fullgrid Docker, matrix slots, "
            "and host estimator zone 0-15. This MySQL script only reinforces mysql_reserved "
            "formal dockers and host processes in 0-15. Do not co-run MySQL grid/goal workers "
            "with fullgrid on phys 16-95."
        ),
        "zones_from_pg_map": pg_map.get("zones"),
        "mysql_formal": mysql_rows,
        "host_process_pins": host_rows,
        "pg_apply_result": {
            "status": pg_result.get("status"),
            "returncode": pg_result.get("returncode"),
        },
        "mutual_exclusion": [
            "fullgrid Docker phys 16-95 vs MySQL multi-worker grid/goal on same banks",
            "matrix slots 220-255 vs any ad-hoc use of those cores",
            "host estimators stay in 0-15; never 16-31 (fullgrid mem banks)",
        ],
    }
    out = ROOT / "config" / "mysql_cpu_pin_plan.json"
    out.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n")
    (ROOT / "config" / "mysql_cpu_pin_plan_20260720.json").write_text(out.read_text())
    if RA_ROOT.exists():
        (RA_ROOT / "config").mkdir(parents=True, exist_ok=True)
        (RA_ROOT / "config" / "host_cpu_pin_plan.json").write_text(out.read_text())
    return out


def write_memory(plan_path: Path, pg_map: dict) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    z = pg_map.get("zones") or {}
    mem = f"""# Project Memory

## Infrastructure / CPU Pinning (aligned with PG map)

Updated: {now}

### Authority
- **PG / SGD / bruteforce / fullgrid / grid+estimate:**  
  `resource_adaptive/shell/apply_pg_cpu_pins.py`  
  map: `resource_adaptive/config/pg_cpu_pin_map_20260720.json`
- **MySQL formal only (reinforce):**  
  `resource_adaptive_mysql/scripts/build_and_apply_cpu_pins.py`  
  (calls PG apply first, then pins MySQL formal + host 0-15)

### Zones (from PG map)
| Zone | cpuset | Purpose |
|------|--------|---------|
| host_control_and_estimator | {z.get('host_control_and_estimator',{}).get('cpuset_cpus')} | OS, binary_plan/grid host clients |
| bruteforce_fullgrid_docker | {z.get('bruteforce_fullgrid_docker',{}).get('cpuset_cpus')} | ra_fullgrid_* PG workers |
| mysql_reserved | {z.get('mysql_reserved',{}).get('cpuset_cpus')} | live MySQL formal GD |
| rootless_lane_pool | {z.get('rootless_lane_pool',{}).get('cpuset_cpus')} | double-loop SGD VMs |
| libvirt_postgre | {z.get('libvirt_postgre',{}).get('cpuset_cpus')} | guest=postgre |
| matrix_slots | {z.get('matrix_slots',{}).get('cpuset_cpus')} | SGD scheduler slots vm41-46 |

### MySQL formal (live)
| container | cpuset |
|-----------|--------|
| ra_mysql_gd_hybench_xp_auto6 | 96-103,352-359 |
| ra-mysql-d1-job-w2 | 104-111,360-367 |
| gptuner_mysql_benchbase | 112-127,368-383 |

### Host processes
- `binary_plan_resource_tuner.py` / grid optimizers → **phys 0-11** (4-core slices inside 0-15)
- `run_mysql_gd_matrix.py` / `mysql_experiment_runner.py` → **phys 12-15**
- Never pin host tuners onto 16-31 (fullgrid mem banks)

### Mutual exclusion
- Do **not** co-run MySQL multi-worker grid/goal dockers with fullgrid on phys **16-95**.
- Do **not** use `build_and_apply_cpu_pins.py` versions that reassign fullgrid/SGD (removed).

### Re-apply
```bash
# PG systems (primary)
cd /home/anonymous/program/resource_adaptive && python3 shell/apply_pg_cpu_pins.py

# MySQL formal + host (also invokes PG apply)
cd /home/anonymous/program/resource_adaptive_mysql && python3 scripts/build_and_apply_cpu_pins.py
```
"""
    (ROOT / "MEMORY.md").write_text(mem)
    if RA_ROOT.exists():
        (RA_ROOT / "MEMORY_CPU_PINS.md").write_text(mem)

    mem_root = Path.home() / ".grok" / "memory"
    mem_root.mkdir(parents=True, exist_ok=True)
    section = f"""
## resource_adaptive CPU pins (PG map authority)

Updated: {now}

Authority: `resource_adaptive/shell/apply_pg_cpu_pins.py` + `config/pg_cpu_pin_map_20260720.json`.
MySQL formal: phys 96-127 only (`resource_adaptive_mysql/scripts/build_and_apply_cpu_pins.py`).
Host binary_plan/grid estimators: phys **0-15** (not 16-31).
Fullgrid Docker: phys 16-95. SGD VMs: 128-153. Matrix slots: 220-255. postgre: 154-217.
Mutual exclusion: MySQL grid/goal vs fullgrid on 16-95.
"""
    global_md = mem_root / "MEMORY.md"
    if global_md.exists():
        text = global_md.read_text()
        markers = (
            "## resource_adaptive CPU pins (PG map authority)",
            "## resource_adaptive (+ mysql) — multi-method CPU pins",
            "## resource_adaptive_mysql — full CPU pin map",
            "## resource_adaptive_mysql — MySQL Docker CPU pins",
        )
        replaced = False
        for marker in markers:
            if marker in text:
                pre, rest = text.split(marker, 1)
                if "\n## " in rest[1:]:
                    _, after = rest[1:].split("\n## ", 1)
                    text = pre + section.lstrip() + "\n## " + after
                else:
                    text = pre + section.lstrip()
                replaced = True
                break
        if not replaced:
            text = text.rstrip() + "\n" + section
        global_md.write_text(text)
    else:
        global_md.write_text("# Global Memory\n" + section)

    for base in (str(ROOT), str(RA_ROOT)):
        h8 = hashlib.sha256(base.encode()).hexdigest()[:8]
        slug = Path(base).name
        for name in (f"{slug}-{h8}", f"home-anonymous-program-{slug}-{h8}"):
            d = mem_root / name
            d.mkdir(parents=True, exist_ok=True)
            (d / "sessions").mkdir(exist_ok=True)
            (d / "MEMORY.md").write_text(mem)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-pg-apply", action="store_true", help="Do not call apply_pg_cpu_pins.py")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not PG_MAP.exists():
        print(f"ERROR: missing PG map {PG_MAP}", file=sys.stderr)
        return 2

    pg_map = load_pg_map()
    pg_result: dict = {"status": "skipped"}
    if not args.skip_pg_apply and not args.dry_run:
        print("=== apply_pg_cpu_pins.py ===", flush=True)
        pg_result = apply_pg_script()
        print(pg_result.get("stdout_tail") or pg_result, flush=True)
        if pg_result.get("status") == "error":
            print("WARNING: PG apply returned non-zero; continuing MySQL formal pins", flush=True)

    mysql_rows = mysql_formal_from_map(pg_map)
    print("=== MySQL formal dockers ===", flush=True)
    mysql_results = []
    for row in mysql_rows:
        if args.dry_run:
            print(f"dry-run {row['container']} -> {row['cpuset']}", flush=True)
            mysql_results.append({**row, "status": "dry_run"})
            continue
        ok, detail = docker_update_cpuset(row["container"], row["cpuset"], row.get("cpus"))
        print(f"{'OK' if ok else 'ERR'} {detail}", flush=True)
        mysql_results.append({**row, "status": "ok" if ok else "error", "detail": detail})

    print("=== Host control/estimator processes (0-15) ===", flush=True)
    if args.dry_run:
        host_results = []
    else:
        host_results = pin_host_control_processes()
        for h in host_results:
            print(
                f"{h['status']:5} pid={h['pid']} {h.get('role')} cpuset={h['cpuset']} {h.get('detail','')}",
                flush=True,
            )

    plan_path = write_compat_plan(pg_map, mysql_results, host_results, pg_result)
    write_memory(plan_path, pg_map)

    wrapper = ROOT / "scripts" / "apply_mysql_cpu_pins.sh"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'ROOT="$(cd "$(dirname "$0")/.." && pwd)"\n'
        'exec python3 "$ROOT/scripts/build_and_apply_cpu_pins.py" "$@"\n'
    )
    wrapper.chmod(0o755)

    errors = sum(1 for r in mysql_results if r.get("status") == "error")
    errors += sum(1 for r in host_results if r.get("status") == "error")
    print(json.dumps({"plan": str(plan_path), "errors": errors, "pg": pg_result.get("status")}, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
