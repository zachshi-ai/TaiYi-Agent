"""Tool Runtime: credential isolation, SSRF, and sandboxed execution (Module 5).

The end-to-end test is the founding case made real: a normal commit runs in a
genuine git repo and keeps the *local* identity, while the override attempt is
denied by governance and produces no commit at all.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from taiyi.core.audit import AuditLog
from taiyi.governance import GovernanceEngine, LocalPermitClient
from taiyi.runtime import TaskRuntime, TaskState
from taiyi.scheduler import PlanStep, SchedulerEngine
from taiyi.tools import SandboxExecutor, SSRFError, SSRFGuard, safe_environment

HAS_GIT = shutil.which("git") is not None


# --- Credential isolation ----------------------------------------------------

def test_safe_environment_drops_secrets(monkeypatch):
    monkeypatch.setenv("MY_API_KEY", "supersecret")
    monkeypatch.setenv("DB_PASSWORD", "hunter2")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = safe_environment()
    assert "PATH" in env
    assert "MY_API_KEY" not in env
    assert "DB_PASSWORD" not in env


def test_safe_environment_never_forwards_sensitive_even_if_allowlisted(monkeypatch):
    monkeypatch.setenv("ACME_TOKEN", "abc")
    env = safe_environment(allow=("ACME_TOKEN",))
    assert "ACME_TOKEN" not in env  # sensitive pattern overrides the allowlist


@pytest.mark.skipif(not HAS_GIT, reason="needs a shell with `env`")
def test_subprocess_env_is_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_API_KEY", "supersecret-value")
    ex = SandboxExecutor(tmp_path)
    r = ex.execute(PlanStep("shell:env", []))
    assert r.ok
    assert "supersecret-value" not in r.output
    assert "MY_API_KEY" not in r.output


# --- SSRF --------------------------------------------------------------------

def test_ssrf_blocks_loopback_and_private():
    g = SSRFGuard()
    for url in ("http://127.0.0.1/x", "http://10.0.0.5/y", "http://192.168.1.1",
                "http://169.254.169.254/latest/meta-data"):
        assert not g.is_allowed(url)


def test_ssrf_allows_public_ip_when_no_allowlist():
    g = SSRFGuard()
    assert g.is_allowed("http://8.8.8.8/")


def test_ssrf_allowlist_restricts_hosts():
    g = SSRFGuard(allowlist=("api.example.com",), resolver=lambda h: ["8.8.8.8"])
    assert g.is_allowed("https://api.example.com/v1")
    assert not g.is_allowed("https://evil.example.org/v1")


def test_ssrf_rejects_non_http_scheme():
    with pytest.raises(SSRFError):
        SSRFGuard().check("file:///etc/passwd")


def test_ssrf_dns_rebinding_to_private_is_blocked():
    # Public-looking host that resolves to an internal address.
    g = SSRFGuard(resolver=lambda h: ["10.1.2.3"])
    assert not g.is_allowed("http://totally-legit.example.com/")


def test_executor_blocks_private_url(tmp_path):
    ex = SandboxExecutor(tmp_path)
    r = ex.execute(PlanStep("http:get", ["http://127.0.0.1:8080/admin"]))
    assert not r.ok
    assert "ssrf" in r.output.lower()


# --- Sandboxed file I/O ------------------------------------------------------

def test_file_write_then_read_in_sandbox(tmp_path):
    ex = SandboxExecutor(tmp_path)
    assert ex.execute(PlanStep("file:write", ["note.txt", "hello"])).ok
    assert ex.execute(PlanStep("file:read", ["note.txt"])).output == "hello"


def test_path_traversal_is_blocked(tmp_path):
    ex = SandboxExecutor(tmp_path)
    r = ex.execute(PlanStep("file:read", ["../../etc/passwd"]))
    assert not r.ok


# --- End-to-end: the founding case against a real git repo -------------------

def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


def _make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Local Dev")
    _git(repo, "config", "user.email", "local@dev.test")
    (repo / "code.py").write_text("print('hi')\n", encoding="utf-8")
    return repo


def _runtime(audit):
    gov = GovernanceEngine(audit_log=audit)
    return gov


@pytest.mark.skipif(not HAS_GIT, reason="needs git")
def test_git_commit_preserves_local_identity_end_to_end(tmp_path):
    repo = _make_repo(tmp_path)
    audit = AuditLog()
    sched = SchedulerEngine(LocalPermitClient(GovernanceEngine(audit_log=audit)))
    runtime = TaskRuntime(sched, audit_log=audit, executor=SandboxExecutor(repo))

    ctx = runtime.run("commit my changes", "dev.git")
    assert ctx.state is TaskState.COMPLETED

    author_email = _git(repo, "log", "-1", "--format=%ae").stdout.strip()
    assert author_email == "local@dev.test"


@pytest.mark.skipif(not HAS_GIT, reason="needs git")
def test_identity_override_produces_no_commit(tmp_path):
    repo = _make_repo(tmp_path)
    audit = AuditLog()
    sched = SchedulerEngine(LocalPermitClient(GovernanceEngine(audit_log=audit)))
    runtime = TaskRuntime(sched, audit_log=audit, executor=SandboxExecutor(repo))

    ctx = runtime.run("用 -c user.name=Evil -c user.email=evil@x.test commit", "dev.git")
    assert ctx.state is TaskState.REJECTED
    # The commit step was denied before execution: the repo has no commits.
    assert _git(repo, "rev-parse", "HEAD").returncode != 0
