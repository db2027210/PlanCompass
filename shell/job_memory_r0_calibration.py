import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/home/anonymous/program/resource_adaptive_mysql')
sys.path.insert(0, str(ROOT))
CONFIG_PATH = ROOT / 'config/formal/mysql_sgd_production/job_memory.algorithm_c.optimizer.json'
HEARTBEAT = ROOT / 'history/mysql_sgd_env_agents_20260723/manual_controllers/job_memory/subagent_heartbeat.json'
EVIDENCE = ROOT / 'history/mysql_sgd_env_agents_20260723/job/algorithm_c_r0_20260726/retry6_bounded_weighted_600s.json'


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(raw, path)
    finally:
        try:
            os.unlink(raw)
        except FileNotFoundError:
            pass


def heartbeat(status, task, evidence, next_action):
    atomic_json(HEARTBEAT, {
        'utc': datetime.now(timezone.utc).isoformat(),
        'status': status,
        'current_task': task,
        'current_pid_or_command': f'R0 calibration helper pid={os.getpid()}',
        'mutex_state': 'JOB-XP isolated_parallel_dynamic_gate',
        'last_evidence': evidence,
        'next_action': next_action,
        'shared_host_concurrent_interference': False,
    })


def main():
    config = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    config['measurement_seconds'] = 600
    config['minimum_measurement_seconds'] = 600
    heartbeat('r0_retry6_running', 'JOB Algorithm C retry6 bounded weighted 600-second R0', 'retry6 uses unchanged SQL set/weights; per-point query coverage is audit-only; strict DB counter boundaries retained; no formal accepted counter path invoked', 'wait for strict DB counter boundaries, stable target-resource windows, and zero workload errors')
    stop = threading.Event()

    def pulse():
        while not stop.wait(60):
            heartbeat('r0_retry6_running', 'JOB Algorithm C retry6 bounded weighted 600-second R0', 'retry6 helper still running; formal accepted counter untouched', 'wait for strict DB counter boundaries, stable target-resource windows, and zero workload errors')

    thread = threading.Thread(target=pulse, daemon=True)
    thread.start()
    try:
        from host02_job_xp_mutex import mutex_from_campaign
        from mysql_algorithm_c_production_services import build
        bundle = build(config)
        mutex = mutex_from_campaign(config, bundle, 'job')
        if mutex is None:
            raise RuntimeError('JOB R0 requires the host02 full-lifecycle mutex')
        with mutex:
            measurement = bundle['r0_measurement'](config)
        if not isinstance(measurement, dict):
            measurement = dict(measurement)
        resources = measurement.get('utilization_pct', {})
        payload = {
            'schema_version': 'mysql_algorithm_c_fresh_r0_calibration_v1',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'scenario': 'job_memory',
            'benchmark': 'job',
            'method_id': 'experience_table_sgd',
            'factory': 'mysql_algorithm_c_production_services:build',
            'formal_accepted_counter_touched': False,
            'shared_host_concurrent_interference': False,
            'target_resource': 'memory',
            'target_band': [0.85, 0.95],
            'non_target_bands': {'cpu': [0.30, 0.50], 'io': [0.30, 0.50]},
            'measurement': measurement,
            'gate_evaluation': {
                'memory_85_95': 0.85 <= float(resources.get('memory', -1)) <= 0.95,
                'cpu_30_50': 0.30 <= float(resources.get('cpu', -1)) <= 0.50,
                'io_30_50': 0.30 <= float(resources.get('io', -1)) <= 0.50,
                'stable': measurement.get('stable') is True,
                'sample_coverage_ge_0_90': float(measurement.get('sample_coverage', 0.0)) >= 0.90,
                'all_gates_passed': (
                    0.85 <= float(resources.get('memory', -1)) <= 0.95
                    and 0.30 <= float(resources.get('cpu', -1)) <= 0.50
                    and 0.30 <= float(resources.get('io', -1)) <= 0.50
                    and measurement.get('stable') is True
                    and float(measurement.get('sample_coverage', 0.0)) >= 0.90
                ),
            },
        }
        atomic_json(EVIDENCE, payload)
        heartbeat('r0_retry6_complete', 'JOB Algorithm C retry6 complete', str(EVIDENCE), 'start formal outer round only if the frozen bottleneck profile gates pass')
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    except BaseException as exc:
        heartbeat('r0_retry6_failed', 'JOB Algorithm C retry6 failed', f'{type(exc).__name__}: {exc}', 'inspect retry6 failure evidence; do not start formal outer round')
        raise
    finally:
        stop.set()
        thread.join(timeout=2)


if __name__ == '__main__':
    main()
