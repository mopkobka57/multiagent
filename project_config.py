"""
Project configuration loader.

Reads multiagent.toml from PROJECT_ROOT and populates the config module.
Falls back to auto-detection if toml is missing.
"""
import tomllib
from pathlib import Path


def load():
    """Load project config from multiagent.toml."""
    from . import config

    toml_path = config.PROJECT_ROOT / "multiagent.toml"

    if toml_path.exists():
        _load_from_toml(toml_path, config)
    else:
        _try_auto_detect(config)


def _load_from_toml(toml_path: Path, config):
    with open(toml_path, "rb") as f:
        data = tomllib.load(f)

    # --- Project ---
    proj = data.get("project", {})
    config.PROJECT_NAME = proj.get("name", config.PROJECT_ROOT.name)
    config.PROJECT_DESCRIPTION = proj.get("description", "")
    config.APP_DIR = config.PROJECT_ROOT / proj.get("app_dir", ".")
    config.DOCS_DIR = config.PROJECT_ROOT / proj.get("docs_dir", "docs")

    # --- Data ---
    d = data.get("data", {})
    config.DATA_DIR = config.PROJECT_ROOT / d.get("dir", "agents_data")
    config.SPECS_DIR = config.PROJECT_ROOT / d.get("specs_dir", str(config.DATA_DIR.relative_to(config.PROJECT_ROOT) / "specs"))
    config.BACKLOG_FILE = config.DATA_DIR / "backlog.md"
    config.REGISTRY_FILE = config.DATA_DIR / "registry.md"
    config.INSIGHTS_FILE = config.DATA_DIR / "agent_insights.md"
    config.PRODUCT_CONTEXT_FILE = config.DATA_DIR / "product_context.md"

    config.CONTEXT_FILES = [
        config.PROJECT_ROOT / p for p in d.get("context_files", [
            str(config.PRODUCT_CONTEXT_FILE.relative_to(config.PROJECT_ROOT)),
            str(config.INSIGHTS_FILE.relative_to(config.PROJECT_ROOT)),
        ])
    ]

    config.FOUNDATIONAL_SPECS = [
        config.PROJECT_ROOT / p for p in d.get("foundational_specs", [])
    ]

    # Spec type dirs
    type_dirs = d.get("spec_types", {})
    if type_dirs:
        config.SPEC_TYPE_DIRS = {
            k: config.PROJECT_ROOT / v for k, v in type_dirs.items()
        }
    else:
        config.SPEC_TYPE_DIRS = {
            "audit": config.SPECS_DIR / "audit",
            "feature": config.SPECS_DIR / "features",
            "tech-debt": config.SPECS_DIR / "tech_debt",
            "refactor": config.SPECS_DIR / "refactor",
            "bugfix": config.SPECS_DIR / "bugfix",
        }

    # --- Quality gates ---
    qg = data.get("quality_gates", {})
    config.QUALITY_GATES = {}
    for name, cmd in qg.items():
        config.QUALITY_GATES[name] = cmd.replace("{app_dir}", str(config.APP_DIR))

    # --- Visual testing ---
    vt = data.get("visual_testing", {})
    config.DEV_COMMAND = vt.get("dev_command", "npm run dev -- -p {port}")
    config.DEV_SERVER_PORT = vt.get("dev_port", 3000)
    config.DEV_SERVER_URL = f"http://localhost:{config.DEV_SERVER_PORT}"
    config.VISUAL_TEST_PAGES = vt.get("pages", ["/"])

    # --- Git ---
    g = data.get("git", {})
    config.MAIN_BRANCH = g.get("main_branch", "main")
    config.DEV_BRANCH = g.get("dev_branch", "auto-dev")
    config.BRANCH_PREFIX = g.get("branch_prefix", "auto/")

    # --- Models ---
    m = data.get("models", {})
    config.ORCHESTRATOR_MODEL = m.get("orchestrator", "opus")
    config.SUBAGENT_MODEL = m.get("subagent", "sonnet")

    # --- Budgets ---
    b = data.get("budgets", {})
    config.MAX_TOKENS_PER_TASK = b.get("max_tokens_per_task", 500_000)
    config.MAX_TURNS_PER_SUBAGENT = b.get("max_turns_per_subagent", 30)
    config.MAX_FIX_RETRIES = b.get("max_fix_retries", 3)

    # --- Autonomy ---
    a = data.get("autonomy", {})
    config.AUTONOMY_MODE = a.get("mode", "batch")
    config.HUMAN_CHECKPOINTS = a.get("human_checkpoints", ["pr_review"])

    # --- Protected paths ---
    pp = data.get("protected_paths", {})
    config.PROTECTED_PATHS = pp.get("paths", [
        "multiagent/", "CLAUDE.md", ".claude/", ".env*", ".gitignore",
    ])
    config.PROTECTED_EXCEPTIONS = pp.get("exceptions", ["multiagent/output/"])

    # --- Backlog ---
    bl = data.get("backlog", {})
    config.TYPE_MAP = bl.get("type_map", {
        "feature": "feature", "tech-debt": "tech-debt",
        "refactor": "refactor", "audit": "audit",
    })

    # --- Writable paths ---
    wp = data.get("writable_paths", {})
    config.WRITABLE_PATHS = wp.get("paths", [
        str(config.APP_DIR.relative_to(config.PROJECT_ROOT)) + "/",
        "docs/",
    ])


def _try_auto_detect(config):
    """Fallback: detect agents_data/ next to multiagent/ for legacy layout."""
    legacy_data = config.PROJECT_ROOT / "agents_data"
    if legacy_data.exists():
        config.PROJECT_NAME = config.PROJECT_ROOT.name
        config.DATA_DIR = legacy_data
        config.SPECS_DIR = legacy_data / "specs"
        config.BACKLOG_FILE = legacy_data / "backlog.md"
        config.REGISTRY_FILE = legacy_data / "registry.md"
        config.INSIGHTS_FILE = legacy_data / "agent_insights.md"
        config.PRODUCT_CONTEXT_FILE = legacy_data / "product_context.md"
        config.CONTEXT_FILES = [config.PRODUCT_CONTEXT_FILE, config.INSIGHTS_FILE]
        config.FOUNDATIONAL_SPECS = [
            config.SPECS_DIR / "_architecture-blocks.md",
            config.SPECS_DIR / "_project-conventions.md",
        ]
        config.SPEC_TYPE_DIRS = {
            "audit": config.SPECS_DIR / "audit",
            "feature": config.SPECS_DIR / "features",
            "tech-debt": config.SPECS_DIR / "tech_debt",
            "refactor": config.SPECS_DIR / "refactor",
            "bugfix": config.SPECS_DIR / "bugfix",
        }
    else:
        config.PROJECT_NAME = config.PROJECT_ROOT.name
