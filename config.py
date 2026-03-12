"""
Multi-Agent System — Configuration

All project-specific values are loaded from multiagent.toml
by project_config.py at import time.

Edit multiagent.toml in your project root to configure.
"""
from pathlib import Path

# --- Core paths (auto-detected, never change) ---
MULTIAGENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MULTIAGENT_DIR.parent

# --- Package directories (internal, never change) ---
PROMPTS_DIR = MULTIAGENT_DIR / "prompts"
OUTPUT_DIR = MULTIAGENT_DIR / "output"
TASK_LOGS_DIR = OUTPUT_DIR / "logs"
STATE_FILE = OUTPUT_DIR / "state.json"
SCREENSHOTS_DIR = OUTPUT_DIR / "screenshots"
AUDIT_REPORTS_DIR = OUTPUT_DIR / "logs" / "audits"
ARCHIVE_FILE = OUTPUT_DIR / "archive.json"

# --- Project-specific (populated by project_config.py) ---
PROJECT_NAME: str = ""
PROJECT_DESCRIPTION: str = ""
APP_DIR: Path = PROJECT_ROOT
DOCS_DIR: Path = PROJECT_ROOT / "docs"
DATA_DIR: Path = PROJECT_ROOT / "agents_data"
SPECS_DIR: Path = DATA_DIR / "specs"
BACKLOG_FILE: Path = DATA_DIR / "backlog.md"
REGISTRY_FILE: Path = DATA_DIR / "registry.md"
INSIGHTS_FILE: Path = DATA_DIR / "agent_insights.md"
PRODUCT_CONTEXT_FILE: Path = DATA_DIR / "product_context.md"

CONTEXT_FILES: list[Path] = []
FOUNDATIONAL_SPECS: list[Path] = []

SPEC_TYPE_DIRS: dict[str, Path] = {}

# --- Quality gates ---
QUALITY_GATES: dict[str, str] = {}

# --- Dev server ---
DEV_COMMAND: str = "npm run dev -- -p {port}"
DEV_SERVER_PORT: int = 3000
DEV_SERVER_URL: str = "http://localhost:3000"
VISUAL_TEST_PAGES: list[str] = ["/"]

# --- Models ---
ORCHESTRATOR_MODEL: str = "opus"
SUBAGENT_MODEL: str = "sonnet"

# --- Budgets ---
MAX_TOKENS_PER_TASK: int = 500_000
MAX_TURNS_PER_SUBAGENT: int = 30
MAX_FIX_RETRIES: int = 3
AUDIT_COOLDOWN_DAYS: int = 14

# --- Retry on rate limits ---
RATE_LIMIT_MAX_RETRIES: int = 50
RATE_LIMIT_BASE_DELAY: int = 30
RATE_LIMIT_MAX_DELAY: int = 300
RATE_LIMIT_BACKOFF_FACTOR: int = 2

# --- Server-level rate limit restart ---
SERVER_RATE_LIMIT_DELAY: int = 1800
SERVER_RATE_LIMIT_MAX_RETRIES: int = 14

# --- Git ---
MAIN_BRANCH: str = "main"
DEV_BRANCH: str = "auto-dev"
BRANCH_PREFIX: str = "auto/"

# --- Autonomy ---
AUTONOMY_MODE: str = "batch"
HUMAN_CHECKPOINTS: list[str] = ["pr_review"]

# --- Guardrails ---
PROTECTED_PATHS: list[str] = [
    "multiagent/",
    "CLAUDE.md",
    ".claude/",
    ".env*",
    ".gitignore",
]
PROTECTED_EXCEPTIONS: list[str] = ["multiagent/output/"]

# --- Backlog ---
TYPE_MAP: dict[str, str] = {
    "feature": "feature",
    "tech-debt": "tech-debt",
    "refactor": "refactor",
    "audit": "audit",
}

# --- Writable paths (for agent prompts) ---
WRITABLE_PATHS: list[str] = []

# --- Load project config on import ---
from .project_config import load as _load_config  # noqa: E402
_load_config()
