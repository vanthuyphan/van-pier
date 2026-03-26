"""Central policy enforcer — reads policy.yaml and enforces all rules.

This is the ONLY hook you need. It replaces all other hooks by reading
from a single policy.yaml config file.

Priority 1 — runs before everything else.
"""

import re
import time
import yaml
from pathlib import Path

name = "policy-enforcer"
description = "Central policy engine — reads hooks/policy.yaml"
hook_type = "pre"
priority = 1

# Load policy on import
_policy_path = Path(__file__).parent / "policy.yaml"
_policy = {}
_rate_window: dict[str, list[float]] = {}
_global_window: list[float] = []

PII_PATTERNS = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),
    "phone": re.compile(r"\b(?:\+1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b"),
}


def _load_policy():
    global _policy
    if _policy_path.exists():
        _policy = yaml.safe_load(_policy_path.read_text()) or {}
    return _policy


def check(ctx):
    from agent_runtime.hooks import HookResult, Decision

    policy = _load_policy()

    # === BLOCKED ACTIONS ===
    blocked = policy.get("blocked", [])
    if ctx.tool_name in blocked:
        return HookResult(Decision.BLOCK, f"Policy: '{ctx.tool_name}' is permanently blocked.")

    # Also block anything with "delete" in the name if it's in filesystem/destructive
    if "delete" in ctx.tool_name.lower() and ctx.tool_name not in policy.get("require_approval", []):
        if ctx.tool_name not in policy.get("blocked", []):
            # Not explicitly listed, but has "delete" — block by default
            return HookResult(Decision.BLOCK, f"Policy: '{ctx.tool_name}' blocked (destructive action).")

    # === RATE LIMITING ===
    rate_config = policy.get("rate_limit", {})
    per_agent = rate_config.get("per_agent_per_tool", 20)
    global_limit = rate_config.get("global_per_minute", 100)
    now = time.time()

    # Per-agent rate limit
    key = f"{ctx.agent_username}:{ctx.tool_name}"
    _rate_window[key] = [t for t in _rate_window.get(key, []) if now - t < 60]
    if len(_rate_window.get(key, [])) >= per_agent:
        return HookResult(
            Decision.BLOCK,
            f"Rate limited: {ctx.agent_name} exceeded {per_agent} calls/min for {ctx.tool_name}."
        )
    _rate_window.setdefault(key, []).append(now)

    # Global rate limit
    global _global_window
    _global_window = [t for t in _global_window if now - t < 60]
    if len(_global_window) >= global_limit:
        return HookResult(Decision.BLOCK, f"Global rate limit exceeded ({global_limit}/min).")
    _global_window.append(now)

    # === MASS EMAIL PROTECTION ===
    max_recipients = policy.get("max_email_recipients", 5)
    if ctx.tool_name in ("send_email", "mcp_gmail_send_email"):
        to = ctx.tool_input.get("to", "")
        recipients = [r.strip() for r in to.split(",") if r.strip()]
        if len(recipients) > max_recipients:
            return HookResult(
                Decision.BLOCK,
                f"Policy: cannot send to {len(recipients)} recipients (max {max_recipients})."
            )

    # === PII PROTECTION ===
    pii_config = policy.get("pii", {})
    if pii_config.get("enabled", True):
        scan_tools = pii_config.get("scan_tools", [])
        pii_action = pii_config.get("action", "redact")

        if ctx.tool_name in scan_tools:
            text_fields = ["body", "text", "content", "message"]
            found_pii = []
            modified = dict(ctx.tool_input)

            for field in text_fields:
                if field not in modified:
                    continue
                text = str(modified[field])
                for pii_type, pattern in PII_PATTERNS.items():
                    if pattern.search(text):
                        found_pii.append(pii_type)
                        if pii_action == "redact":
                            text = pattern.sub(f"[REDACTED_{pii_type.upper()}]", text)
                modified[field] = text

            if found_pii:
                if pii_action == "block":
                    return HookResult(
                        Decision.BLOCK,
                        f"Policy: PII detected ({', '.join(found_pii)}). Cannot send."
                    )
                elif pii_action == "redact":
                    return HookResult(
                        Decision.ALLOW,
                        f"PII detected ({', '.join(found_pii)}) and redacted.",
                        modified_input=modified,
                    )

    # === REQUIRE APPROVAL ===
    require_approval = policy.get("require_approval", [])
    if ctx.tool_name in require_approval:
        return HookResult(
            Decision.REQUIRE_APPROVAL,
            f"Policy: '{ctx.tool_name}' requires human approval."
        )

    return HookResult(Decision.ALLOW)
