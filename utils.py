"""
utils.py
--------
Single source of truth for every reusable helper function used across the
application: email validation, text cleaning, time formatting, countdown
display, filesystem helpers, and progress/log file I/O.

No other module re-implements any of the logic below — they import from
here instead, to avoid duplicated utility functions.
"""

from __future__ import annotations

import csv
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Set

# RFC-5322 "good enough" practical email regex. Deliberately conservative:
# it rejects obviously malformed addresses while accepting the vast
# majority of real-world addresses.
_EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)

# Default, empty progress.json payload shape. Kept here (not in main.py)
# so every module that needs to create/read/reset progress agrees on it.
_DEFAULT_PROGRESS: Dict[str, Any] = {
    "last_successful_row": 0,
    "emails_sent_today": 0,
    "last_run_date": "",
}

CSV_FIELDNAMES = ["Timestamp", "Row", "Company", "HR Name", "Email", "Status", "Error"]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_email(email: Optional[str]) -> bool:
    """
    Validate an email address against a practical regex pattern.

    Args:
        email: The email address to validate. May be None or empty.

    Returns:
        True if the address looks structurally valid, False otherwise.
    """
    if not email:
        return False
    email = email.strip()
    if len(email) > 254:
        return False
    return bool(_EMAIL_REGEX.match(email))


