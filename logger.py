"""
logger.py
---------
Application-wide logging: a file-based diagnostic logger (application.log),
a CSV send-log wrapper (delegates its actual file I/O to utils.py so the
read/write logic exists in exactly one place), an exception logger, and
colour-coded terminal display helpers.

Colour convention:
    Green  -> successful email
    Yellow -> skipped email
    Red    -> failed email
    Blue   -> general information
"""

from __future__ import annotations

import logging
import time
import traceback
from pathlib import Path
from typing import Dict, Optional, Set

from colorama import Fore, Style, init as colorama_init

import config
from utils import append_csv_log, create_csv_log, create_folder, read_sent_log

colorama_init(autoreset=True)

LOGGER_NAME = "email_automation"

# Shared terminal display widths — defined once here so every bordered
# box in the app (banners, dashboard, preview, summaries) stays visually
# consistent instead of scattering literal "=" * N magic numbers.
BANNER_WIDTH = 55
DASHBOARD_WIDTH = 42
STATUS_BLOCK_WIDTH = 37
PREVIEW_WIDTH = 36


# ---------------------------------------------------------------------------
# File logging (application.log) — diagnostics and full tracebacks.
# The colourful terminal output below is what the user actually watches.
# ---------------------------------------------------------------------------
def get_logger(name: str = LOGGER_NAME) -> logging.Logger:
    """
    Configure and return the application's file logger.

    Args:
        name: Logger name.

    Returns:
        A configured logging.Logger instance writing to logs/application.log.
    """
    create_folder(config.LOG_FOLDER)

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    file_handler = logging.FileHandler(config.APP_LOG, encoding="utf-8")
    file_formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)
    return logger


def log_event(logger: logging.Logger, event: str, detail: str = "") -> None:
    """
    Record a lifecycle event to application.log in a consistent format.
    Used for every notable milestone: application start/exit, SMTP
    connect/disconnect, PDF/resume loaded, email sent/failed/skipped,
    progress saved, daily limit reached, etc.

    Args:
        logger: The application file logger.
        event: Short event name, e.g. "SMTP Connected".
        detail: Optional additional detail to append.
    """
    if detail:
        logger.info("%s | %s", event, detail)
    else:
        logger.info("%s", event)


def log_exception(logger: logging.Logger, exc: BaseException) -> None:
    """
    Write a full traceback for an unexpected exception to application.log.

    Args:
        logger: The application file logger.
        exc: The caught exception.
    """
    logger.error(
        "Unexpected error: %s\n%s",
        exc,
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
    )


# ---------------------------------------------------------------------------
# CSV send-log — thin wrapper around utils.py's file I/O functions.
# Kept as a class purely for ergonomic call-sites (one object to pass
# around); it does not duplicate any read/write logic itself.
# ---------------------------------------------------------------------------
class CSVLogger:
    """
    Convenience wrapper around the CSV send-log. All actual reading and
    writing is delegated to utils.py (create_csv_log / append_csv_log /
    read_sent_log) so there is exactly one implementation of that logic.
    """

    def __init__(self, csv_path: Path = config.CSV_LOG) -> None:
        """
        Args:
            csv_path: Path to the CSV log file. Created with a header
                row if it does not already exist. Never overwritten.
        """
        self.csv_path = csv_path
        create_csv_log(self.csv_path)

    def log(
        self,
        row: int,
        company: str,
        hr_name: str,
        email: str,
        status: str,
        error: Optional[str] = "",
    ) -> None:
        """
        Append a single row describing one send attempt. If the CSV file
        is temporarily locked or inaccessible (e.g. open in Excel on
        Windows), the write is retried a few times with a short pause;
        if it still fails, the error is logged to application.log and
        swallowed rather than crashing an otherwise-successful send —
        losing one audit-log line is far less costly than aborting a
        1000+ contact campaign over a transient file lock.

        Args:
            row: Row number (1-indexed position among cleaned contacts).
            company: Company name.
            hr_name: HR contact's name.
            email: Recipient email address.
            status: One of "SENT", "FAILED", "SKIPPED".
            error: Optional human-readable error/skip reason.
        """
        csv_write_retries = 3
        csv_write_retry_delay_seconds = 2.0

        for attempt in range(1, csv_write_retries + 1):
            try:
                append_csv_log(self.csv_path, row, company, hr_name, email, status, error or "")
                return
            except PermissionError as exc:
                if attempt < csv_write_retries:
                    time.sleep(csv_write_retry_delay_seconds)
                    continue
                logging.getLogger(LOGGER_NAME).warning(
                    "Could not write to CSV log (row %d, %s) after %d attempts — "
                    "the file may be open in another program: %s",
                    row, email, csv_write_retries, exc,
                )

    def already_sent_emails(self) -> Set[str]:
        """
        Return the set of email addresses already marked "SENT" in the
        CSV log. Works even if progress.json has been deleted.

        Returns:
            A set of lower-cased email addresses already sent to.
        """
        return read_sent_log(self.csv_path)


# ---------------------------------------------------------------------------
# Colour-coded terminal display helpers
# ---------------------------------------------------------------------------
def print_banner(title: str) -> None:
    """Print a titled banner line in blue."""
    print(f"{Fore.BLUE}{'=' * BANNER_WIDTH}{Style.RESET_ALL}")
    print(f"{Fore.BLUE}{title}{Style.RESET_ALL}")
    print(f"{Fore.BLUE}{'=' * BANNER_WIDTH}{Style.RESET_ALL}")


def print_kv_block(pairs: Dict[str, str]) -> None:
    """
    Print a bordered key/value status block.

    Args:
        pairs: Ordered mapping of label -> value to display.
    """
    print(f"{Fore.BLUE}{'=' * BANNER_WIDTH}{Style.RESET_ALL}")
    for label, value in pairs.items():
        print(f"{label}")
        print(f"{value}")
    print(f"{Fore.BLUE}{'=' * BANNER_WIDTH}{Style.RESET_ALL}")


def print_info(message: str) -> None:
    """Print an informational message in blue."""
    print(f"{Fore.BLUE}{message}{Style.RESET_ALL}")


def print_success(message: str) -> None:
    """Print a success message in green."""
    print(f"{Fore.GREEN}{message}{Style.RESET_ALL}")


def print_failure(message: str, reason: str = "") -> None:
    """Print a failure message in red, with an optional reason line."""
    print(f"{Fore.RED}{message}{Style.RESET_ALL}")
    if reason:
        print(f"{Fore.RED}Reason: {reason}{Style.RESET_ALL}")


def print_skip(message: str, reason: str = "") -> None:
    """Print a skip message in yellow, with an optional reason line."""
    print(f"{Fore.YELLOW}{message}{Style.RESET_ALL}")
    if reason:
        print(f"{Fore.YELLOW}Reason: {reason}{Style.RESET_ALL}")