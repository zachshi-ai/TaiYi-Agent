"""Internal subprocess entrypoint for the controlled TaiYi benchmark cell."""
from __future__ import annotations

import argparse
from pathlib import Path

from taiyi.benchmark.model_fixture import CONTROLLED_MODEL_ID
from taiyi.benchmark.schema import COMPARATIVE_WORKER_SCHEMA, write_artifact
from taiyi.gateway import build_gateway
from taiyi.llm import OpenAICompatProvider
from taiyi.tools import SandboxExecutor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--prompt", required=True)
    args = parser.parse_args(argv)

    run_root = Path(args.run_root).resolve()
    workspace = run_root / "workspace"
    result_path = run_root / "worker-result.json"
    payload: dict[str, object] = {
        "reported_state": "HARNESS_ERROR",
        "tool_calls": 0,
        "execution_environment": "workspace",
        "effect_statuses": [],
        "error": None,
    }
    try:
        provider = OpenAICompatProvider(
            args.base_url,
            model=CONTROLLED_MODEL_ID,
            api_key="benchmark-local-only",
            name="taiyi-controlled-comparison",
            connect_timeout=5,
            first_token_timeout=5,
            stream_idle_timeout=5,
            hard_timeout=20,
        )
        executor = SandboxExecutor(workspace, backend="local")
        gateway = build_gateway(
            base_dir=run_root / "state",
            mode="agent",
            operating_mode="balanced",
            executor=executor,
            provider=provider,
            validator=False,
            llm_sleep=lambda _delay: None,
        )
        ctx = gateway.submit(args.prompt, operating_mode="balanced")
        payload.update({
            "reported_state": ctx.state.value,
            "tool_calls": len(ctx.executed_steps),
            "execution_environment": ctx.execution_environment,
            "effect_statuses": [effect.status.value for effect in ctx.effects],
        })
    except Exception as exc:  # parent still needs a normalized receipt
        payload["error"] = f"{type(exc).__name__}: {exc}"

    write_artifact(result_path, COMPARATIVE_WORKER_SCHEMA, payload)
    return 1 if payload["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
