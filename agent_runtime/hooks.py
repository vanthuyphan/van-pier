"""Tool hooks — pluggable gates that run before and after every tool call.

The LLM NEVER sees this code. It cannot bypass, override, or negotiate with hooks.

Hooks are Python files in the hooks/ directory. Each file exports:
  - name: str
  - description: str
  - hook_type: "pre" | "post" | "both"
  - priority: int (lower = runs first)
  - check(ctx: HookContext) -> HookResult

Install a hook by dropping a .py file in the hooks/ directory.
"""

import re
import time
import importlib.util
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable


class Decision(Enum):
    ALLOW = "allow"
    BLOCK = "block"
    REQUIRE_APPROVAL = "require_approval"


@dataclass
class HookResult:
    decision: Decision
    reason: str = ""
    modified_input: dict | None = None


@dataclass
class HookContext:
    agent_name: str
    agent_username: str
    tool_name: str
    tool_input: dict
    user: str
    room_id: str
    output: str = ""  # populated for post-hooks
    timestamp: float = field(default_factory=time.time)


@dataclass
class HookPlugin:
    name: str
    description: str
    hook_type: str  # "pre", "post", "both"
    priority: int
    check: Callable
    source_file: str = ""
    enabled: bool = True


class HookEngine:
    """Runs hook plugins before and after tool calls. Cannot be bypassed."""

    def __init__(self):
        self.plugins: list[HookPlugin] = []

    def register(self, plugin: HookPlugin):
        """Register a hook plugin."""
        self.plugins.append(plugin)
        self.plugins.sort(key=lambda p: p.priority)
        print(f"    Hook registered: [{plugin.priority}] {plugin.name} ({plugin.hook_type})")

    def unregister(self, name: str):
        """Remove a hook plugin by name."""
        self.plugins = [p for p in self.plugins if p.name != name]

    def list_plugins(self) -> list[dict]:
        """List all registered plugins."""
        return [
            {
                "name": p.name,
                "description": p.description,
                "hook_type": p.hook_type,
                "priority": p.priority,
                "enabled": p.enabled,
                "source_file": p.source_file,
            }
            for p in self.plugins
        ]

    def check_pre(self, ctx: HookContext) -> HookResult:
        """Run all pre-hooks. First BLOCK wins. REQUIRE_APPROVAL escalates."""
        approval_result = None

        for plugin in self.plugins:
            if not plugin.enabled:
                continue
            if plugin.hook_type not in ("pre", "both"):
                continue

            try:
                result = plugin.check(ctx)

                if result.decision == Decision.BLOCK:
                    return result

                if result.decision == Decision.REQUIRE_APPROVAL:
                    approval_result = result

                if result.modified_input is not None:
                    ctx.tool_input = result.modified_input

            except Exception as e:
                return HookResult(Decision.BLOCK, f"Hook '{plugin.name}' error (fail-closed): {e}")

        if approval_result:
            return approval_result

        return HookResult(Decision.ALLOW)

    def check_post(self, ctx: HookContext) -> HookResult:
        """Run all post-hooks on tool output."""
        for plugin in self.plugins:
            if not plugin.enabled:
                continue
            if plugin.hook_type not in ("post", "both"):
                continue

            try:
                result = plugin.check(ctx)
                if result.decision == Decision.BLOCK:
                    return result
                if result.modified_input is not None:
                    ctx.output = result.modified_input.get("output", ctx.output)
            except Exception as e:
                return HookResult(Decision.BLOCK, f"Post-hook '{plugin.name}' error (fail-closed): {e}")

        return HookResult(Decision.ALLOW)


def load_hooks_from_directory(engine: HookEngine, hooks_dir: str = "./hooks"):
    """Load all hook plugins from the hooks/ directory."""
    hooks_path = Path(hooks_dir)
    if not hooks_path.exists():
        hooks_path.mkdir(parents=True, exist_ok=True)
        _create_default_hooks(hooks_path)

    for py_file in sorted(hooks_path.glob("*.py")):
        if py_file.name.startswith("_"):
            continue

        try:
            spec = importlib.util.spec_from_file_location(py_file.stem, py_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            plugin = HookPlugin(
                name=getattr(module, "name", py_file.stem),
                description=getattr(module, "description", ""),
                hook_type=getattr(module, "hook_type", "pre"),
                priority=getattr(module, "priority", 100),
                check=module.check,
                source_file=str(py_file),
            )
            engine.register(plugin)

        except Exception as e:
            print(f"    Failed to load hook {py_file.name}: {e}")


def _write_pii_hook(filepath: Path):
    """Write the PII redaction hook file."""
    lines = [
        '"""Detect and redact PII in outgoing content."""',
        'import re',
        '',
        'name = "pii-redactor"',
        'description = "Scans outgoing content for PII (SSN, credit cards, phone) and redacts it"',
        'hook_type = "pre"',
        'priority = 50',
        '',
        'PII_PATTERNS = {',
        r'    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),',
        r'    "credit_card": re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),',
        r'    "phone": re.compile(r"\b(?:\+1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b"),',
        '}',
        '',
        'OUTGOING_TOOLS = [',
        '    "send_email", "mcp_gmail_send_email",',
        '    "mcp_slack_post_message",',
        ']',
        '',
        'def check(ctx):',
        '    from agent_runtime.hooks import HookResult, Decision',
        '',
        '    if ctx.tool_name not in OUTGOING_TOOLS:',
        '        return HookResult(Decision.ALLOW)',
        '',
        '    text_fields = ["body", "text", "content", "message"]',
        '    found_pii = []',
        '    modified = dict(ctx.tool_input)',
        '',
        '    for field in text_fields:',
        '        if field not in modified:',
        '            continue',
        '        text = str(modified[field])',
        '        for pii_type, pattern in PII_PATTERNS.items():',
        '            if pattern.search(text):',
        '                found_pii.append(pii_type)',
        '                text = pattern.sub(f"[REDACTED_{pii_type.upper()}]", text)',
        '        modified[field] = text',
        '',
        '    if found_pii:',
        '        return HookResult(',
        '            Decision.ALLOW,',
        """            f"PII detected ({', '.join(found_pii)}) and redacted.",""",
        '            modified_input=modified,',
        '        )',
        '',
        '    return HookResult(Decision.ALLOW)',
    ]
    filepath.write_text('\n'.join(lines) + '\n')


def _create_default_hooks(hooks_dir: Path):
    """Create default hook plugins as individual files."""

    # --- Rate limiter ---
    (hooks_dir / "01_rate_limit.py").write_text('''"""Rate limit tool calls per agent."""
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
''')

    # --- Block mass email ---
    (hooks_dir / "02_block_mass_email.py").write_text('''"""Block sending emails to more than 5 recipients."""

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
''')

    # --- Block destructive ops ---
    (hooks_dir / "03_block_destructive.py").write_text('''"""Block or require approval for destructive operations."""

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
''')

    # --- Require approval for sends ---
    (hooks_dir / "04_approve_sends.py").write_text('''"""Require human approval for any action that sends or publishes."""

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
''')

    # --- PII redaction ---
    _write_pii_hook(hooks_dir / "05_pii_redact.py")

    print(f"    Created default hooks in {hooks_dir}/")


def create_default_engine(hooks_dir: str = "./hooks") -> HookEngine:
    """Create a hook engine and load plugins from directory."""
    engine = HookEngine()
    load_hooks_from_directory(engine, hooks_dir)
    return engine
