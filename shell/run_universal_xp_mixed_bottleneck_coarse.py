#!/usr/bin/env python3
"""Run one HyBench XP mixed 16:8 workload against cpu/memory/io bottleneck profiles."""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


DEFAULT_SPEC = "config/optimizer_config_universal_hybench_xp_mixed_16_8_coarse.json"
DEFAULT_PROFILES = ["io", "memory", "cpu"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate cpu/memory/io bottleneck configs from one XP 16:8 spec and run coarse tuning."
    )
    parser.add_argument("--spec", default=DEFAULT_SPEC, help="Universal workload spec JSON.")
    parser.add_argument(
        "--profiles",
        default=",".join(DEFAULT_PROFILES),
        help="Comma-separated profile order, e.g. io,memory,cpu.",
    )
    parser.add_argument(
        "--runner",
        choices=["conda", "python"],
        default="conda",
        help="Use conda env or current Python to run parameter_optimizer.py.",
    )
    parser.add_argument("--conda-env", default="resource_adaptive", help="Conda env name when --runner=conda.")
    parser.add_argument("--dry-run", action="store_true", help="Only generate configs and print commands.")
    parser.add_argument(
        "--skip-calibration",
        action="store_true",
        help="Skip the baseline calibration pass and use capability values from the spec.",
    )
    parser.add_argument(
        "--calibration-target-util",
        type=float,
        default=None,
        help="Target initial utilization for the bottleneck resource after calibration.",
    )
    parser.add_argument(
        "--calibration-nontarget-util",
        type=float,
        default=None,
        help="Target initial utilization ceiling for non-bottleneck resources after calibration.",
    )
    parser.add_argument(
        "--calibration-capability-policy",
        choices=["exact", "floor"],
        default=None,
        help=(
            "How to set calibrated non-target capability. 'exact' uses observed/non-target-util; "
            "'floor' keeps at least the spec capability."
        ),
    )
    parser.add_argument(
        "--continue-on-failure",
        action="store_true",
        help="Continue to the next profile even if a run fails.",
    )
    return parser.parse_args()


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def build_workload_cmd(workload: Dict[str, Any]) -> str:
    hybench_dir = str(workload["hybench_dir"])
    tmp_props = str(workload.get("tmp_props", "conf/pg.props.runxp.universal_16_8.tmp"))
    mode = str(workload.get("mode", "runxp")).strip().lower()
    tp_clients = int(workload.get("tp_clients", 16))
    ap_clients = int(workload.get("ap_clients", 8))
    run_mins = int(workload.get("run_mins", 10))
    random_seed = int(workload.get("random_seed", 4508333))
    ap_query_sequence_raw = workload.get("ap_query_sequence", "6,12,8,6,12,13,6,8,12,6")
    ap_query_sequence = "" if ap_query_sequence_raw is None else str(ap_query_sequence_raw).strip()
    progress_windows = int(workload.get("progress_windows", 10))
    url_ap = str(workload.get("url_ap", ""))

    parts = [
        f"cd {hybench_dir}",
        f"tmp={tmp_props}",
        'cp conf/pg.props "$tmp"',
        f"sed -i 's/^random_seed=.*/random_seed={random_seed}/' \"$tmp\"",
    ]
    if mode == "runap":
        parts.extend(
            [
                f"sed -i 's/^apclient=.*/apclient={ap_clients}/' \"$tmp\"",
                f"sed -i 's/^apRunMins=.*/apRunMins={run_mins}/' \"$tmp\"",
            ]
        )
    else:
        parts.extend(
            [
                f"sed -i 's/^xtpclient=.*/xtpclient={tp_clients}/' \"$tmp\"",
                f"sed -i 's/^xapclient=.*/xapclient={ap_clients}/' \"$tmp\"",
                f"sed -i 's/^xpRunMins=.*/xpRunMins={run_mins}/' \"$tmp\"",
            ]
        )
    if url_ap:
        parts.append(f"sed -i 's#^url_ap=.*#url_ap={url_ap}#' \"$tmp\"")
    parts.append("sed -i '/^ap_query_ids=/d;/^ap_query_weights=/d;/^ap_query_sequence=/d;/^ap_query_sequence_limit=/d' \"$tmp\"")
    if ap_query_sequence and ap_query_sequence.lower() not in {"all", "full"}:
        parts.append(
            "printf '\\n"
            f"ap_query_sequence={ap_query_sequence}\\n"
            f"progress_windows={progress_windows}\\n"
            "' >> \"$tmp\""
        )
    elif progress_windows > 0:
        parts.append(f"printf '\\nprogress_windows={progress_windows}\\n' >> \"$tmp\"")
    parts.append(f'bash hybench -t {mode} -c "$tmp" -f conf/stmt_postgres.toml')
    return " && ".join(parts)


