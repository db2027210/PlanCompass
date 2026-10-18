#!/usr/bin/env python3
"""Issue fresh reciprocal paired qualification with one sealed H adapter."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import time


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "issuer_v1", str(HERE / "issue_mysql_shared_qualification_headroom_v1.py")
)
v1 = importlib.util.module_from_spec(spec); spec.loader.exec_module(v1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("--tpch-old-command", "--tpch-old-authority",
                 "--tpcc-old-command", "--tpcc-old-authority",
                 "--fresh-token-file", "--tpch-output-dir", "--tpcc-output-dir"):
        parser.add_argument(name, type=Path, required=True)
    parser.add_argument("--tpch-old-attempt", required=True)
    parser.add_argument("--tpcc-old-attempt", required=True)
    parser.add_argument("--tpch-new-attempt", required=True)
    parser.add_argument("--tpcc-new-attempt", required=True)
    parser.add_argument("--collector-bundle-id", required=True)
    parser.add_argument("--collector-manifest-sha256", required=True)
    parser.add_argument("--adapter-bundle-id", required=True)
    parser.add_argument("--adapter-path", type=Path, required=True)
    parser.add_argument("--adapter-sha256", required=True)
    parser.add_argument("--lifetime-seconds", type=int, default=1800)
    args = parser.parse_args()
    attempts = (args.tpch_old_attempt, args.tpcc_old_attempt,
                args.tpch_new_attempt, args.tpcc_new_attempt)
    hexes = (args.collector_bundle_id, args.collector_manifest_sha256,
             args.adapter_bundle_id, args.adapter_sha256)
    if (any(len(value) < 32 or not value.replace("-", "").isalnum()
            for value in attempts) or len(set(attempts)) != 4
            or any(len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
                   for value in hexes)
            or not 600 <= args.lifetime_seconds <= 3600):
        raise v1.IssueError("issuer identity or TTL differs")
    h = v1.load_sealed(args.tpch_old_command, "TPC-H old command")
    ha = v1.load_sealed(args.tpch_old_authority, "TPC-H old authority")
    c = v1.load_sealed(args.tpcc_old_command, "TPC-C old command")
    ca = v1.load_sealed(args.tpcc_old_authority, "TPC-C old authority")
    v1.validate_predecessor(h, ha, args.tpch_old_command,
                           args.tpch_old_attempt, args.tpcc_old_attempt)
    v1.validate_predecessor(c, ca, args.tpcc_old_command,
                           args.tpcc_old_attempt, args.tpch_old_attempt)
    old_binding = h.get("native_sql_adapter_binding") or {}
    old_adapter = old_binding.get("path"); old_hash = old_binding.get("sha256")
    if ((h.get("argv") or [None, None])[1] != old_adapter
            or (h.get("code_files") or {}).get(old_adapter) != old_hash
            or old_binding.get("workload_duration_seconds") != 420
            or old_binding.get("collector_sample_window_seconds") != 180
            or (h.get("argv") or []).count("--duration-seconds=420") != 1):
        raise v1.IssueError("TPC-H 420s adapter predecessor differs")
    adapter_path = args.adapter_path.resolve(strict=True)
    if (adapter_path.is_symlink() or not adapter_path.is_file()
            or v1.sha(adapter_path) != args.adapter_sha256
            or args.adapter_bundle_id not in str(adapter_path)):
        raise v1.IssueError("new adapter content identity differs")
    mapping = {args.tpch_old_attempt:args.tpch_new_attempt,
               args.tpcc_old_attempt:args.tpcc_new_attempt}
    h = v1.replace(h, mapping); c = v1.replace(c, mapping)
    h["argv"][1] = str(adapter_path); del h["code_files"][old_adapter]
    if str(adapter_path) in h["code_files"]:
        raise v1.IssueError("new adapter code-file collision")
    h["code_files"][str(adapter_path)] = args.adapter_sha256
    h["native_sql_adapter_binding"].update({
        "schema_version":"mysql_sql_debug_metric_adapter_binding_v4",
        "bundle_id":args.adapter_bundle_id,"path":str(adapter_path),
        "sha256":args.adapter_sha256,
        "q15_view_policy":"deterministic_attempt_worker_unique_identifier",
        "q15_query_semantics_changed":False,
    })
    if (h.get("qualification_attempt_id") != args.tpch_new_attempt
            or c.get("qualification_attempt_id") != args.tpcc_new_attempt
            or h.get("paired_peer_workload",{}).get("qualification_attempt_id")
            != args.tpcc_new_attempt
            or c.get("paired_peer_workload",{}).get("qualification_attempt_id")
            != args.tpch_new_attempt
            or args.tpch_old_attempt in json.dumps(h,sort_keys=True)
            or args.tpcc_old_attempt in json.dumps(c,sort_keys=True)):
        raise v1.IssueError("new reciprocal binding differs")
    fresh=v1.token(args.fresh_token_file); now=time.time()
    def authority(old, command, own):
        value=v1.replace(old,mapping)
        value.update({"qualification_attempt_id":own,
                      "command_spec_sha256":v1.sha_bytes(v1.canonical(command)),
                      "main_agent_token_sha256":hashlib.sha256(fresh.encode("ascii")).hexdigest(),
                      "not_before_epoch":now-1.0,"expires_epoch":now+float(args.lifetime_seconds),
                      "collector_bundle_id":args.collector_bundle_id,
                      "collector_manifest_sha256":args.collector_manifest_sha256,
                      "qualification_only":True,"debug_only":True,
                      "capture_authorized":False,"formal_r0_authorized":False,
                      "formal_authorized":False,"accepted_round_authorized":False})
        return value
    v1.write_one(args.tpch_output_dir,h,authority(ha,h,args.tpch_new_attempt))
    v1.write_one(args.tpcc_output_dir,c,authority(ca,c,args.tpcc_new_attempt))
    print(json.dumps({"status":"ISSUED_PAIRED_DEBUG_QUALIFICATION_V3_ONLY",
                      "tpch_attempt":args.tpch_new_attempt,
                      "tpcc_attempt":args.tpcc_new_attempt,
                      "tpch_guard_seconds":420,"collector_sample_window_seconds":180,
                      "collector_bundle_id":args.collector_bundle_id,
                      "adapter_bundle_id":args.adapter_bundle_id,
                      "expires_epoch":now+float(args.lifetime_seconds),
                      "formal_r0_authorized":False,"accepted_round_authorized":False},sort_keys=True))


if __name__=="__main__":
    try: main()
    except (v1.IssueError,OSError,ValueError,KeyError,IndexError) as exc:
        print("NOT_READY: "+str(exc),file=__import__("sys").stderr); raise SystemExit(2)
