"""Read-only GitHub verification for pushed refs and account attribution."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

from taiyi.policy import VerificationDepth
from taiyi.validation.checks import Check, external

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_SAFE_BRANCH = re.compile(r"^[A-Za-z0-9._/-]+$")
Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class GitHubSnapshot:
    local_head: str | None
    owner: str | None
    repository: str | None
    ref: str
    expected_login: str
    problem: str | None
    configuration_digest: str


class GitHubAuthority:
    """Ask GitHub itself which SHA and account identity it displays.

    Local Git identity and a remote ref are necessary but do not prove that
    GitHub mapped the commit email to the intended user account. This authority
    freezes the target GitHub repository, branch, SHA, and expected login before
    execution, then reads the platform API independently after the push.
    """

    name = "github-cli"
    environment = "remote-platform"

    def __init__(
        self,
        repository: str | Path,
        expected_login: str,
        *,
        timeout: float = 20.0,
        git_binary: str | None = None,
        gh_binary: str | None = None,
        runner: Runner | None = None,
    ):
        if not expected_login or not _SAFE_NAME.fullmatch(expected_login):
            raise ValueError("GitHubAuthority requires a valid expected GitHub login")
        self.repository = Path(repository).resolve()
        self.expected_login = expected_login
        self.timeout = timeout
        self.git_binary = git_binary or shutil.which("git")
        self.gh_binary = gh_binary or shutil.which("gh")
        self._runner = runner or subprocess.run
        if not self.git_binary:
            raise RuntimeError("GitHubAuthority requires the git executable")
        if not self.gh_binary:
            raise RuntimeError("GitHubAuthority requires the gh executable")

    def checks(
        self,
        task_type: str,
        scenario: str,
        parameters: Mapping[str, str] | None = None,
    ) -> list[Check]:
        if task_type != "git_push":
            return []
        values = parameters or {}
        snapshot = self._snapshot(
            remote=values.get("remote", "origin"),
            ref=values.get("ref", "main"),
        )
        target = (
            f"{snapshot.owner}/{snapshot.repository}" if snapshot.owner and snapshot.repository
            else "the frozen GitHub repository"
        )
        common = {
            "depth": VerificationDepth.CRITICAL,
            "authority": self.name,
            "environment": self.environment,
            "configuration_digest": snapshot.configuration_digest,
        }
        return [
            external(
                "github:ref_matches_frozen_head",
                lambda ctx: self._ref_matches(snapshot),
                description=(
                    f"GitHub branch {target}:{snapshot.ref} points to the commit frozen before push."
                ),
                **common,
            ),
            external(
                "github:commit_identity_matches_expected_login",
                lambda ctx: self._identity_matches(snapshot),
                description=(
                    "GitHub maps both author and committer of the frozen commit "
                    f"to expected account {snapshot.expected_login}."
                ),
                **common,
            ),
        ]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "environment": self.environment,
            "repository_digest": self._digest(str(self.repository)),
            "expected_login": self.expected_login,
            "read_only": True,
            "network_access": True,
        }

    def _snapshot(self, *, remote: str, ref: str) -> GitHubSnapshot:
        normalized_ref = ref.removeprefix("refs/heads/")
        problem = self._parameter_problem(remote, normalized_ref)
        local_head = self._git_value("rev-parse", "--verify", "HEAD")
        remote_url = None if problem else self._git_value("remote", "get-url", remote)
        owner = repository = None
        if not problem and not remote_url:
            problem = f"Git remote {remote!r} is not configured"
        if not problem and remote_url:
            owner, repository, problem = self._parse_github_repository(remote_url)
        payload = {
            "workspace_digest": self._digest(str(self.repository)),
            "local_head": local_head,
            "owner": owner,
            "repository": repository,
            "ref": normalized_ref,
            "expected_login": self.expected_login.casefold(),
            "problem": problem,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return GitHubSnapshot(
            local_head,
            owner,
            repository,
            normalized_ref,
            self.expected_login,
            problem,
            digest,
        )

    def _ref_matches(self, snapshot: GitHubSnapshot) -> tuple[bool, str]:
        problem = self._snapshot_problem(snapshot)
        if problem:
            return False, problem
        endpoint = (
            f"repos/{snapshot.owner}/{snapshot.repository}/git/ref/heads/"
            f"{quote(snapshot.ref, safe='/')}"
        )
        payload, error = self._api(endpoint)
        if error:
            return False, error
        observed = str((payload.get("object") or {}).get("sha") or "")
        if observed != snapshot.local_head:
            return False, (
                f"GitHub branch {snapshot.ref} points to {observed[:12] or 'no commit'}, "
                f"expected frozen commit {snapshot.local_head[:12]}"
            )
        return True, (
            f"GitHub branch {snapshot.ref} matches frozen commit {snapshot.local_head[:12]}"
        )

    def _identity_matches(self, snapshot: GitHubSnapshot) -> tuple[bool, str]:
        problem = self._snapshot_problem(snapshot)
        if problem:
            return False, problem
        endpoint = f"repos/{snapshot.owner}/{snapshot.repository}/commits/{snapshot.local_head}"
        payload, error = self._api(endpoint)
        if error:
            return False, error
        expected = snapshot.expected_login.casefold()
        author = str((payload.get("author") or {}).get("login") or "")
        committer = str((payload.get("committer") or {}).get("login") or "")
        if author.casefold() != expected or committer.casefold() != expected:
            return False, (
                "GitHub account attribution mismatch: "
                f"expected {snapshot.expected_login}, author={author or 'unmapped'}, "
                f"committer={committer or 'unmapped'}"
            )
        return True, (
            f"GitHub maps author and committer to expected account {snapshot.expected_login}"
        )

    @staticmethod
    def _snapshot_problem(snapshot: GitHubSnapshot) -> str | None:
        if snapshot.problem:
            return snapshot.problem
        if not snapshot.local_head:
            return "Git reports no local HEAD to verify on GitHub"
        if not snapshot.owner or not snapshot.repository:
            return "frozen remote is not a supported GitHub repository"
        return None

    def _api(self, endpoint: str) -> tuple[dict, str | None]:
        try:
            proc = self._runner(
                [self.gh_binary, "api", "--method", "GET", endpoint],
                cwd=self.repository,
                env=self._environment(),
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {}, "GitHub API verification was unavailable"
        if proc.returncode != 0:
            return {}, f"GitHub API verification failed (exit {proc.returncode})"
        try:
            payload = json.loads(proc.stdout)
        except (json.JSONDecodeError, TypeError):
            return {}, "GitHub API returned an invalid response"
        if not isinstance(payload, dict):
            return {}, "GitHub API returned an invalid response"
        return payload, None

    def _git_value(self, *args: str) -> str | None:
        try:
            proc = subprocess.run(
                [self.git_binary, *args],
                cwd=self.repository,
                env=self._environment(),
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return proc.stdout.rstrip("\n") if proc.returncode == 0 else None

    @staticmethod
    def _parameter_problem(remote: str, ref: str) -> str | None:
        if not remote or remote.startswith("-") or not _SAFE_BRANCH.fullmatch(remote):
            return "frozen Git remote name is invalid"
        if not ref or ref.startswith("-") or ".." in ref or not _SAFE_BRANCH.fullmatch(ref):
            return "frozen Git branch name is invalid"
        return None

    @staticmethod
    def _parse_github_repository(remote_url: str) -> tuple[str | None, str | None, str | None]:
        value = remote_url.strip()
        scp = re.fullmatch(r"(?:[^@/]+@)?github\.com:([^/]+)/([^/]+?)(?:\.git)?", value, re.I)
        if scp:
            owner, repository = scp.group(1), scp.group(2)
        else:
            parsed = urlsplit(value)
            if parsed.scheme.casefold() not in {"https", "ssh"}:
                return None, None, "configured GitHub remote uses an unsupported transport"
            if parsed.hostname is None or parsed.hostname.casefold() != "github.com":
                return None, None, "configured remote is not on github.com"
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) != 2:
                return None, None, "configured GitHub remote path is invalid"
            owner, repository = parts
            repository = repository.removesuffix(".git")
        if not _SAFE_NAME.fullmatch(owner) or not _SAFE_NAME.fullmatch(repository):
            return None, None, "configured GitHub owner or repository name is invalid"
        return owner, repository, None

    @staticmethod
    def _digest(value: str) -> str:
        return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _environment() -> dict[str, str]:
        allowed = {
            "PATH",
            "HOME",
            "LANG",
            "LC_ALL",
            "LC_CTYPE",
            "TZ",
            "TERM",
            "USER",
            "LOGNAME",
            "SHELL",
            "TMPDIR",
            "XDG_CONFIG_HOME",
            "GH_CONFIG_DIR",
            "GH_HOST",
            "GH_TOKEN",
            "GITHUB_TOKEN",
        }
        env = {key: value for key, value in os.environ.items() if key in allowed}
        env.update({"GH_PROMPT_DISABLED": "1", "GH_PAGER": "cat", "NO_COLOR": "1"})
        return env


__all__ = ["GitHubAuthority", "GitHubSnapshot"]
