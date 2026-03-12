"""
Filesystem-based project detection.

Detects: language, framework, app directory, package manager,
build/test/dev commands, key config files, directory structure.

No AI required — pure filesystem analysis.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ProjectDetection:
    name: str = ""
    language: str = "unknown"
    framework: str | None = None
    app_dir: str = "."
    package_manager: str | None = None
    has_typescript: bool = False
    has_tests: bool = False
    test_command: str | None = None
    build_command: str | None = None
    lint_command: str | None = None
    dev_command: str | None = None
    key_files: list[str] = field(default_factory=list)
    src_dirs: list[str] = field(default_factory=list)
    structure_summary: str = ""


def detect(project_root: Path) -> ProjectDetection:
    """Detect project characteristics from filesystem."""
    result = ProjectDetection()
    result.name = project_root.name

    # --- Node.js ---
    pkg_json = project_root / "package.json"
    if pkg_json.exists():
        _detect_node(project_root, pkg_json, result)

    # --- Monorepo: check subdirs for package.json ---
    if not pkg_json.exists():
        for subdir in project_root.iterdir():
            if subdir.is_dir() and (subdir / "package.json").exists():
                _detect_node(project_root, subdir / "package.json", result)
                result.app_dir = str(subdir.relative_to(project_root))
                break

    # --- Python ---
    pyproject = project_root / "pyproject.toml"
    setup_py = project_root / "setup.py"
    requirements = project_root / "requirements.txt"
    if pyproject.exists() or setup_py.exists() or requirements.exists():
        _detect_python(project_root, result)

    # --- Rust ---
    cargo = project_root / "Cargo.toml"
    if cargo.exists():
        _detect_rust(project_root, cargo, result)

    # --- Go ---
    gomod = project_root / "go.mod"
    if gomod.exists():
        _detect_go(project_root, gomod, result)

    # --- Common key files ---
    for f in ["README.md", "CLAUDE.md", ".env.example", "docker-compose.yml",
              "Makefile", "Dockerfile", "tsconfig.json", ".eslintrc.js",
              ".eslintrc.json", "tailwind.config.ts", "tailwind.config.js",
              "next.config.js", "next.config.mjs", "next.config.ts",
              "prisma/schema.prisma", "drizzle.config.ts"]:
        if (project_root / f).exists():
            result.key_files.append(f)

    # --- Source directories ---
    search_root = project_root / result.app_dir if result.app_dir != "." else project_root
    for d in ["src", "app", "lib", "components", "pages", "api", "routes",
              "models", "services", "utils", "hooks", "types"]:
        full_path = search_root / d
        if full_path.is_dir():
            if result.app_dir != ".":
                result.src_dirs.append(f"{result.app_dir}/{d}")
            else:
                result.src_dirs.append(d)

    # --- Structure summary ---
    result.structure_summary = _summarize_structure(project_root)

    return result


def _detect_node(root: Path, pkg_path: Path, result: ProjectDetection):
    """Detect Node.js project characteristics."""
    try:
        data = json.loads(pkg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    pkg_root = pkg_path.parent
    result.name = data.get("name", root.name)
    result.language = "typescript" if (pkg_root / "tsconfig.json").exists() else "javascript"
    result.has_typescript = result.language == "typescript"
    result.package_manager = "npm"

    if (root / "yarn.lock").exists():
        result.package_manager = "yarn"
    elif (root / "pnpm-lock.yaml").exists():
        result.package_manager = "pnpm"
    elif (root / "bun.lockb").exists():
        result.package_manager = "bun"

    scripts = data.get("scripts", {})
    result.dev_command = _pick_script(scripts, ["dev", "start:dev", "serve", "start"])
    result.build_command = _pick_script(scripts, ["build", "compile"])
    result.test_command = _pick_script(scripts, ["test", "test:unit", "test:e2e"])
    result.lint_command = _pick_script(scripts, ["lint", "lint:fix"])
    result.has_tests = result.test_command is not None

    deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}

    # Framework detection
    if "next" in deps:
        result.framework = "nextjs"
    elif "nuxt" in deps:
        result.framework = "nuxt"
    elif "@remix-run/react" in deps:
        result.framework = "remix"
    elif "express" in deps:
        result.framework = "express"
    elif "fastify" in deps:
        result.framework = "fastify"
    elif "@angular/core" in deps:
        result.framework = "angular"
    elif "vue" in deps:
        result.framework = "vue"
    elif "svelte" in deps:
        result.framework = "svelte"
    elif "react" in deps:
        result.framework = "react"


def _detect_python(root: Path, result: ProjectDetection):
    """Detect Python project characteristics."""
    if result.language == "unknown":
        result.language = "python"

    # Check for frameworks
    requirements_files = list(root.glob("requirements*.txt"))
    all_deps = ""
    for rf in requirements_files:
        all_deps += rf.read_text(encoding="utf-8").lower()

    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        all_deps += pyproject.read_text(encoding="utf-8").lower()

    if "django" in all_deps:
        result.framework = "django"
    elif "fastapi" in all_deps:
        result.framework = "fastapi"
    elif "flask" in all_deps:
        result.framework = "flask"

    result.package_manager = "pip"
    if (root / "poetry.lock").exists():
        result.package_manager = "poetry"
    elif (root / "Pipfile").exists():
        result.package_manager = "pipenv"
    elif (root / "uv.lock").exists():
        result.package_manager = "uv"

    if (root / "pytest.ini").exists() or (root / "tests").is_dir():
        result.has_tests = True
        result.test_command = "pytest"


def _detect_rust(root: Path, cargo_path: Path, result: ProjectDetection):
    """Detect Rust project characteristics."""
    if result.language == "unknown":
        result.language = "rust"
    result.package_manager = "cargo"
    result.build_command = "cargo build"
    result.test_command = "cargo test"
    result.has_tests = True

    content = cargo_path.read_text(encoding="utf-8").lower()
    if "actix" in content:
        result.framework = "actix"
    elif "axum" in content:
        result.framework = "axum"
    elif "rocket" in content:
        result.framework = "rocket"


def _detect_go(root: Path, gomod_path: Path, result: ProjectDetection):
    """Detect Go project characteristics."""
    if result.language == "unknown":
        result.language = "go"
    result.package_manager = "go"
    result.build_command = "go build ./..."
    result.test_command = "go test ./..."
    result.has_tests = True

    content = gomod_path.read_text(encoding="utf-8").lower()
    if "gin-gonic" in content:
        result.framework = "gin"
    elif "fiber" in content:
        result.framework = "fiber"
    elif "echo" in content:
        result.framework = "echo"


def _pick_script(scripts: dict, candidates: list[str]) -> str | None:
    """Pick the first matching script from candidates."""
    for c in candidates:
        if c in scripts:
            return c
    return None


def _summarize_structure(root: Path, max_depth: int = 2) -> str:
    """Generate a tree-like summary of the project structure."""
    lines = []

    def _walk(path: Path, prefix: str, depth: int):
        if depth > max_depth:
            return
        try:
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
        except PermissionError:
            return

        # Filter out common non-essential dirs
        skip = {".git", "node_modules", ".next", "__pycache__", ".venv",
                "venv", "dist", "build", ".cache", "target", ".DS_Store",
                "coverage", ".turbo", ".vercel"}

        dirs = [e for e in entries if e.is_dir() and e.name not in skip]
        files = [e for e in entries if e.is_file() and not e.name.startswith(".")]

        # Show key files at this level
        key_extensions = {".json", ".toml", ".yaml", ".yml", ".ts", ".js",
                          ".md", ".prisma", ".py", ".rs", ".go"}
        for f in files[:10]:
            if f.suffix in key_extensions or f.name in {"Makefile", "Dockerfile"}:
                lines.append(f"{prefix}{f.name}")

        for d in dirs[:15]:
            lines.append(f"{prefix}{d.name}/")
            _walk(d, prefix + "  ", depth + 1)

    _walk(root, "", 0)
    return "\n".join(lines[:60])  # cap at 60 lines
