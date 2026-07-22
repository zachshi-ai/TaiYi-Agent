"""Interfaces for read-only authorities independent from task execution."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from taiyi.validation.checks import Check


@runtime_checkable
class ExternalAuthority(Protocol):
    name: str
    environment: str

    def checks(
        self,
        task_type: str,
        scenario: str,
        parameters: Mapping[str, str],
    ) -> list[Check]: ...


__all__ = ["ExternalAuthority"]
