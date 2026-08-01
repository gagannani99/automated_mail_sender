"""
main.py
-------
Entry point for the Email Automation Application. This module only
coordinates the workflow; all actual logic lives in the other modules.

Workflow:
    Load Configuration -> Validate Required Files -> Load Progress ->
    Parse PDF -> Validate Emails -> Remove Duplicate Emails ->
    Calculate Remaining Contacts -> Display Summary -> Ask User
    Confirmation -> Start Email Sending -> Print Final Summary -> Exit
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Set

import pandas as pd
from dotenv import load_dotenv

import config
from logger import (
    CSVLogger,
    get_logger,
    log_exception,
    print_banner,
    print_failure,
    print_info,
    print_kv_block,
    print_skip,
    print_success,
)
from parser import ParseStats, parse_hr_contacts
from sender import EmailSender, EmailSenderError
from utils import ensure_directory, file_exists, format_duration

logger = get_logger()


# ---------------------------------------------------------------------------
# Progress tracking (progress.json)
# ---------------------------------------------------------------------------
@dataclass
class Progress:
    """
    In-memory representation of progress.json.

    Structure on disk:
        {
          "last_successful_row": 157,
          "emails_sent_today": 43,
          "last_run_date": "YYYY-MM-DD"
        }
    """

    last_successful_row: int
    emails_sent_today: int
    last_run_date: str
    existed_on_disk: bool

    @classmethod
    def load(cls, path: Path) -> "Progress":
        """
        Load progress from disk. If the file is missing or corrupt, a
        fresh, zeroed Progress is created (and persisted).

        Args:
            path: Path to progress.json.

        Returns:
            A Progress instance reflecting the file's contents (or defaults).
        """
        existed = path.exists()
        if not existed:
            progress = cls(last_successful_row=0, emails_sent_today=0, last_run_date="", existed_on_disk=False)
            progress.save(path)
            return progress

        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            return cls(
                last_successful_row=int(raw.get("last_successful_row", 0)),
                emails_sent_today=int(raw.get("emails_sent_today", 0)),
                last_run_date=raw.get("last_run_date", ""),
                existed_on_disk=True,
            )
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            logger.warning("progress.json was unreadable (%s); starting fresh.", exc)
            progress = cls(last_successful_row=0, emails_sent_today=0, last_run_date="", existed_on_disk=False)
            progress.save(path)
            return progress

    def save(self, path: Path) -> None:
        """
        Persist the current progress state to disk atomically.

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

    def reset_if_new_day(self) -> None:
        """Reset emails_sent_today to 0 if last_run_date differs from today."""
        today_str = date.today().isoformat()
        if self.last_run_date != today_str:
            self.last_run_date = today_str
            self.emails_sent_today = 0

    def mark_sent(self, row: int) -> None:
        """Record a successful send at the given row and bump today's counter."""
        self.last_successful_row = row
        self.emails_sent_today += 1


# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------
def verify_prerequisites() -> None:
    """
    Verify the resume and HR contacts PDF exist before doing any work.
    Exits the process with a clear error message if either is missing.
    """
    missing = []
    if not file_exists(config.RESUME_PATH):
        missing.append(f"Resume PDF not found: {config.RESUME_PATH}")
    if not file_exists(config.PDF_PATH):
        missing.append(f"HR contacts PDF not found: {config.PDF_PATH}")

    if missing:
        for item in missing:
            print_failure(item)
        print_failure("Please place the required files at the expected locations and re-run.")
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
        print_failure(
            "Missing EMAIL and/or APP_PASSWORD.",
            "Copy .env.example to .env and fill in your Gmail address and a "
            "Gmail App Password (https://myaccount.google.com/apppasswords).",
        )
        sys.exit(1)

    return email_address, app_password


# ---------------------------------------------------------------------------
# Startup summary + confirmation
# ---------------------------------------------------------------------------
def display_startup_summary(
    stats: ParseStats,
    already_sent_count: int,
    resume_found: bool,
    pdf_found: bool,
) -> None:
    """
    Display the startup summary box before asking for send confirmation.

    Args:
        stats: Parsing statistics from parse_hr_contacts.
        already_sent_count: Number of cleaned contacts already marked SENT.
        resume_found: Whether progress.json existed on disk at startup.
        pdf_found: Whether the HR contacts PDF was found.
    """
    print_banner("Email Automation")
    print(f"Total Contacts Found : {stats.total_contacts_found}")
    print(f"Duplicate Emails Removed : {stats.duplicates_removed}")
    print(f"Invalid Emails Removed : {stats.invalid_removed}")
    print(f"Remaining Emails : {stats.remaining}")
    print(f"Already Sent : {already_sent_count}")
    print(f"Today's Limit : {config.DAILY_LIMIT}")
    print(f"Resume Found : {'YES' if resume_found else 'NO'}")
    print(f"PDF Found : {'YES' if pdf_found else 'NO'}")
    print("=" * 44)


