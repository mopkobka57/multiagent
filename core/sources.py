"""
Backlog source management — CRUD backed by output/sources.json.

Supports multiple backlog sources: the default multiagent_specs/ plus
user-added folders, each containing its own backlog.md and spec files.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path

from ..config import DATA_DIR, BACKLOG_FILE, OUTPUT_DIR


SOURCES_FILE = OUTPUT_DIR / "sources.json"


@dataclass
class BacklogSource:
    id: str              # "default" or kebab-case slug from folder name
    name: str            # Display name
    path: str            # Absolute path to folder
    backlog_file: str    # Absolute path to backlog.md inside folder
    is_default: bool     # True for multiagent_specs — cannot be deleted
    task_prefix: str = ""  # Prefix for generated task IDs (e.g. "MVP" → MVP_BF1)

    @property
    def registry_file(self) -> Path:
        """Path to this source's registry.md (may not exist)."""
        return Path(self.path) / "registry.md"


_DEFAULT_SOURCE = BacklogSource(
    id="default",
    name="Main Backlog",
    path=str(DATA_DIR),
    backlog_file=str(BACKLOG_FILE),
    is_default=True,
    task_prefix="MAIN",
)


def _slugify(name: str) -> str:
    """Convert folder name to kebab-case slug."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-")
    return slug or "source"


def _save_sources(sources: list[BacklogSource]) -> None:
    """Write non-default sources to output/sources.json."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    custom = [asdict(s) for s in sources if not s.is_default]
    SOURCES_FILE.write_text(json.dumps(custom, indent=2), encoding="utf-8")


def load_sources() -> list[BacklogSource]:
    """Load all sources: default first, then custom from sources.json."""
    sources = [_DEFAULT_SOURCE]

    if SOURCES_FILE.exists():
        try:
            raw = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
            for item in raw:
                sources.append(BacklogSource(**item))
        except (json.JSONDecodeError, TypeError, KeyError):
            pass

    return sources


def get_source_by_id(source_id: str) -> BacklogSource | None:
    """Find a source by its ID."""
    for s in load_sources():
        if s.id == source_id:
            return s
    return None


def add_source(folder_path: str, task_prefix: str = "") -> BacklogSource:
    """
    Add a new backlog source from a folder path.

    Validates the folder exists and contains backlog.md.
    Returns the new BacklogSource.
    Raises ValueError on validation errors.
    """
    folder = Path(folder_path).resolve()
    if not folder.exists():
        raise ValueError(f"Folder not found: {folder}")
    if not folder.is_dir():
        raise ValueError(f"Not a directory: {folder}")

    backlog = folder / "backlog.md"
    if not backlog.exists():
        raise ValueError(f"backlog.md not found in {folder}")

    sources = load_sources()

    # Check for duplicates
    folder_str = str(folder)
    for s in sources:
        if s.path == folder_str:
            raise ValueError(f"Source already exists: {s.name}")

    # Generate unique ID
    base_slug = _slugify(folder.name)
    slug = base_slug
    existing_ids = {s.id for s in sources}
    counter = 2
    while slug in existing_ids:
        slug = f"{base_slug}-{counter}"
        counter += 1

    source = BacklogSource(
        id=slug,
        name=folder.name,
        path=folder_str,
        backlog_file=str(backlog),
        is_default=False,
        task_prefix=task_prefix,
    )

    sources.append(source)
    _save_sources(sources)
    return source


def remove_source(source_id: str) -> bool:
    """Remove a non-default source. Returns True if removed."""
    if source_id == "default":
        return False

    sources = load_sources()
    original_len = len(sources)
    sources = [s for s in sources if s.id != source_id or s.is_default]

    if len(sources) == original_len:
        return False

    _save_sources(sources)
    return True
