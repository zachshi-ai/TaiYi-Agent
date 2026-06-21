"""Gateway — the single entry point, plus CLI and HTTP channels.

Routing, auth, rate limiting, and OpenAI compatibility sit on top of the stack
wired by `build_gateway`. Channels translate transport; the logic lives below them.
"""

from taiyi.gateway.app import GatewayApp, task_summary
from taiyi.gateway.auth import AuthPolicy, RateLimiter
from taiyi.gateway.core import Gateway, build_gateway, build_gateway_from_config

__all__ = [
    "GatewayApp",
    "task_summary",
    "AuthPolicy",
    "RateLimiter",
    "Gateway",
    "build_gateway",
    "build_gateway_from_config",
]
