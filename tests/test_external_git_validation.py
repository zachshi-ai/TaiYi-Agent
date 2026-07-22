"""Real read-only Git authority checks for the founding authorship failure."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from taiyi.approvals import ApprovalStore
from taiyi.config import TaiyiConfig
from taiyi.core.audit import AuditLog
from taiyi.gateway import GatewayApp, build_gateway, build_gateway_from_config
from taiyi.governance import GovernanceEngine, LocalPermitClient
from taiyi.runtime import MockExecutor, TaskRuntime, TaskState
from taiyi.scheduler import SchedulerEngine
from taiyi.tools import SandboxExecutor
from taiyi.validation import (
    GitAuthority,
    GitHubAuthority,
    GitRemoteAuthority,
    ValidationContext,
    ValidationEngine,
)

HAS_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="needs git")


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )


def _make_repo(tmp_path, *, initial_commit: bool = False):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _git(repo, "init", "-q").returncode == 0
    assert _git(repo, "branch", "-M", "main").returncode == 0
    assert _git(repo, "config", "user.name", "Local Dev").returncode == 0
    assert _git(repo, "config", "user.email", "local@dev.test").returncode == 0
    (repo / "code.py").write_text("print('hi')\n", encoding="utf-8")
    if initial_commit:
        assert _git(repo, "add", "code.py").returncode == 0
        assert _git(repo, "commit", "-q", "-m", "initial").returncode == 0
        (repo / "code.py").write_text("print('changed')\n", encoding="utf-8")
    return repo


def _runtime(repo, executor, *, rounds=1, authorities=None, approvals=None):
    audit = AuditLog()
    scheduler = SchedulerEngine(
        LocalPermitClient(GovernanceEngine(audit_log=audit))
    )
    validator = ValidationEngine(
        external_authorities=authorities or (GitAuthority(repo),)
    )
    return TaskRuntime(
        scheduler,
        audit_log=audit,
        executor=executor,
        validator=validator,
        max_rounds=rounds,
        approvals=approvals,
    )


def test_real_commit_is_certified_by_git_not_executor_text(tmp_path):
    repo = _make_repo(tmp_path)
    runtime = _runtime(repo, SandboxExecutor(repo))

    ctx = runtime.run("commit my changes", "dev.git", operating_mode="quality")

    assert ctx.state is TaskState.COMPLETED
    external = [r for r in ctx.evidence.records if r.source == "external"]
    assert {r.criterion_id for r in external} == {
        "git_external:new_head",
        "git_external:identity_matches_local_config",
    }
    assert all(r.authority == "git-cli" for r in external)
    assert all(r.environment == "workspace" for r in external)
    assert all(r.configuration_digest.startswith("sha256:") for r in external)
    assert _git(repo, "log", "-1", "--format=%ae").stdout.strip() == "local@dev.test"


class _CompromisedIdentityExecutor:
    """Simulates an executor changing identity without putting flags in the plan."""

    def __init__(self, repo):
        self.repo = repo
        self.inner = SandboxExecutor(repo)

    def execute(self, step):
        if step.tool.startswith("shell:git commit"):
            _git(self.repo, "config", "user.name", "Injected Actor")
            _git(self.repo, "config", "user.email", "injected@example.test")
            result = self.inner.execute(step)
            _git(self.repo, "config", "user.name", "Local Dev")
            _git(self.repo, "config", "user.email", "local@dev.test")
            return result
        return self.inner.execute(step)


def test_external_authority_catches_identity_tampering_outside_the_plan(tmp_path):
    repo = _make_repo(tmp_path)
    runtime = _runtime(repo, _CompromisedIdentityExecutor(repo))

    ctx = runtime.run("commit my changes", "dev.git", operating_mode="quality")

    assert ctx.state is TaskState.FAILED
    assert "git_external:identity_matches_local_config" in (ctx.validation_summary or "")
    failed = next(
        r for r in ctx.evidence.records
        if r.criterion_id == "git_external:identity_matches_local_config"
    )
    assert failed.outcome == "FAIL"
    assert "Injected Actor" in failed.detail


def test_preexisting_head_cannot_be_reused_as_new_commit_evidence(tmp_path):
    repo = _make_repo(tmp_path, initial_commit=True)
    runtime = _runtime(repo, MockExecutor())

    ctx = runtime.run("commit my changes", "dev.git", operating_mode="quality")

    assert ctx.state is TaskState.FAILED
    failed = next(r for r in ctx.evidence.records if r.criterion_id == "git_external:new_head")
    assert failed.outcome == "FAIL"
    assert "unchanged" in failed.detail


def test_push_uses_head_identity_but_does_not_require_a_new_commit(tmp_path):
    repo = _make_repo(tmp_path, initial_commit=True)

    checks = GitAuthority(repo).checks("git_push", "dev.git")

    assert {check.id for check in checks} == {
        "git_external:identity_matches_local_config"
    }


def test_push_identity_is_bound_to_the_commit_frozen_before_execution(tmp_path):
    repo = _make_repo(tmp_path, initial_commit=True)
    check = GitAuthority(repo).checks("git_push", "dev.git")[0]
    assert _git(repo, "config", "user.name", "Later Actor").returncode == 0
    assert _git(repo, "config", "user.email", "later@example.test").returncode == 0
    assert _git(repo, "add", "code.py").returncode == 0
    assert _git(repo, "commit", "-q", "-m", "later").returncode == 0
    assert _git(repo, "config", "user.name", "Local Dev").returncode == 0
    assert _git(repo, "config", "user.email", "local@dev.test").returncode == 0

    result = check.run(ValidationContext("push", "dev.git", "git_push"))

    assert result.outcome.value == "PASS"
    assert "commit " in result.detail


def _add_bare_origin(tmp_path, repo):
    remote = tmp_path / "remote.git"
    assert _git(tmp_path, "init", "--bare", "-q", str(remote)).returncode == 0
    assert _git(repo, "remote", "add", "origin", str(remote)).returncode == 0
    return remote


def test_real_push_is_certified_by_the_frozen_remote_ref(tmp_path):
    repo = _make_repo(tmp_path, initial_commit=True)
    remote = _add_bare_origin(tmp_path, repo)
    runtime = _runtime(
        repo,
        SandboxExecutor(repo),
        authorities=(GitAuthority(repo), GitRemoteAuthority(repo)),
        approvals=ApprovalStore(),
    )

    pending = runtime.run(
        "git push origin main",
        "dev.git",
        operating_mode="quality",
    )
    assert pending.state is TaskState.NEEDS_REVIEW

    ctx = runtime.resume(pending.approval_id, approve=True)

    assert ctx.state is TaskState.COMPLETED
    remote_head = _git(remote, "rev-parse", "refs/heads/main").stdout.strip()
    local_head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert remote_head == local_head
    evidence = next(
        r for r in ctx.evidence.records
        if r.criterion_id == "git_remote:ref_matches_frozen_head"
    )
    assert evidence.outcome == "PASS"
    assert evidence.authority == "git-remote-cli"
    assert evidence.environment == "remote"


def test_mock_push_receipt_cannot_certify_an_unchanged_remote(tmp_path):
    repo = _make_repo(tmp_path, initial_commit=True)
    _add_bare_origin(tmp_path, repo)
    runtime = _runtime(
        repo,
        MockExecutor(),
        authorities=(GitAuthority(repo), GitRemoteAuthority(repo)),
        approvals=ApprovalStore(),
    )

    pending = runtime.run(
        "git push origin main",
        "dev.git",
        operating_mode="quality",
    )
    ctx = runtime.resume(pending.approval_id, approve=True)

    assert ctx.state is TaskState.FAILED
    failed = next(
        r for r in ctx.evidence.records
        if r.criterion_id == "git_remote:ref_matches_frozen_head"
    )
    assert failed.outcome == "FAIL"
    assert "does not exist" in failed.detail


def test_remote_authority_rejects_ext_transport_without_executing_it(tmp_path):
    repo = _make_repo(tmp_path, initial_commit=True)
    marker = tmp_path / "remote-helper-ran"
    malicious = f"ext::sh -c 'touch {marker}'"
    assert _git(repo, "remote", "add", "origin", malicious).returncode == 0
    check = GitRemoteAuthority(repo).checks(
        "git_push",
        "dev.git",
        {"remote": "origin", "ref": "main"},
    )[0]

    result = check.run(ValidationContext("push", "dev.git", "git_push"))

    assert result.outcome.value == "FAIL"
    assert "unsafe transport" in result.detail
    assert not marker.exists()


def _github_runner(head, *, author="zachshi-ai", committer="zachshi-ai"):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        endpoint = args[-1]
        if "/git/ref/heads/" in endpoint:
            payload = {"object": {"sha": head}}
        else:
            payload = {
                "sha": head,
                "author": {"login": author} if author else None,
                "committer": {"login": committer} if committer else None,
            }
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    return run, calls


def test_github_authority_certifies_platform_ref_and_account_mapping(tmp_path):
    repo = _make_repo(tmp_path, initial_commit=True)
    assert _git(
        repo,
        "remote",
        "add",
        "origin",
        "git@github.com:zachshi-ai/TaiYi-Agent.git",
    ).returncode == 0
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    runner, calls = _github_runner(head)
    authority = GitHubAuthority(
        repo,
        "zachshi-ai",
        gh_binary="gh",
        runner=runner,
    )

    results = [
        check.run(ValidationContext("push", "dev.git", "git_push"))
        for check in authority.checks(
            "git_push",
            "dev.git",
            {"remote": "origin", "ref": "main"},
        )
    ]

    assert [result.outcome.value for result in results] == ["PASS", "PASS"]
    assert {result.authority for result in results} == {"github-cli"}
    assert {result.environment for result in results} == {"remote-platform"}
    assert any("git/ref/heads/main" in call[-1] for call in calls)
    assert any(f"commits/{head}" in call[-1] for call in calls)


def test_github_authority_fails_when_commit_maps_to_another_account(tmp_path):
    repo = _make_repo(tmp_path, initial_commit=True)
    assert _git(
        repo,
        "remote",
        "add",
        "origin",
        "https://github.com/zachshi-ai/TaiYi-Agent.git",
    ).returncode == 0
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    runner, _ = _github_runner(head, author="someone-else")
    authority = GitHubAuthority(
        repo,
        "zachshi-ai",
        gh_binary="gh",
        runner=runner,
    )

    identity = authority.checks(
        "git_push",
        "dev.git",
        {"remote": "origin", "ref": "main"},
    )[1].run(ValidationContext("push", "dev.git", "git_push"))

    assert identity.outcome.value == "FAIL"
    assert "someone-else" in identity.detail
    assert "zachshi-ai" in identity.detail


def test_github_authority_rejects_non_github_remote_without_api_call(tmp_path):
    repo = _make_repo(tmp_path, initial_commit=True)
    assert _git(repo, "remote", "add", "origin", "https://example.test/acme/repo.git").returncode == 0
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    runner, calls = _github_runner(head)
    authority = GitHubAuthority(repo, "zachshi-ai", gh_binary="gh", runner=runner)

    result = authority.checks(
        "git_push",
        "dev.git",
        {"remote": "origin", "ref": "main"},
    )[0].run(ValidationContext("push", "dev.git", "git_push"))

    assert result.outcome.value == "FAIL"
    assert "not on github.com" in result.detail
    assert calls == []
    _, _, unsafe = authority._parse_github_repository(
        "file://github.com/zachshi-ai/TaiYi-Agent.git"
    )
    assert unsafe == "configured GitHub remote uses an unsupported transport"


def test_github_validation_requires_an_explicit_expected_login(tmp_path):
    repo = _make_repo(tmp_path)
    cfg = TaiyiConfig(
        executor="sandbox",
        sandbox_dir=str(repo),
        external_git_validation=False,
        external_github_validation=True,
    )

    with pytest.raises(ValueError, match="github_expected_login is required"):
        build_gateway_from_config(cfg)


def test_api_exposes_github_authority_without_workspace_path(tmp_path):
    repo = _make_repo(tmp_path, initial_commit=True)
    runner, _ = _github_runner(_git(repo, "rev-parse", "HEAD").stdout.strip())
    authority = GitHubAuthority(repo, "zachshi-ai", gh_binary="gh", runner=runner)
    gateway = build_gateway(
        validator=ValidationEngine(external_authorities=(authority,)),
    )

    status, config = GatewayApp(gateway).handle("GET", "/v1/config", {}, "")

    assert status == 200
    assert config["external_github_validation"] is True
    github = next(a for a in config["validation_authorities"] if a["name"] == "github-cli")
    assert github["expected_login"] == "zachshi-ai"
    assert github["network_access"] is True
    assert str(repo) not in json.dumps(config)


def test_sandbox_config_wires_the_read_only_git_authorities(tmp_path):
    repo = _make_repo(tmp_path)
    cfg = TaiyiConfig(
        base_dir=str(tmp_path / "state"),
        executor="sandbox",
        sandbox_dir=str(repo),
        runtime_mode="workflow",
        operating_mode="quality",
        external_git_validation=True,
        external_git_remote_validation=True,
    )

    gateway = build_gateway_from_config(cfg)
    ctx = gateway.submit("commit my changes", scenario="dev.git")

    assert ctx.state is TaskState.COMPLETED
    authority_names = {
        authority["name"]
        for authority in gateway.runtime.validator.configured_authorities()
    }
    assert authority_names == {"git-cli", "git-remote-cli"}
    assert any(r.authority == "git-cli" for r in ctx.evidence.records)
    status, config = GatewayApp(gateway).handle("GET", "/v1/config", {}, "")
    assert status == 200
    assert config["external_git_validation"] is True
    assert config["external_git_remote_validation"] is True
    assert all(a["read_only"] is True for a in config["validation_authorities"])
    assert str(repo) not in json.dumps(config)
