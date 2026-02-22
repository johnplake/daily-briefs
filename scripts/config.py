"""
Shared configuration loader for daily-briefs scripts.

Loads config from DAILY_BRIEFS_CONFIG env var, or falls back to
the default config.yaml in the project root.

All paths are resolved relative to the configured root directory.
"""

import os
import sqlite3
from pathlib import Path

import yaml


class ConfigError(Exception):
    """Raised when config validation fails."""
    pass


def validate_config(config: dict, config_path: Path) -> None:
    """
    Validate config has required sections and fields.
    Raises ConfigError with helpful message if validation fails.
    """
    errors = []
    
    # Required: paths.root (or we can't find data)
    if "paths" not in config:
        errors.append("Missing 'paths' section")
    elif "root" not in config["paths"]:
        errors.append("Missing 'paths.root' - must specify data directory")
    
    # Required: categories with at least one tier
    if "categories" not in config:
        errors.append("Missing 'categories' section")
    else:
        cats = config["categories"]
        has_categories = any(
            cats.get(tier) for tier in ["tier1", "tier2", "tier3"]
        )
        if not has_categories:
            errors.append("'categories' must have at least one of: tier1, tier2, tier3")
    
    # Optional but warn: interests
    if "interests" not in config:
        # Just a warning, not an error - filtering will work without it
        pass
    
    # Optional with defaults: filtering, report, dashboard
    # These have sensible defaults in the scripts, so not required
    
    if errors:
        error_msg = f"Config validation failed for {config_path}:\n"
        error_msg += "\n".join(f"  - {e}" for e in errors)
        raise ConfigError(error_msg)


def load_config() -> dict:
    """
    Load configuration from YAML file.
    
    Checks DAILY_BRIEFS_CONFIG env var first, falls back to default location.
    Returns parsed config dict with resolved paths.
    """
    # Check env var first
    config_path_str = os.environ.get("DAILY_BRIEFS_CONFIG")
    
    if config_path_str:
        config_path = Path(config_path_str)
    else:
        # Fall back to default: config.yaml in project root (parent of scripts/)
        config_path = Path(__file__).parent.parent / "config.yaml"
    
    if not config_path.exists():
        raise RuntimeError(f"Config file not found: {config_path}")
    
    with open(config_path) as f:
        config = yaml.safe_load(f)
    
    # Validate required sections
    validate_config(config, config_path)
    
    # Resolve paths section
    paths = config.get("paths", {})
    
    # Get root - default to project root if not specified
    root_path = paths.get("root")
    if root_path:
        root = Path(root_path)
    else:
        root = Path(__file__).parent.parent
    
    # Resolve all paths relative to root
    config["_resolved"] = {
        "root": root,
        "db": root / paths.get("db", "data/papers.db"),
        "text": root / paths.get("text", "data/text/"),
        "embeddings": root / paths.get("embeddings", "data/embeddings/"),
        "filtered": root / paths.get("filtered", "data/filtered/"),
        "reports": root / paths.get("reports", "reports/"),
        "config_path": config_path,
    }
    
    return config


def get_db_connection(config: dict = None) -> sqlite3.Connection:
    """
    Get a database connection using the configured path.
    
    Args:
        config: Config dict (will load if not provided)
        
    Returns:
        sqlite3.Connection to the papers database
    """
    if config is None:
        config = load_config()
    
    db_path = config["_resolved"]["db"]
    
    if not db_path.exists():
        raise RuntimeError(
            f"Database not found at {db_path}. Run init_db.py first."
        )
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# Convenience: load config on import for simple access
# Scripts can use: from config import CONFIG, DB_PATH, get_db_connection
CONFIG = load_config()
PROJECT_ROOT = CONFIG["_resolved"]["root"]
DB_PATH = CONFIG["_resolved"]["db"]
TEXT_DIR = CONFIG["_resolved"]["text"]
EMBEDDINGS_DIR = CONFIG["_resolved"]["embeddings"]
FILTERED_DIR = CONFIG["_resolved"]["filtered"]
REPORTS_DIR = CONFIG["_resolved"]["reports"]
