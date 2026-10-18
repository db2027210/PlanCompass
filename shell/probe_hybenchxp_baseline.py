#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from binary_plan_resource_tuner import (  # noqa: E402
    is_none_target,
    load_config,
    none_resource_ceiling_details,
    run_hybench_xp_validation,
)


def select_worker(cfg: Dict[str, Any], worker_name: str) -> Optional[Dict[str, Any]]:
    if worker_name in ("", "main", "none"):
        return None
    workers = (((cfg.get("binary_plan_tuning") or {}).get("validation") or {}).get("docker_workers") or {}).get("workers") or []
    for worker in workers:
        if str(worker.get("name")) == worker_name:
            return dict(worker)
    raise SystemExit(f"worker not found in config: {worker_name}")


def csv_ints(value: str, *, name: str, min_value: Optional[int] = None, max_value: Optional[int] = None) -> str:
    items = []
    for raw in str(value or "").split(","):
        item = raw.strip()
        if not item:
            continue
        try:
            parsed = int(item)
        except ValueError as exc:
            raise SystemExit(f"{name} must be comma-separated integers, got: {value}") from exc
        if min_value is not None and parsed < min_value:
            raise SystemExit(f"{name} contains {parsed}, below minimum {min_value}")
        if max_value is not None and parsed > max_value:
            raise SystemExit(f"{name} contains {parsed}, above maximum {max_value}")
        items.append(str(parsed))
    if not items:
        raise SystemExit(f"{name} must not be empty")
    return ",".join(items)


def statement_override_items(values: List[str], *, option_name: str) -> List[Tuple[str, str]]:
    items: List[Tuple[str, str]] = []
    for item in values:
        if "=" not in item:
            raise SystemExit(f"{option_name} must be SECTION=VALUE, got: {item}")
        section, value = item.split("=", 1)
        section = section.strip()
        if not section:
            raise SystemExit(f"{option_name} section is empty: {item}")
        items.append((section, value))
    return items


def param_override_items(values: List[str]) -> Dict[str, float]:
    allowed = {
        "seq_page_cost",
        "random_page_cost",
        "cpu_tuple_cost",
        "cpu_index_tuple_cost",
        "cpu_operator_cost",
        "parallel_setup_cost",
        "parallel_tuple_cost",
    }
    params: Dict[str, float] = {}
    for item in values:
        if "=" not in item:
            raise SystemExit(f"--param must be NAME=VALUE, got: {item}")
        name, value = item.split("=", 1)
        name = name.strip()
        if name not in allowed:
            raise SystemExit(f"--param {name!r} is not one of the 7 allowed planner cost params")
        try:
            params[name] = float(value.strip())
        except ValueError as exc:
            raise SystemExit(f"--param {name} value must be numeric, got: {value!r}") from exc
    return params


def replace_statement_sql(toml_text: str, section: str, sql: str) -> str:
    if '"""' in sql:
        raise SystemExit(f"SQL override for {section} must not contain triple double quotes")
    section_pattern = re.compile(rf"(?m)^\[{re.escape(section)}\]\s*$")
    section_match = section_pattern.search(toml_text)
    if not section_match:
        raise SystemExit(f"statement section not found: [{section}]")
    next_section_match = re.search(r"(?m)^\[[^\]]+\]\s*$", toml_text[section_match.end():])
    body_start = section_match.end()
    body_end = section_match.end() + next_section_match.start() if next_section_match else len(toml_text)
    body = toml_text[body_start:body_end]
    sql_pattern = re.compile(
        r"(?ms)^sql\s*=\s*(?:\"\"\".*?\"\"\"|\"(?:\\.|[^\"\\])*\"|\[.*?\])\s*",
        re.MULTILINE,
    )
    replacement = f'sql = """{sql.strip()}"""\n'
    if sql_pattern.search(body):
        new_body = sql_pattern.sub(replacement, body, count=1)
    else:
        if body and not body.endswith("\n"):
            body += "\n"
        new_body = body + replacement
    return toml_text[:body_start] + new_body + toml_text[body_end:]


