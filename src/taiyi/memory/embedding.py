"""A deterministic, dependency-free local embedder for semantic retrieval.

Uses feature hashing of word tokens into a fixed-dimension, L2-normalized vector,
so cosine similarity reflects shared vocabulary. It is a real vector pipeline —
store embeddings, rank by cosine — not a keyword match, but it is a *local
stand-in*, not a trained sentence embedder. A real embedding model implements the
same ``Embedder`` interface and is a later opt-in (it needs a model/network and,
for hosted models, a budget); swapping it in does not change the storage or query
paths.

Hashing uses md5 (not the salted built-in ``hash``) so vectors are stable across
processes and a persisted index stays valid.
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol, runtime_checkable

_TOKEN = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _bucket(token: str, dim: int) -> int:
    return int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % dim


@runtime_checkable
class Embedder(Protocol):
    dim: int

    def embed(self, text: str) -> list[float]: ...


class HashingEmbedder:
    def __init__(self, dim: int = 256):
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in _tokens(text):
            vec[_bucket(tok, self.dim)] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return vec
        return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    """Dot product; inputs are expected L2-normalized, so this is cosine."""
    return sum(x * y for x, y in zip(a, b))
