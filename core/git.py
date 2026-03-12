"""Git operations for the multi-agent orchestrator."""

from __future__ import annotations

import subprocess

from ..config import PROJECT_ROOT, DEV_BRANCH, MAIN_BRANCH, BRANCH_PREFIX


def git_run(cmd: str) -> tuple[bool, str]:
    """Run a git command and return (success, output)."""
    result = subprocess.run(
        f"git {cmd}",
        shell=True,
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def ensure_dev_branch() -> bool:
    """Ensure auto-dev branch exists. Create from main if not. Returns True if ready."""
    ok, _ = git_run(f"rev-parse --verify {DEV_BRANCH}")
    if ok:
        return True

    print(f"Creating '{DEV_BRANCH}' branch from '{MAIN_BRANCH}'...")
    ok, output = git_run(f"branch {DEV_BRANCH} {MAIN_BRANCH}")
    if not ok:
        print(f"Failed to create {DEV_BRANCH}: {output}")
        return False
    return True


def create_feature_branch(task_id: str) -> tuple[bool, str]:
    """Create a feature branch from auto-dev. Returns (success, branch_name)."""
    branch = f"{BRANCH_PREFIX}{task_id.replace('_', '-')}"

    ok, output = git_run(f"checkout {DEV_BRANCH}")
    if not ok:
        return False, f"Failed to checkout {DEV_BRANCH}: {output}"

    git_run(f"pull origin {DEV_BRANCH}")  # Ignore failure (no remote is OK)

    ok, output = git_run(f"checkout -b {branch}")
    if not ok:
        ok, output = git_run(f"checkout {branch}")
        if not ok:
            return False, f"Failed to create/checkout {branch}: {output}"

    return True, branch


def merge_to_dev(branch: str) -> tuple[bool, str]:
    """Merge feature branch back into auto-dev."""
    ok, output = git_run(f"checkout {DEV_BRANCH}")
    if not ok:
        return False, f"Failed to checkout {DEV_BRANCH}: {output}"

    ok, output = git_run(f"merge --no-ff {branch}")
    if not ok:
        return False, f"Merge failed: {output}"

    return True, f"Merged {branch} → {DEV_BRANCH}"


def commit_work(task_id: str, branch: str, success: bool) -> tuple[bool, str]:
    """Commit any uncommitted changes on current branch (safety-net)."""
    ok, status = git_run("status --porcelain")
    if not status.strip():
        return True, "Nothing to commit"
    git_run("add -A")
    prefix = "feat" if success else "wip"
    msg = f"{prefix}({task_id}): {'completed' if success else 'partial work'}"
    return git_run(f'commit -m "{msg}"')


def has_uncommitted_changes() -> bool:
    """Check if there are uncommitted changes (staged or unstaged)."""
    ok, status = git_run("status --porcelain")
    return bool(status.strip())


def count_changed_files(branch: str) -> int:
    """Count files changed on branch vs auto-dev."""
    ok, output = git_run(f"diff --name-only {DEV_BRANCH}...{branch}")
    return len([f for f in output.split('\n') if f.strip()]) if ok else 0


def checkout_branch(branch: str) -> tuple[bool, str]:
    """Checkout a branch."""
    return git_run(f"checkout {branch}")