def update_config_for_profile(
    *,
    project_root: Path,
    base_config: Dict[str, Any],
    spec: Dict[str, Any],
    profile_name: str,
    profile: Dict[str, Any],
    generated_config_path: Path,
) -> Dict[str, Any]:
    cfg = deep_merge(base_config, spec.get("coarse_strategy") or {})
    cfg = deep_merge(cfg, profile.get("config_overrides") or {})

    history_prefix = str(spec.get("history_prefix", "universal_xp_16_8"))
    workload = deep_merge(spec.get("workload") or {}, profile.get("workload") or {})
    tmp_props = str(workload.get("tmp_props", "conf/pg.props.runxp.universal_16_8.tmp"))

    cfg["capability"] = copy.deepcopy(profile["capability"])
    cfg["vm_resource"] = copy.deepcopy(profile["vm_resource"])
    cfg["benchmark"] = cfg.get("benchmark") or {}
    cfg["benchmark"]["workload_cmd"] = build_workload_cmd(workload)
    cfg["benchmark"]["warmup_enabled"] = False

    cfg["bottleneck_calibration"] = cfg.get("bottleneck_calibration") or {}
    cfg["bottleneck_calibration"]["enabled"] = False
    cfg["bottleneck_calibration"]["resource"] = str(profile.get("target_resource", profile_name))

    cfg["logging"] = cfg.get("logging") or {}
    cfg["logging"]["history_path"] = f"history/{history_prefix}_{profile_name}_coarse_history.jsonl"

    cfg["plan_capture"] = cfg.get("plan_capture") or {}
    cfg["plan_capture"]["output_dir"] = f"history/{history_prefix}_{profile_name}_coarse_plan_captures"

    cfg["auto_explain"] = cfg.get("auto_explain") or {}
    cfg["auto_explain"]["output_dir"] = f"history/{history_prefix}_{profile_name}_coarse_auto_explain"

    cfg["pre_optimizer_process_cleanup"] = cfg.get("pre_optimizer_process_cleanup") or {}
    patterns = list(cfg["pre_optimizer_process_cleanup"].get("patterns") or [])
    if tmp_props not in patterns:
        patterns.append(tmp_props)
    cfg["pre_optimizer_process_cleanup"]["patterns"] = patterns

    recovery = cfg.get("pre_benchmark_recovery") or {}
    snapshot_restore = recovery.get("snapshot_restore") or {}
    command = str(snapshot_restore.get("command") or "")
    if command:
        abs_config = generated_config_path.resolve()
        tokens = command.split()
        updated_tokens = []
        replaced = False
        for token in tokens:
            if token.startswith("OPTIMIZER_CONFIG_PATH="):
                updated_tokens.append(f"OPTIMIZER_CONFIG_PATH={abs_config}")
                replaced = True
            else:
                updated_tokens.append(token)
        if not replaced:
            updated_tokens.insert(0, f"OPTIMIZER_CONFIG_PATH={abs_config}")
        snapshot_restore["command"] = " ".join(updated_tokens)
    recovery["snapshot_restore"] = snapshot_restore
    cfg["pre_benchmark_recovery"] = recovery

    cfg["_generated_from"] = {
        "spec": str((project_root / DEFAULT_SPEC).resolve()) if spec else "",
        "profile": profile_name,
        "target_resource": str(profile.get("target_resource", profile_name)),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    return cfg


def make_calibration_config(config: Dict[str, Any], *, history_path: str) -> Dict[str, Any]:
    cfg = copy.deepcopy(config)
    cfg["logging"] = cfg.get("logging") or {}
    cfg["logging"]["history_path"] = history_path

    optimizer = cfg.get("optimizer") or {}
    gradient = optimizer.get("gradient") or {}
    gradient["max_iter"] = 0
    gradient["aggressive_trial"] = {
        **(gradient.get("aggressive_trial") or {}),
        "enabled": False,
    }
    optimizer["gradient"] = gradient
    cfg["optimizer"] = optimizer

    cfg["final_evaluation"] = cfg.get("final_evaluation") or {}
    cfg["final_evaluation"]["run_validation"] = False
    cfg["final_evaluation"]["degradation_check_enabled"] = False
    cfg["plan_capture"] = cfg.get("plan_capture") or {}
    cfg["plan_capture"]["enabled"] = False
    return cfg


def calibrated_capability(
    *,
    profile_capability: Dict[str, Any],
    initial_resource: Dict[str, Any],
    target_resource: str,
    target_initial_util: float,
    non_target_initial_util: float,
    capability_policy: str = "floor",
) -> Dict[str, float]:
    target_initial_util = max(1e-9, float(target_initial_util))
    non_target_initial_util = max(1e-9, float(non_target_initial_util))
    capability_policy = str(capability_policy or "floor").strip().lower()
    capability: Dict[str, float] = {}
    for resource_name, configured_cap in profile_capability.items():
        configured_value = as_float(configured_cap)
        observed_value = as_float(initial_resource.get(resource_name))
        if observed_value is None or observed_value <= 0:
            if configured_value is not None:
                capability[resource_name] = configured_value
            continue
        if resource_name == target_resource:
            capability[resource_name] = observed_value / target_initial_util
        else:
            calibrated_value = observed_value / non_target_initial_util
            if capability_policy == "exact":
                capability[resource_name] = calibrated_value
            else:
                capability[resource_name] = max(configured_value or 0.0, calibrated_value)
    return capability


def print_calibration_result(
    *,
    profile_name: str,
    target_resource: str,
    initial_resource: Dict[str, Any],
    old_capability: Dict[str, Any],
    new_capability: Dict[str, Any],
) -> None:
    print("\n" + "-" * 88)
    print(f"[{profile_name}] calibration result")
    print("-" * 88)
    for resource_name, new_cap in new_capability.items():
        observed = as_float(initial_resource.get(resource_name))
        old_cap = as_float(old_capability.get(resource_name))
        new_cap_float = as_float(new_cap)
        old_util = (
            observed / old_cap
            if observed is not None and old_cap not in (None, 0)
            else None
        )
        new_util = (
            observed / new_cap_float
            if observed is not None and new_cap_float not in (None, 0)
            else None
        )
        marker = "target" if resource_name == target_resource else "non-target"
        print(
            f"{resource_name:<8} {marker:<10} observed={fmt(observed)} "
            f"capability={fmt(old_cap)} -> {fmt(new_cap_float)} "
            f"util={fmt(old_util)} -> {fmt(new_util)}"
        )
    print("-" * 88 + "\n")


def run_with_tee(command: List[str], *, env: Dict[str, str], log_path: Path, cwd: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log_fp:
        proc = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="")
            log_fp.write(line)
            log_fp.flush()
        return proc.wait()


def optimizer_command(args: argparse.Namespace) -> List[str]:
    if args.runner == "conda":
        return [
            "conda",
            "run",
            "--no-capture-output",
            "-n",
            str(args.conda_env),
            "python",
            "parameter_optimizer.py",
        ]
    return [sys.executable, "parameter_optimizer.py"]


def extract_summary(project_root: Path, log_path: Path, summary_path: Path) -> Optional[Dict[str, Any]]:
    cmd = [
        sys.executable,
        "shell/extract_final_summary.py",
        str(log_path),
        "--output-json",
        str(summary_path),
    ]
    result = subprocess.run(cmd, cwd=str(project_root), text=True)
    if result.returncode != 0 or not summary_path.exists():
        return None
    return load_json(summary_path)


def as_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def fmt(value: Optional[float]) -> str:
    return "NA" if value is None else f"{value:.4f}"


def print_profile_result(profile_name: str, target_resource: str, summary: Optional[Dict[str, Any]]) -> None:
    print("\n" + "=" * 88)
    print(f"[{profile_name}] coarse tuning result")
    print("=" * 88)
    if summary is None:
        print("summary: not found")
        return

    initial_util = summary.get("initial_utilization") or {}
    final_util = summary.get("final_utilization") or {}
    initial_resource = summary.get("initial_resource") or {}
    final_resource = summary.get("final_resource") or {}
    degradation_summary = summary.get("degradation_summary") or {}

    init_util = as_float(initial_util.get(target_resource))
    final_util_value = as_float(final_util.get(target_resource))
    init_resource = as_float(initial_resource.get(target_resource))
    final_resource_value = as_float(final_resource.get(target_resource))
    util_delta = (
        final_util_value - init_util
        if final_util_value is not None and init_util is not None
        else None
    )

    print(f"target_resource: {target_resource}")
    print(f"target_resource_value: {fmt(init_resource)} -> {fmt(final_resource_value)}")
    print(f"target_utilization: {fmt(init_util)} -> {fmt(final_util_value)} (delta={fmt(util_delta)})")
    print(f"score: {fmt(as_float(summary.get('initial_score')))} -> {fmt(as_float(summary.get('final_score')))}")
    print(f"score_improvement_pct: {fmt(as_float(summary.get('score_improvement_pct')))}")
    print(f"iterations: {summary.get('iterations')}")
    print(f"degraded_sql_count: {degradation_summary.get('degraded_count')}")
    print(f"max_degradation_pct: {fmt(as_float(degradation_summary.get('max_degradation_pct')))}")
    print("=" * 88 + "\n")


def selected_profiles(spec: Dict[str, Any], profile_names: Iterable[str]) -> List[tuple[str, Dict[str, Any]]]:
    profiles = spec.get("profiles") or {}
    out: List[tuple[str, Dict[str, Any]]] = []
    for name in profile_names:
        profile = profiles.get(name)
        if not isinstance(profile, dict):
            raise KeyError(f"profile '{name}' not found in spec")
        out.append((name, profile))
    return out


def main() -> int:
    args = parse_args()
    project_root = Path(__file__).resolve().parents[1]
    spec_path = (project_root / args.spec).resolve()
    spec = load_json(spec_path)
    base_config_path = (project_root / str(spec["base_config"])).resolve()
    base_config = load_json(base_config_path)

    generated_dir = (project_root / str(spec.get("generated_config_dir", "config/generated"))).resolve()
    history_prefix = str(spec.get("history_prefix", "universal_xp_16_8"))
    calibration_cfg = spec.get("calibration") or {}
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    profile_names = [item.strip() for item in str(args.profiles).split(",") if item.strip()]

    for profile_name, profile in selected_profiles(spec, profile_names):
        target_resource = str(profile.get("target_resource", profile_name))
        profile_calibration_cfg = deep_merge(calibration_cfg, profile.get("calibration") or {})
        calibration_enabled = bool(profile_calibration_cfg.get("enabled", True)) and not args.skip_calibration
        calibration_target_util = (
            float(args.calibration_target_util)
            if args.calibration_target_util is not None
            else float(profile_calibration_cfg.get("target_initial_util", 0.9))
        )
        calibration_nontarget_util = (
            float(args.calibration_nontarget_util)
            if args.calibration_nontarget_util is not None
            else float(profile_calibration_cfg.get("non_target_initial_util", 0.5))
        )
        calibration_capability_policy = (
            str(args.calibration_capability_policy)
            if args.calibration_capability_policy is not None
            else str(profile_calibration_cfg.get("capability_policy", "floor"))
        ).strip().lower()
        generated_config_path = generated_dir / f"optimizer_config_{history_prefix}_{profile_name}_coarse.json"
        calibration_config_path = generated_dir / f"optimizer_config_{history_prefix}_{profile_name}_calibration.json"
        calibration_log_path = project_root / "history" / f"run_{history_prefix}_{profile_name}_calibration_{timestamp}.log"
        calibration_summary_path = project_root / "history" / f"summary_{history_prefix}_{profile_name}_calibration_{timestamp}.json"
        run_log_path = project_root / "history" / f"run_{history_prefix}_{profile_name}_coarse_{timestamp}.log"
        summary_path = project_root / "history" / f"summary_{history_prefix}_{profile_name}_coarse_{timestamp}.json"
        generated_config = update_config_for_profile(
            project_root=project_root,
            base_config=base_config,
            spec=spec,
            profile_name=profile_name,
            profile=profile,
            generated_config_path=generated_config_path,
        )
        generated_config["_generated_from"]["spec"] = str(spec_path)

        command = optimizer_command(args)
        env = os.environ.copy()

        if calibration_enabled:
            calibration_base_config = update_config_for_profile(
                project_root=project_root,
                base_config=base_config,
                spec=spec,
                profile_name=profile_name,
                profile=profile,
                generated_config_path=calibration_config_path,
            )
            calibration_base_config["_generated_from"]["spec"] = str(spec_path)
            calibration_config = make_calibration_config(
                calibration_base_config,
                history_path=f"history/{history_prefix}_{profile_name}_calibration_history.jsonl",
            )
            write_json(calibration_config_path, calibration_config)

            print("\n" + "#" * 88)
            print(f"Profile: {profile_name} calibration")
            print(f"Target resource: {target_resource}")
            print(f"Calibration config: {calibration_config_path}")
            print(f"Calibration log: {calibration_log_path}")
            print("Command: " + " ".join(command))
            print("#" * 88 + "\n")

            if not args.dry_run:
                calibration_env = env.copy()
                calibration_env["OPTIMIZER_CONFIG"] = str(calibration_config_path)
                calibration_env["OPTIMIZER_CONFIG_PATH"] = str(calibration_config_path)
                rc = run_with_tee(
                    command,
                    env=calibration_env,
                    log_path=calibration_log_path,
                    cwd=project_root,
                )
                calibration_summary = extract_summary(
                    project_root,
                    calibration_log_path,
                    calibration_summary_path,
                )
                if rc != 0:
                    print(f"[{profile_name}] calibration exited with code {rc}")
                    if not args.continue_on_failure:
                        return rc
                if calibration_summary is None:
                    print(f"[{profile_name}] calibration summary not found; use spec capability")
                else:
                    initial_resource = calibration_summary.get("initial_resource") or {}
                    old_capability = copy.deepcopy(generated_config.get("capability") or {})
                    new_capability = calibrated_capability(
                        profile_capability=old_capability,
                        initial_resource=initial_resource,
                        target_resource=target_resource,
                        target_initial_util=calibration_target_util,
                        non_target_initial_util=calibration_nontarget_util,
                        capability_policy=calibration_capability_policy,
                    )
                    generated_config["capability"] = new_capability
                    generated_config["_generated_from"]["calibration"] = {
                        "enabled": True,
                        "target_initial_util": calibration_target_util,
                        "non_target_initial_util": calibration_nontarget_util,
                        "capability_policy": calibration_capability_policy,
                        "log_path": str(calibration_log_path),
                        "summary_path": str(calibration_summary_path),
                        "initial_resource": initial_resource,
                        "old_capability": old_capability,
                        "new_capability": new_capability,
                    }
                    print_calibration_result(
                        profile_name=profile_name,
                        target_resource=target_resource,
                        initial_resource=initial_resource,
                        old_capability=old_capability,
                        new_capability=new_capability,
                    )
        else:
            generated_config["_generated_from"]["calibration"] = {"enabled": False}

        write_json(generated_config_path, generated_config)
        env["OPTIMIZER_CONFIG"] = str(generated_config_path)
        env["OPTIMIZER_CONFIG_PATH"] = str(generated_config_path)

        print("\n" + "#" * 88)
        print(f"Profile: {profile_name}")
        print(f"Target resource: {target_resource}")
        print(f"Generated config: {generated_config_path}")
        print(f"Run log: {run_log_path}")
        print("Command: " + " ".join(command))
        print("#" * 88 + "\n")

        if args.dry_run:
            continue

        rc = run_with_tee(command, env=env, log_path=run_log_path, cwd=project_root)
        summary = extract_summary(project_root, run_log_path, summary_path)
        print_profile_result(
            profile_name,
            target_resource,
            summary,
        )
        if rc != 0:
            print(f"[{profile_name}] parameter_optimizer.py exited with code {rc}")
            if not args.continue_on_failure:
                return rc

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