def build_statement_profile(
    *,
    base_statement_file: Path,
    output_dir: Path,
    label: str,
    sql_overrides: List[Tuple[str, str]],
    sql_file_overrides: List[Tuple[str, str]],
) -> Path:
    if not base_statement_file.exists():
        raise SystemExit(f"statement file not found: {base_statement_file}")
    text = base_statement_file.read_text(encoding="utf-8")
    for section, sql in sql_overrides:
        text = replace_statement_sql(text, section, sql)
    for section, sql_path in sql_file_overrides:
        path = Path(sql_path).expanduser()
        if not path.exists():
            raise SystemExit(f"statement SQL file not found for {section}: {path}")
        text = replace_statement_sql(text, section, path.read_text(encoding="utf-8"))
    profile_dir = output_dir / "generated_statement_profiles"
    profile_dir.mkdir(parents=True, exist_ok=True)
    profile_path = profile_dir / f"{label}.toml"
    profile_path.write_text(text, encoding="utf-8")
    return profile_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one current-config HyBench XP baseline validation.")
    parser.add_argument("--config", default=str(ROOT_DIR / "config/binary_plan_tuning_hybenchxp.json"))
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--label", default="baseline_current_config")
    parser.add_argument("--worker", default="cpu_mem_w1")
    parser.add_argument("--target", default="", choices=["", "cpu", "memory", "io", "none"])
    parser.add_argument("--xtpclient", type=int, default=-1)
    parser.add_argument("--xapclient", type=int, default=-1)
    parser.add_argument("--xp-run-mins", type=int, default=0)
    parser.add_argument("--io-mode", default="", help="Temporarily override validation.docker_runtime.io_mode.")
    parser.add_argument(
        "--no-restore",
        action="store_true",
        help="Skip restore_from_template_before_validation for prepared concurrent replay workers.",
    )
    parser.add_argument("--ap-query-ids", default="", help="Set HyBench ap_query_ids, e.g. 6 or 1,3,6.")
    parser.add_argument("--ap-query-weights", default="", help="Set HyBench ap_query_weights; length must match ids.")
    parser.add_argument("--ap-query-sequence", default="", help="Set fixed HyBench ap_query_sequence, e.g. 6,6,6.")
    parser.add_argument("--ap-query-sequence-limit", type=int, default=-1)
    parser.add_argument("--statement-file", default="", help="Temporarily override the HyBench statement TOML file.")
    parser.add_argument(
        "--statement-sql",
        action="append",
        default=[],
        metavar="SECTION=SQL",
        help="Create a temporary statement TOML by replacing one section SQL, e.g. AP-6='select count(*) from transfer'.",
    )
    parser.add_argument(
        "--statement-sql-file",
        action="append",
        default=[],
        metavar="SECTION=PATH",
        help="Create a temporary statement TOML by replacing one section SQL with file contents.",
    )
    parser.add_argument(
        "--setup-sql",
        action="append",
        default=[],
        metavar="SQL",
        help="Execute setup SQL after optional restore and before applying params/checkpointing.",
    )
    parser.add_argument(
        "--setup-sql-file",
        action="append",
        default=[],
        metavar="PATH",
        help="Execute setup SQL file after optional restore and before applying params/checkpointing.",
    )
    parser.add_argument(
        "--at-percent-mix",
        default="",
        help="Set at1_percent..at6_percent as six comma-separated integers summing to 100.",
    )
    parser.add_argument(
        "--extra-prop",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Temporarily override/add one HyBench property in generated props.",
    )
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="Temporarily override one of the 7 PostgreSQL planner cost params for this probe.",
    )
    parser.add_argument("--check-none-gate", action="store_true")
    parser.add_argument(
        "--ignore-sigterm",
        action="store_true",
        help="Record external SIGTERM but keep the validation running; intended for isolated neutral probes.",
    )
    args = parser.parse_args()

    forbidden_cli = []
    if args.ap_query_ids:
        forbidden_cli.append("--ap-query-ids")
    if args.ap_query_weights:
        forbidden_cli.append("--ap-query-weights")
    if args.ap_query_sequence:
        forbidden_cli.append("--ap-query-sequence")
    if args.ap_query_sequence_limit >= 0:
        forbidden_cli.append("--ap-query-sequence-limit")
    if args.extra_prop:
        forbidden_cli.append("--extra-prop")
    if args.statement_file:
        forbidden_cli.append("--statement-file")
    if args.statement_sql:
        forbidden_cli.append("--statement-sql")
    if args.statement_sql_file:
        forbidden_cli.append("--statement-sql-file")
    if args.setup_sql:
        forbidden_cli.append("--setup-sql")
    if args.setup_sql_file:
        forbidden_cli.append("--setup-sql-file")
    if forbidden_cli:
        parser.error(
            "2026-08-06 workload policy forbids XP query selection, SQL rewrites, "
            f"setup SQL, and arbitrary properties: {', '.join(forbidden_cli)}"
        )

    target = args.target.strip().lower()
    label = args.label
    if target and label == "baseline_current_config":
        label = f"{target}_baseline_current_config"

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    marker_path = output_dir / "probe_progress.jsonl"

    def marker(event: str, **extra: Any) -> None:
        payload = {"time": time.time(), "event": event, "label": label, "worker": args.worker}
        payload.update(extra)
        with marker_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    marker("probe_start", config=args.config)
    cfg = load_config(args.config)
    if args.io_mode:
        docker_cfg = (((cfg.get("binary_plan_tuning") or {}).get("validation") or {}).get("docker_runtime") or {})
        docker_cfg["io_mode"] = args.io_mode.strip()
        marker("io_mode_override_applied", io_mode=docker_cfg.get("io_mode"))
    hybench_cfg = (((cfg.get("binary_plan_tuning") or {}).get("validation") or {}).get("hybench_xp") or {})
    policy_scopes = [("hybench_xp", hybench_cfg)]
    if target:
        target_scope = (hybench_cfg.get("target_overrides") or {}).get(target) or {}
        policy_scopes.append((f"target_overrides.{target}", target_scope))
    forbidden_property_names = {
        "ap_query_ids",
        "ap_query_weights",
        "ap_query_sequence",
        "ap_query_sequence_limit",
    }
    for scope_name, scope in policy_scopes:
        extra_props = scope.get("extra_props") or {}
        forbidden_properties = sorted(forbidden_property_names.intersection(extra_props))
        if forbidden_properties:
            raise SystemExit(
                f"2026-08-06 workload policy violation in {scope_name}.extra_props: "
                f"{forbidden_properties}"
            )
        if scope.get("setup_sql") or scope.get("setup_sql_files"):
            raise SystemExit(f"2026-08-06 workload policy forbids XP setup SQL in {scope_name}")
    if args.no_restore:
        hybench_cfg["restore_from_template_before_validation"] = False
        if target:
            target_override = hybench_cfg.setdefault("target_overrides", {}).setdefault(target, {})
            target_override["restore_from_template_before_validation"] = False
        marker("restore_disabled_for_probe")
    has_workload_override = (
        args.xtpclient >= 0
        or args.xapclient >= 0
        or args.xp_run_mins > 0
        or args.extra_prop
        or args.ap_query_ids
        or args.ap_query_weights
        or args.ap_query_sequence
        or args.ap_query_sequence_limit >= 0
        or args.statement_file
        or args.statement_sql
        or args.statement_sql_file
        or args.setup_sql
        or args.setup_sql_file
        or args.at_percent_mix
    )
    if target and has_workload_override:
        overrides = hybench_cfg.setdefault("target_overrides", {})
        target_override = overrides.setdefault(target, {})
        if args.xtpclient >= 0:
            target_override["xtpclient"] = args.xtpclient
            hybench_cfg["xtpclient"] = args.xtpclient
        if args.xapclient >= 0:
            target_override["xapclient"] = args.xapclient
            hybench_cfg["xapclient"] = args.xapclient
        if args.xp_run_mins > 0:
            hybench_cfg["xp_run_mins"] = args.xp_run_mins
        if args.extra_prop or args.ap_query_ids or args.ap_query_weights or args.ap_query_sequence or args.ap_query_sequence_limit >= 0 or args.at_percent_mix:
            extra_props = target_override.setdefault("extra_props", {})
            direct_extra_props = hybench_cfg.setdefault("extra_props", {})
            for item in args.extra_prop:
                if "=" not in item:
                    raise SystemExit(f"--extra-prop must be KEY=VALUE, got: {item}")
                key, value = item.split("=", 1)
                key = key.strip()
                if not key:
                    raise SystemExit(f"--extra-prop key is empty: {item}")
                value = value.strip()
                extra_props[key] = value
                direct_extra_props[key] = value
            query_max = 6 if target in ("cpu", "io", "none") else 13
            if args.ap_query_ids:
                value = csv_ints(
                    args.ap_query_ids,
                    name="--ap-query-ids",
                    min_value=1,
                    max_value=query_max,
                )
                extra_props["ap_query_ids"] = value
                direct_extra_props["ap_query_ids"] = value
            if args.ap_query_weights:
                value = csv_ints(args.ap_query_weights, name="--ap-query-weights", min_value=1)
                extra_props["ap_query_weights"] = value
                direct_extra_props["ap_query_weights"] = value
                if args.ap_query_ids:
                    id_count = len(extra_props["ap_query_ids"].split(","))
                    weight_count = len(extra_props["ap_query_weights"].split(","))
                    if id_count != weight_count:
                        raise SystemExit(
                            f"--ap-query-weights length ({weight_count}) must match --ap-query-ids length ({id_count})"
                        )
            if args.ap_query_sequence:
                value = csv_ints(
                    args.ap_query_sequence,
                    name="--ap-query-sequence",
                    min_value=1,
                    max_value=query_max,
                )
                extra_props["ap_query_sequence"] = value
                direct_extra_props["ap_query_sequence"] = value
            if args.ap_query_sequence_limit >= 0:
                value = str(args.ap_query_sequence_limit)
                extra_props["ap_query_sequence_limit"] = value
                direct_extra_props["ap_query_sequence_limit"] = value
            if args.at_percent_mix:
                mix = [int(part) for part in csv_ints(args.at_percent_mix, name="--at-percent-mix", min_value=0).split(",")]
                if len(mix) != 6:
                    raise SystemExit("--at-percent-mix must contain exactly six integers")
                if sum(mix) != 100:
                    raise SystemExit(f"--at-percent-mix must sum to 100, got {sum(mix)}")
                for index, percent in enumerate(mix, start=1):
                    value = str(percent)
                    extra_props[f"at{index}_percent"] = value
                    direct_extra_props[f"at{index}_percent"] = value
        if args.setup_sql or args.setup_sql_file:
            if args.setup_sql:
                target_override["setup_sql"] = list(args.setup_sql)
                hybench_cfg["setup_sql"] = list(args.setup_sql)
            if args.setup_sql_file:
                setup_sql_files = [str(Path(path).expanduser()) for path in args.setup_sql_file]
                target_override["setup_sql_files"] = setup_sql_files
                hybench_cfg["setup_sql_files"] = setup_sql_files
        marker(
            "target_override_applied",
            target=target,
            xtpclient=target_override.get("xtpclient"),
            xapclient=target_override.get("xapclient"),
            xp_run_mins=hybench_cfg.get("xp_run_mins"),
            extra_props=target_override.get("extra_props"),
            setup_sql_files=target_override.get("setup_sql_files"),
            setup_sql_count=len(target_override.get("setup_sql") or []),
        )
    elif args.setup_sql or args.setup_sql_file:
        if args.setup_sql:
            hybench_cfg["setup_sql"] = list(args.setup_sql)
        if args.setup_sql_file:
            hybench_cfg["setup_sql_files"] = [str(Path(path).expanduser()) for path in args.setup_sql_file]
        marker(
            "setup_sql_override_applied",
            setup_sql_files=hybench_cfg.get("setup_sql_files"),
            setup_sql_count=len(hybench_cfg.get("setup_sql") or []),
        )
    if args.statement_file or args.statement_sql or args.statement_sql_file:
        if target:
            overrides = hybench_cfg.setdefault("target_overrides", {})
            statement_cfg = overrides.setdefault(target, {})
        else:
            statement_cfg = hybench_cfg
        base_statement_file = Path(
            str(
                args.statement_file
                or statement_cfg.get("statement_file")
                or hybench_cfg.get("statement_file")
                or "/home/anonymous/HyBench-2024-master/conf/stmt_postgres.toml"
            )
        ).expanduser()
        if args.statement_sql or args.statement_sql_file:
            statement_profile = build_statement_profile(
                base_statement_file=base_statement_file,
                output_dir=output_dir,
                label=label,
                sql_overrides=statement_override_items(args.statement_sql, option_name="--statement-sql"),
                sql_file_overrides=statement_override_items(args.statement_sql_file, option_name="--statement-sql-file"),
            )
            statement_cfg["statement_file"] = str(statement_profile)
            hybench_cfg["statement_file"] = str(statement_profile)
            marker(
                "statement_profile_generated",
                base_statement_file=str(base_statement_file),
                statement_file=str(statement_profile),
            )
        else:
            statement_cfg["statement_file"] = str(base_statement_file)
            hybench_cfg["statement_file"] = str(base_statement_file)
            marker("statement_file_override_applied", statement_file=str(base_statement_file))
    marker("config_loaded")
    params = dict(cfg.get("initial_params") or {})
    param_overrides = param_override_items(args.param)
    if param_overrides:
        params.update(param_overrides)
        marker("param_overrides_applied", params=param_overrides)
    signal_path = output_dir / "probe_signal.jsonl"

    def handle_signal(signum: int, _frame: Any) -> None:
        with signal_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "time": time.time(),
                        "signal": signum,
                        "label": label,
                        "worker": args.worker,
                        "ignored": bool(args.ignore_sigterm and signum == signal.SIGTERM),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        if args.ignore_sigterm and signum == signal.SIGTERM:
            marker("sigterm_ignored_for_probe")
            return
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    marker("signal_handlers_installed")

    worker = select_worker(cfg, args.worker)
    marker("worker_selected", selected_worker=(worker or {}).get("name"))
    marker("validation_call_start")
    result = run_hybench_xp_validation(
        cfg,
        params,
        output_dir=output_dir,
        label=label,
        worker=worker,
    )
    if args.check_none_gate or is_none_target(target):
        result["none_baseline_hard_gate"] = none_resource_ceiling_details(result, cfg)
        result["none_baseline_hard_gate_ok"] = bool(result["none_baseline_hard_gate"].get("ok"))
    result_path = output_dir / f"{label}.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": result.get("status"),
        "label": label,
        "result_path": str(result_path),
        "elapsed_s": result.get("elapsed_s"),
        "resource_stats": result.get("resource_stats"),
        "resources": result.get("resources"),
        "none_baseline_hard_gate": result.get("none_baseline_hard_gate"),
        "none_baseline_hard_gate_ok": result.get("none_baseline_hard_gate_ok"),
        "db_qps": result.get("db_qps"),
        "hybench": result.get("hybench"),
        "error": result.get("error"),
        "returncode": result.get("returncode"),
    }, ensure_ascii=False), flush=True)
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
