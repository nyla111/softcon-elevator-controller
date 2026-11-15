"""Part 5 scenario setups and checks for the shared registry (ems/sim/scenarios.py imports this
package lazily, inside the setup / check functions)."""
from .sim06 import check_sim_06, setup_sim_06
from .sim11 import check_sim_11, setup_sim_11
from .sim12 import check_sim_12, setup_sim_12
from .sim18 import check_sim_18, setup_sim_18

__all__ = ["check_sim_06", "check_sim_11", "check_sim_12", "check_sim_18", "setup_sim_06",
           "setup_sim_11", "setup_sim_12", "setup_sim_18"]
