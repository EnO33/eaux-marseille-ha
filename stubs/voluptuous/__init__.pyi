# Home Assistant runs on probatio, a drop-in reimplementation of voluptuous:
# since 2026.9 the `homeassistant` package aliases `voluptuous` to probatio in
# sys.modules before any integration loads. Type `voluptuous` the same way so
# our schemas and HA's own annotations (voluptuous- or probatio-spelled,
# depending on the release) resolve to one library. Mirrors HA core's own
# stubs/voluptuous; enabled through `mypy_path` in pyproject.toml.
from probatio import *  # noqa: F403
