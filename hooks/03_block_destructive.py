"""Block or require approval for destructive operations."""

name = "block-destructive"
description = "Blocks file deletions, requires approval for destructive GitHub/Jira ops"
hook_type = "pre"
priority = 30

BLOCKED = [
    "delete_file", "remove_file",
    "mcp_filesystem_delete",
]

APPROVAL_REQUIRED = [
    "mcp_github_delete_repository",
    "mcp_github_delete_branch",
    "mcp_github_merge_pull_request",
    "mcp_jira_delete_issue",
]

def check(ctx):
    from agent_runtime.hooks import HookResult, Decision

    if ctx.tool_name in BLOCKED or "delete" in ctx.tool_name.lower():
        return HookResult(Decision.BLOCK, f"Blocked: {ctx.tool_name} is not allowed.")

    if ctx.tool_name in APPROVAL_REQUIRED:
        return HookResult(
            Decision.REQUIRE_APPROVAL,
            f"Destructive action: {ctx.tool_name} requires human approval."
        )

    return HookResult(Decision.ALLOW)
