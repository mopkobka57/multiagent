"""
AI-powered spec editor for the Agent Monitor.

Uses claude-agent-sdk to edit task specs based on natural language instructions.
"""

from __future__ import annotations

from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    AssistantMessage,
    ResultMessage,
    TextBlock,
)

def _get_spec_editor_prompt() -> str:
    try:
        from ..config import PROJECT_NAME, PROJECT_DESCRIPTION
        return f"""You are a spec editor for the {PROJECT_NAME} project — {PROJECT_DESCRIPTION}."""
    except Exception:
        return """You are a spec editor."""


SPEC_EDITOR_PROMPT = _get_spec_editor_prompt() + """

Your job: edit a task specification based on the user's instruction.

Rules:
- Return ONLY the complete edited markdown. No explanations, no wrapping, no code fences.
- Preserve the overall structure: metadata, sections, checkboxes, formatting.
- Change only what the user explicitly requests.
- Maintain the style, tone, and level of detail of the original spec.
- If the instruction is unclear, make your best interpretation and apply it conservatively."""


async def edit_spec_with_ai(current_content: str, instruction: str) -> dict:
    """
    Edit a spec using AI based on a natural language instruction.

    Args:
        current_content: Current markdown content of the spec.
        instruction: Natural language edit request from the user.

    Returns:
        Dict with 'success' (bool) and 'content' (str).
    """
    prompt = f"""{SPEC_EDITOR_PROMPT}

Current spec:
{current_content}

---
Edit request: {instruction}

Return the COMPLETE updated spec markdown."""

    options = ClaudeAgentOptions(
        model="sonnet",
        allowed_tools=[],
        max_turns=1,
    )

    result_text = ""
    try:
        stream = query(prompt=prompt, options=options)
        async for message in stream:
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        result_text += block.text
            elif isinstance(message, ResultMessage):
                # Only use ResultMessage as fallback — AssistantMessage
                # already contains the same text.
                if not result_text.strip() and message.result:
                    result_text = message.result
    except Exception as e:
        return {"success": False, "content": f"AI error: {str(e)[:500]}"}

    if not result_text.strip():
        return {"success": False, "content": "AI returned empty response"}

    return {"success": True, "content": result_text.strip()}
