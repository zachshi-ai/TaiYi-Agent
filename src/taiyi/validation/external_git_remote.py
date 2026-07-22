"""Read-only verification that a Git push reached its frozen remote ref."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from taiyi.policy import VerificationDepth
from taiyi.validation.checks import Check, external

_SAFE_GIT_NAME = re.compile(r"^[A-Za-z0-9._/-]+$")
_ALLOWED_REMOTE_SCHEMES = {"file", "git", "http", "https", "ssh"}


@dataclass(frozen=True)
class GitRemoteSnapshot:
    local_head: str | None
    remote: str
    ref: str
    remote_url: str | None
    remote_url_digest: str
    problem: str | None
    configuration_digest: str


class GitRemoteAuthority:
    """Ask the remote independently whether its branch reached frozen HEAD.

    The Task Contract freezes local HEAD, remote name, ref, and the resolved
    remote URL before execution. Validation later runs ``git ls-remote`` against
    that frozen URL, not the possibly modified repository configuration. An
    executor's successful ``git push`` text is therefore insufficient evidence.
    """

    name = "git-remote-cli"
    environment = "remote"

    def __init__(
        self,
        repository: str | Path,
        *,
        timeout: float = 15.0,
        git_binary: str | None = None,
    ):
        self.repository = Path(repository).resolve()
        self.timeout = timeout
        self.git_binary = git_binary or shutil.which("git")
        if not self.git_binary:
            raise RuntimeError("GitRemoteAuthority requires the git executable")

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
        return [
            external(
                "git_remote:ref_matches_frozen_head",
                lambda ctx: self._remote_matches(snapshot),
                description=(
                    f"The frozen remote/ref {snapshot.remote} {snapshot.ref} "
                    "points to the commit frozen before push."
                ),
                depth=VerificationDepth.CRITICAL,
                authority=self.name,
                environment=self.environment,
                configuration_digest=snapshot.configuration_digest,
            )
        ]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "environment": self.environment,
            "repository_digest": self._repository_digest(),
            "read_only": True,
            "network_access": True,
        }

    def _snapshot(self, *, remote: str, ref: str) -> GitRemoteSnapshot:
        problem = self._parameter_problem(remote, ref)
        normalized_ref = ref.removeprefix("refs/heads/")
        local_head = self._value("rev-parse", "--verify", "HEAD")
        remote_url = None if problem else self._value("remote", "get-url", remote)
        if not problem and not remote_url:
            problem = f"Git remote {remote!r} is not configured"
        if not problem and remote_url:
            problem = self._remote_url_problem(remote_url)
        remote_url_digest = self._digest(remote_url or "")
        payload = {
            "repository_digest": self._repository_digest(),
            "local_head": local_head,
            "remote": remote,
            "ref": normalized_ref,
            "remote_url_digest": remote_url_digest,
            "problem": problem,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        configuration_digest = "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return GitRemoteSnapshot(
            local_head=local_head,
            remote=remote,
            ref=normalized_ref,
            remote_url=remote_url,
            remote_url_digest=remote_url_digest,
            problem=problem,
            configuration_digest=configuration_digest,
        )

    def _remote_matches(self, snapshot: GitRemoteSnapshot) -> tuple[bool, str]:
        if snapshot.problem:
            return False, snapshot.problem
        if not snapshot.local_head:
            return False, "Git reports no local HEAD to push"
        assert snapshot.remote_url is not None
        full_ref = f"refs/heads/{snapshot.ref}"
        try:
            proc = subprocess.run(
                [
                    self.git_binary,
                    "-c",
                    "protocol.ext.allow=never",
                    "ls-remote",
                    "--heads",
                    "--",
                    snapshot.remote_url,
                    full_ref,
                ],
                cwd=self.repository,
                env=self._environment(),
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False, (
                f"Git could not read frozen remote/ref "
                f"{snapshot.remote} {snapshot.ref}"
            )
        if proc.returncode != 0:
            return False, (
                f"Git could not read frozen remote/ref {snapshot.remote} "
                f"{snapshot.ref} (exit {proc.returncode})"
            )
        observed = self._parse_ref(proc.stdout, full_ref)
        if not observed:
            return False, f"remote branch {snapshot.remote}/{snapshot.ref} does not exist"
        if observed != snapshot.local_head:
            return False, (
                f"remote {snapshot.remote}/{snapshot.ref} points to {observed[:12]}, "
                f"expected frozen commit {snapshot.local_head[:12]}"
            )
        return True, (
            f"remote {snapshot.remote}/{snapshot.ref} matches frozen commit "
            f"{snapshot.local_head[:12]}"
        )

    def _value(self, *args: str) -> str | None:
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
        if proc.returncode != 0:
            return None
        return proc.stdout.rstrip("\n")

    @staticmethod
    def _parameter_problem(remote: str, ref: str) -> str | None:
        normalized_ref = ref.removeprefix("refs/heads/")
        if not remote or remote.startswith("-") or not _SAFE_GIT_NAME.fullmatch(remote):
            return "frozen Git remote name is invalid"
        if (
            not normalized_ref
            or normalized_ref.startswith("-")
            or ".." in normalized_ref
            or not _SAFE_GIT_NAME.fullmatch(normalized_ref)
        ):
            return "frozen Git branch name is invalid"
        return None

    @staticmethod
    def _remote_url_problem(remote_url: str) -> str | None:
        if (
            not remote_url
            or remote_url.startswith("-")
            or "::" in remote_url
            or any(ord(char) < 32 for char in remote_url)
        ):
            return "configured Git remote URL uses an unsafe transport"
        if "://" in remote_url:
            scheme = remote_url.split("://", 1)[0].casefold()
            if scheme not in _ALLOWED_REMOTE_SCHEMES:
                return "configured Git remote URL uses an unsupported transport"
        return None

    @staticmethod
    def _parse_ref(output: str, full_ref: str) -> str | None:
        for line in output.splitlines():
            fields = line.split("\t", 1)
            if len(fields) == 2 and fields[1] == full_ref:
                return fields[0]
        return None

    def _repository_digest(self) -> str:
        return self._digest(str(self.repository))

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
            "SSH_AUTH_SOCK",
        }
        env = {key: value for key, value in os.environ.items() if key in allowed}
        env.update({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
        return env


__all__ = ["GitRemoteAuthority", "GitRemoteSnapshot"]
