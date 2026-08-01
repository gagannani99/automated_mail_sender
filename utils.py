"""
utils.py
--------
Small, dependency-light helper functions shared across the application:
email validation, text cleaning, duration formatting, and filesystem helpers.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

# RFC-5322 "good enough" practical email regex. Deliberately conservative:
# it rejects obviously malformed addresses while accepting the vast
# majority of real-world addresses.
_EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)


def is_valid_email(email: Optional[str]) -> bool:
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


def ensure_directory(path: Path) -> None:
    """
    Ensure a directory exists, creating parents as needed.

    Args:
        path: Directory path to create if missing.
    """
    path.mkdir(parents=True, exist_ok=True)


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


def file_exists(path: Path) -> bool:
    """
    Check whether a given path exists and is a regular file.

    Args:
        path: Path to check.

    Returns:
        True if the file exists, False otherwise.
    """
    return path.is_file()


def format_duration(total_seconds: float) -> str:
    """
    Format a duration in seconds as HH:MM:SS.

    Args:
        total_seconds: Duration in seconds (may be fractional; truncated
            to whole seconds).

    Returns:
        A zero-padded "HH:MM:SS" string. Hours can exceed 99 if needed.
    """
    total_seconds = max(0, int(total_seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"