#!/usr/bin/env python3
"""
Robust supervisor for the 5 benchmark tests.
Monitors processes, auto-restarts crashes/hangs, logs bottlenecks.
"""

import subprocess
import time
from pathlib import Path

BATCH_DIR = Path("campaigns/pg5_common_outer_bgallowed_20260722_v1/hybench_ap_node01")
LOG_DIR = Path("result/five_benchmark_new_direction_20260723/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
MONITOR_LOG = LOG_DIR / "monitor_status.log"
BOTTLENECK_LOG = LOG_DIR / "bottleneck_log.txt"

def main():
    print("=== Benchmark Monitor started ===")
    MONITOR_LOG.touch()
    BOTTLENECK_LOG.touch()
    
    # Ensure log files exist
    for attempt in [6, 13, 20, 27, 34]:
        (LOG_DIR / f"attempt_{attempt}.log").touch()
    
    print("Monitor initialized - now running in loop")
    
    while True:
        try:
            # Get active attempts with better ps parsing
            ps_cmd = "ps aux | grep -E 'bash.*launch_hybench_ap_attempt' | grep -v grep"
            result = subprocess.run(ps_cmd, shell=True, capture_output=True, text=True)
            lines = result.stdout.strip().split("\n")
            active_attempts = {}
            for line in lines:
                if "launch_hybench_ap_attempt" in line:
                    try:
                        # Parse PID
                        parts = line.split()
                        pid = int(parts[1])
                        # Try to extract attempt number from the line
                        attempt = None
                        for a in [6,13,20,27,34]:
                            if f"attempt{a}" in line:
                                attempt = a
                                break
                        if attempt:
                            active_attempts[attempt] = pid
                    except:
                        continue
            
            # Log status
            with open(MONITOR_LOG, "a") as f:
                f.write(f"{time.time()} - Active attempts: {active_attempts}\n")
            
            # Bottleneck detection
            for attempt, pid in active_attempts.items():
                try:
                    ps_output = subprocess.check_output(["ps", "-p", str(pid), "-o", "%cpu,%mem", "--no-headers"], text=True).strip()
                    cpu, mem = map(float, ps_output.split())
                    if cpu > 80 or mem > 80:
                        print(f"ATTEMPT {attempt} - BOTTLENECK DETECTED: CPU={cpu:.1f}%, MEM={mem:.1f}% - PID {pid}")
                        with open(BOTTLENECK_LOG, "a") as bf:
                            bf.write(f"{time.time()} - ATTEMPT {attempt} - CPU={cpu:.1f}% MEM={mem:.1f}% - PID {pid}\n")
                except:
                    pass
            
            # Dead process detection with retry
            for attempt, pid in active_attempts.items():
                try:
                    subprocess.run(["ps", "-p", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except:
                    print(f"Process attempt {attempt} (PID {pid}) died - restarting...")
                    log_file = LOG_DIR / f"attempt_{attempt}.restart.log"
                    for retry in range(5):
                        try:
                            subprocess.Popen(["bash", str(BATCH_DIR / f"launch_hybench_ap_attempt{attempt}.sh")],
                                           stdout=log_file.open("w"), stderr=subprocess.STDOUT)
                            break
                        except:
                            time.sleep(5 * retry + 1)
                            continue
            
            time.sleep(30)
            
        except Exception as e:
            print(f"Monitor error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
