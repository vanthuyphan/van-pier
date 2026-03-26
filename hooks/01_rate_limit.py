"""Rate limit tool calls per agent."""
import time

name = "rate-limiter"
description = "Limits each agent to 20 tool calls per minute per tool"
hook_type = "pre"
priority = 10

_window: dict[str, list[float]] = {}

def check(ctx):
    from agent_runtime.hooks import HookResult, Decision

    key = f"{ctx.agent_username}:{ctx.tool_name}"
    now = time.time()
    _window[key] = [t for t in _window.get(key, []) if now - t < 60]

    if len(_window.get(key, [])) >= 20:
        return HookResult(
            Decision.BLOCK,
            f"Rate limited: {ctx.agent_name} exceeded 20 calls/minute for {ctx.tool_name}."
        )

    _window.setdefault(key, []).append(now)
    return HookResult(Decision.ALLOW)
