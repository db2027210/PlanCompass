"""Module."""


import time
import subprocess
from system_resource_monitor import monitor_resources_from_prometheus


def main() -> None:
    prom_url = "http://localhost:9090"
    mem_promql = (
        'max_over_time('
        'container_memory_usage_bytes{name=~"pg-master"}[5s]'
        ')'
    )
    cpu_promql = (
        'rate(container_cpu_user_seconds_total{name=~"pg-master"}[5s])'
    )
    io_read_promql = (
        'rate(node_disk_read_bytes_total{device="loop1"}[5s])'
    )
    io_write_promql = (
        'rate(node_disk_write_bytes_total{device="loop1"}[5s])'
    )
    iops_read_promql = (
        'rate(node_disk_reads_completed_total{device="loop1"}[5s])'
    )
    iops_write_promql = (
        'rate(node_disk_writes_completed_total{device="loop1"}[5s])'
    )

    workload_cmd = (
        "cd /home/anonymous/benchbase-postgres && "
        "sed -i 's/>UNLIMITED</>unlimited</g' config/postgres/sample_chbenchmark_config.xml && "
        "java -jar benchbase.jar "
        "-b tpcc,chbenchmark "
        "-c config/postgres/sample_chbenchmark_config.xml "
        "--create=false --load=false --execute=true"
    )

    print("\nWarm up ...")
    subprocess.run(workload_cmd, shell=True, check=True)
    print("Warm up ")

    print("\n Prometheus ...")
    start_ts = time.time()
    subprocess.run(workload_cmd, shell=True, check=True)
    end_ts = time.time()
    try:
        resource_avg, resource_stats = monitor_resources_from_prometheus(
            prom_url=prom_url,
            start_ts=start_ts,
            end_ts=end_ts,
            step="5s",
            cpu_promql=cpu_promql,
            mem_promql=mem_promql,
            io_read_promql=io_read_promql,
            io_write_promql=io_write_promql,
            iops_read_promql=iops_read_promql,
            iops_write_promql=iops_write_promql
        )
    except RuntimeError as exc:
        print(f"Prometheus : {exc}")
        return


    if 'memory' in resource_avg:
        resource_avg['memory'] /= (1024 ** 3)
    if 'io' in resource_avg:
        resource_avg['io'] /= (1024 ** 2)


    print("Prometheus ():")
    print(f"  avg: {resource_avg}")
    print(f"  stats: {resource_stats}")


    print("-" * 40)
    if 'memory' in resource_avg: print(f"Memory : {resource_avg['memory'] / 32.0 * 100:.2f}% (: 32GB)")
    if 'io' in resource_avg:     print(f"IO     : {resource_avg['io'] / 3500.0 * 100:.2f}% (: 3500MB/s)")


if __name__ == "__main__":
    main()
