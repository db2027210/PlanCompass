"""Module."""


from prometheus_api_client import PrometheusConnect, MetricRangeDataFrame
from prometheus_api_client.utils import parse_datetime
from datetime import datetime
import argparse
import pandas as pd
import os
import sys
import json
import glob

# ==========================================

# ==========================================


DISK_DEVICES = {
    "DISK1": "nvme1n1",
    "DISK2": "nvme2n1",
}


CPU_BINDING_MAP = {
    "L": {
        "node1": "0-15",
        "node2": "16-31",
        "node3": "32-47",
        "node4": "48-63",
        "node5": "256-271",
        "node6": "272-287",
        "node7": "288-303",
        "node8": "304-319",
    },
    "M": {
        "node1": "0-31",
        "node2": "32-63",
        "node5": "256-287",
        "node6": "288-319",

    },
    "H": {
        "node1": "0-63",
        "node5": "256-319",

    },
}

# ==========================================

# ==========================================


def get_node_config(node_name, profile):
    """get node config."""


    profile_map = CPU_BINDING_MAP.get(profile)
    if not profile_map:
        raise ValueError(f"Unknown profile: {profile}")

    cpuset = profile_map.get(node_name)
    if not cpuset or cpuset == "0-0":
        raise ValueError(
            f"Node '{node_name}' is not active or not defined in Profile '{profile}' (cpuset is 0-0 or None)."
        )


    try:
        node_idx = int(node_name.replace("node", ""))
        disk_dev = (
            DISK_DEVICES["DISK1"] if 1 <= node_idx <= 4 else DISK_DEVICES["DISK2"]
        )
    except ValueError:
        disk_dev = None


    try:
        exporter_instance = f"127.0.0.1:{12000 + node_idx}"
    except Exception:
        exporter_instance = f"exporter_{node_name}:9187"

    return {"cpuset": cpuset, "disk": disk_dev, "exporter": exporter_instance}


def parse_cpu_input(input_str):
    """parse cpu input."""
    cores = set()
    for part in input_str.split(","):
        part = part.strip()
        if "-" in part:
            try:
                start, end = map(int, part.split("-"))
                cores.update(range(start, end + 1))
            except ValueError:
                pass
        elif part.isdigit():
            cores.add(int(part))
    return "|".join(map(str, sorted(list(cores))))


def save_metric_to_csv(
    prom_client,
    metric_name,
    label_config,
    start,
    end,
    output_path,
    value_converter=None,
    column_name="value",
):
    """save metric to csv."""
    print(f" -> Querying {metric_name}...")
    try:
        labels = label_config if label_config else {}
        data = prom_client.get_metric_range_data(
            metric_name=metric_name, label_config=labels, start_time=start, end_time=end
        )
    except Exception as e:
        print(f"    [Error] Prometheus query failed: {e}")
        return

    if data:
        df = MetricRangeDataFrame(data)


        if "timestamp" not in df.columns:
            df.reset_index(inplace=True)


        if value_converter and "value" in df.columns:
            df["value"] = pd.to_numeric(df["value"], errors="coerce")
            df["value"] = df["value"].apply(value_converter)


        cols_to_keep = ["timestamp"]
        for col in df.columns:
            if col not in ["timestamp", "value"]:
                cols_to_keep.append(col)


        if "value" in df.columns:
            df.rename(columns={"value": column_name}, inplace=True)
            cols_to_keep.append(column_name)


        final_cols = [c for c in cols_to_keep if c in df.columns]
        df_final = df[final_cols]

        df_final.to_csv(output_path, index=False)
        print(
            f"    -> Saved to {os.path.basename(output_path)} (Rows: {len(df_final)})"
        )
    else:
        print(f"    -> Warning: No data found for {metric_name}")