def confirm_send() -> bool:
    """
    Ask the user to explicitly confirm before sending any email.

    Returns:
        True if the user confirmed with 'Y', False otherwise.
    """
    answer = input("Send Emails? (Y/N): ").strip().lower()
    return answer == "y"


# ---------------------------------------------------------------------------
# Start-row resolution
# ---------------------------------------------------------------------------
def resolve_start_row(progress: Progress) -> int:
    """
    Determine which row (1-indexed) to start sending from.

    If config.START_ROW is an integer, it overrides progress.json
    entirely. Otherwise, sending auto-resumes from
    progress.last_successful_row + 1 (or row 1 if no prior progress).

    Args:
        progress: The loaded Progress state.

    Returns:
        The 1-indexed row number to start sending from.
    """
    if config.START_ROW is not None:
        print_info("Manual Start Row Enabled")
        print_info(f"Starting From Row {config.START_ROW}")
        return int(config.START_ROW)

    start_row = progress.last_successful_row + 1
    return max(start_row, 1)


# ---------------------------------------------------------------------------
# Per-email status display
# ---------------------------------------------------------------------------
def display_status_block(
    row: int,
    total: int,
    company: str,
    hr_name: str,
    email: str,
    sent_today: int,
    failed: int,
    skipped: int,
    elapsed_seconds: float,
    remaining_seconds: float,
) -> None:
    """Print the bordered status block shown before every send attempt."""
    print("=" * 37)
    print("Current Row")
    print(f"{row} / {total}")
    print("Company")
    print(company or "N/A")
    print("HR")
    print(hr_name or "N/A")
    print("Email")
    print(email)
    print("Today's Sent")
    print(f"{sent_today} / {config.DAILY_LIMIT}")
    print("Failed")
    print(failed)
    print("Skipped")
    print(skipped)
    print("Elapsed Time")
    print(format_duration(elapsed_seconds))
    print("Estimated Remaining Time")
    print(format_duration(remaining_seconds))
    print("=" * 37)


# ---------------------------------------------------------------------------
# Daily-limit-reached / interrupted / final summaries
# ---------------------------------------------------------------------------
def display_daily_limit_reached(sent_today: int) -> None:
    print("=" * 37)
    print("Daily Sending Limit Reached")
    print(f"Emails Sent Today : {sent_today}")
    print("Progress Saved Successfully")
    print("Resume Tomorrow")
    print("=" * 37)


def display_interrupted(last_successful_row: int) -> None:
    print("=" * 37)
    print("Program Interrupted")
    print("Progress Saved")
    print("Last Successful Row")
    print(last_successful_row)
    print("Next Start Row")
    print(last_successful_row + 1)
    print("=" * 37)


