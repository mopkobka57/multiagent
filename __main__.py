"""
CLI entry point: python -m multiagent

Usage:
    python -m multiagent init                # Initialize for current project
    python -m multiagent --next              # Run next priority task
    python -m multiagent --task FE1          # Run specific task by ID
    python -m multiagent --resume            # Resume interrupted task
    python -m multiagent --list              # List all tasks with status
    python -m multiagent --batch --phase 2   # Run all Phase 2 tasks
"""

from __future__ import annotations

import sys
import argparse
import asyncio


def main():
    # Handle 'init' as a subcommand (before importing config)
    if len(sys.argv) >= 2 and sys.argv[1] == "init":
        _handle_init()
        return

    # Import config AFTER init check (init creates the config file)
    from . import config
    from .core.orchestrator import (
        list_tasks,
        resume_task,
        run_batch,
        run_next_task,
        run_specific_task,
    )

    parser = argparse.ArgumentParser(
        description=f"{config.PROJECT_NAME or 'Multi-Agent'} Orchestrator",
        epilog=f"Git: features branch from {config.DEV_BRANCH}, human merges {config.DEV_BRANCH} → {config.MAIN_BRANCH}",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--next", action="store_true", help="Run next priority task")
    group.add_argument("--task", type=str, help="Run specific task by ID")
    group.add_argument("--resume", action="store_true", help="Resume interrupted task")
    group.add_argument("--list", action="store_true", help="List all tasks with status")
    group.add_argument("--batch", action="store_true", help="Run tasks in batch mode")

    parser.add_argument("--phase", type=str, help="Filter batch to specific roadmap phase")
    parser.add_argument("--source-id", type=str, default="default", help="Source ID for task spec lookup")
    parser.add_argument("--branch", type=str, help="Use existing branch instead of creating new")
    parser.add_argument("--no-branch-cleanup", action="store_true",
                        help="Don't checkout auto-dev after task completion")
    parser.add_argument(
        "--mode",
        choices=["supervised", "batch", "autonomous"],
        help="Override autonomy mode for this run",
    )

    args = parser.parse_args()

    if args.mode:
        config.AUTONOMY_MODE = args.mode

    if args.list:
        list_tasks()
    elif args.next:
        asyncio.run(run_next_task())
    elif args.task:
        asyncio.run(run_specific_task(
            args.task,
            source_id=args.source_id,
            branch_override=args.branch,
            skip_branch_cleanup=args.no_branch_cleanup,
        ))
    elif args.resume:
        asyncio.run(resume_task())
    elif args.batch:
        asyncio.run(run_batch(args.phase))


def _handle_init():
    """Handle the init subcommand."""
    parser = argparse.ArgumentParser(description="Initialize multiagent for this project")
    parser.add_argument("--data-dir", default="agents_data", help="Name for data directory")
    parser.add_argument("--non-interactive", action="store_true", help="Skip confirmations")
    parser.add_argument("--refresh", action="store_true", help="Re-run analysis only")

    # Remove 'init' from argv before parsing
    args = parser.parse_args(sys.argv[2:])

    from .core.init import run_init
    success = run_init(
        data_dir_name=args.data_dir,
        non_interactive=args.non_interactive,
        refresh=args.refresh,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