def save_custom_query_to_csv(
    prom_client,
    query,
    start,
    end,
    output_path,
    column_name="value",
):
    """save custom query to csv."""
    print(f" -> Querying: {query}")
    try:
        data = prom_client.custom_query_range(
            query=query, start_time=start, end_time=end, step="15s"
        )
    except Exception as e:
        print(f"    [Error] Prometheus query failed: {e}")
        return

    if data:
        df = MetricRangeDataFrame(data)
        if "timestamp" not in df.columns:
            df.reset_index(inplace=True)

        if "value" in df.columns:
            df.rename(columns={"value": column_name}, inplace=True)
            df[column_name] = pd.to_numeric(df[column_name], errors="coerce")
            df_final = df[["timestamp", column_name]]
            df_final.to_csv(output_path, index=False)
            print(f"    -> Saved to {os.path.basename(output_path)} (Rows: {len(df_final)})")
    else:
        print(f"    -> Warning: No data found for query")


def save_runtime_jsonl_metrics(runtime_jsonl_path, output_dir):
    """save runtime jsonl metrics."""
    if not os.path.exists(runtime_jsonl_path):
        print(f" -> Warning: Runtime JSONL not found: {runtime_jsonl_path}")
        return

    rows = []
    with open(runtime_jsonl_path, "r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not rows:
        print(" -> Warning: Runtime JSONL is empty or invalid.")
        return

    df = pd.DataFrame(rows)
    if "timestamp" not in df.columns:
        print(" -> Warning: Runtime JSONL has no 'timestamp' field.")
        return

    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp")

    for col in [
        "cpu_used",
        "read_bytes_per_sec",
        "write_bytes_per_sec",
        "read_iops",
        "write_iops",
        "p99_read",
        "p99_write",
        "memory_working_set_bytes",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "read_bytes_per_sec" in df.columns:
        tmp = df[["timestamp", "read_bytes_per_sec"]].copy()
        tmp["read_mb_s"] = tmp["read_bytes_per_sec"] / 1024 / 1024
        tmp[["timestamp", "read_mb_s"]].to_csv(
            f"{output_dir}/container_fs_read_mb.csv", index=False
        )

    if "write_bytes_per_sec" in df.columns:
        tmp = df[["timestamp", "write_bytes_per_sec"]].copy()
        tmp["write_mb_s"] = tmp["write_bytes_per_sec"] / 1024 / 1024
        tmp[["timestamp", "write_mb_s"]].to_csv(
            f"{output_dir}/container_fs_write_mb.csv", index=False
        )

    if "read_iops" in df.columns:
        df[["timestamp", "read_iops"]].to_csv(
            f"{output_dir}/container_fs_read_iops.csv", index=False
        )

    if "write_iops" in df.columns:
        df[["timestamp", "write_iops"]].to_csv(
            f"{output_dir}/container_fs_write_iops.csv", index=False
        )

    if "cpu_used" in df.columns:
        tmp = df[["timestamp", "cpu_used"]].copy()
        tmp.rename(columns={"cpu_used": "cpu_usage_pct"}, inplace=True)
        tmp.to_csv(f"{output_dir}/container_cpu_usage.csv", index=False)

    if "memory_working_set_bytes" in df.columns:
        tmp = df[["timestamp", "memory_working_set_bytes"]].copy()
        tmp["memory_mb"] = tmp["memory_working_set_bytes"] / 1024 / 1024
        tmp[["timestamp", "memory_mb"]].to_csv(
            f"{output_dir}/container_memory_working_set_mb.csv", index=False
        )

    if "p99_read" in df.columns:
        tmp = df[["timestamp", "p99_read"]].copy()
        tmp.rename(columns={"p99_read": "p99_read_bytes_s"}, inplace=True)
        tmp.to_csv(f"{output_dir}/container_fs_read_p99_iops.csv", index=False)

    if "p99_write" in df.columns:
        tmp = df[["timestamp", "p99_write"]].copy()
        tmp.rename(columns={"p99_write": "p99_write_bytes_s"}, inplace=True)
        tmp.to_csv(f"{output_dir}/container_fs_write_p99_iops.csv", index=False)

    # 1-minute count from rate-like iops series
    if "read_iops" in df.columns:
        tmp = df[["timestamp", "read_iops"]].copy()
        tmp["read_1m_count"] = tmp["read_iops"] * 60
        tmp[["timestamp", "read_1m_count"]].to_csv(
            f"{output_dir}/container_fs_read_1m_count.csv", index=False
        )

    if "write_iops" in df.columns:
        tmp = df[["timestamp", "write_iops"]].copy()
        tmp["write_1m_count"] = tmp["write_iops"] * 60
        tmp[["timestamp", "write_1m_count"]].to_csv(
            f"{output_dir}/container_fs_write_1m_count.csv", index=False
        )

    print(f" -> Runtime container metrics saved from: {runtime_jsonl_path}")


# ==========================================

# ==========================================


def main():
    parser = argparse.ArgumentParser(
        description="Prometheus Monitor Collector (Node & Profile)"
    )
    parser.add_argument(
        "--time", required=True, help="Start time (YYYY-MM-DD HH:MM:SS)"
    )
    parser.add_argument(
        "--node", type=str, required=True, help="Target Node (e.g., node1, node5)"
    )
    parser.add_argument(
        "--profile",
        type=str,
        required=True,
        choices=["L", "M", "H"],
        help="Configuration Profile (L/M/H)",
    )
    parser.add_argument(
        "--output", type=str, default="./output", help="Base output directory"
    )
    parser.add_argument(
        "--db-engine",
        type=str,
        default="pg",
        choices=["pg", "mysql"],
        help="Database engine type (pg/mysql)",
    )
    parser.add_argument(
        "--db-name",
        type=str,
        default="benchbase",
        help="Database/schema name",
    )
    parser.add_argument(
        "--container-metrics-source",
        type=str,
        default="docker-runtime",
        choices=["docker-runtime", "cadvisor"],
        help="Container metrics source",
    )
    parser.add_argument(
        "--runtime-jsonl",
        type=str,
        default="",
        help="Path to runtime_metrics_*.jsonl generated by collect_metrics_runtime.sh",
    )

    args = parser.parse_args()

    try:
        node_conf = get_node_config(args.node, args.profile)
    except ValueError as e:
        print(f"[Error] Configuration invalid: {e}")
        sys.exit(1)

    output_dir = os.path.join(args.output)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    prom = PrometheusConnect(url="http://localhost:9090", disable_ssl=True)
    start_time = parse_datetime(args.time)
    end_time = datetime.now()

    print(f"=== Monitor Collection ===")
    print(f"Time: {start_time} -> {end_time}")
    print(f"Target: {args.node} (Profile: {args.profile})")
    print(
        f"Resource Map: CPU={node_conf['cpuset']}, Disk={node_conf['disk']}, Exporter={node_conf['exporter']}"
    )

    # =========================================================

    # =========================================================
    print(f"\n=== [Database Metrics] ===")

    db_labels = {"instance": node_conf["exporter"]}
    print(f"Filter Labels: {db_labels}")

    if args.db_engine == "pg":
        # --- Task A: Thread Memory & Count ---
        print("\n[Task A] Thread Memory & Count")
        save_metric_to_csv(
            prom,
            "user_thread_memory_memory_bytes",
            db_labels,
            start_time,
            end_time,
            f"{output_dir}/thread_memory.csv",
            value_converter=lambda x: round(float(x) / 1024, 2),
            column_name="memory_usage (KB)",
        )
        save_metric_to_csv(
            prom,
            "user_thread_memory_thread_count",
            db_labels,
            start_time,
            end_time,
            f"{output_dir}/thread_count.csv",
            column_name="thread_count",
        )

        # --- Task B: Global Memory (All 4 Metrics) ---
        print("\n[Task B] Global Memory Details")
        global_mem_metrics = {
            "pg_memory_global_dynamic_used_bytes": "global_dynamic_used.csv",
            "pg_memory_global_dynamic_peak_bytes": "global_dynamic_peak.csv",
            "pg_memory_global_backend_used_bytes": "global_backend_used.csv",
            "pg_memory_global_process_used_bytes": "global_process_used.csv",
        }
        for metric_name, filename in global_mem_metrics.items():
            save_metric_to_csv(
                prom,
                metric_name,
                db_labels,
                start_time,
                end_time,
                f"{output_dir}/{filename}",
                value_converter=lambda x: round(float(x) / 1024 / 1024, 2),
                column_name="memory_usage (MB)",
            )

        # --- Task C: Database Size ---
        print(f"\n[Task C] Database Size ({args.db_name})")
        size_labels = db_labels.copy()
        size_labels["datname"] = args.db_name
        save_metric_to_csv(
            prom,
            "pg_database_size_bytes",
            size_labels,
            start_time,
            end_time,
            f"{output_dir}/database_size.csv",
            value_converter=lambda x: round(float(x) / 1024 / 1024, 2),
            column_name="size (MB)",
        )

        # --- Task F: Active Sessions ---
        print("\n[Task F] Active Sessions")
        session_labels = db_labels.copy()
        session_labels["dbname"] = args.db_name
        save_metric_to_csv(
            prom,
            "pg_active_sessions_active_sessions",
            session_labels,
            start_time,
            end_time,
            f"{output_dir}/pg_active_sessions.csv",
            column_name="active_sessions",
        )

        # --- Task G: Shared Buffers ---
        print("\n[Task G] Shared Buffers")
        save_metric_to_csv(
            prom,
            "pg_shared_buffers_shared_buffers_bytes",
            db_labels,
            start_time,
            end_time,
            f"{output_dir}/pg_shared_buffers.csv",
            value_converter=lambda x: round(float(x) / 1024 / 1024, 2),
            column_name="shared_buffers (MB)",
        )

        # --- Task H: Cache Hit Rate ---
        print("\n[Task H] Cache Hit Rate")
        instance = node_conf["exporter"]
        cache_hit_query = (
            f'clamp_min('
            f'rate(pg_block_io_blks_hit{{instance="{instance}",dbname="{args.db_name}"}}[5m]) '
            f'/ clamp_min((rate(pg_block_io_blks_hit{{instance="{instance}",dbname="{args.db_name}"}}[5m]) '
            f'+ rate(pg_block_io_blks_read{{instance="{instance}",dbname="{args.db_name}"}}[5m])), 1e-12)'
            f', 0)'
        )
        save_custom_query_to_csv(
            prom,
            cache_hit_query,
            start_time,
            end_time,
            f"{output_dir}/pg_cache_hit_rate.csv",
            column_name="hit_rate",
        )
    else:
        print("\n[Task A] Thread Count (MySQL)")
        save_metric_to_csv(
            prom,
            "mysql_global_status_threads_running",
            db_labels,
            start_time,
            end_time,
            f"{output_dir}/thread_count.csv",
            column_name="thread_count",
        )

        print("\n[Task B] InnoDB Memory Details (MySQL)")
        mysql_mem_metrics = {
            "mysql_global_status_innodb_buffer_pool_bytes_data": "global_dynamic_used.csv",
            "mysql_global_status_innodb_buffer_pool_bytes_dirty": "global_dynamic_peak.csv",
            "mysql_global_status_innodb_buffer_pool_bytes_free": "global_backend_used.csv",
            "mysql_global_variables_innodb_buffer_pool_size": "global_process_used.csv",
        }
        for metric_name, filename in mysql_mem_metrics.items():
            save_metric_to_csv(
                prom,
                metric_name,
                db_labels,
                start_time,
                end_time,
                f"{output_dir}/{filename}",
                value_converter=lambda x: round(float(x) / 1024 / 1024, 2),
                column_name="memory_usage (MB)",
            )

        print(f"\n[Task C] Database Size ({args.db_name})")
        instance = node_conf["exporter"]
        db_size_query = (
            f'sum(('
            f'mysql_info_schema_table_size_data_length{{instance="{instance}",schema="{args.db_name}"}} '
            f'+ mysql_info_schema_table_size_index_length{{instance="{instance}",schema="{args.db_name}"}}'
            f'))'
        )
        save_custom_query_to_csv(
            prom,
            db_size_query,
            start_time,
            end_time,
            f"{output_dir}/database_size.csv",
            column_name="size_bytes",
        )

        print("\n[Task F] Active Sessions (MySQL)")
        save_metric_to_csv(
            prom,
            "mysql_global_status_threads_running",
            db_labels,
            start_time,
            end_time,
            f"{output_dir}/mysql_active_sessions.csv",
            column_name="active_sessions",
        )

        print("\n[Task G] InnoDB Buffer Pool")
        save_metric_to_csv(
            prom,
            "mysql_global_variables_innodb_buffer_pool_size",
            db_labels,
            start_time,
            end_time,
            f"{output_dir}/mysql_innodb_buffer_pool.csv",
            value_converter=lambda x: round(float(x) / 1024 / 1024, 2),
            column_name="innodb_buffer_pool_size (MB)",
        )

        print("\n[Task H] Cache Hit Rate (MySQL)")
        instance = node_conf["exporter"]
        cache_hit_query = (
            f'clamp_min(1 - ('
            f'rate(mysql_global_status_innodb_buffer_pool_reads{{instance="{instance}"}}[5m]) '
            f'/ clamp_min(rate(mysql_global_status_innodb_buffer_pool_read_requests{{instance="{instance}"}}[5m]), 1e-12)'
            f'), 0)'
        )
        save_custom_query_to_csv(
            prom,
            cache_hit_query,
            start_time,
            end_time,
            f"{output_dir}/mysql_cache_hit_rate.csv",
            column_name="hit_rate",
        )

    # =========================================================

    # =========================================================
    print("\n=== [System Metrics] ===")

    # --- Task D: CPU iowait ---
    print("\n[Task D] CPU iowait Metrics")
    try:
        cpu_range = node_conf["cpuset"]
        cpu_regex = parse_cpu_input(cpu_range)
        print(f" -> CPU Set: {cpu_range} (Regex: {cpu_regex})")

        cpu_query = f'rate(node_cpu_seconds_total{{mode="iowait", cpu=~"{cpu_regex}"}}[30s]) * 100'
        cpu_data = prom.custom_query_range(
            query=cpu_query, start_time=start_time, end_time=end_time, step="15s"
        )

        if cpu_data:
            df_cpu = MetricRangeDataFrame(cpu_data)
            if "timestamp" not in df_cpu.columns:
                df_cpu.reset_index(inplace=True)
            if "cpu" in df_cpu.columns:
                df_cpu = df_cpu[["timestamp", "cpu", "value"]]
                df_cpu.rename(columns={"value": "iowait (%)"}, inplace=True)
                df_cpu["iowait (%)"] = pd.to_numeric(
                    df_cpu["iowait (%)"], errors="coerce"
                )

            df_cpu.to_csv(f"{output_dir}/cpu_iowait_data.csv", index=False)
            print(f" -> Saved to cpu_iowait_data.csv")
        else:
            print(" -> Warning: No CPU data found.")
    except Exception as e:
        print(f" -> Error in CPU query: {e}")

    # --- Task E: Disk I/O ---
    print("\n[Task E] Disk I/O Utilization")
    target_device = node_conf["disk"]
    if target_device:
        print(f" -> Target Device: {target_device}")

        disk_query = f'rate(node_disk_io_time_seconds_total{{device="{target_device}"}}[30s]) * 100'
        disk_data = prom.custom_query_range(
            query=disk_query, start_time=start_time, end_time=end_time, step="15s"
        )

        if disk_data:
            df_disk = MetricRangeDataFrame(disk_data)
            if "timestamp" not in df_disk.columns:
                df_disk.reset_index(inplace=True)
            if "device" in df_disk.columns:
                df_disk = df_disk[["timestamp", "device", "value"]]
                df_disk.rename(columns={"value": "utilization (%)"}, inplace=True)
                df_disk["utilization (%)"] = pd.to_numeric(
                    df_disk["utilization (%)"], errors="coerce"
                )

            df_disk.to_csv(f"{output_dir}/disk_io_data.csv", index=False)
            print(f" -> Saved to disk_io_data.csv")
        else:
            print(f" -> Warning: No Disk data found")
    else:
        print(f" -> Error: No Disk mapping found")

    # =========================================================

    # =========================================================
    print("\n=== [Container Metrics] ===")
    if args.container_metrics_source == "docker-runtime":
        if args.db_engine == "pg":
            container_name = f"opengauss_{args.node}"
        else:
            container_name = f"mysql_{args.node}"

        runtime_jsonl = args.runtime_jsonl
        if not runtime_jsonl:
            pattern = os.path.join(output_dir, f"runtime_metrics_{container_name}.jsonl")
            matches = glob.glob(pattern)
            runtime_jsonl = matches[0] if matches else ""
        if not runtime_jsonl:
            generic = sorted(glob.glob(os.path.join(output_dir, "runtime_metrics_*.jsonl")))
            runtime_jsonl = generic[-1] if generic else ""

        if runtime_jsonl:
            print(f"Using Docker runtime JSONL: {runtime_jsonl}")
            save_runtime_jsonl_metrics(runtime_jsonl, output_dir)
        else:
            print(" -> Warning: no runtime_metrics_*.jsonl found, skip container metrics.")
    else:
        if args.db_engine == "pg":
            container_name = f"opengauss_{args.node}"
        else:
            container_name = f"mysql_{args.node}"
        print(f"Target Container: {container_name}")

        cadvisor_labels = f'name="{container_name}",instance="localhost:8080",image!=""'
        container_rate_window = "5m"
        container_metrics = [
            (
                f'sum(rate(container_fs_writes_bytes_total{{{cadvisor_labels}}}[{container_rate_window}])) / 1024 / 1024',
                "container_fs_write_mb.csv",
                "write_mb_s"
            ),
            (
                f'sum(rate(container_fs_reads_bytes_total{{{cadvisor_labels}}}[{container_rate_window}])) / 1024 / 1024',
                "container_fs_read_mb.csv",
                "read_mb_s"
            ),
            (
                f'sum(rate(container_fs_writes_total{{{cadvisor_labels}}}[{container_rate_window}]))',
                "container_fs_write_iops.csv",
                "write_iops"
            ),
            (
                f'sum(rate(container_fs_reads_total{{{cadvisor_labels}}}[{container_rate_window}]))',
                "container_fs_read_iops.csv",
                "read_iops"
            ),
            (
                f'100 * sum(rate(container_cpu_usage_seconds_total{{{cadvisor_labels}}}[{container_rate_window}]))',
                "container_cpu_usage.csv",
                "cpu_usage_pct"
            ),
            (
                f'sum(container_memory_working_set_bytes{{{cadvisor_labels}}}) / 1024 / 1024',
                "container_memory_working_set_mb.csv",
                "memory_mb"
            ),
            (
                f'quantile_over_time(0.99, sum(rate(container_fs_reads_total{{{cadvisor_labels}}}[{container_rate_window}]))[1m:5s])',
                "container_fs_read_p99_iops.csv",
                "p99_read_iops"
            ),
            (
                f'quantile_over_time(0.99, sum(rate(container_fs_writes_total{{{cadvisor_labels}}}[{container_rate_window}]))[1m:5s])',
                "container_fs_write_p99_iops.csv",
                "p99_write_iops"
            ),
            (
                f'sum(increase(container_fs_reads_total{{{cadvisor_labels}}}[1m]))',
                "container_fs_read_1m_count.csv",
                "read_1m_count"
            ),
            (
                f'sum(increase(container_fs_writes_total{{{cadvisor_labels}}}[1m]))',
                "container_fs_write_1m_count.csv",
                "write_1m_count"
            )
        ]

        for query, filename, col_name in container_metrics:
            save_custom_query_to_csv(
                prom, query, start_time, end_time, f"{output_dir}/{filename}", column_name=col_name
            )

    print("\nAll tasks completed.")


if __name__ == "__main__":
    main()
