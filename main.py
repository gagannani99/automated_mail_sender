"""
main.py
-------
Entry point for the Email Automation Application.

Workflow:
    1. Load configuration and environment variables.
    2. Verify the resume and HR-contacts PDF exist.
    3. Ensure the logs folder and progress.json exist.
    4. Read prior progress (already-sent emails, today's send count).
    5. Parse and clean HR contacts from the PDF.
    6. Filter out already-sent / invalid / duplicate contacts.
    7. Ask for explicit user confirmation before sending anything.
    8. Send emails one at a time with randomised human-like delays and
       periodic breaks, persisting progress after every successful send.
    9. Stop automatically at the configured daily limit.
   10. Print a final summary and exit cleanly (including on Ctrl+C).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Set

from dotenv import load_dotenv

import config
from logger import CSVLogger, get_logger
from parser import parse_hr_contacts
from sender import EmailSender, EmailSenderError
from utils import ensure_directory, file_exists, random_break_minutes, random_delay

logger = get_logger()


@dataclass
class Progress:
    """In-memory representation of progress.json."""

    sent_emails: Set[str]
    last_sent_date: str
    sent_today: int

    @classmethod
    def load(cls, path: Path) -> "Progress":
        """
        Load progress from disk, creating a fresh file if missing or corrupt.

        Args:
            path: Path to progress.json.

        Returns:
            A Progress instance reflecting the file's contents (or defaults).
        """
        if not path.exists():
            progress = cls(sent_emails=set(), last_sent_date="", sent_today=0)
            progress.save(path)
            return progress

        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw: Dict[str, Any] = json.load(fh)
            return cls(
                sent_emails=set(e.lower() for e in raw.get("sent_emails", [])),
                last_sent_date=raw.get("last_sent_date", ""),
                sent_today=int(raw.get("sent_today", 0)),
            )
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            logger.warning("progress.json was unreadable (%s); starting fresh.", exc)
            progress = cls(sent_emails=set(), last_sent_date="", sent_today=0)
            progress.save(path)
            return progress

    def save(self, path: Path) -> None:
        """
        Persist the current progress state to disk.

        Args:
            path: Path to progress.json.
        """
        payload = {
            "sent_emails": sorted(self.sent_emails),
            "last_sent_date": self.last_sent_date,
            "sent_today": self.sent_today,
        }
        tmp_path = path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        tmp_path.replace(path)

    def reset_daily_counter_if_new_day(self) -> None:
        """Reset sent_today to 0 if the stored date differs from today."""
        today_str = date.today().isoformat()
        if self.last_sent_date != today_str:
            self.last_sent_date = today_str
            self.sent_today = 0

    def mark_sent(self, email: str) -> None:
        """Record a successful send for the given email address."""
        self.sent_emails.add(email.strip().lower())
        self.sent_today += 1


def verify_prerequisites() -> None:
    """
    Verify that the resume and HR contacts PDF exist before doing any work.

    Exits the process with a clear error message if a required file is missing.
    """
    missing: List[str] = []
    if not file_exists(config.RESUME_PATH):
        missing.append(f"Resume PDF not found: {config.RESUME_PATH}")
    if not file_exists(config.PDF_PATH):
        missing.append(f"HR contacts PDF not found: {config.PDF_PATH}")

    if missing:
        for item in missing:
            logger.error(item)
        logger.error(
            "Please place the required files at the expected locations and re-run."
        )
        sys.exit(1)


def load_credentials() -> tuple[str, str]:
    """
    Load Gmail credentials from environment variables (.env).

    Returns:
        A tuple of (email_address, app_password).

    Exits the process with a clear error message if credentials are missing.
    """
    load_dotenv()
    email_address = os.getenv("EMAIL", "").strip()
    app_password = os.getenv("APP_PASSWORD", "").strip()

    if not email_address or not app_password:
        logger.error(
            "Missing EMAIL and/or APP_PASSWORD. Copy .env.example to .env and fill "
            "in your Gmail address and a Gmail App Password "
            "(https://myaccount.google.com/apppasswords)."
        )
        sys.exit(1)

    return email_address, app_password


def confirm_send(pending_count: int, daily_limit: int) -> bool:
    """
    Ask the user to explicitly confirm before sending any email.

    Args:
        pending_count: Number of contacts eligible to receive an email.
        daily_limit: The configured daily sending cap.

    Returns:
        True if the user confirmed with 'Y', False otherwise.
    """
    to_send_today = min(pending_count, daily_limit)
    print()
    print("=" * 70)
    print(f"Eligible recipients remaining : {pending_count}")
    print(f"Daily sending limit           : {daily_limit}")
    print(f"Will attempt to send today    : {to_send_today}")
    print("=" * 70)
    answer = input("Are you sure you want to send emails? (Y/N): ").strip().lower()
    return answer == "y"


def run() -> None:
    """Run the full email-sending workflow end to end."""
    logger.info("Starting Email Automation Application.")

    verify_prerequisites()
    ensure_directory(config.LOG_FOLDER)
    csv_logger = CSVLogger(config.CSV_LOG)

    progress = Progress.load(config.PROGRESS_FILE)
    progress.reset_daily_counter_if_new_day()
    progress.save(config.PROGRESS_FILE)

    # Secondary safety net: anything already marked SENT in the CSV log
    # is also treated as already sent, even if progress.json was reset.
    already_sent = progress.sent_emails | csv_logger.already_sent_emails()

    logger.info("Loading HR contacts from PDF...")
    try:
        contacts_df = parse_hr_contacts(config.PDF_PATH)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Failed to parse HR contacts: %s", exc)
        sys.exit(1)

    total_contacts = len(contacts_df)
    pending_df = contacts_df[~contacts_df["Email"].str.lower().isin(already_sent)].copy()
    already_count = total_contacts - len(pending_df)

    logger.info("Total valid unique contacts in PDF : %d", total_contacts)
    logger.info("Already sent previously             : %d", already_count)
    logger.info("Pending (eligible to send today)    : %d", len(pending_df))

    if pending_df.empty:
        logger.info("No pending contacts to send to. Nothing to do. Exiting.")
        return

    remaining_today = max(config.DAILY_LIMIT - progress.sent_today, 0)
    if remaining_today <= 0:
        logger.info(
            "Daily limit of %d already reached today. Try again tomorrow.",
            config.DAILY_LIMIT,
        )
        return

    if not confirm_send(len(pending_df), remaining_today):
        logger.info("User declined to proceed. Exiting without sending anything.")
        return

    email_address, app_password = load_credentials()

    sent_count = 0
    failed_count = 0
    skipped_count = 0

    try:
        with EmailSender(email_address, app_password, config.RESUME_PATH) as sender:
            for position, (_, row) in enumerate(pending_df.iterrows(), start=1):
                if progress.sent_today >= config.DAILY_LIMIT:
                    logger.info("Reached daily limit of %d. Stopping for today.", config.DAILY_LIMIT)
                    break

                sno = row["SNo"]
                hr_name = row["Name"] or "Hiring Manager"
                company_name = row["Company"] or "your company"
                email = row["Email"]

                try:
                    sender.send(hr_name=hr_name, company_name=company_name, recipient_email=email)
                except EmailSenderError as exc:
                    failed_count += 1
                    logger.error("FAILED -> %s (%s): %s", email, company_name, exc)
                    csv_logger.log(sno, hr_name, company_name, email, "FAILED", str(exc))
                    continue

                sent_count += 1
                progress.mark_sent(email)
                progress.save(config.PROGRESS_FILE)
                csv_logger.log(sno, hr_name, company_name, email, "SENT")
                logger.info(
                    "SENT [%d/%d today] -> %s | %s (%s)",
                    progress.sent_today,
                    config.DAILY_LIMIT,
                    email,
                    hr_name,
                    company_name,
                )

                is_last = position == len(pending_df) or progress.sent_today >= config.DAILY_LIMIT
                if is_last:
                    break

                if sent_count % config.BREAK_AFTER == 0:
                    minutes = random_break_minutes(
                        config.BREAK_MINUTES_MIN, config.BREAK_MINUTES_MAX
                    )
                    logger.info("Taking a break of %.1f minutes after %d emails...", minutes, sent_count)
                else:
                    delay = random_delay(config.MIN_DELAY, config.MAX_DELAY)
                    logger.info("Waiting %.1f seconds before next email...", delay)

    except EmailSenderError as exc:
        logger.error("Could not establish SMTP connection: %s", exc)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.warning("Interrupted by user. Progress has been saved up to the last successful send.")
        progress.save(config.PROGRESS_FILE)
        print_summary(sent_count, failed_count, skipped_count, interrupted=True)
        sys.exit(130)

    print_summary(sent_count, failed_count, skipped_count, interrupted=False)


def print_summary(sent: int, failed: int, skipped: int, interrupted: bool) -> None:
    """
    Print a final run summary to the console.

    Args:
        sent: Number of emails successfully sent this run.
        failed: Number of emails that failed to send this run.
        skipped: Number of contacts skipped this run.
        interrupted: Whether the run was interrupted by the user.
    """
    print()
    print("=" * 70)
    print("RUN SUMMARY" + (" (INTERRUPTED)" if interrupted else ""))
    print("=" * 70)
    print(f"Timestamp        : {datetime.now().isoformat(timespec='seconds')}")
    print(f"Emails sent      : {sent}")
    print(f"Emails failed    : {failed}")
    print(f"Contacts skipped : {skipped}")
    print(f"Detailed log     : {config.CSV_LOG}")
    print(f"Progress file    : {config.PROGRESS_FILE}")
    print("=" * 70)


if __name__ == "__main__":
    run()
