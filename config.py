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
+91 6302896438
gagansaialasapuri99@gmail.com
linkedin.com/in/gagan-sai-alasapuri
https://github.com/gagannani99
"""

# ---------------------------------------------------------------------------
# Sending limits and pacing
# ---------------------------------------------------------------------------
# Gmail personal accounts are capped at ~500 recipients/day by Google, and
# sending too fast or in a single burst is the fastest way to get an
# account flagged or suspended. DAILY_LIMIT is intentionally conservative.
DAILY_LIMIT: int = 80

# 1-indexed row (by SNo) to start from the very first time the app is run
# and progress.json has no prior history for the recipient list.
START_ROW: int = 1

# Random delay (in seconds) inserted between two consecutive emails.
MIN_DELAY: float = 25.0
MAX_DELAY: float = 55.0

# Take a longer break after this many emails have been sent in the
# current run, to further mimic human sending behaviour.
BREAK_AFTER: int = 20
BREAK_MINUTES_MIN: float = 5.0
BREAK_MINUTES_MAX: float = 10.0

# ---------------------------------------------------------------------------
# SMTP (Gmail) configuration
# ---------------------------------------------------------------------------
SMTP_SERVER: str = "smtp.gmail.com"
SMTP_PORT: int = 465  # SSL port

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
