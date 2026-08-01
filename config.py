"""
config.py
---------
Central configuration module for the Email Automation Application.

All tunable constants (paths, SMTP settings, pacing/rate-limit values,
and email content) live here so the rest of the codebase never contains
hard-coded values.
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Base paths
# ---------------------------------------------------------------------------
BASE_DIR: Path = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Email content
# ---------------------------------------------------------------------------
EMAIL_SUBJECT: str = "Application for Software Engineer Position"

EMAIL_BODY_TEMPLATE: str = """Hi {HR_NAME},

I hope you are doing well.

I am writing to express my interest in opportunities at {COMPANY_NAME}.

I am a Full Stack & AI Engineer with experience in Python, Flask, React, Next.js and PostgreSQL.

Please find my resume attached.

I would be grateful if you could consider my profile for any Software Engineer or Full Stack Developer opportunities.

Thank you for your valuable time.

Best Regards,
Gagan Sai Alasapuri
(+91) 6302896438
gagansaialasapuri99@gmail.com
linkedin.com/in/gagan-sai-alasapuri
gagansaiportfolio.netlify.app
"""

# ---------------------------------------------------------------------------
# Sending limits and pacing
# ---------------------------------------------------------------------------
# Gmail personal accounts are capped at ~500 recipients/day by Google, and
# sending too fast or in a single burst is one of the fastest ways to get
# an account flagged or rate-limited. These defaults are intentionally
# conservative. NOTE: this application cannot and does not guarantee that
# your account will be safe from Gmail's own rate limiting or spam
# detection — it only implements best-effort, human-like pacing.
DAILY_LIMIT: int = 120

# Manual resume-row override.
#   - If START_ROW is None: the app auto-resumes from progress.json
#     (last_successful_row + 1), or from row 1 if no progress exists yet.
#   - If START_ROW is an integer: progress.json's row is ignored and
#     sending starts from that row number instead (1-indexed).
START_ROW: "int | None" = None

# Random delay (in seconds) inserted between two consecutive emails.
MIN_DELAY: float = 30.0
MAX_DELAY: float = 90.0

# Take a longer break after this many *successful* emails have been sent
# in the current run, to further mimic human sending behaviour.
BREAK_AFTER: int = 20
BREAK_MINUTES_MIN: float = 5.0
BREAK_MINUTES_MAX: float = 10.0

# ---------------------------------------------------------------------------
# SMTP (Gmail) configuration
# ---------------------------------------------------------------------------
SMTP_SERVER: str = "smtp.gmail.com"
SMTP_PORT: int = 465  # SSL port

# Retry behaviour for transient SMTP/network errors while sending a
# single email (connection drops, timeouts, etc.).
MAX_RETRIES: int = 3
RETRY_DELAY_SECONDS: float = 15.0

# ---------------------------------------------------------------------------
# File locations
# ---------------------------------------------------------------------------
LOG_FOLDER: Path = BASE_DIR / "logs"
CSV_LOG: Path = LOG_FOLDER / "sent_emails.csv"
APP_LOG: Path = LOG_FOLDER / "application.log"
PROGRESS_FILE: Path = BASE_DIR / "progress.json"

PDF_PATH: Path = BASE_DIR / "data" / "CompanyWise HR contact.pdf"
RESUME_PATH: Path = BASE_DIR / "attachments" / "Resume.pdf"

# ---------------------------------------------------------------------------
# Required expandable placeholders inside EMAIL_BODY_TEMPLATE
# ---------------------------------------------------------------------------
PLACEHOLDER_HR_NAME: str = "{HR_NAME}"
PLACEHOLDER_COMPANY_NAME: str = "{COMPANY_NAME}"


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------
class ConfigError(Exception):
    """Raised when one or more configuration values are invalid."""


def validate_config() -> None:
    """
    Validate every configurable value in this module before the
    application does anything else. Checked: EMAIL_SUBJECT, SMTP_SERVER,
    SMTP_PORT, PDF_PATH, RESUME_PATH, MIN_DELAY, MAX_DELAY, DAILY_LIMIT,
    BREAK_AFTER, and START_ROW.

    Raises:
        ConfigError: With a clear, human-readable message describing
            every problem found (not just the first one), if any
            configuration value is invalid.
    """
    problems: list[str] = []

    if not EMAIL_SUBJECT or not EMAIL_SUBJECT.strip():
        problems.append("EMAIL_SUBJECT must not be empty.")

    if not SMTP_SERVER or not SMTP_SERVER.strip():
        problems.append("SMTP_SERVER must not be empty.")

    if not isinstance(SMTP_PORT, int) or not (0 < SMTP_PORT <= 65535):
        problems.append(f"SMTP_PORT must be an integer between 1 and 65535 (got {SMTP_PORT!r}).")

    if not isinstance(PDF_PATH, Path) or not str(PDF_PATH).strip():
        problems.append("PDF_PATH must be a valid, non-empty path.")

    if not isinstance(RESUME_PATH, Path) or not str(RESUME_PATH).strip():
        problems.append("RESUME_PATH must be a valid, non-empty path.")

    if not isinstance(MIN_DELAY, (int, float)) or MIN_DELAY < 0:
        problems.append(f"MIN_DELAY must be a non-negative number (got {MIN_DELAY!r}).")

    if not isinstance(MAX_DELAY, (int, float)) or MAX_DELAY < 0:
        problems.append(f"MAX_DELAY must be a non-negative number (got {MAX_DELAY!r}).")

    if isinstance(MIN_DELAY, (int, float)) and isinstance(MAX_DELAY, (int, float)) and MIN_DELAY > MAX_DELAY:
        problems.append(f"MIN_DELAY ({MIN_DELAY}) cannot be greater than MAX_DELAY ({MAX_DELAY}).")

    if not isinstance(DAILY_LIMIT, int) or DAILY_LIMIT <= 0:
        problems.append(f"DAILY_LIMIT must be a positive integer (got {DAILY_LIMIT!r}).")

    if not isinstance(BREAK_AFTER, int) or BREAK_AFTER <= 0:
        problems.append(f"BREAK_AFTER must be a positive integer (got {BREAK_AFTER!r}).")

    if START_ROW is not None and (not isinstance(START_ROW, int) or START_ROW < 1):
        problems.append(f"START_ROW must be None or an integer >= 1 (got {START_ROW!r}).")

    if not isinstance(MAX_RETRIES, int) or MAX_RETRIES < 0:
        problems.append(f"MAX_RETRIES must be a non-negative integer (got {MAX_RETRIES!r}).")

    if not isinstance(RETRY_DELAY_SECONDS, (int, float)) or RETRY_DELAY_SECONDS < 0:
        problems.append(f"RETRY_DELAY_SECONDS must be a non-negative number (got {RETRY_DELAY_SECONDS!r}).")

    if problems:
        details = "\n".join(f"  - {p}" for p in problems)
        raise ConfigError(f"Invalid configuration in config.py:\n{details}")