"""
Shared configuration loader for daily-briefs scripts.

Loads config from DAILY_BRIEFS_CONFIG env var, or falls back to
the default config.yaml in the project root.

All paths are resolved relative to the configured root directory.
"""

import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import yaml


def validate_date(date_str: str) -> str:
    """Validate date format YYYY-MM-DD. Returns the date or exits with error."""
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return date_str
    except ValueError:
        print(f"Error: Invalid date format '{date_str}'. Expected YYYY-MM-DD.")
        sys.exit(1)


class ConfigError(Exception):
    """Raised when config validation fails."""
    pass


def validate_config(config: dict, config_path: Path) -> None:
    """
    Validate config has required sections and fields.
    Raises ConfigError with helpful message if validation fails.
    """
    errors = []
    
    # Required: paths section
    if "paths" not in config:
        errors.append("Missing 'paths' section")
    # paths.root is optional; load_config will default to project root
    
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

    if not isinstance(config, dict):
        raise ConfigError(f"Config is empty or invalid YAML: {config_path}")
    
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
        "logs": root / paths.get("logs", "data/logs/"),
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
    # Enable WAL mode for better concurrency (readers + one writer)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# -----------------------------------------------------------------------------
# HTTP Status Code Constants
# -----------------------------------------------------------------------------
HTTP_NOT_FOUND = 404
HTTP_RATE_LIMITED = 429


# -----------------------------------------------------------------------------
# Config Accessors with Defaults
# -----------------------------------------------------------------------------
# These provide safe access to config values with sensible defaults.

def get_filtering_config(config: dict = None) -> dict:
    """Get filtering settings with defaults."""
    if config is None:
        config = CONFIG
    filtering = config.get("filtering", {})
    return {
        "tier1_min_score": filtering.get("tier1_min_score", 0.1),
        "tier2_min_score": filtering.get("tier2_min_score", 0.5),
        "tier3_min_score": filtering.get("tier3_min_score", 0.8),
        "citation_weight_s2": filtering.get("citation_weight_s2", 0.6),
        "citation_weight_oa": filtering.get("citation_weight_oa", 0.4),
        "keyword_weight": filtering.get("keyword_weight", 0.6),
        "popularity_weight": filtering.get("popularity_weight", 0.4),
        "near_miss_multiplier": filtering.get("near_miss_multiplier", 0.8),
        "near_miss_threshold": filtering.get("near_miss_threshold", 0.05),
        "near_miss_count": filtering.get("near_miss_count", 3),
        "serendipity_count": filtering.get("serendipity_count", 5),
        "random_negative_count": filtering.get("random_negative_count", 2),
    }


def get_embeddings_config(config: dict = None) -> dict:
    """Get embeddings/UMAP settings with defaults."""
    if config is None:
        config = CONFIG
    embeddings = config.get("embeddings", {})
    return {
        "model_name": embeddings.get("model_name", "sentence-transformers/allenai-specter"),
        "dimension": embeddings.get("dimension", 768),
        "umap_n_neighbors": embeddings.get("umap_n_neighbors", 15),
        "umap_min_dist": embeddings.get("umap_min_dist", 0.1),
        "umap_random_state": embeddings.get("umap_random_state", 42),
    }


def get_search_config(config: dict = None) -> dict:
    """Get search settings with defaults."""
    if config is None:
        config = CONFIG
    search = config.get("search", {})
    return {
        "default_results": search.get("default_results", 10),
    }


def get_api_config(config: dict = None) -> dict:
    """Get API settings with defaults."""
    if config is None:
        config = CONFIG
    apis = config.get("apis", {})
    return {
        "arxiv_rate_limit": apis.get("arxiv", {}).get("rate_limit_seconds", 1.0),
        "arxiv_retry_base_seconds": apis.get("arxiv", {}).get("retry_base_seconds", 5.0),
        "s2_enabled": apis.get("semantic_scholar", {}).get("enabled", True),
        "s2_delay": apis.get("semantic_scholar", {}).get("delay_seconds", 0.15),
        "oa_enabled": apis.get("openalex", {}).get("enabled", True),
        "oa_delay": apis.get("openalex", {}).get("delay_seconds", 0.1),
    }


def get_report_config(config: dict = None) -> dict:
    """Get report settings with defaults."""
    if config is None:
        config = CONFIG
    report = config.get("report", {})
    return {
        "max_papers_per_stream": report.get("max_papers_per_stream", 10),
        "max_authors": report.get("max_authors", 3),
        "include_abstract_preview": report.get("include_abstract_preview", True),
        "abstract_preview_length": report.get("abstract_preview_length", 300),
    }


# -----------------------------------------------------------------------------
# Convenience: load config on import for simple access
# -----------------------------------------------------------------------------
# Scripts can use: from config import CONFIG, DB_PATH, get_db_connection
CONFIG = load_config()
PROJECT_ROOT = CONFIG["_resolved"]["root"]
DB_PATH = CONFIG["_resolved"]["db"]
TEXT_DIR = CONFIG["_resolved"]["text"]
EMBEDDINGS_DIR = CONFIG["_resolved"]["embeddings"]
FILTERED_DIR = CONFIG["_resolved"]["filtered"]
REPORTS_DIR = CONFIG["_resolved"]["reports"]
LOGS_DIR = CONFIG["_resolved"]["logs"]

# Pre-load commonly used configs
FILTERING = get_filtering_config(CONFIG)
EMBEDDINGS = get_embeddings_config(CONFIG)
SEARCH = get_search_config(CONFIG)
APIS = get_api_config(CONFIG)
REPORT = get_report_config(CONFIG)