def display_final_summary(
    total_contacts: int,
    sent: int,
    failed: int,
    skipped: int,
    execution_seconds: float,
) -> None:
    print("=" * 37)
    print("Today's Work Completed")
    print("Total Contacts")
    print(total_contacts)
    print("Emails Sent")
    print(sent)
    print("Emails Failed")
    print(failed)
    print("Emails Skipped")
    print(skipped)
    print("Execution Time")
    print(format_duration(execution_seconds))
    print("Average Time Per Email")
    avg = execution_seconds / sent if sent else 0.0
    print(format_duration(avg))
    print("Progress Saved")
    print("=" * 37)


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------
def run() -> None:
    """Run the full email-sending workflow end to end."""
    ensure_directory(config.LOG_FOLDER)
    csv_logger = CSVLogger(config.CSV_LOG)

    # --- Validate required files -----------------------------------------
    pdf_found = file_exists(config.PDF_PATH)
    resume_found = file_exists(config.RESUME_PATH)
    if not pdf_found or not resume_found:
        verify_prerequisites()  # exits the process

    # --- Load progress ------------------------------------------------------
    resume_found_flag = config.PROGRESS_FILE.exists()
    progress = Progress.load(config.PROGRESS_FILE)
    progress.reset_if_new_day()
    progress.save(config.PROGRESS_FILE)

    # --- Parse PDF, validate emails, remove duplicates ----------------------
    try:
        contacts_df, stats = parse_hr_contacts(config.PDF_PATH)
    except (FileNotFoundError, ValueError) as exc:
        print_failure("Failed to parse HR contacts.", str(exc))
        sys.exit(1)

    # --- Calculate remaining contacts (already-sent lookup via CSV) --------
    already_sent: Set[str] = csv_logger.already_sent_emails()
    already_sent_count = int(contacts_df["Email"].str.lower().isin(already_sent).sum())

    # --- Display summary + ask confirmation ---------------------------------
    display_startup_summary(stats, already_sent_count, resume_found_flag, pdf_found)

    if stats.remaining - already_sent_count <= 0:
        print_info("No pending contacts to send to. Nothing to do. Exiting.")
        return

    if not confirm_send():
        print_info("User declined to proceed. Exiting without sending anything.")
        return

    email_address, app_password = load_credentials()

    start_row = resolve_start_row(progress)
    total_rows = len(contacts_df)

    sent_count = 0
    failed_count = 0
    skipped_count = 0
    run_start_time = datetime.now()

    try:
        with EmailSender(email_address, app_password, config.RESUME_PATH) as sender:
            for _, row_data in contacts_df.iterrows():
                row_number = int(row_data["SNo"])

                if row_number < start_row:
                    continue

                if progress.emails_sent_today >= config.DAILY_LIMIT:
                    progress.save(config.PROGRESS_FILE)
                    display_daily_limit_reached(progress.emails_sent_today)
                    return

                company = row_data["Company"] or "N/A"
                hr_name = row_data["Name"] or "Hiring Manager"
                email = row_data["Email"]

                elapsed = (datetime.now() - run_start_time).total_seconds()
                processed = sent_count + failed_count + skipped_count
                avg_per_email = (elapsed / processed) if processed else (config.MIN_DELAY + config.MAX_DELAY) / 2
                remaining_contacts = max(total_rows - row_number + 1, 0)
                estimated_remaining = avg_per_email * remaining_contacts

                display_status_block(
                    row=row_number,
                    total=total_rows,
                    company=company,
                    hr_name=hr_name,
                    email=email,
                    sent_today=progress.emails_sent_today,
                    failed=failed_count,
                    skipped=skipped_count,
                    elapsed_seconds=elapsed,
                    remaining_seconds=estimated_remaining,
                )

                # Duplicate check against the CSV log (authoritative,
                # survives progress.json deletion).
                if email.strip().lower() in already_sent:
                    skipped_count += 1
                    print_skip("Already Sent", "Skipping")
                    csv_logger.log(row_number, company, hr_name, email, "SKIPPED", "Already sent")
                    continue

                if not EmailSender.validate_email(email):
                    skipped_count += 1
                    print_skip("Invalid Email", "Skipped")
                    csv_logger.log(row_number, company, hr_name, email, "SKIPPED", "Invalid email")
                    continue

                try:
                    sender.send_email(hr_name=hr_name, company_name=company, recipient_email=email)
                except EmailSenderError as exc:
                    failed_count += 1
                    print_failure("Email Failed", str(exc))
                    csv_logger.log(row_number, company, hr_name, email, "FAILED", str(exc))
                    continue

                sent_count += 1
                already_sent.add(email.strip().lower())
                progress.mark_sent(row_number)
                progress.save(config.PROGRESS_FILE)
                csv_logger.log(row_number, company, hr_name, email, "SENT")
                print_success("Email Sent Successfully")

                is_last_row = row_number == total_rows
                if is_last_row or progress.emails_sent_today >= config.DAILY_LIMIT:
                    continue

                sender.emails_sent_since_break += 1
                if sender.emails_sent_since_break % config.BREAK_AFTER == 0:
                    sender.take_break()
                else:
                    sender.random_delay()

    except EmailSenderError as exc:
        print_failure("Could not establish SMTP connection.", str(exc))
        progress.save(config.PROGRESS_FILE)
        sys.exit(1)
    except KeyboardInterrupt:
        progress.save(config.PROGRESS_FILE)
        display_interrupted(progress.last_successful_row)
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001 - top-level safety net
        traceback.print_exc()
        log_exception(logger, exc)
        progress.save(config.PROGRESS_FILE)
        print_failure("An unexpected error occurred. Progress has been saved.")
        sys.exit(1)

    execution_seconds = (datetime.now() - run_start_time).total_seconds()
    display_final_summary(total_rows, sent_count, failed_count, skipped_count, execution_seconds)


if __name__ == "__main__":
    run()