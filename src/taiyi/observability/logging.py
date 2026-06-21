"""Structured JSON logging, correlated by task/trace id.

Records are dicts (one JSON object per event). Pass a ``sink`` callable to forward
them somewhere (stdout, a file, a collector); without one they are retained in
memory, which is what tests assert against.
"""
from __future__ import annotations

import json
import time


class StructuredLogger:
    def __init__(self, sink=None, *, service: str = "taiyi"):
        self.sink = sink
        self.service = service
        self.records: list[dict] = []

    def log(self, level: str, event: str, **fields) -> dict:
        rec = {"ts": time.time(), "level": level, "service": self.service, "event": event, **fields}
        self.records.append(rec)
        if self.sink is not None:
            self.sink(json.dumps(rec, ensure_ascii=False))
        return rec

    def info(self, event: str, **fields) -> dict:
        return self.log("info", event, **fields)

    def warning(self, event: str, **fields) -> dict:
        return self.log("warning", event, **fields)

    def error(self, event: str, **fields) -> dict:
        return self.log("error", event, **fields)
