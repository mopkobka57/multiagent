"""
Retry logic for rate limit handling.

Handles rate limit errors (429) and overloaded API errors (529)
with exponential backoff. The SDK session survives retries —
the agent's context is NOT lost on a rate limit pause.

Two retry strategies:
1. query_with_retry() — retries the initial query() call if it fails before streaming
2. resilient_stream() — wraps the async generator to retry mid-stream failures
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import AsyncIterator, Any

from ..config import (
    RATE_LIMIT_MAX_RETRIES,
    RATE_LIMIT_BASE_DELAY,
    RATE_LIMIT_MAX_DELAY,
    RATE_LIMIT_BACKOFF_FACTOR,
)


def is_rate_limit_error(error: Exception) -> bool:
    """
    Check if an exception is a rate limit or overloaded error.
    Covers: HTTP 429, HTTP 529, Anthropic-specific rate limit errors.
    """
    error_str = str(error).lower()
    error_type = type(error).__name__.lower()

    rate_limit_types = [
        "ratelimiterror",
        "ratelimit",
        "overloaded",
        "overloadederror",
        "apierror",
    ]
    if any(t in error_type for t in rate_limit_types):
        return True

    rate_limit_messages = [
        "rate limit",
        "rate_limit",
        "429",
        "529",
        "too many requests",
        "overloaded",
        "capacity",
        "throttl",
        "hit your limit",        # Claude Code CLI subscription limit
        "you've hit your limit", # Claude Code CLI subscription limit
    ]
    if any(msg in error_str for msg in rate_limit_messages):
        return True

    return False


def extract_retry_after(error: Exception) -> int | None:
    """
    Try to extract wait time in seconds from the error.

    Handles:
      - Standard "Retry-After: 30" header
      - Claude Code CLI "resets 6am (TZ)" (today)
      - Claude Code CLI "resets Feb 18 at 4pm (TZ)" (future date)
    """
    error_str = str(error)

    # Standard Retry-After header
    match = re.search(r"retry.?after[:\s]+(\d+)", error_str, re.IGNORECASE)
    if match:
        return int(match.group(1))

    # Claude Code CLI: "resets <time>"
    reset_delay = _parse_cli_reset_time(error_str)
    if reset_delay is not None:
        return reset_delay

    return None


def _parse_cli_reset_time(error_str: str) -> int | None:
    """
    Parse Claude Code CLI reset time like:
      "resets 6am (Europe/Moscow)"
      "resets Feb 18 at 4pm (Europe/Moscow)"
    Returns seconds to wait, or None if not parseable.
    """
    from datetime import datetime, timedelta
    import zoneinfo

    # Extract timezone
    tz_match = re.search(r"\(([A-Za-z/_]+)\)", error_str)
    try:
        tz = zoneinfo.ZoneInfo(tz_match.group(1)) if tz_match else None
    except (KeyError, zoneinfo.ZoneInfoNotFoundError):
        tz = None

    now = datetime.now(tz) if tz else datetime.now()

    # Pattern 1: "resets 6am" or "resets 11pm" (today or tomorrow)
    m = re.search(r"resets\s+(\d{1,2})(am|pm)", error_str, re.IGNORECASE)
    if m:
        hour = int(m.group(1))
        if m.group(2).lower() == "pm" and hour != 12:
            hour += 12
        elif m.group(2).lower() == "am" and hour == 12:
            hour = 0
        reset_time = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if reset_time <= now:
            reset_time += timedelta(days=1)
        return max(int((reset_time - now).total_seconds()) + 60, 60)  # +60s buffer

    # Pattern 2: "resets Feb 18 at 4pm"
    m = re.search(r"resets\s+(\w+)\s+(\d{1,2})\s+at\s+(\d{1,2})(am|pm)", error_str, re.IGNORECASE)
    if m:
        month_str, day, hour = m.group(1), int(m.group(2)), int(m.group(3))
        if m.group(4).lower() == "pm" and hour != 12:
            hour += 12
        elif m.group(4).lower() == "am" and hour == 12:
            hour = 0
        month_names = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }
        month = month_names.get(month_str[:3].lower())
        if month:
            year = now.year
            reset_time = now.replace(year=year, month=month, day=day,
                                     hour=hour, minute=0, second=0, microsecond=0)
            if reset_time <= now:
                reset_time = reset_time.replace(year=year + 1)
            return max(int((reset_time - now).total_seconds()) + 60, 60)

    return None


def calculate_delay(attempt: int, retry_after: int | None = None) -> float:
    """Calculate delay with exponential backoff. No cap for CLI resets."""
    if retry_after and retry_after > 0:
        # For CLI resets (hours-long waits), don't cap at MAX_DELAY
        if retry_after > RATE_LIMIT_MAX_DELAY:
            return retry_after
        return min(retry_after + 5, RATE_LIMIT_MAX_DELAY)

    delay = RATE_LIMIT_BASE_DELAY * (RATE_LIMIT_BACKOFF_FACTOR ** attempt)
    return min(delay, RATE_LIMIT_MAX_DELAY)


async def wait_for_rate_limit(attempt: int, error: Exception) -> float:
    """Wait for rate limit to clear. Prints countdown. Returns actual time waited."""
    retry_after = extract_retry_after(error)
    delay = calculate_delay(attempt, retry_after)

    source = f"(API suggested {retry_after}s)" if retry_after else "(exponential backoff)"
    print(f"\n[RATE LIMIT] Hit rate limit on attempt {attempt + 1}/{RATE_LIMIT_MAX_RETRIES}")
    print(f"[RATE LIMIT] Waiting {delay:.0f}s {source}")
    print(f"[RATE LIMIT] Error: {str(error)[:200]}")

    start = time.time()
    remaining = delay
    while remaining > 0:
        if remaining > 3600:
            hours = remaining / 3600
            print(f"[RATE LIMIT] Resuming in {hours:.1f}h...")
            await asyncio.sleep(1800)  # Log every 30 min for long waits
        elif remaining > 60:
            minutes = remaining / 60
            print(f"[RATE LIMIT] Resuming in {minutes:.0f}m...")
            await asyncio.sleep(600)  # Log every 10 min
        elif remaining > 30:
            print(f"[RATE LIMIT] Resuming in {remaining:.0f}s...")
            await asyncio.sleep(30)
        else:
            await asyncio.sleep(remaining)
        remaining = delay - (time.time() - start)

    actual_waited = time.time() - start
    print(f"[RATE LIMIT] Waited {actual_waited:.0f}s. Resuming...")
    return actual_waited


async def query_with_retry(query_fn, prompt: str, options: Any) -> AsyncIterator:
    """
    Call query() with retry on rate limit errors.
    Handles errors that occur when STARTING the query.
    """
    last_error = None

    for attempt in range(RATE_LIMIT_MAX_RETRIES):
        try:
            return query_fn(prompt=prompt, options=options)
        except Exception as e:
            if is_rate_limit_error(e) and attempt < RATE_LIMIT_MAX_RETRIES - 1:
                last_error = e
                await wait_for_rate_limit(attempt, e)
                continue
            raise

    raise last_error or RuntimeError("Max retries exceeded")


async def resilient_stream(
    query_fn,
    prompt: str,
    options: Any,
    on_message: Any = None,
) -> list[Any]:
    """
    Run a query with full resilience: retry on rate limits both at
    startup and mid-stream.

    When a rate limit hits mid-stream, we save what we collected so far
    and start a NEW query with a continuation prompt.

    Also detects Claude Code CLI limit messages in the output
    (e.g. "You've hit your limit · resets 6am") even when the
    exception itself is a generic "exit code 1".
    """
    all_messages: list[Any] = []
    current_prompt = prompt
    total_waited = 0.0

    for attempt in range(RATE_LIMIT_MAX_RETRIES):
        try:
            stream = query_fn(prompt=current_prompt, options=options)
            async for message in stream:
                all_messages.append(message)
                if on_message:
                    on_message(message)

            if total_waited > 0:
                print(f"[RATE LIMIT] Task completed after {total_waited:.0f}s total wait time")
            return all_messages

        except KeyboardInterrupt:
            raise

        except Exception as e:
            # Check both the exception text AND recent output for rate limit signals
            is_limit = is_rate_limit_error(e)
            cli_limit_msg = ""
            if not is_limit:
                cli_limit_msg = _detect_cli_limit_in_output(all_messages)
                if cli_limit_msg:
                    is_limit = True

            if is_limit and attempt < RATE_LIMIT_MAX_RETRIES - 1:
                # Use CLI message for better delay parsing if available
                effective_error = _FakeError(cli_limit_msg) if cli_limit_msg else e
                waited = await wait_for_rate_limit(attempt, effective_error)
                total_waited += waited

                progress_summary = _summarize_progress(all_messages)
                if progress_summary:
                    current_prompt = (
                        f"{prompt}\n\n"
                        f"NOTE: This is a RESUMED session after a rate limit pause.\n"
                        f"Progress so far:\n{progress_summary}\n"
                        f"Continue from where you left off. Do NOT repeat completed work."
                    )
                continue

            raise

    print(f"[RATE LIMIT] Exhausted all {RATE_LIMIT_MAX_RETRIES} retries. Giving up.")
    return all_messages


class _FakeError(Exception):
    """Wrapper to pass CLI limit message text to extract_retry_after."""
    pass


def _detect_cli_limit_in_output(messages: list[Any]) -> str:
    """
    Check last few messages for Claude Code CLI limit text.
    Returns the limit message if found, empty string otherwise.
    """
    from claude_agent_sdk import AssistantMessage, TextBlock

    # Check last 3 messages
    for msg in reversed(messages[-3:]):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    text_lower = block.text.lower()
                    if "hit your limit" in text_lower or "you've hit your limit" in text_lower:
                        return block.text
    return ""


def _summarize_progress(messages: list[Any]) -> str:
    """Extract a brief summary of progress from collected messages."""
    from claude_agent_sdk import AssistantMessage, TextBlock

    text_parts: list[str] = []
    for msg in messages:
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    text_parts.append(block.text.strip())

    if not text_parts:
        return ""

    combined = "\n---\n".join(text_parts[-5:])
    if len(combined) > 2000:
        combined = "...\n" + combined[-2000:]

    return combined
