"""
logger.py
---------
Application-wide logging setup and a CSV logger dedicated to recording
every send attempt (success, failure, or skip) for auditing purposes.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from colorama import Fore, Style, init as colorama_init

import config
from utils import ensure_directory

colorama_init(autoreset=True)

CSV_FIELDNAMES = [
    "timestamp",
    "sno",
    "hr_name",
    "company_name",
    "email",
    "status",
    "reason",
]


def get_logger(name: str = "email_automation") -> logging.Logger:
    """
    Configure and return the application logger.

    Logs to both the console (with colour) and a rotating-free plain
    text file under the logs/ directory.

    Args:
        name: Logger name.

    Returns:
        A configured logging.Logger instance.
    """
    ensure_directory(config.LOG_FOLDER)

    logger = logging.getLogger(name)
    if logger.handlers:
        # Logger already configured (e.g. re-imported); avoid duplicate handlers.
        return logger

    logger.setLevel(logging.INFO)

    file_handler = logging.FileHandler(config.APP_LOG, encoding="utf-8")
    file_formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(_ColorFormatter())

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


class _ColorFormatter(logging.Formatter):
    """Console formatter that colours log lines based on severity."""

    _COLORS = {
        logging.DEBUG: Fore.CYAN,
        logging.INFO: Fore.GREEN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.MAGENTA + Style.BRIGHT,
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self._COLORS.get(record.levelno, "")
        message = super().format(record)
        return f"{color}{message}{Style.RESET_ALL}"

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%H:%M:%S",
        )


class CSVLogger:
    """
    Append-only CSV logger that records one row per send attempt
    (SENT, FAILED, or SKIPPED) for auditing and resumability.
    """

    def __init__(self, csv_path: Path = config.CSV_LOG) -> None:
        """
        Args:
            csv_path: Path to the CSV log file. Created with a header
                row if it does not already exist.
        """
        self.csv_path = csv_path
        ensure_directory(self.csv_path.parent)
        if not self.csv_path.exists():
            with open(self.csv_path, mode="w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=CSV_FIELDNAMES)
                writer.writeheader()

    def log(
        self,
        sno: int,
        hr_name: str,
        company_name: str,
        email: str,
        status: str,
        reason: Optional[str] = "",
    ) -> None:
        """
        Append a single row describing one send attempt.

        Args:
            sno: Serial number of the contact in the source PDF.
            hr_name: HR contact's name.
            company_name: Company name.
            email: Recipient email address.
            status: One of "SENT", "FAILED", "SKIPPED".
            reason: Optional human-readable reason (used for FAILED/SKIPPED).
        """
        with open(self.csv_path, mode="a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDNAMES)
            writer.writerow(
                {
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "sno": sno,
                    "hr_name": hr_name,
                    "company_name": company_name,
                    "email": email,
                    "status": status,
                    "reason": reason or "",
                }
            )

    def already_sent_emails(self) -> set[str]:
        """
        Read the CSV log and return the set of email addresses that
        already have a "SENT" status recorded. Used as a secondary,
        file-based safety net in addition to progress.json.

        Returns:
            A set of lower-cased email addresses that were already sent.
        """
        sent: set[str] = set()
        if not self.csv_path.exists():
            return sent
        with open(self.csv_path, mode="r", newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                if row.get("status") == "SENT" and row.get("email"):
                    sent.add(row["email"].strip().lower())
        return sent
