"""Operating-policy-aware LLM provider routing.

The router is intentionally outside governance: it chooses which model reasons,
never whether a proposed action is allowed. Missing mode-specific routes fall
back to the deployment's default provider and record that fallback as evidence.
"""
from __future__ import annotations

from dataclasses import dataclass

from taiyi.llm.base import LLMProvider


@dataclass(frozen=True)
class ProviderSelection:
    provider: LLMProvider
    requested_strategy: str
    route: str
    provider_name: str
    model: str | None
    fallback: bool

    def to_dict(self) -> dict:
        return {
            "requested_strategy": self.requested_strategy,
            "route": self.route,
            "provider": self.provider_name,
            "model": self.model,
            "fallback": self.fallback,
        }


class ProviderRouter:
    """Select a concrete provider for a resolved ``TaskPolicy``."""

    def __init__(
        self,
        default: LLMProvider,
        *,
        strongest_capable: LLMProvider | None = None,
        adaptive: LLMProvider | None = None,
        fastest_capable: LLMProvider | None = None,
    ):
        self.default_provider = default
        self._routes = {
            "strongest_capable": strongest_capable,
            "adaptive": adaptive,
            "fastest_capable": fastest_capable,
        }

    def select(self, policy) -> ProviderSelection:
        strategy = policy.model_strategy
        selected = self._routes.get(strategy)
        fallback = selected is None
        provider = selected or self.default_provider
        return ProviderSelection(
            provider=provider,
            requested_strategy=strategy,
            route=policy.requested_mode.value,
            provider_name=getattr(provider, "name", type(provider).__name__),
            model=getattr(provider, "model", None),
            fallback=fallback,
        )

    def candidates(self, policy) -> list[ProviderSelection]:
        """Return a deterministic, mode-prioritized failover pool.

        The requested route is always first.  A missing route resolves to the
        default provider exactly as :meth:`select` does.  Duplicate provider/model
        pairs are removed so a configured alias is not presented as failover.
        """

        preference = {
            "strongest_capable": ("strongest_capable", "adaptive", "default", "fastest_capable"),
            "adaptive": ("adaptive", "default", "strongest_capable", "fastest_capable"),
            "fastest_capable": ("fastest_capable", "default", "adaptive", "strongest_capable"),
        }.get(policy.model_strategy, (policy.model_strategy, "default"))
        candidates: list[ProviderSelection] = []
        seen: set[tuple[int, str | None]] = set()
        for strategy in preference:
            configured = self.default_provider if strategy == "default" else self._routes.get(strategy)
            if configured is None:
                continue
            key = (id(configured), getattr(configured, "model", None))
            if key in seen:
                continue
            seen.add(key)
            candidates.append(ProviderSelection(
                provider=configured,
                requested_strategy=policy.model_strategy,
                route=(policy.requested_mode.value if strategy == policy.model_strategy else strategy),
                provider_name=getattr(configured, "name", type(configured).__name__),
                model=getattr(configured, "model", None),
                fallback=strategy != policy.model_strategy,
            ))
        # The default provider is mandatory, so this is defensive only.
        return candidates or [self.select(policy)]

    def configured_routes(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for strategy, provider in self._routes.items():
            selected = provider or self.default_provider
            out[strategy] = {
                "provider": getattr(selected, "name", type(selected).__name__),
                "model": getattr(selected, "model", None),
                "fallback": provider is None,
            }
        return out
