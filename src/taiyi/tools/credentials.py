"""Credential isolation for tool subprocesses.

Default-deny: a spawned tool process inherits only an explicit allowlist of safe
environment variables. API keys, tokens, and secrets are never passed down, even
if the parent process holds them. This is defense in depth behind governance — a
tool that somehow tries to read a secret from its environment finds nothing there.
"""
from __future__ import annotations

import fnmatch
import os

# The only variables a tool subprocess inherits by default.
SAFE_ENV_KEYS: frozenset[str] = frozenset(
    {
        "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TERM",
        "USER", "LOGNAME", "SHELL", "PWD", "TMPDIR",
    }
)

# Anything matching these is treated as sensitive and never forwarded, even if a
# caller mistakenly adds it to the allowlist.
SENSITIVE_PATTERNS: tuple[str, ...] = (
    "*API_KEY*", "*_TOKEN*", "*TOKEN", "*_SECRET*", "*SECRET*",
    "*PASSWORD*", "*PASSWD*", "*PRIVATE_KEY*", "*ACCESS_KEY*", "*CREDENTIAL*",
)


def is_sensitive(name: str) -> bool:
    up = name.upper()
    return any(fnmatch.fnmatch(up, pat) for pat in SENSITIVE_PATTERNS)


def safe_environment(
    allow: tuple[str, ...] = (),
    *,
    base: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return a scrubbed environment for a tool subprocess.

    Includes only ``SAFE_ENV_KEYS`` plus any names in ``allow`` — and never any
    name that looks sensitive, regardless of the allowlist.
    """
    source = os.environ if base is None else base
    allowed = SAFE_ENV_KEYS | set(allow)
    return {
        k: v
        for k, v in source.items()
        if k in allowed and not is_sensitive(k)
    }
