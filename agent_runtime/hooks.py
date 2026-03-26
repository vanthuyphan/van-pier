"""Tool hooks — hard-coded gates that run before and after every tool call.

The LLM NEVER sees this code. It cannot bypass, override, or negotiate with hooks.
Hooks are pure Python functions that return ALLOW or BLOCK.
"""

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class Decision(Enum):
    ALLOW = "allow"
    BLOCK = "block"
    REQUIRE_APPROVAL = "require_approval"


@dataclass
class HookResult:
    decision: Decision
    reason: str = ""
    modified_input: dict | None = None  # optionally modify tool input


@dataclass
class HookContext:
    agent_name: str
    agent_username: str
    tool_name: str
    tool_input: dict
    user: str          # who triggered this
    room_id: str
    timestamp: float = field(default_factory=time.time)


# --- PII Patterns (regex, not LLM) ---

PII_PATTERNS = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),
    "phone": re.compile(r"\b(?:\+1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
}


def scan_pii(text: str) -> list[str]:
    """Scan text for PII. Returns list of PII types found."""
    found = []
    for pii_type, pattern in PII_PATTERNS.items():
        if pattern.search(str(text)):
            found.append(pii_type)
    return found


def redact_pii(text: str) -> str:
    """Redact PII from text."""
    result = str(text)
    for pii_type, pattern in PII_PATTERNS.items():
        result = pattern.sub(f"[REDACTED_{pii_type.upper()}]", result)
    return result


# ============================================================
# HOOK DEFINITIONS
# Each hook is a function: (HookContext) -> HookResult
# Hooks are registered in order. First BLOCK wins.
# ============================================================

def hook_block_mass_email(ctx: HookContext) -> HookResult:
    """Block sending emails to more than 5 recipients."""
    if ctx.tool_name not in ("send_email", "mcp_gmail_send_email"):
        return HookResult(Decision.ALLOW)

    to = ctx.tool_input.get("to", "")
    recipients = [r.strip() for r in to.split(",") if r.strip()]
    if len(recipients) > 5:
        return HookResult(
            Decision.BLOCK,
            f"Blocked: cannot send email to {len(recipients)} recipients (max 5)."
        )
    return HookResult(Decision.ALLOW)


def hook_block_destructive_github(ctx: HookContext) -> HookResult:
    """Block destructive GitHub operations."""
    blocked_tools = [
        "mcp_github_delete_repository",
        "mcp_github_delete_branch",
        "mcp_github_merge_pull_request",
    ]
    if ctx.tool_name in blocked_tools:
        return HookResult(
            Decision.REQUIRE_APPROVAL,
            f"Destructive action: {ctx.tool_name} requires human approval."
        )
    return HookResult(Decision.ALLOW)


def hook_block_file_deletion(ctx: HookContext) -> HookResult:
    """Block file system deletions."""
    if "delete" in ctx.tool_name.lower() and "file" in ctx.tool_name.lower():
        return HookResult(
            Decision.BLOCK,
            "Blocked: agents cannot delete files."
        )
    return HookResult(Decision.ALLOW)


def hook_require_approval_for_sends(ctx: HookContext) -> HookResult:
    """Any action that sends/publishes requires approval."""
    send_tools = [
        "send_email", "mcp_gmail_send_email",
        "mcp_slack_post_message",
        "mcp_jira_create_issue", "mcp_jira_update_issue",
        "mcp_github_create_issue", "mcp_github_create_pull_request",
    ]
    if ctx.tool_name in send_tools:
        return HookResult(
            Decision.REQUIRE_APPROVAL,
            f"Action '{ctx.tool_name}' requires human approval before execution."
        )
    return HookResult(Decision.ALLOW)


def hook_redact_pii_in_output(ctx: HookContext) -> HookResult:
    """Redact PII from tool inputs before they're sent."""
    if ctx.tool_name in ("send_email", "mcp_gmail_send_email", "mcp_slack_post_message"):
        body = ctx.tool_input.get("body", "") or ctx.tool_input.get("text", "")
        pii_found = scan_pii(body)
        if pii_found:
            cleaned_input = dict(ctx.tool_input)
            for key in ("body", "text", "content"):
                if key in cleaned_input:
                    cleaned_input[key] = redact_pii(cleaned_input[key])
            return HookResult(
                Decision.ALLOW,
                f"PII detected ({', '.join(pii_found)}) and redacted.",
                modified_input=cleaned_input,
            )
    return HookResult(Decision.ALLOW)


def hook_rate_limit(ctx: HookContext) -> HookResult:
    """Rate limit tool calls per agent."""
    key = f"{ctx.agent_username}:{ctx.tool_name}"
    now = time.time()

    # Clean old entries
    _rate_window[key] = [t for t in _rate_window.get(key, []) if now - t < 60]

    if len(_rate_window.get(key, [])) >= 20:
        return HookResult(
            Decision.BLOCK,
            f"Rate limited: {ctx.agent_name} exceeded 20 calls/minute for {ctx.tool_name}."
        )

    _rate_window.setdefault(key, []).append(now)
    return HookResult(Decision.ALLOW)


# Rate limit state
_rate_window: dict[str, list[float]] = {}


# ============================================================
# HOOK ENGINE
# ============================================================

class HookEngine:
    """Runs hooks before and after tool calls. Cannot be bypassed."""

    def __init__(self):
        self.pre_hooks: list[Callable] = []
        self.post_hooks: list[Callable] = []

    def add_pre_hook(self, hook: Callable):
        self.pre_hooks.append(hook)

    def add_post_hook(self, hook: Callable):
        self.post_hooks.append(hook)

    def check(self, ctx: HookContext) -> HookResult:
        """Run all pre-hooks. First BLOCK wins. REQUIRE_APPROVAL escalates."""
        approval_needed = None

        for hook in self.pre_hooks:
            try:
                result = hook(ctx)

                if result.decision == Decision.BLOCK:
                    return result  # Hard stop, no negotiation

                if result.decision == Decision.REQUIRE_APPROVAL:
                    approval_needed = result

                if result.modified_input is not None:
                    ctx.tool_input = result.modified_input

            except Exception as e:
                # Hook error = BLOCK (fail closed, not open)
                return HookResult(
                    Decision.BLOCK,
                    f"Hook error (fail-closed): {e}"
                )

        if approval_needed:
            return approval_needed

        return HookResult(Decision.ALLOW)

    def check_output(self, ctx: HookContext, output: str) -> tuple[bool, str]:
        """Run post-hooks on tool output. Returns (allowed, cleaned_output)."""
        cleaned = output
        for hook in self.post_hooks:
            try:
                result = hook(ctx)
                if result.decision == Decision.BLOCK:
                    return False, result.reason
            except Exception:
                return False, "Output hook error (fail-closed)"
        return True, cleaned


def create_default_engine() -> HookEngine:
    """Create the default hook engine with standard safety hooks."""
    engine = HookEngine()

    # Pre-hooks: run BEFORE tool call
    engine.add_pre_hook(hook_rate_limit)
    engine.add_pre_hook(hook_block_mass_email)
    engine.add_pre_hook(hook_block_destructive_github)
    engine.add_pre_hook(hook_block_file_deletion)
    engine.add_pre_hook(hook_require_approval_for_sends)
    engine.add_pre_hook(hook_redact_pii_in_output)

    return engine
