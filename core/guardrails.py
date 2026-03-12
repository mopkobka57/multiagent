"""
Guardrails — protect system-critical files from agent modifications.

Defense-in-depth layer 2 (hard guard).
Layer 1 (soft guard) is the system prompt instructions.

After each agent execution, check git diff for modifications to protected paths.
If violations found: revert them and report.
"""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path

from ..config import PROJECT_ROOT, PROTECTED_PATHS, PROTECTED_EXCEPTIONS


def _matches_pattern(file_path: str, pattern: str) -> bool:
    """Check if a file path matches a protection pattern.

    Pattern rules:
      "dir/"         → matches everything under dir/
      "file.md"      → exact match
      "dir/_*"       → files starting with _ in dir/
      ".env*"        → files starting with .env
    """
    if pattern.endswith("/"):
        return file_path.startswith(pattern) or file_path == pattern.rstrip("/")

    if "*" in pattern:
        # For patterns like "multiagent_specs/specs/_*" or ".env*"
        return fnmatch.fnmatch(file_path, pattern)

    return file_path == pattern


def _is_protected(file_path: str) -> bool:
    """Check if a file is protected (in PROTECTED_PATHS and not in PROTECTED_EXCEPTIONS)."""
    for exception in PROTECTED_EXCEPTIONS:
        if _matches_pattern(file_path, exception):
            return False

    for protected in PROTECTED_PATHS:
        if _matches_pattern(file_path, protected):
            return True

    return False


def check_protected_paths() -> tuple[bool, list[str]]:
    """Check git working tree for modifications to protected paths.

    Returns:
        (all_clean, violations) where:
        - all_clean: True if no protected files were modified
        - violations: list of protected file paths that were modified
    """
    try:
        # Get all modified files (staged + unstaged + untracked)
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True, text=True, cwd=PROJECT_ROOT,
        )
        changed = set(result.stdout.strip().splitlines()) if result.stdout.strip() else set()

        # Also check staged files
        result_staged = subprocess.run(
            ["git", "diff", "--name-only", "--cached"],
            capture_output=True, text=True, cwd=PROJECT_ROOT,
        )
        if result_staged.stdout.strip():
            changed |= set(result_staged.stdout.strip().splitlines())

        # Also check untracked files (new files created by agent)
        result_untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True, text=True, cwd=PROJECT_ROOT,
        )
        if result_untracked.stdout.strip():
            changed |= set(result_untracked.stdout.strip().splitlines())

    except (subprocess.CalledProcessError, FileNotFoundError):
        print("[GUARDRAIL] WARNING: Could not run git diff. Skipping protection check.")
        return True, []

    violations = [f for f in changed if _is_protected(f)]
    return len(violations) == 0, sorted(violations)


def revert_protected_files(violations: list[str]) -> int:
    """Revert modifications to protected files.

    For tracked files: git checkout HEAD -- <file>
    For untracked files: rm <file>

    Returns number of files reverted.
    """
    reverted = 0
    for file_path in violations:
        full_path = PROJECT_ROOT / file_path
        # Check if file is tracked
        result = subprocess.run(
            ["git", "ls-files", file_path],
            capture_output=True, text=True, cwd=PROJECT_ROOT,
        )
        if result.stdout.strip():
            # Tracked file — restore from HEAD
            subprocess.run(
                ["git", "checkout", "HEAD", "--", file_path],
                cwd=PROJECT_ROOT,
            )
            reverted += 1
            print(f"  [REVERTED] {file_path} (restored from HEAD)")
        elif full_path.exists():
            # Untracked new file in protected area — remove
            full_path.unlink()
            reverted += 1
            print(f"  [REMOVED] {file_path} (unauthorized new file)")

    return reverted


def enforce_guardrails() -> bool:
    """Run the full guardrail check: detect violations, revert them, report.

    Returns True if clean (no violations), False if violations were found and reverted.
    """
    clean, violations = check_protected_paths()
    if clean:
        return True

    print(f"\n{'='*60}")
    print(f"GUARDRAIL VIOLATION: {len(violations)} protected file(s) modified!")
    print(f"{'='*60}")
    for v in violations:
        print(f"  - {v}")

    reverted = revert_protected_files(violations)
    print(f"\nReverted {reverted} file(s). Agent changes to protected paths have been undone.")
    print(f"{'='*60}\n")

    return False