# ---------------------------------------------------------------------------
# Text / time helpers
# ---------------------------------------------------------------------------
def clean_text(value: Optional[str]) -> str:
    """
    Trim whitespace and collapse internal whitespace runs in a string.

    Args:
        value: Raw string, possibly None.

    Returns:
        A cleaned string, or an empty string if value was None.
    """
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def format_time(total_seconds: float) -> str:
    """
    Format a duration in seconds as HH:MM:SS.

    Args:
        total_seconds: Duration in seconds (may be fractional; truncated
            to whole seconds). Negative values are clamped to zero.

    Returns:
        A zero-padded "HH:MM:SS" string. Hours can exceed 99 if needed.
    """
    total_seconds = max(0, int(total_seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def current_timestamp() -> str:
    """
    Return the current local timestamp as an ISO-8601 string
    (second precision), used consistently for every log entry.

    Returns:
        e.g. "2026-08-01T09:15:32"
    """
    return datetime.now().isoformat(timespec="seconds")


def estimate_remaining_time(
    average_seconds_per_email: float,
    remaining_contacts: int,
    break_after: int,
    average_break_seconds: float,
) -> float:
    """
    Estimate the remaining time (in seconds) to finish sending, factoring
    in the average per-email send/delay time plus the periodic long
    breaks that will still occur before the run finishes.

    Args:
        average_seconds_per_email: Observed (or assumed default) average
            number of seconds spent per email so far, including delays.
        remaining_contacts: Number of contacts still left to process.
        break_after: Number of successful sends between long breaks.
        average_break_seconds: Average duration of one long break, in seconds.

    Returns:
        Estimated remaining time in seconds (never negative).
    """
    if remaining_contacts <= 0:
        return 0.0

    base_time = average_seconds_per_email * remaining_contacts

    upcoming_breaks = remaining_contacts // break_after if break_after > 0 else 0
    break_time = upcoming_breaks * average_break_seconds

    return max(base_time + break_time, 0.0)


def countdown(total_seconds: int, label: str) -> None:
    """
    Display a live, single-line countdown in the terminal for the given
    duration, then move to a fresh line. Never floods the terminal with
    repeated newlines — the same line is overwritten on every tick.

    Args:
        total_seconds: Number of seconds to count down from.
        label: A format string containing "{time}", rendered fresh on
            every tick with either a plain seconds count or MM:SS,
            depending on the duration.
    """
    use_clock_format = total_seconds >= 60

    for remaining in range(total_seconds, 0, -1):
        display_value = format_time(remaining)[3:] if use_clock_format else str(remaining)
        sys.stdout.write("\r" + label.format(time=display_value) + "   ")
        sys.stdout.flush()
        time.sleep(1)

    sys.stdout.write("\r" + " " * 60 + "\r")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------
def create_folder(path: Path) -> None:
    """
    Ensure a directory exists, creating parents as needed.

    Args:
        path: Directory path to create if missing.

    Raises:
        PermissionError: If the directory cannot be created due to
            insufficient filesystem permissions.
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise PermissionError(f"Permission denied while creating folder: {path}") from exc


def file_exists(path: Path) -> bool:
    """
    Check whether a given path exists and is a regular file.

    Args:
        path: Path to check.

    Returns:
        True if the file exists, False otherwise.
    """
    return path.is_file()


# ---------------------------------------------------------------------------
# progress.json helpers
# ---------------------------------------------------------------------------
def create_progress_file(path: Path) -> Dict[str, Any]:
    """
    Create progress.json with default values if it does not already exist.

    Args:
        path: Path to progress.json.

    Returns:
        The progress payload now guaranteed to be on disk (either the
        pre-existing one, left untouched, or a freshly created default).

    Raises:
        PermissionError: If the file cannot be written.
    """
    if path.exists():
        return _DEFAULT_PROGRESS.copy()
    save_progress(path, _DEFAULT_PROGRESS.copy())
    return _DEFAULT_PROGRESS.copy()


def read_progress(path: Path) -> Dict[str, Any]:
    """
    Read progress.json from disk. If the file is missing, corrupt, or
    contains invalid data, a fresh default payload is created on disk
    and returned instead of raising.

    Args:
        path: Path to progress.json.

    Returns:
        A dict with keys "last_successful_row", "emails_sent_today",
        and "last_run_date".
    """
    if not path.exists():
        return create_progress_file(path)

    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return {
            "last_successful_row": int(raw.get("last_successful_row", 0)),
            "emails_sent_today": int(raw.get("emails_sent_today", 0)),
            "last_run_date": str(raw.get("last_run_date", "")),
        }
    except (json.JSONDecodeError, OSError, ValueError, UnicodeDecodeError):
        # Corrupt or unreadable progress file: reset to a safe default
        # rather than crashing the whole application.
        default = _DEFAULT_PROGRESS.copy()
        save_progress(path, default)
        return default


def save_progress(path: Path, data: Dict[str, Any]) -> None:
    """
    Persist a progress payload to disk atomically (write to a temp file,
    then rename), so a crash mid-write can never corrupt progress.json.

    Args:
        path: Path to progress.json.
        data: Dict with "last_successful_row", "emails_sent_today", and
            "last_run_date".

    Raises:
        PermissionError: If the file cannot be written.
    """
    tmp_path = path.with_suffix(".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        tmp_path.replace(path)
    except PermissionError as exc:
        raise PermissionError(f"Permission denied while saving progress to: {path}") from exc


# ---------------------------------------------------------------------------
# CSV send-log helpers
# ---------------------------------------------------------------------------
def create_csv_log(path: Path) -> None:
    """
    Create logs/sent_emails.csv with the correct header row if it does
    not already exist. Never overwrites or truncates an existing file.

    Args:
        path: Path to the CSV log file.

    Raises:
        PermissionError: If the file cannot be created.
    """
    if path.exists():
        return
    create_folder(path.parent)
    try:
        with open(path, mode="w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
    except PermissionError as exc:
        raise PermissionError(f"Permission denied while creating CSV log: {path}") from exc


def append_csv_log(
    path: Path,
    row: int,
    company: str,
    hr_name: str,
    email: str,
    status: str,
    error: str = "",
) -> None:
    """
    Append a single send-attempt row to the CSV log. Always appends,
    never overwrites existing history, and writes immediately (no
    buffering) so the log is accurate even if the process is killed.

    Args:
        path: Path to the CSV log file.
        row: Row number (1-indexed position among cleaned contacts).
        company: Company name.
        hr_name: HR contact's name.
        email: Recipient email address.
        status: One of "SENT", "FAILED", "SKIPPED".
        error: Optional human-readable error/skip reason.

    Raises:
        PermissionError: If the file cannot be written.
    """
    create_csv_log(path)
    try:
        with open(path, mode="a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDNAMES)
            writer.writerow(
                {
                    "Timestamp": current_timestamp(),
                    "Row": row,
                    "Company": company,
                    "HR Name": hr_name,
                    "Email": email,
                    "Status": status,
                    "Error": error or "",
                }
            )
    except PermissionError as exc:
        raise PermissionError(f"Permission denied while writing to CSV log: {path}") from exc


def read_sent_log(path: Path) -> Set[str]:
    """
    Read the CSV send-log and return the set of email addresses that
    already have a "SENT" status recorded. This is the authoritative,
    file-based duplicate-prevention mechanism: it works correctly even
    if progress.json has been deleted or reset.

    Args:
        path: Path to the CSV log file.

    Returns:
        A set of lower-cased email addresses already sent to. Returns
        an empty set if the file does not exist or cannot be parsed.
    """
    sent: Set[str] = set()
    if not path.exists():
        return sent
    try:
        with open(path, mode="r", newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                if row.get("Status") == "SENT" and row.get("Email"):
                    sent.add(row["Email"].strip().lower())
    except (OSError, UnicodeDecodeError, csv.Error):
        # A damaged CSV log should never crash the app; treat it as if
        # nothing had been sent yet rather than losing all duplicate
        # protection information silently.
        return set()
    return sent