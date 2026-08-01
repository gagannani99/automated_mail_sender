"""
logger.py
---------
Application-wide file logging, the CSV send-log (source of truth for
duplicate prevention), and colour-coded terminal display helpers.

Colour convention (per spec):
    Green  -> successful email
    Yellow -> skipped email
    Red    -> failed email
    Blue   -> general information
"""

from __future__ import annotations

import csv
import logging
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Set

from colorama import Fore, Style, init as colorama_init

import config
from utils import ensure_directory

colorama_init(autoreset=True)

CSV_FIELDNAMES = ["Timestamp", "Row", "Company", "HR Name", "Email", "Status", "Error"]

BANNER_WIDTH = 55


# ---------------------------------------------------------------------------
# File logging (application.log) — used for diagnostics and tracebacks only.
# The colourful terminal output below is what the user actually watches.
# ---------------------------------------------------------------------------
def get_logger(name: str = "email_automation") -> logging.Logger:
    """
    Configure and return the application's file logger.

    Args:
        name: Logger name.

    Returns:
        A configured logging.Logger instance writing to logs/application.log.
    """
    ensure_directory(config.LOG_FOLDER)

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


def log_exception(logger: logging.Logger, exc: BaseException) -> None:
    """
    Write a full traceback for an unexpected exception to application.log.

    Args:
        logger: The application file logger.
        exc: The caught exception.
    """
    logger.error("Unexpected error: %s\n%s", exc, "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    ))


# ---------------------------------------------------------------------------
# CSV send-log — the authoritative, file-based duplicate-prevention source.
# Works even if progress.json is deleted, since it is keyed by email/status.
# ---------------------------------------------------------------------------
class CSVLogger:
    """
    Append-only CSV logger recording one row per send attempt
    (SENT, FAILED, or SKIPPED), and providing duplicate lookups.
    """

    def __init__(self, csv_path: Path = config.CSV_LOG) -> None:
        """
        Args:
            csv_path: Path to the CSV log file. Created with a header
                row if it does not already exist. Never overwritten.
        """
        self.csv_path = csv_path
        ensure_directory(self.csv_path.parent)
        if not self.csv_path.exists():
            with open(self.csv_path, mode="w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=CSV_FIELDNAMES)
                writer.writeheader()

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
        Append a single row describing one send attempt. Always appends;
        never overwrites or truncates existing history.

        Args:
            row: Row number (1-indexed position among cleaned contacts).
            company: Company name.
            hr_name: HR contact's name.
            email: Recipient email address.
            status: One of "SENT", "FAILED", "SKIPPED".
            error: Optional human-readable error/skip reason.
        """
        with open(self.csv_path, mode="a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDNAMES)
            writer.writerow(
                {
                    "Timestamp": datetime.now().isoformat(timespec="seconds"),
                    "Row": row,
                    "Company": company,
                    "HR Name": hr_name,
                    "Email": email,
                    "Status": status,
                    "Error": error or "",
                }
            )

    def already_sent_emails(self) -> Set[str]:
        """
        Read the CSV log and return the set of email addresses that
        already have a "SENT" status recorded. This is the primary,
        file-based duplicate-prevention mechanism and works even if
        progress.json has been deleted.

        Returns:
            A set of lower-cased email addresses already sent to.
        """
        sent: Set[str] = set()
        if not self.csv_path.exists():
            return sent
        with open(self.csv_path, mode="r", newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                if row.get("Status") == "SENT" and row.get("Email"):
                    sent.add(row["Email"].strip().lower())
        return sent


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
    Print a bordered key/value status block, e.g. the per-email status
    display or the startup summary.

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