"""ACT backend."""
from . import demo as _demo

_demo.apply_defaults()  # ACT_DEMO_MODE=1: fill env defaults before any submodule reads them
