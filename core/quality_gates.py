"""
Quality gates — automated checks between agent steps.

Gates:
  - tsc:    TypeScript type checking (fast, after each step)
  - build:  Full production build (slow, end of task)
  - visual: Screenshot comparison via Playwright (after implementation)
"""

from __future__ import annotations

import asyncio
import subprocess
import signal
from pathlib import Path

from .. import config
from ..config import (
    QUALITY_GATES,
    APP_DIR,
    SCREENSHOTS_DIR,
    DEV_SERVER_PORT,
    DEV_SERVER_URL,
    VISUAL_TEST_PAGES,
)


# ---------------------------------------------------------------------------
# Code quality gates
# ---------------------------------------------------------------------------

async def run_gate(name: str) -> tuple[bool, str]:
    """Run a single quality gate by name. Returns (passed, output)."""
    command = QUALITY_GATES.get(name)
    if not command:
        return False, f"Unknown gate: {name}"

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(APP_DIR),
        )
        passed = result.returncode == 0
        output = result.stdout + result.stderr
        return passed, output.strip()
    except subprocess.TimeoutExpired:
        return False, f"Gate '{name}' timed out after 120s"
    except Exception as e:
        return False, f"Gate '{name}' error: {e}"


async def run_full_gates() -> tuple[bool, str]:
    """Run tsc then build. Used at end of task."""
    outputs = []

    tsc_passed, tsc_output = await run_gate("tsc")
    outputs.append(f"[tsc] {'PASS' if tsc_passed else 'FAIL'}\n{tsc_output}")
    if not tsc_passed:
        return False, "\n\n".join(outputs)

    build_passed, build_output = await run_gate("build")
    outputs.append(f"[build] {'PASS' if build_passed else 'FAIL'}\n{build_output}")
    return build_passed, "\n\n".join(outputs)


# ---------------------------------------------------------------------------
# Visual testing gates
# ---------------------------------------------------------------------------

class DevServer:
    """Manages the Next.js dev server lifecycle for visual testing."""

    def __init__(self):
        self._process: subprocess.Popen | None = None

    async def start(self) -> bool:
        """Start dev server if not already running. Returns True if ready."""
        if await self._is_running():
            return True

        try:
            self._process = subprocess.Popen(
                config.DEV_COMMAND.format(port=DEV_SERVER_PORT),
                shell=True,
                cwd=str(APP_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN),
            )
            for _ in range(30):
                await asyncio.sleep(1)
                if await self._is_running():
                    return True
            return False
        except Exception:
            return False

    async def stop(self):
        """Stop the dev server."""
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None

    async def _is_running(self) -> bool:
        """Check if dev server responds on the expected port."""
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                f"curl -s -o /dev/null -w '%{{http_code}}' {DEV_SERVER_URL}",
                shell=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout.strip().startswith("200") or result.stdout.strip().startswith("3")
        except Exception:
            return False


async def capture_screenshots(
    task_id: str,
    phase: str,  # "before" or "after"
) -> tuple[bool, str, list[str]]:
    """
    Capture screenshots of key pages using Playwright CLI.
    Returns (success, output_message, screenshot_paths).
    """
    output_dir = SCREENSHOTS_DIR / task_id / phase
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshot_paths: list[str] = []
    errors: list[str] = []

    for page_path in VISUAL_TEST_PAGES:
        page_name = page_path.strip("/").replace("/", "_") or "dashboard"
        screenshot_file = output_dir / f"{page_name}.png"
        url = f"{DEV_SERVER_URL}{page_path}"

        script = f"""
const {{ chromium }} = require('playwright');
(async () => {{
    const browser = await chromium.launch();
    const page = await browser.newPage({{ viewport: {{ width: 1280, height: 720 }} }});
    try {{
        await page.goto('{url}', {{ waitUntil: 'networkidle', timeout: 15000 }});
        await page.waitForTimeout(2000);
        await page.screenshot({{ path: '{screenshot_file}', fullPage: false }});

        const errors = [];
        page.on('console', msg => {{ if (msg.type() === 'error') errors.push(msg.text()); }});
        await page.waitForTimeout(1000);

        console.log(JSON.stringify({{ success: true, errors }}));
    }} catch(e) {{
        console.log(JSON.stringify({{ success: false, error: e.message }}));
    }} finally {{
        await browser.close();
    }}
}})();
"""
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                f"node -e {repr(script)}",
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(APP_DIR),
            )
            if result.returncode == 0 and screenshot_file.exists():
                screenshot_paths.append(str(screenshot_file))
            else:
                errors.append(f"Failed to capture {page_path}: {result.stderr[:200]}")
        except subprocess.TimeoutExpired:
            errors.append(f"Timeout capturing {page_path}")
        except Exception as e:
            errors.append(f"Error capturing {page_path}: {e}")

    success = len(errors) == 0 and len(screenshot_paths) > 0
    output = f"Screenshots captured: {len(screenshot_paths)}/{len(VISUAL_TEST_PAGES)}"
    if errors:
        output += f"\nErrors:\n" + "\n".join(f"  - {e}" for e in errors)

    return success, output, screenshot_paths


async def run_visual_test(task_id: str) -> tuple[bool, str]:
    """
    Full visual test: capture 'after' screenshots and compare with 'before'.
    Returns (passed, report).
    """
    before_dir = SCREENSHOTS_DIR / task_id / "before"
    after_dir = SCREENSHOTS_DIR / task_id / "after"

    if not before_dir.exists() or not list(before_dir.glob("*.png")):
        return True, "No baseline screenshots found — skipping visual comparison."

    success, capture_output, after_paths = await capture_screenshots(task_id, "after")
    if not success:
        return False, f"Failed to capture post-change screenshots:\n{capture_output}"

    report_lines = [
        f"VISUAL TEST REPORT for {task_id}",
        f"Before screenshots: {before_dir}",
        f"After screenshots: {after_dir}",
        "",
        "Screenshots captured:",
    ]

    for after_path in after_paths:
        page_name = Path(after_path).stem
        before_path = before_dir / f"{page_name}.png"
        has_before = before_path.exists()
        report_lines.append(
            f"  - {page_name}: before={'exists' if has_before else 'MISSING'}, after=captured"
        )

    report_lines.append("")
    report_lines.append(
        "NOTE: Screenshots saved. Use the Visual Tester agent to compare them "
        "and check for regressions."
    )

    return True, "\n".join(report_lines)
