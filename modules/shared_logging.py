#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shared Logging Utility for Pipeline Scripts
Author: Florian Dheer

Provides consistent logging across all pipeline modules:
- All logs go to: ~/AppData/Local/PipelineManager/logs/
- Per-module daily files: modulename_2025-12-28.log
- Deferred file creation: log files only created when something is logged
- Console output included by default
"""

import os
import sys
import logging
import datetime

from shared_appdata import get_appdata_path


# Default log directory
def _get_log_dir():
    """Get the appropriate log directory for the platform."""
    if sys.platform == "darwin":
        # Native macOS location for app log files (distinct from the
        # Application Support folder shared_appdata otherwise points at).
        return os.path.join(
            os.path.expanduser("~"), "Library", "Logs", "PipelineManager"
        )
    return str(get_appdata_path() / "logs")

LOG_DIR = _get_log_dir()

# Standard log format
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"


class DeferredFileHandler(logging.Handler):
    """
    A logging handler that defers file creation until the first log message.
    Prevents empty log files from being created when no logging occurs.
    """

    def __init__(self, log_dir: str, module_name: str, encoding: str = 'utf-8'):
        super().__init__()
        self.log_dir = log_dir
        self.module_name = module_name
        self.encoding = encoding
        self._file_handler = None
        self._log_file_path = None

    def _get_log_filename(self) -> str:
        """Get the log filename for today (allows daily rotation)."""
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        return f"{self.module_name}_{date_str}.log"

    def _create_file_handler(self):
        """Create the actual file handler on first use."""
        if self._file_handler is None:
            os.makedirs(self.log_dir, exist_ok=True)
            self._log_file_path = os.path.join(self.log_dir, self._get_log_filename())
            self._file_handler = logging.FileHandler(
                self._log_file_path,
                mode='a',  # Append mode for daily files
                encoding=self.encoding
            )
            self._file_handler.setFormatter(self.formatter)
            self._file_handler.setLevel(self.level)

    def emit(self, record):
        """Emit a record. Creates the file handler on first call."""
        self._create_file_handler()
        self._file_handler.emit(record)

    def close(self):
        """Close the handler and underlying file handler if created."""
        if self._file_handler is not None:
            self._file_handler.close()
        super().close()

    def setFormatter(self, fmt):
        """Set formatter for both this handler and underlying file handler."""
        super().setFormatter(fmt)
        if self._file_handler is not None:
            self._file_handler.setFormatter(fmt)

    def setLevel(self, level):
        """Set level for both this handler and underlying file handler."""
        super().setLevel(level)
        if self._file_handler is not None:
            self._file_handler.setLevel(level)


def get_logger(module_name: str) -> logging.Logger:
    """
    Get a logger instance for a module (without file handler yet).

    Use this at module level to get a logger reference.
    Call setup_logging() in main() to configure the file handler.

    Args:
        module_name: Name of the module (used in log filename)

    Returns:
        Logger instance
    """
    return logging.getLogger(module_name)


def setup_logging(
    module_name: str,
    level: int = logging.INFO,
    include_console: bool = True,
    log_dir: str = None,
    file_level: int = None,
    console_level: int = None
) -> logging.Logger:
    """
    Configure logging for a module. Call this in main().

    Creates a daily log file that's only written when something is logged.
    Log files are named: modulename_2025-12-28.log

    Args:
        module_name: Name of the module (used in log filename and logger name)
        level: Logging level for the logger itself (default: INFO)
        include_console: Whether to also log to console (default: True)
        log_dir: Custom log directory (default: ~/AppData/Local/PipelineManager/logs)
        file_level: Logging level for the file handler (default: same as level)
        console_level: Logging level for the console handler (default: same as level)

    Returns:
        Configured logger instance
    """
    if log_dir is None:
        log_dir = LOG_DIR
    if file_level is None:
        file_level = level
    if console_level is None:
        console_level = level

    logger = logging.getLogger(module_name)
    # Set logger to the lowest level so both handlers can filter independently
    logger.setLevel(min(level, file_level, console_level))

    # Clear any existing handlers to avoid duplicates
    logger.handlers = []

    # Create formatter
    formatter = logging.Formatter(LOG_FORMAT)

    # Add deferred file handler (creates file only when first log message is written)
    file_handler = DeferredFileHandler(log_dir, module_name)
    file_handler.setFormatter(formatter)
    file_handler.setLevel(file_level)
    logger.addHandler(file_handler)

    # Add console handler if requested
    if include_console:
        # On Windows, sys.stdout defaults to cp1252 which can't encode emoji
        # (e.g. 🚀, ⏹). Reconfigure to utf-8 with errors='replace' so log
        # messages with Unicode characters don't raise UnicodeEncodeError.
        if hasattr(sys.stdout, "reconfigure"):
            try:
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(console_level)
        logger.addHandler(console_handler)

    return logger
