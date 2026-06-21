"""The boundary between scheduling and governance.

The scheduler never calls the GovernanceEngine directly — it depends only on the
small ``PermitClient`` interface. Today that interface is satisfied in-process by
``LocalPermitClient``; later it can be satisfied by an IPC/gRPC client talking to
a separate governance process, with no change to the scheduler. That is what
"governance and scheduling are physically separable" means in code: the seam is
this one method.

Crucially, ``LocalPermitClient`` exposes *only* ``issue_permit``. The scheduler
gets no handle on the engine's rules or audit log, so it cannot grant itself a
permit or tamper with the record.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from taiyi.core.types import PermitRequest, PermitResponse
from taiyi.governance.engine import GovernanceEngine


@runtime_checkable
class PermitClient(Protocol):
    """The only capability the scheduler has toward governance: ask."""

    def issue_permit(self, req: PermitRequest) -> PermitResponse: ...


class LocalPermitClient:
    """In-process PermitClient backed by a GovernanceEngine.

    Wraps the engine so callers see a single method and nothing else.
    """

    def __init__(self, engine: GovernanceEngine):
        self._engine = engine

    def issue_permit(self, req: PermitRequest) -> PermitResponse:
        return self._engine.issue_permit(req)
