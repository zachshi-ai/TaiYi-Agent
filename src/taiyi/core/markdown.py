"""Tiny helper for Markdown files with YAML frontmatter.

Frontmatter makes skills, scenarios, and gates machine-readable (parse the YAML)
while keeping a human-readable body — the same "data, not prose" stance the rules
take, applied to skills and scenarios.
"""
from __future__ import annotations

import yaml


def split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body). Empty dict if there is no frontmatter."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            meta = yaml.safe_load(parts[1]) or {}
            if not isinstance(meta, dict):
                meta = {}
            return meta, parts[2].strip()
    return {}, text.strip()
