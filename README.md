# PlanCompass — MySQL

MySQL implementation of resource-aware optimizer-cost tuning. The `main` branch contains the PostgreSQL implementation.

## Setup

```bash
python -m pip install -r requirements.txt
python mysql_parameter_optimizer.py --config config/mysql_optimizer_config.json --validate-config
```

After setting database access and workload paths in the configuration, run `python mysql_parameter_optimizer.py --config config/mysql_optimizer_config.json`. The optimizer uses MySQL server and engine cost parameters; tests and workload scripts are in `tests/`, `shell/`, and `scripts/`.

Deployment paths, SSH users, hostnames, and network addresses are placeholders. Replace them with your own environment settings before running deployment scripts. Bundled third-party components retain their original copyright and license notices.
