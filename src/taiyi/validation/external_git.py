"""Independent, read-only verification of Git commit existence and identity."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from taiyi.policy import VerificationDepth
from taiyi.validation.checks import Check, external


@dataclass(frozen=True)
class GitSnapshot:
    initial_head: str | None
    expected_name: str | None
    expected_email: str | None
    configuration_digest: str


class GitAuthority:
    """Use Git itself as a post-execution authority, never executor prose.

    ``checks()`` snapshots HEAD and repository-local identity while the Task
    Contract is being prepared. The resulting closures later inspect the same
    repository read-only. A pre-existing commit therefore cannot masquerade as
    the commit created for this task.
    """

    name = "git-cli"
    environment = "workspace"

    def __init__(
        self,
        repository: str | Path,
        *,
        timeout: float = 5.0,
        git_binary: str | None = None,
    ):
        self.repository = Path(repository).resolve()
        self.timeout = timeout
        self.git_binary = git_binary or shutil.which("git")
        if not self.git_binary:
            raise RuntimeError("GitAuthority requires the git executable")

    def checks(
        self,
        task_type: str,
        scenario: str,
        parameters: Mapping[str, str] | None = None,
    ) -> list[Check]:
        if task_type not in {"git_safe_commit", "git_push"}:
            return []
        snapshot = self._snapshot()
        checks: list[Check] = []
        if task_type == "git_safe_commit":
            checks.append(
                external(
                    "git_external:new_head",
                    lambda ctx: self._new_head(snapshot),
                    description=(
                        "Git independently confirms that HEAD changed after the "
                        "acceptance contract was frozen."
                    ),
                    depth=VerificationDepth.CRITICAL,
                    authority=self.name,
                    environment=self.environment,
                    configuration_digest=self._criterion_digest(snapshot, "new_head"),
                )
            )
        checks.append(
            external(
                "git_external:identity_matches_local_config",
                lambda ctx: self._identity_matches(
                    snapshot,
                    revision=(
                        snapshot.initial_head if task_type == "git_push" else "HEAD"
                    ),
                ),
                description=(
                    "Git independently confirms that the accepted commit's author "
                    "and committer match repository-local user.name and user.email."
                ),
                depth=VerificationDepth.CRITICAL,
                authority=self.name,
                environment=self.environment,
                configuration_digest=self._criterion_digest(snapshot, "identity"),
            )
        )
        return checks

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "environment": self.environment,
            "repository_digest": self._repository_digest(),
            "read_only": True,
        }

    def _snapshot(self) -> GitSnapshot:
        initial_head = self._value("rev-parse", "--verify", "HEAD")
        expected_name = self._value("config", "--local", "--get", "user.name")
        expected_email = self._value("config", "--local", "--get", "user.email")
        payload = {
            "repository_digest": self._repository_digest(),
            "initial_head": initial_head,
            "expected_name": expected_name,
            "expected_email": expected_email,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return GitSnapshot(initial_head, expected_name, expected_email, digest)

    def _new_head(self, snapshot: GitSnapshot) -> tuple[bool, str]:
        current = self._value("rev-parse", "--verify", "HEAD")
        if not current:
            return False, "Git reports that HEAD does not exist after execution"
        if snapshot.initial_head == current:
            return False, f"Git HEAD is unchanged at {current[:12]}"
        before = snapshot.initial_head[:12] if snapshot.initial_head else "unborn"
        return True, f"Git HEAD advanced from {before} to {current[:12]}"

    def _identity_matches(
        self,
        snapshot: GitSnapshot,
        *,
        revision: str | None,
    ) -> tuple[bool, str]:
        if not snapshot.expected_name or not snapshot.expected_email:
            return False, "repository-local user.name/user.email is not fully configured"
        if not revision:
            return False, "Git reports no frozen commit to inspect"
        raw = self._value(
            "show",
            "-s",
            "--format=%an%x00%ae%x00%cn%x00%ce",
            revision,
        )
        if raw is None:
            return False, f"Git could not read commit identity for {revision[:12]}"
        fields = raw.split("\x00")
        if len(fields) != 4:
            return False, "Git returned an invalid commit identity record"
        author_name, author_email, committer_name, committer_email = fields
        expected = (snapshot.expected_name, snapshot.expected_email)
        author = (author_name, author_email)
        committer = (committer_name, committer_email)
        if author != expected or committer != expected:
            return False, (
                f"commit identity mismatch: expected {expected[0]} <{expected[1]}>, "
                f"author is {author[0]} <{author[1]}>, "
                f"committer is {committer[0]} <{committer[1]}>"
            )
        return True, (
            f"commit {revision[:12]} author and committer match "
            f"{expected[0]} <{expected[1]}>"
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

    def _repository_digest(self) -> str:
        return "sha256:" + hashlib.sha256(str(self.repository).encode("utf-8")).hexdigest()

    @staticmethod
    def _criterion_digest(snapshot: GitSnapshot, criterion: str) -> str:
        raw = f"{snapshot.configuration_digest}\0{criterion}".encode("utf-8")
        return "sha256:" + hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _environment() -> dict[str, str]:
        env = {
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
        if os.environ.get("SYSTEMROOT"):
            env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
        return env


__all__ = ["GitAuthority", "GitSnapshot"]
