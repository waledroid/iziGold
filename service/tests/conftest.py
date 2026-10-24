"""Test isolation for the optional supervisory agent.

The DeepSeek agent must be OFF for every test, regardless of what a
developer's `service/.env` says. Tests must be deterministic and must never
reach the network, and this also proves the modular contract: with the agent
disabled the service behaves exactly as it did before the agent existed.

Set at import time (not in a fixture) so it is in place before any test module
reloads `app.config` inside its own fixture.
"""

import os

os.environ["AGENT_ENABLED"] = "false"
os.environ["AGENT_BASE_URL"] = ""
os.environ["AGENT_API_KEY"] = ""
