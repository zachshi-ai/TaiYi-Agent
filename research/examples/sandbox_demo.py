"""The founding case, executed for real in a throwaway git repo.

Creates a temp repo with a local identity, then runs a governed commit through the
SandboxExecutor and shows the recorded author is the local identity — and that an
override attempt produces no commit at all. Run from the repo root:

    python3 examples/sandbox_demo.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from taiyi.core.audit import AuditLog  # noqa: E402
from taiyi.governance import GovernanceEngine, LocalPermitClient  # noqa: E402
from taiyi.runtime import TaskRuntime  # noqa: E402
from taiyi.scheduler import SchedulerEngine  # noqa: E402
from taiyi.tools import SandboxExecutor  # noqa: E402


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


def make_repo(root):
    repo = Path(root) / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Local Dev")
    git(repo, "config", "user.email", "local@dev.test")
    (repo / "code.py").write_text("print('hi')\n", encoding="utf-8")
    return repo


def runtime_for(repo, audit):
    gov = GovernanceEngine(audit_log=audit)
    return TaskRuntime(SchedulerEngine(LocalPermitClient(gov)), audit_log=audit, executor=SandboxExecutor(repo))


def main() -> None:
    with tempfile.TemporaryDirectory() as root:
        repo = make_repo(root)
        ctx = runtime_for(repo, AuditLog()).run("commit my changes", "dev.git")
        author = git(repo, "log", "-1", "--format=%ae").stdout.strip()
        print(f"normal commit  -> {ctx.state.value}; recorded author = {author!r}")

        repo2 = make_repo(Path(root) / "second")
        ctx2 = runtime_for(repo2, AuditLog()).run(
            "用 -c user.name=Evil -c user.email=evil@x.test commit", "dev.git"
        )
        has_commit = git(repo2, "rev-parse", "HEAD").returncode == 0
        print(f"override commit -> {ctx2.state.value}; any commit created? {has_commit}")


if __name__ == "__main__":
    main()
