"""Budgeted assembly and deterministic, artifact-backed context compaction."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from taiyi.llm.base import LLMMessage
from taiyi.context.index_jobs import RepositoryIndexJobManager
from taiyi.context.repository import RepositoryContext, RepositoryContextIndex, RepositoryIndexResult


_COMPACTION_MARKER = "[TaiYi structured compaction v1]"
_TOOL_RESULT_MARKER = "[tool result]"
_PATHISH = re.compile(r"(?:^|[\s'\"])([A-Za-z0-9_.@-]+(?:/[A-Za-z0-9_.@-]+)+)")


def estimate_text_tokens(text: str) -> int:
    """Conservative tokenizer-independent estimate for English, code, and CJK."""

    ascii_count = sum(ord(char) < 128 for char in text)
    non_ascii = len(text) - ascii_count
    return max(1, math.ceil(ascii_count / 4) + non_ascii)


def estimate_message_tokens(messages: list[LLMMessage]) -> int:
    return sum(estimate_text_tokens(message.content) + 4 for message in messages)


class ContextBudgetError(RuntimeError):
    failure_kind = "CONTEXT_OVERFLOW"


@dataclass(frozen=True)
class ContextAssembly:
    messages: list[LLMMessage]
    canonical_messages: list[LLMMessage]
    estimated_tokens: int
    prompt_budget_tokens: int
    repository: RepositoryContext | None = None
    compaction: dict[str, Any] | None = None


class ContextEngine:
    """Own model-context projection while leaving the canonical transcript durable."""

    def __init__(
        self,
        *,
        repository: RepositoryContextIndex | None = None,
        base_dir: str | Path | None = None,
        context_window_tokens: int = 128_000,
        response_reserve_tokens: int = 16_384,
        tool_result_max_tokens: int = 4_000,
        index_jobs: RepositoryIndexJobManager | None = None,
    ):
        self.repository = repository
        self.base_dir = Path(base_dir) if base_dir is not None else None
        self.context_window_tokens = max(4096, int(context_window_tokens))
        self.response_reserve_tokens = max(256, int(response_reserve_tokens))
        if self.response_reserve_tokens >= self.context_window_tokens:
            raise ValueError("response_reserve_tokens must be smaller than context_window_tokens")
        self.tool_result_max_tokens = max(256, int(tool_result_max_tokens))
        self.index_jobs = index_jobs

    @property
    def prompt_budget_tokens(self) -> int:
        return self.context_window_tokens - self.response_reserve_tokens

    def ensure_repository(
        self,
        ctx,
        *,
        force: bool = False,
        progress=None,
        attached=None,
        park: bool = False,
    ) -> RepositoryIndexResult | None:
        if self.repository is None:
            return None
        state = dict(ctx.repository_context or {})
        latest = self.repository.latest()
        needs_refresh = force or state.get("needs_refresh", latest is None)
        if needs_refresh or latest is None:
            if "index_generation" in state:
                generation = max(0, int(state["index_generation"] or 0))
            elif self.index_jobs is not None:
                generation = self.index_jobs.claim_generation(
                    self.repository.repository_id
                )
            else:
                generation = 0
            state["index_generation"] = generation
            ctx.repository_context = state
            if self.index_jobs is not None and self.repository.db_path is not None:
                operation_id = self.index_jobs.operation_id(
                    self.repository.repository_id, generation
                )
                result = self.index_jobs.run(
                    repository_root=self.repository.root,
                    db_path=self.repository.db_path,
                    max_files=self.repository.max_files,
                    max_file_bytes=self.repository.max_file_bytes,
                    chunk_lines=self.repository.chunk_lines,
                    operation_id=operation_id,
                    consumer_id=ctx.task_id,
                    park=park,
                    attached=attached,
                    progress=progress,
                )
                state["index_operation_id"] = operation_id
            else:
                result = self.repository.refresh(progress=progress)
        else:
            result = latest
        state.update(result.to_dict())
        state["needs_refresh"] = False
        state["status"] = "ready" if result.complete else "partial"
        state.pop("index_job_id", None)
        ctx.repository_context = state
        return result

    def mark_repository_dirty(self, ctx) -> None:
        if self.repository is None:
            return
        state = dict(ctx.repository_context or {})
        state["needs_refresh"] = True
        if self.index_jobs is not None:
            state["index_generation"] = self.index_jobs.advance_generation(
                self.repository.repository_id
            )
        else:
            state["index_generation"] = max(
                0, int(state.get("index_generation", 0) or 0)
            ) + 1
        ctx.repository_context = state

    def poll_repository_job(self, job_id: str):
        if self.index_jobs is None:
            raise RuntimeError("durable repository index jobs are not configured")
        return self.index_jobs.poll(job_id)

    def cancel_repository_job(self, job_id: str, *, consumer_id: str):
        if self.index_jobs is None:
            raise RuntimeError("durable repository index jobs are not configured")
        return self.index_jobs.cancel_consumer(job_id, consumer_id)

    def assemble(
        self,
        ctx,
        messages: list[LLMMessage],
        *,
        force_compaction: bool = False,
        repository_scale: float = 1.0,
    ) -> ContextAssembly:
        """Return a bounded projection and, if needed, a new canonical transcript."""

        if ctx.policy is None:
            raise ValueError("context assembly requires a resolved task policy")
        canonical = list(messages)
        repository_context = self._retrieve_repository(ctx, scale=repository_scale)
        repo_message = self._repository_message(repository_context)
        projected = self._sanitize_tool_results(canonical)
        if repo_message is not None:
            projected = self._insert_repository_message(projected, repo_message)
        total = estimate_message_tokens(projected)
        compaction = None
        if force_compaction or total > self.prompt_budget_tokens:
            repo_tokens = estimate_text_tokens(repo_message.content) + 4 if repo_message else 0
            compacted, compaction = self._compact(
                ctx,
                canonical,
                target_tokens=max(512, self.prompt_budget_tokens - repo_tokens),
                force=force_compaction,
            )
            canonical = compacted
            projected = self._sanitize_tool_results(canonical)
            if repo_message is not None:
                projected = self._insert_repository_message(projected, repo_message)
            total = estimate_message_tokens(projected)

        # The repository section is always expendable before frozen system/goal
        # context. Reduce it deterministically instead of cutting source chunks.
        scale = max(0.0, min(1.0, repository_scale))
        while total > self.prompt_budget_tokens and repository_context and scale > 0.05:
            scale *= 0.5
            repository_context = self._retrieve_repository(ctx, scale=scale)
            repo_message = self._repository_message(repository_context)
            projected = self._sanitize_tool_results(canonical)
            if repo_message is not None:
                projected = self._insert_repository_message(projected, repo_message)
            total = estimate_message_tokens(projected)
        if total > self.prompt_budget_tokens and repo_message is not None:
            repository_context = None
            projected = self._sanitize_tool_results(canonical)
            total = estimate_message_tokens(projected)
        if total > self.prompt_budget_tokens:
            raise ContextBudgetError(
                f"required task invariants need about {total} tokens but the prompt budget is "
                f"{self.prompt_budget_tokens}; cannot safely discard the contract or current goal"
            )

        state = dict(ctx.context_state or {})
        state.update({
            "estimated_prompt_tokens": total,
            "prompt_budget_tokens": self.prompt_budget_tokens,
            "response_reserve_tokens": self.response_reserve_tokens,
            "repository_tokens": repository_context.estimated_tokens if repository_context else 0,
            "repository_snippets": len(repository_context.snippets) if repository_context else 0,
            "repository_inventory_complete": (
                repository_context.inventory_complete if repository_context else None
            ),
            "repository_unsearchable_files": (
                repository_context.unsearchable_files if repository_context else 0
            ),
            "repository_omitted_files": (
                repository_context.omitted_files if repository_context else 0
            ),
        })
        if compaction:
            state["compaction_count"] = int(state.get("compaction_count", 0)) + 1
            state["last_compaction_id"] = compaction["compaction_id"]
            state["last_compaction_digest"] = compaction["source_digest"]
        ctx.context_state = state
        return ContextAssembly(
            messages=projected,
            canonical_messages=canonical,
            estimated_tokens=total,
            prompt_budget_tokens=self.prompt_budget_tokens,
            repository=repository_context,
            compaction=compaction,
        )

    def repository_prompt(self, ctx, *, scale: float = 1.0) -> str | None:
        context = self._retrieve_repository(ctx, scale=scale)
        return context.render() if context and context.snippets else None

    def _retrieve_repository(self, ctx, *, scale: float) -> RepositoryContext | None:
        if self.repository is None or ctx.policy is None:
            return None
        latest = self.repository.latest()
        if latest is None:
            return None
        query_parts = [ctx.prompt]
        if ctx.validation_summary:
            query_parts.append(ctx.validation_summary)
        for result in ctx.step_results[-4:]:
            query_parts.extend(str(arg) for arg in result.step.args)
        token_budget = max(0, int(ctx.policy.repository_context_tokens * max(0.0, scale)))
        max_chunks = max(0, int(math.ceil(ctx.policy.repository_context_chunks * max(0.0, scale))))
        return self.repository.retrieve(
            "\n".join(query_parts), token_budget=token_budget, max_chunks=max_chunks,
        )

    @staticmethod
    def _repository_message(context: RepositoryContext | None) -> LLMMessage | None:
        if context is None or not context.render():
            return None
        # Repository bytes are evidence supplied to the task, never trusted
        # instructions. Keeping them out of the system role is a hard boundary.
        return LLMMessage("user", context.render())

    @staticmethod
    def _insert_repository_message(
        messages: list[LLMMessage], repository_message: LLMMessage
    ) -> list[LLMMessage]:
        # Keep all trusted system instructions ahead of untrusted repository data.
        index = 0
        while index < len(messages) and messages[index].role == "system":
            index += 1
        return [*messages[:index], repository_message, *messages[index:]]

    def _sanitize_tool_results(self, messages: list[LLMMessage]) -> list[LLMMessage]:
        out: list[LLMMessage] = []
        for message in messages:
            if (
                not message.content.startswith(_TOOL_RESULT_MARKER)
                or estimate_text_tokens(message.content) <= self.tool_result_max_tokens
            ):
                out.append(message)
                continue
            digest = "sha256:" + hashlib.sha256(message.content.encode("utf-8")).hexdigest()
            payload_budget = max(64, self.tool_result_max_tokens - 80)
            head = self._take_token_budget(message.content, payload_budget // 3, reverse=False)
            tail = self._take_token_budget(
                message.content, payload_budget - payload_budget // 3, reverse=True
            )
            clipped = (
                head
                + f"\n[tool result projection truncated; original_chars={len(message.content)} digest={digest}]\n"
                + tail
            )
            out.append(LLMMessage(message.role, clipped))
        return out

    @staticmethod
    def _take_token_budget(text: str, budget: int, *, reverse: bool) -> str:
        chars = reversed(text) if reverse else iter(text)
        selected: list[str] = []
        used = 0.0
        for char in chars:
            cost = 0.25 if ord(char) < 128 else 1.0
            if used + cost > budget:
                break
            selected.append(char)
            used += cost
        if reverse:
            selected.reverse()
        return "".join(selected)

    def _compact(
        self,
        ctx,
        messages: list[LLMMessage],
        *,
        target_tokens: int,
        force: bool,
    ) -> tuple[list[LLMMessage], dict[str, Any] | None]:
        task_prompt_index = self._task_prompt_index(messages, ctx.prompt)
        invariant_indices = {
            index for index, message in enumerate(messages)
            if message.role == "system" and not message.content.startswith(_COMPACTION_MARKER)
        }
        if task_prompt_index is not None:
            invariant_indices.add(task_prompt_index)
        candidate_indices = [
            index for index in range(len(messages))
            if index not in invariant_indices
            and not messages[index].content.startswith(_COMPACTION_MARKER)
        ]
        if not candidate_indices:
            return messages, None

        groups = self._atomic_groups(messages, candidate_indices)
        invariant = [messages[index] for index in sorted(invariant_indices)]
        # Leave room for the deterministic summary itself and never let a mode
        # weaken the invariant system/task material.
        keep_budget = min(
            int(ctx.policy.context_keep_recent_tokens),
            max(0, target_tokens - estimate_message_tokens(invariant) - 1024),
        )
        kept_groups: list[list[int]] = []
        kept_tokens = 0
        for group in reversed(groups):
            group_tokens = estimate_message_tokens([messages[index] for index in group])
            if kept_groups and kept_tokens + group_tokens > keep_budget:
                break
            if not kept_groups and group_tokens > keep_budget:
                # The projection sanitizer will clip a huge trailing tool result.
                kept_groups.append(group)
                break
            kept_groups.append(group)
            kept_tokens += group_tokens
        kept_indices = {index for group in kept_groups for index in group}
        removed_indices = [index for index in candidate_indices if index not in kept_indices]
        if not removed_indices and not force:
            return messages, None
        if not removed_indices:
            # A provider can reject a prompt even when the local estimate is low.
            # Forced recovery must make real progress, so drop the oldest group.
            removed_indices = list(groups[0])
            kept_indices.difference_update(removed_indices)

        source_digest = self._messages_digest(messages)
        compaction_id = source_digest.split(":", 1)[1][:24]
        artifact = self._write_compaction_artifact(
            ctx,
            compaction_id=compaction_id,
            source_digest=source_digest,
            messages=messages,
            removed_indices=removed_indices,
            kept_indices=sorted(kept_indices),
        )
        summary = self._structured_summary(
            ctx,
            artifact=artifact,
            messages=messages,
            removed_indices=removed_indices,
            source_digest=source_digest,
            previous_summary=next(
                (
                    message.content for message in reversed(messages)
                    if message.content.startswith(_COMPACTION_MARKER)
                ),
                None,
            ),
        )
        system_invariants = [
            messages[index] for index in sorted(invariant_indices)
            if messages[index].role == "system"
        ]
        other_invariants = [
            messages[index] for index in sorted(invariant_indices)
            if messages[index].role != "system"
        ]
        compacted = [*system_invariants, LLMMessage("system", summary), *other_invariants]
        compacted.extend(messages[index] for index in sorted(kept_indices))
        record = {
            "compaction_id": compaction_id,
            "source_digest": source_digest,
            "artifact": artifact,
            "messages_before": len(messages),
            "messages_after": len(compacted),
            "tokens_before": estimate_message_tokens(messages),
            "tokens_after": estimate_message_tokens(compacted),
            "removed_messages": len(removed_indices),
            "kept_messages": len(kept_indices),
        }
        return compacted, record

    @staticmethod
    def _task_prompt_index(messages: list[LLMMessage], prompt: str) -> int | None:
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].role == "user" and messages[index].content == prompt:
                return index
        return None

    @staticmethod
    def _atomic_groups(messages: list[LLMMessage], candidate_indices: list[int]) -> list[list[int]]:
        allowed = set(candidate_indices)
        groups: list[list[int]] = []
        index = 0
        while index < len(messages):
            if index not in allowed:
                index += 1
                continue
            if (
                messages[index].role == "assistant"
                and messages[index].content.startswith("tool_call:")
                and index + 1 in allowed
                and messages[index + 1].content.startswith(_TOOL_RESULT_MARKER)
            ):
                groups.append([index, index + 1])
                index += 2
            else:
                groups.append([index])
                index += 1
        return groups

    def _write_compaction_artifact(
        self,
        ctx,
        *,
        compaction_id: str,
        source_digest: str,
        messages: list[LLMMessage],
        removed_indices: list[int],
        kept_indices: list[int],
    ) -> str | None:
        if self.base_dir is None:
            return None
        safe_task = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(ctx.task_id)).strip("._")
        safe_task = (safe_task[:96] or hashlib.sha256(str(ctx.task_id).encode()).hexdigest()[:24])
        directory = self.base_dir / "context" / "compactions" / safe_task
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{compaction_id}.json"
        doc = {
            "schema_version": "taiyi.context-compaction/v1",
            "task_id": ctx.task_id,
            "compaction_id": compaction_id,
            "source_digest": source_digest,
            "created_at": time.time(),
            "parent_compaction_id": (ctx.context_state or {}).get("last_compaction_id"),
            "removed_indices": removed_indices,
            "kept_indices": kept_indices,
            "messages": [{"role": item.role, "content": item.content} for item in messages],
        }
        temp = path.with_suffix(f".tmp-{os.getpid()}")
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(doc, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        try:
            directory_fd = os.open(directory, os.O_RDONLY)
        except OSError:
            pass
        else:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return str(path)

    @staticmethod
    def _structured_summary(
        ctx,
        *,
        artifact: str | None,
        messages: list[LLMMessage],
        removed_indices: list[int],
        source_digest: str,
        previous_summary: str | None,
    ) -> str:
        constraints: list[str] = []
        files: set[str] = set()
        for index in removed_indices:
            message = messages[index]
            if message.role == "user" and not message.content.startswith(_TOOL_RESULT_MARKER):
                text = " ".join(message.content.split())
                if text:
                    constraints.append(text[:400])
            files.update(match.group(1) for match in _PATHISH.finditer(message.content))
        lines = [
            _COMPACTION_MARKER,
            "Goal is preserved intact in the current user message. "
            f"digest=sha256:{hashlib.sha256(ctx.prompt.encode('utf-8')).hexdigest()} "
            f"preview={ctx.prompt[:1200]!r}",
            f"Source transcript digest: {source_digest}",
            f"Full transcript artifact: {artifact or 'not persisted (in-memory run)'}",
            f"Compacted messages: {len(removed_indices)}",
            "This is deterministic runtime metadata, not proof that any action succeeded.",
        ]
        if constraints:
            lines.append("Prior user constraints and corrections:")
            lines.extend(f"- {item}" for item in constraints[-8:])
        if previous_summary:
            bounded_previous = (
                previous_summary
                if len(previous_summary) <= 4000
                else previous_summary[:2000]
                + "\n[previous compacted state clipped]\n"
                + previous_summary[-2000:]
            )
            lines.extend(["Previous compacted state (bounded):", bounded_previous])
        if ctx.step_results:
            lines.append("Governed step ledger:")
            for number, result in enumerate(ctx.step_results, 1):
                output = result.output or ""
                output_digest = "sha256:" + hashlib.sha256(output.encode("utf-8")).hexdigest()
                preview = " ".join(output.split())[:240]
                args_text = repr(result.step.args)
                if len(args_text) > 800:
                    args_text = (
                        args_text[:600]
                        + "...<args clipped; digest=sha256:"
                        + hashlib.sha256(args_text.encode("utf-8")).hexdigest()
                        + ">"
                    )
                lines.append(
                    f"- {number}. {result.step.tool} {args_text}; "
                    f"verdict={result.verdict}; executed={result.executed}; "
                    f"output_digest={output_digest}; preview={preview!r}"
                )
        if files:
            lines.append("Referenced files:")
            lines.extend(f"- {path}" for path in sorted(files)[:100])
        if ctx.validation_summary:
            validation = ctx.validation_summary
            if len(validation) > 2000:
                validation = (
                    validation[:1600]
                    + "...<validation clipped; digest=sha256:"
                    + hashlib.sha256(validation.encode("utf-8")).hexdigest()
                    + ">"
                )
            lines.append(f"Latest validation evidence: {validation}")
        lines.append("Continue from the intact recent messages below; do not repeat executed effects.")
        return "\n".join(lines)

    @staticmethod
    def _messages_digest(messages: list[LLMMessage]) -> str:
        payload = json.dumps(
            [{"role": item.role, "content": item.content} for item in messages],
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(payload).hexdigest()


__all__ = [
    "ContextAssembly",
    "ContextBudgetError",
    "ContextEngine",
    "estimate_message_tokens",
    "estimate_text_tokens",
]
