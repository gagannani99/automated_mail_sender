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
import time
import traceback
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List

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

    last_successful_row: int
    emails_sent_today: int
    last_run_date: str

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
            progress = cls(last_successful_row=0, emails_sent_today=0, last_run_date="")
            progress.save(path)
            return progress

        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw: Dict[str, Any] = json.load(fh)
            return cls(
                last_successful_row=int(raw.get("last_successful_row", 0)),
                emails_sent_today=int(raw.get("emails_sent_today", 0)),
                last_run_date=str(raw.get("last_run_date", "")),
            )
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            logger.warning("progress.json was unreadable (%s); starting fresh.", exc)
            progress = cls(last_successful_row=0, emails_sent_today=0, last_run_date="")
            progress.save(path)
            return progress

    def save(self, path: Path) -> None:
        """
        Persist the current progress state to disk.

        Args:
            path: Path to progress.json.
        """
        payload = {
            "last_successful_row": self.last_successful_row,
            "emails_sent_today": self.emails_sent_today,
            "last_run_date": self.last_run_date,
        }
        tmp_path = path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        tmp_path.replace(path)

    def reset_daily_counter_if_new_day(self) -> None:
        """Reset the daily count automatically when the calendar day changes."""
        today_str = date.today().isoformat()
        if self.last_run_date != today_str:
            self.last_run_date = today_str
            self.emails_sent_today = 0

    def mark_sent(self, row_number: int) -> None:
        """Record a successful send and the source row that completed it."""
        self.last_successful_row = row_number
        self.emails_sent_today += 1


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

    # The CSV is the authoritative duplicate-protection record. It is read
    # again immediately before each send to protect against a concurrent run.
    already_sent = csv_logger.already_sent_emails()

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

    remaining_today = max(config.DAILY_LIMIT - progress.emails_sent_today, 0)
    if remaining_today <= 0:
        logger.info("Daily Limit Reached")
        return

    if not confirm_send(len(pending_df), remaining_today):
        logger.info("User declined to proceed. Exiting without sending anything.")
        return

    email_address, app_password = load_credentials()

    sent_count = failed_count = skipped_count = 0
    run_started = time.monotonic()
    sender: EmailSender | None = None
    try:
        sender = EmailSender(email_address, app_password, config.RESUME_PATH)
        # The connection is opened lazily by send(), then retained for all
        # later recipients unless Gmail drops it.
        for position, (_, row) in enumerate(pending_df.iterrows(), start=1):
            if progress.emails_sent_today >= config.DAILY_LIMIT:
                progress.save(config.PROGRESS_FILE)
                logger.info("Daily Limit Reached")
                break

            sno = row["SNo"]
            hr_name = row["Name"] or "Hiring Manager"
            company_name = row["Company"] or "your company"
            email = row["Email"]
            _display_current_status(
                row_number=sno,
                total=total_contacts,
                company=company_name,
                hr_name=hr_name,
                email=email,
                sent_today=progress.emails_sent_today,
                failed=failed_count,
                skipped=skipped_count,
                elapsed=time.monotonic() - run_started,
                estimated_remaining=_estimate_remaining(
                    time.monotonic() - run_started, position - 1, len(pending_df) - position + 1
                ),
            )

            if email.strip().lower() in csv_logger.already_sent_emails():
                skipped_count += 1
                csv_logger.log(sno, hr_name, company_name, email, "SKIPPED", "Already marked SENT in CSV")
                logger.warning("SKIPPED -> %s already has status SENT.", email)
                continue

            try:
                sender.send(hr_name=hr_name, company_name=company_name, recipient_email=email)
            except EmailSenderError as exc:
                failed_count += 1
                logger.error("FAILED -> %s (%s): %s", email, company_name, exc)
                csv_logger.log(sno, hr_name, company_name, email, "FAILED", str(exc))
                continue

            sent_count += 1
            progress.mark_sent(_row_number(sno, position))
            csv_logger.log(sno, hr_name, company_name, email, "SENT")
            progress.save(config.PROGRESS_FILE)
            logger.info("SENT [%d/%d today] -> %s | %s (%s)", progress.emails_sent_today, config.DAILY_LIMIT, email, hr_name, company_name)

            if progress.emails_sent_today >= config.DAILY_LIMIT:
                logger.info("Daily Limit Reached")
                break
            if position == len(pending_df):
                continue
            if sent_count % config.BREAK_AFTER == 0:
                logger.info("Taking a break after %d successful emails.", sent_count)
                random_break_minutes(config.BREAK_MINUTES_MIN, config.BREAK_MINUTES_MAX)
            else:
                random_delay(config.MIN_DELAY, config.MAX_DELAY)
    except KeyboardInterrupt:
        progress.save(config.PROGRESS_FILE)
        if sender is not None:
            sender.close()
        logger.warning("Interrupted by user. Progress has been saved up to the last successful send.")
        print(f"Last Successful Row: {progress.last_successful_row}")
        print(f"Next Start Row: {progress.last_successful_row + 1}")
        print_summary(sent_count, failed_count, skipped_count, interrupted=True)
        return
    except Exception:  # noqa: BLE001 - final safety boundary for CLI execution
        progress.save(config.PROGRESS_FILE)
        if sender is not None:
            sender.close()
        logger.error("Unexpected error. Progress was saved; SMTP disconnected.")
        traceback.print_exc()
        return
    finally:
        if sender is not None:
            sender.close()

    print_summary(sent_count, failed_count, skipped_count, interrupted=False)


def _row_number(value: object, fallback: int) -> int:
    """Convert a source SNo value to an integer, using its position if needed."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def _format_duration(seconds: float) -> str:
    """Return a compact HH:MM:SS representation of a duration."""
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _estimate_remaining(elapsed: float, completed: int, remaining: int) -> float:
    """Estimate remaining runtime using average time per processed contact."""
    return 0.0 if completed <= 0 else elapsed / completed * remaining


def _display_current_status(
    row_number: object, total: int, company: str, hr_name: str, email: str,
    sent_today: int, failed: int, skipped: int, elapsed: float,
    estimated_remaining: float,
) -> None:
    """Print the required pre-send status panel for the current contact."""
    print("\n" + "-" * 35)
    print(f"Current Row: {_row_number(row_number, 0)} / {total}")
    print(f"Company: {company}")
    print(f"HR Name: {hr_name}")
    print(f"Email: {email}")
    print(f"Today's Sent: {sent_today}")
    print(f"Failed: {failed}")
    print(f"Skipped: {skipped}")
    print(f"Elapsed Time: {_format_duration(elapsed)}")
    print(f"Estimated Remaining Time: {_format_duration(estimated_remaining)}")
    print("-" * 35)


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
