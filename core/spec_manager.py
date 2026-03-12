"""
Spec management — delete spec files and backlog entries.

Used by the server to remove tasks entirely (file + backlog row).
"""

from __future__ import annotations

import re
from pathlib import Path

from ..config import SPECS_DIR, SPEC_TYPE_DIRS
from .prompt_builder import find_task_spec
from .sources import get_source_by_id


class SpecDeleteError(Exception):
    """Raised when spec deletion fails validation."""
    pass


def _find_all_spec_versions(
    task_id: str,
    task_source: str | None = None,
    extra_search_dirs: list[Path] | None = None,
) -> list[Path]:
    """
    Find ALL versions of a task spec (v1, v2, etc.).
    Returns list of Paths to all matching spec files.
    """
    prefix = task_id.replace("_", "-")

    def _scan_dir(directory: Path) -> list[Path]:
        hits = []
        if not directory.exists():
            return hits
        for f in directory.iterdir():
            if f.name.startswith("_") or f.is_dir():
                continue
            name = f.stem
            # Match: "FE5-something" or "FE5-something.v2"
            if name.startswith(prefix + "-") or name == prefix:
                hits.append(f)
        return hits

    def _scan_recursive(directory: Path) -> list[Path]:
        hits = _scan_dir(directory)
        if directory.exists():
            for child in directory.iterdir():
                if child.is_dir() and not child.name.startswith("."):
                    hits.extend(_scan_recursive(child))
        return hits

    candidates: list[Path] = []

    if SPECS_DIR.exists():
        if task_source and task_source in SPEC_TYPE_DIRS:
            candidates = _scan_dir(SPEC_TYPE_DIRS[task_source])
        if not candidates:
            candidates = _scan_dir(SPECS_DIR)
            for subdir in SPEC_TYPE_DIRS.values():
                candidates.extend(_scan_dir(subdir))

    if extra_search_dirs:
        for extra_dir in extra_search_dirs:
            candidates.extend(_scan_recursive(extra_dir))

    return candidates


def _validate_path_inside(file_path: Path, allowed_root: Path) -> bool:
    """Check that file_path resolves inside allowed_root."""
    try:
        file_path.resolve().relative_to(allowed_root.resolve())
        return True
    except ValueError:
        return False


def delete_task_spec(
    task_id: str,
    source_id: str = "default",
) -> list[str]:
    """
    Delete all spec file versions for a task.

    Returns list of deleted file paths.
    Raises SpecDeleteError on validation failures.
    """
    extra_dirs = None
    allowed_roots = [SPECS_DIR]

    if source_id != "default":
        src = get_source_by_id(source_id)
        if src:
            src_path = Path(src.path)
            extra_dirs = [src_path]
            allowed_roots.append(src_path)

    all_versions = _find_all_spec_versions(
        task_id, extra_search_dirs=extra_dirs,
    )

    deleted: list[str] = []
    for spec_path in all_versions:
        # Security: validate path resolves inside an allowed root
        is_safe = any(
            _validate_path_inside(spec_path, root) for root in allowed_roots
        )
        if not is_safe:
            raise SpecDeleteError(
                f"Spec path {spec_path} is outside allowed directories"
            )
        if spec_path.exists():
            spec_path.unlink()
            deleted.append(str(spec_path))

    return deleted


def remove_backlog_entry(task_id: str, source_id: str = "default") -> bool:
    """
    Remove a task row from the correct backlog.md.

    Matches rows like: | FE5 | ... |
    Returns True if a row was removed.
    """
    if source_id != "default":
        src = get_source_by_id(source_id)
        if not src:
            return False
        backlog_path = Path(src.backlog_file)
    else:
        from ..config import BACKLOG_FILE
        backlog_path = BACKLOG_FILE

    if not backlog_path.exists():
        return False

    content = backlog_path.read_text(encoding="utf-8")
    # Match: | TASK_ID | ... | (entire row)
    pattern = re.compile(
        r"^\|\s*" + re.escape(task_id) + r"\s*\|.*\|[ \t]*$",
        re.MULTILINE,
    )

    new_content, count = pattern.subn("", content)
    if count == 0:
        return False

    # Clean up double blank lines left by removal
    new_content = re.sub(r"\n{3,}", "\n\n", new_content)
    backlog_path.write_text(new_content, encoding="utf-8")
    return True
