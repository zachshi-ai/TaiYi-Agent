"""Pillar B: multi-turn context — AgentRuntime replays session history.

Before this fix, AgentRuntime.run rebuilt the message list from scratch each
call, so a second turn in the same session had no memory of the first. Now it
reads memory.get_messages(session_id) and replays prior user/assistant turns,
and records its own final answer back as an assistant turn. This file drives
two turns with a scripted provider that asserts it received the first turn.
"""
from __future__ import annotations

from taiyi.agent import AgentRuntime
from taiyi.approvals import ApprovalStore
from taiyi.core.audit import AuditLog
from taiyi.governance import GovernanceEngine, LocalPermitClient
from taiyi.llm import LLMMessage, LLMResponse, ScriptedProvider
from taiyi.memory import MemoryEngine
from taiyi.runtime import TaskState
from taiyi.runtime.executor import MockExecutor
from taiyi.scheduler import SchedulerEngine


class _CapturingProvider(ScriptedProvider):
    """ScriptedProvider that snapshots the messages it receives each call."""

    def __init__(self, responses):
        super().__init__(responses)
        self.seen: list[list[LLMMessage]] = []

    def complete(self, messages, *, tools=None):
        self.seen.append([LLMMessage(m.role, m.content) for m in messages])
        return super().complete(messages, tools=tools)


def _runtime(provider, memory):
    audit = AuditLog()
    gov = GovernanceEngine(audit_log=audit)
    sched = SchedulerEngine(LocalPermitClient(gov))
    return AgentRuntime(
        sched, audit, provider, executor=MockExecutor(), validator=None,
        memory=memory, approvals=ApprovalStore(),
    )


def test_second_turn_receives_first_turns_history(tmp_path):
    mem = MemoryEngine(tmp_path)
    # Turn 1: model answers "I am taiyi". Turn 2: model answers "yes".
    provider = _CapturingProvider([
        LLMResponse(text="I am taiyi."),
        LLMResponse(text="yes."),
    ])
    rt = _runtime(provider, mem)

    rt.run("who are you?", "default", session_id="conv-1")
    assert provider.seen[0][-1].role == "user"
    assert provider.seen[0][-1].content == "who are you?"
    # First turn saw only the system + scenario + its own prompt (no history).
    assert len([m for m in provider.seen[0] if m.role == "user"]) == 1

    rt.run("do you remember?", "default", session_id="conv-1")
    # Second turn MUST contain the first turn's user + assistant exchange.
    turn2 = provider.seen[1]
    contents = [(m.role, m.content) for m in turn2]
    assert ("user", "who are you?") in contents
    assert ("assistant", "I am taiyi.") in contents
    assert ("user", "do you remember?") in contents


def test_history_limit_caps_replay(tmp_path):
    mem = MemoryEngine(tmp_path)
    provider = _CapturingProvider([LLMResponse(text="ok") for _ in range(50)])
    audit = AuditLog()
    gov = GovernanceEngine(audit_log=audit)
    sched = SchedulerEngine(LocalPermitClient(gov))
    rt = AgentRuntime(
        sched, audit, provider, executor=MockExecutor(), validator=None,
        memory=mem, approvals=ApprovalStore(), history_limit=3,
    )
    for i in range(10):
        rt.run(f"msg {i}", "default", session_id="s")
    # The 10th call should have replayed at most history_limit user turns, not all 9 prior.
    last = provider.seen[-1]
    replayed_users = [m for m in last if m.role == "user"]
    # history_limit=3 caps replayed turns; plus the current prompt = at most 4.
    assert len(replayed_users) <= 4


def test_different_sessions_do_not_cross_contaminate(tmp_path):
    mem = MemoryEngine(tmp_path)
    provider = _CapturingProvider([LLMResponse(text="a"), LLMResponse(text="b")])
    rt = _runtime(provider, mem)
    rt.run("in session A", "default", session_id="A")
    rt.run("in session B", "default", session_id="B")
    # Session B's turn must NOT see session A's history.
    turn_b = provider.seen[1]
    contents = [m.content for m in turn_b]
    assert "in session A" not in contents
    assert "in session B" in contents
