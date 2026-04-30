"""Port allocation for dynamically registered agents and channels.

Agents use 18100-18199. Channels use 18090-18099. Both ranges are local-only
host ports for health checks; container-to-container traffic uses the
internal docker network.
"""


def _next_available_port(conf: dict) -> int:
    used = {a.get("public_port") for a in conf.get("external_agents", {}).values() if a.get("public_port")}
    for p in range(18100, 18200):
        if p not in used:
            return p
    raise RuntimeError("No available ports in range 18100-18199")


def _next_available_channel_port(conf: dict) -> int:
    used = {c.get("public_port") for c in conf.get("dynamic_channels", {}).values() if c.get("public_port")}
    for p in range(18090, 18100):
        if p not in used:
            return p
    raise RuntimeError("No available ports in range 18090-18099")
