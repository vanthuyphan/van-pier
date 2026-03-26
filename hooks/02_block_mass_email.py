"""Block sending emails to more than 5 recipients."""

name = "block-mass-email"
description = "Prevents agents from sending emails to more than 5 recipients"
hook_type = "pre"
priority = 20

def check(ctx):
    from agent_runtime.hooks import HookResult, Decision

    if ctx.tool_name not in ("send_email", "mcp_gmail_send_email"):
        return HookResult(Decision.ALLOW)

    to = ctx.tool_input.get("to", "")
    recipients = [r.strip() for r in to.split(",") if r.strip()]
    if len(recipients) > 5:
        return HookResult(
            Decision.BLOCK,
            f"Blocked: cannot send to {len(recipients)} recipients (max 5)."
        )
    return HookResult(Decision.ALLOW)
