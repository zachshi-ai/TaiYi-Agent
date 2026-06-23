"""Taiyi's capabilities, exposed as MCP tools.

Each tool is backed by the Gateway, so a call from an MCP client (Claude Code,
Cursor, …) flows through exactly the same governance, validation, and audit as a
CLI or HTTP request. `taiyi_run_task` is governed end-to-end — an MCP client cannot
bypass a red line any more than anyone else can.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from taiyi.gateway.app import task_summary


@dataclass
class ToolDef:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], str]


def build_tools(gateway) -> dict[str, ToolDef]:
    def run_task(args: dict) -> str:
        ctx = gateway.submit(
            args["prompt"],
            scenario=args.get("scenario"),
            user_id=args.get("user_id", "mcp"),
            session_id=args.get("session_id", "mcp"),
        )
        return json.dumps(task_summary(ctx), ensure_ascii=False, indent=2)

    def list_skills(args: dict) -> str:
        return json.dumps([s.name for s in gateway.skills.production_skills()], ensure_ascii=False)

    def get_skill(args: dict) -> str:
        skill = gateway.skills.get(args["name"])
        if skill is None:
            return f"unknown skill: {args['name']}"
        return json.dumps(
            {
                "name": skill.name,
                "category": skill.category,
                "risk": skill.risk,
                "scenario": skill.scenario,
                "production_eligible": skill.production_eligible,
            },
            ensure_ascii=False,
        )

    def search_memory(args: dict) -> str:
        hits = gateway.memory.search_fulltext(args["query"], limit=int(args.get("top_k", 5)))
        return json.dumps(
            [{"layer": h.layer, "content": h.content, "score": h.score} for h in hits],
            ensure_ascii=False,
        )

    def review(args: dict) -> str:
        if gateway.committee is None:
            return "multi-agent review not enabled"
        return json.dumps(gateway.committee.review(args["subject"]).to_dict(), ensure_ascii=False)

    defs = [
        ToolDef(
            "taiyi_run_task",
            "Submit a task to Taiyi (governed end-to-end). Returns the task summary.",
            {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "the task request"},
                    "scenario": {"type": "string", "description": "optional scenario id"},
                },
                "required": ["prompt"],
            },
            run_task,
        ),
        ToolDef(
            "taiyi_list_skills",
            "List the production-eligible skills.",
            {"type": "object", "properties": {}},
            list_skills,
        ),
        ToolDef(
            "taiyi_get_skill",
            "Get details for a skill by name.",
            {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
            get_skill,
        ),
        ToolDef(
            "taiyi_search_memory",
            "Full-text search over Taiyi's long-term memory.",
            {
                "type": "object",
                "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}},
                "required": ["query"],
            },
            search_memory,
        ),
        ToolDef(
            "taiyi_review",
            "Run a multi-agent expert review (red-line veto + arbitration) over a subject.",
            {"type": "object", "properties": {"subject": {"type": "string"}}, "required": ["subject"]},
            review,
        ),
    ]
    return {d.name: d for d in defs}
