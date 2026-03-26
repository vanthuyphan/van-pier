"""Require human approval for any action that sends or publishes."""

name = "approve-sends"
description = "Requires approval before sending emails, creating issues, posting messages"
hook_type = "pre"
priority = 40

SEND_TOOLS = [
    "send_email",
    "mcp_gmail_send_email", "mcp_gmail_send_message",
    "mcp_slack_post_message", "mcp_slack_send_message",
    "mcp_jira_create_issue", "mcp_jira_update_issue",
    "mcp_github_create_issue", "mcp_github_create_pull_request",
    "mcp_github_add_issue_comment",
]

def check(ctx):
    from agent_runtime.hooks import HookResult, Decision

    if ctx.tool_name in SEND_TOOLS:
        return HookResult(
            Decision.REQUIRE_APPROVAL,
            f"'{ctx.tool_name}' requires human approval."
        )
    return HookResult(Decision.ALLOW)
