"""v26 binding for the fail-closed local Algorithm C services.

The implementation remains in v25 so existing local imports stay compatible;
formal successor bundles bind this versioned module explicitly.
"""

from shell.mysql_algorithm_c_local_services_v25 import (  # noqa: F401
    LocalCampaign,
    LocalProductionServices,
    build,
)

__all__ = ["LocalCampaign", "LocalProductionServices", "build"]
