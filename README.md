# PlanCompass — PostgreSQL

PostgreSQL implementation of resource-aware optimizer-cost tuning. The `mysql` branch contains the MySQL implementation.

## Setup

```bash
python -m pip install -r requirements.txt
```

Configure `config/optimizer_config.json` or set `OPTIMIZER_CONFIG` to another configuration file before running `python parameter_optimizer.py`. Database access and workload data are supplied by the user.

Core modules include `resource_utility.py`, `gradient_descent_optimizer.py`, `double_loop_grid_optimizer.py`, and `system_resource_monitor.py`. Tests and workload scripts are in `tests/` and `shell/`.

Deployment paths, SSH users, hostnames, and network addresses are placeholders. Replace them with your own environment settings before running deployment scripts. Bundled third-party components retain their original copyright and license notices.
