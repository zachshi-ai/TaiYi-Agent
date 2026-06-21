"""Memory types."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MemoryHit:
    layer: str        # "L3" (semantic) | "L5" (full-text)
    content: str
    ref: str          # memory id or source identifier
    score: float = 0.0
