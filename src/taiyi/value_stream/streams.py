"""Load the value-stream templates (data, not code)."""
from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_STREAMS_FILE = Path(__file__).resolve().parent / "value_streams.yaml"


def load_streams(path: str | Path = DEFAULT_STREAMS_FILE) -> dict:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return data.get("streams", {})


def stream_for(streams: dict, scenario: str) -> dict:
    """Return the template for a scenario, falling back to 'default'."""
    return streams.get(scenario) or streams.get("default", {"stream_id": "generic", "default_stack": ["task"], "task": {"goal_id": "task-generic", "title": "Complete the task"}})
