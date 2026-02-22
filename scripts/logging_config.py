"""
Centralized logging configuration for daily-briefs.

Logs to both console and file (data/logs/daily-briefs.log).
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

from config import PROJECT_ROOT


def setup_logging(name: str = "daily-briefs", level: int = logging.INFO) -> logging.Logger:
    """
    Set up logging with console and file handlers.
    
    Args:
        name: Logger name (usually script name)
        level: Logging level (default INFO)
    
    Returns:
        Configured logger
    """
    # Create logs directory
    log_dir = PROJECT_ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Log file with date
    log_file = log_dir / f"daily-briefs-{datetime.now().strftime('%Y-%m')}.log"
    
    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Avoid duplicate handlers if called multiple times
    if logger.handlers:
        return logger
    
    # Format
    formatter = logging.Formatter(
        '%(asctime)s | %(name)s | %(levelname)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Console handler (INFO and above)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # File handler (DEBUG and above)
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    return logger
