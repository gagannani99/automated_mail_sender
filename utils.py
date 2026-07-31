"""
utils.py
--------
Small, dependency-light helper functions shared across the application:
email validation, human-like delay generation, and filesystem helpers.
"""

from __future__ import annotations

import random
import re
import time
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


def random_delay(min_seconds: float, max_seconds: float) -> float:
    """
    Sleep for a random duration between min_seconds and max_seconds.

    Args:
        min_seconds: Lower bound (inclusive) in seconds.
        max_seconds: Upper bound (inclusive) in seconds.

    Returns:
        The actual number of seconds slept.
    """
    duration = random.uniform(min_seconds, max_seconds)
    countdown(duration, "Next email in")
    return duration


def random_break_minutes(min_minutes: float, max_minutes: float) -> float:
    """
    Sleep for a random duration between min_minutes and max_minutes
    (expressed in minutes), used for longer periodic breaks.

    Args:
        min_minutes: Lower bound (inclusive) in minutes.
        max_minutes: Upper bound (inclusive) in minutes.

    Returns:
        The actual number of minutes slept.
    """
    minutes = random.uniform(min_minutes, max_minutes)
    countdown(minutes * 60, "Break remaining")
    return minutes


def countdown(seconds: float, label: str) -> None:
    """Display a one-second countdown while waiting for ``seconds``.

    The final partial second is intentionally rounded up so the displayed
    duration never understates the time the sender will wait.
    """
    remaining = max(0, int(seconds + 0.999))
    while remaining:
        minutes, secs = divmod(remaining, 60)
        print(f"\r{label}: {minutes:02d}:{secs:02d}", end="", flush=True)
        time.sleep(1)
        remaining -= 1
    print("\r" + " " * 40 + "\r", end="", flush=True)


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
