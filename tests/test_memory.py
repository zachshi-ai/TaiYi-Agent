"""5-layer memory (Module 7): persistence, retrieval, user model, integration."""
from __future__ import annotations

from taiyi.core.audit import AuditLog
from taiyi.governance import GovernanceEngine, LocalPermitClient
from taiyi.memory import HashingEmbedder, MemoryEngine, cosine
from taiyi.runtime import TaskRuntime, TaskState
from taiyi.scheduler import SchedulerEngine


# --- Embedding ---------------------------------------------------------------

def test_embedding_cosine_identical_vs_unrelated():
    e = HashingEmbedder()
    a = e.embed("deploy the service to production")
    same = e.embed("deploy the service to production")
    other = e.embed("a poem about the quiet sea at dawn")
    assert cosine(a, same) > 0.99
    assert cosine(a, other) < cosine(a, same)


# --- L1 short-term -----------------------------------------------------------

def test_l1_session_messages():
    m = MemoryEngine()
    m.add_message("s1", "user", "hello")
    m.add_message("s1", "assistant", "hi")
    m.add_message("s2", "user", "other")
    assert len(m.get_messages("s1")) == 2
    assert m.get_messages("s1", limit=1)[0]["content"] == "hi"
    m.clear_session("s1")
    assert m.get_messages("s1") == []


# --- L5 full-text + L3 semantic ---------------------------------------------

def test_remember_and_fulltext_search():
    m = MemoryEngine()
    m.remember("the deployment failed because the migration timed out")
    hits = m.search_fulltext("migration")
    assert hits and "migration" in hits[0].content


def test_semantic_search_ranks_relevant_first():
    m = MemoryEngine()
    m.remember("git commit and push workflow for the backend repo")
    m.remember("customer refund processing and escalation policy")
    hits = m.search_semantic("how do I commit code with git", top_k=2)
    assert hits
    assert "git" in hits[0].content


# --- L4 Honcho user model ----------------------------------------------------

def test_user_model_dialectical_merge():
    m = MemoryEngine()
    m.observe_user("prefers tabs over spaces")
    m.observe_user("prefers tabs over spaces")  # duplicate -> no growth
    m.observe_user("dislikes emoji in reports")
    model = m.get_user_model()
    assert model.count("prefers tabs") == 1
    assert "dislikes emoji" in model


# --- L2 skill index ----------------------------------------------------------

def test_skill_index():
    m = MemoryEngine()
    m.register_skill("git_safe_commit", "safe commits", tags=("git",))
    assert "git_safe_commit" in m.list_skills()
    assert m.get_skill("git_safe_commit")["tags"] == ["git"]


# --- Persistence -------------------------------------------------------------

def test_persists_across_reopen(tmp_path):
    m = MemoryEngine(tmp_path)
    m.remember("remember this fact about the cache layer", tags=("note",))
    m.observe_user("likes concise answers")
    m.close()

    reopened = MemoryEngine(tmp_path)
    assert reopened.search_fulltext("cache")
    assert "concise" in reopened.get_user_model()
    # Markdown mirror was written.
    assert list((tmp_path / "memory").glob("*.md"))


# --- Runtime integration -----------------------------------------------------

def test_runtime_records_to_memory():
    audit = AuditLog()
    gov = GovernanceEngine(audit_log=audit)
    sched = SchedulerEngine(LocalPermitClient(gov))
    mem = MemoryEngine()
    runtime = TaskRuntime(sched, audit_log=audit, memory=mem)

    ctx = runtime.run("commit my changes", "dev.git")
    assert ctx.state is TaskState.COMPLETED
    assert len(mem.get_messages(ctx.session_id)) >= 1     # L1 recorded the prompt
    assert mem.search_fulltext("commit")                  # L5 archived the task
    assert mem.get_user_model()                            # L4 updated
