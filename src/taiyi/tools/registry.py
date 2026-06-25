"""The tool registry — what tools exist and how to describe them to a model.

A real model in the ReAct loop needs to know (a) which tools it may call and
(b) the exact tool-id convention governance and the executor expect (e.g.
``shell:git status``, not ``git status``). Without this, the model guesses tool
names, governance rules miss (red lines bypassed), and the executor returns
``[deferred]``. This registry is the single source of truth that closes that gap.

The registry is intentionally a flat declaration, not a plugin loader: taiyi's
governance rules and executors key off the ``shell:`` / ``file:`` / ``http:`` /
``sql:`` / ``notify:`` prefixes, so the tools are the prefixes plus a few named
business tools. Adding a tool means adding an entry here AND a matching rule/
executor branch — by design they move together.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolSpec:
    """One tool the agent may call. ``id`` is what governance/executor match on."""

    id: str
    description: str
    example: str = ""


# The built-in tool catalog. The ``id`` is the tool name the model must emit
# verbatim (with args) when it wants to call the tool. Descriptions are short so
# the system-prompt hint stays small.
BUILTIN_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec("shell:<command>", "run a shell command (git, ls, echo, …) and return its output",
             "shell:echo hello"),
    ToolSpec("file:read", "read a file inside the sandbox", "file:read report.txt"),
    ToolSpec("file:write", "write content to a file inside the sandbox",
             "file:write notes.txt hello"),
    ToolSpec("http:get", "fetch a URL (after SSRF screening) and return the body",
             "http:get https://example.com"),
)


def tool_names() -> list[str]:
    """The bare tool ids, for the provider's tool hint."""
    return [t.id for t in BUILTIN_TOOLS]


def tool_hint_block() -> str:
    """A self-contained description block for the system prompt.

    Teaches the model BOTH the call syntax (``tool: <id> <args…>``) and the
    available tools with examples, so it emits the exact ids governance and the
    executor expect.
    """
    lines = [
        "## Tools",
        "To call a tool, reply with a SINGLE line in this exact form and stop:",
        "    tool: <tool-id> <arg1> <arg2> ...",
        "Use the tool id verbatim (including the prefix like shell:). Wait for the",
        "result before continuing. When the task is fully done and needs no more",
        "tools, reply with your final answer in plain text (no `tool:` line).",
        "",
        "Available tools:",
    ]
    for t in BUILTIN_TOOLS:
        ex = f"  (e.g. `{t.example}`)" if t.example else ""
        lines.append(f"- `{t.id}` — {t.description}{ex}")
    return "\n".join(lines)


def normalize_tool_name(raw: str) -> str:
    """Coerce a model-emitted tool id to the canonical form.

    Models often drop the ``shell:`` prefix (saying ``git status`` or
    ``echo hi``) or add a stray space (``shell: git status``). Governance rules
    and the executor match on the prefix, so we normalize before either sees it:
      shell: git status  -> shell:git status
      git status         -> shell:git status      (leading bare command)
      echo hello         -> shell:echo hello
      file:read x        -> file:read x           (already canonical)
    A name that already carries a known prefix is returned unchanged.
    """
    name = raw.strip()
    # Already carries a known prefix (possibly with a stray space after ':')?
    for prefix in ("shell:", "file:read", "file:write", "http:", "https:",
                   "sql:", "notify:", "tool:"):
        if name.startswith(prefix):
            # Collapse a stray space like "shell: git status" -> "shell:git status".
            rest = name[len(prefix):]
            if rest.startswith(" "):
                rest = rest.lstrip()
                # file:read / file:write keep the space before the path arg.
                if prefix in ("file:read", "file:write"):
                    return f"{prefix} {rest}".strip()
                return f"{prefix}{rest}"
            return name
    # No known prefix → assume a bare shell command and prepend shell:.
    return f"shell:{name}"


__all__ = ["ToolSpec", "BUILTIN_TOOLS", "tool_names", "tool_hint_block", "normalize_tool_name"]
