"""
main.py
-------
Entry point for the Email Automation Application. This module only
coordinates the workflow; all actual logic lives in the other modules
(config validation in config.py, parsing in parser.py, sending in
sender.py, logging/display in logger.py, and shared helpers in utils.py).

Workflow:
    Load & Validate Configuration -> Validate Required Files -> Load
    Progress -> Parse PDF -> Validate Emails -> Remove Duplicate Emails
    -> Calculate Remaining Contacts -> Display Summary -> Ask User
    Confirmation -> Preview First Email -> Start Email Sending -> Print
    Final Summary -> Exit
"""

from __future__ import annotations

import csv
import json
import os
import sys
import traceback
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Set

from dotenv import load_dotenv

import config
from logger import (
    CSVLogger,
    get_logger,
    log_event,
    log_exception,
    print_banner,
    print_failure,
    print_info,
    print_skip,
    print_success,
)
from parser import ParseStats, parse_hr_contacts
from sender import EmailSender, EmailSenderError
from utils import (
    create_folder,
    create_progress_file,
    estimate_remaining_time,
    file_exists,
    format_time,
    read_progress,
    save_progress,
    validate_email,
)

logger = get_logger()

AVERAGE_BREAK_SECONDS = ((config.BREAK_MINUTES_MIN + config.BREAK_MINUTES_MAX) / 2) * 60
DEFAULT_AVERAGE_SEND_SECONDS = (config.MIN_DELAY + config.MAX_DELAY) / 2


# ---------------------------------------------------------------------------
# Progress tracking (progress.json) — thin wrapper around utils.py, which
# owns the actual file I/O so the logic exists in exactly one place.
# ---------------------------------------------------------------------------
@dataclass
class Progress:
    """In-memory view of progress.json's last_successful_row, emails_sent_today,
    and last_run_date, backed by utils.read_progress / utils.save_progress."""

    last_successful_row: int
    emails_sent_today: int
    last_run_date: str
    existed_on_disk: bool

    @classmethod
    def load(cls, path: Path) -> "Progress":
        """
        Load progress from disk via utils.read_progress, creating the
        file with defaults first if it doesn't exist.

        Args:
            path: Path to progress.json.

        Returns:
            A Progress instance reflecting the file's contents.
        """
        existed = path.exists()
        if not existed:
            create_progress_file(path)
        data = read_progress(path)
        return cls(
            last_successful_row=data["last_successful_row"],
            emails_sent_today=data["emails_sent_today"],
            last_run_date=data["last_run_date"],
            existed_on_disk=existed,
        )

    def save(self, path: Path) -> None:
        """Persist the current progress state to disk via utils.save_progress."""
        save_progress(
            path,
            {
                "last_successful_row": self.last_successful_row,
                "emails_sent_today": self.emails_sent_today,
                "last_run_date": self.last_run_date,
            },
        )
        log_event(logger, "Progress Saved", f"row={self.last_successful_row}, sent_today={self.emails_sent_today}")

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
# Startup validation: config, files, environment
# ---------------------------------------------------------------------------
def validate_configuration() -> None:
    """
    Validate config.py before doing anything else. Prints a clear error
    and exits if any configuration value is invalid.
    """
    try:
        config.validate_config()
    except config.ConfigError as exc:
        print_failure("Invalid configuration detected.", str(exc))
        sys.exit(1)


def verify_prerequisites() -> None:
    """
    Verify required files/folders before doing any work:
        - logs/ folder: created automatically if missing.
        - progress.json: created automatically if missing.
        - Resume PDF: the app stops if missing.
        - HR contacts PDF: the app stops if missing.
    """
    try:
        create_folder(config.LOG_FOLDER)
    except PermissionError as exc:
        print_failure("Cannot create logs folder.", str(exc))
        sys.exit(1)

    if not config.PROGRESS_FILE.exists():
        try:
            create_progress_file(config.PROGRESS_FILE)
        except PermissionError as exc:
            print_failure("Cannot create progress.json.", str(exc))
            sys.exit(1)

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

    log_event(logger, "Resume Loaded", str(config.RESUME_PATH))


def load_credentials() -> tuple[str, str]:
    """
    Load Gmail credentials from environment variables (.env), validating
    EMAIL and APP_PASSWORD independently so the user gets a specific,
    actionable error message.

    Returns:
        A tuple of (email_address, app_password).

    Exits the process with a clear error message if either is missing.
    """
    load_dotenv()
    email_address = os.getenv("EMAIL", "").strip()
    app_password = os.getenv("APP_PASSWORD", "").strip()

    if not email_address:
        print_failure("Missing EMAIL in .env")
        print_info("Copy .env.example to .env and set your Gmail address.")
        sys.exit(1)

    if not app_password:
        print_failure("Missing APP_PASSWORD in .env")
        print_info(
            "Copy .env.example to .env and set a Gmail App Password "
            "(https://myaccount.google.com/apppasswords)."
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


def preview_first_email(sender: EmailSender, hr_name: str, company: str, email: str) -> bool:
    """
    Show a preview of the very first email that will be sent (subject,
    recipient, company, HR name, and the first few lines of the body),
    and ask for one final confirmation before sending begins.

    Args:
        sender: The connected EmailSender (used only to render the body).
        hr_name: HR name for the first pending contact.
        company: Company name for the first pending contact.
        email: Recipient email for the first pending contact.

    Returns:
        True if the user confirmed with 'Y', False otherwise.
    """
    message = sender.create_email(hr_name, company, email)
    body = message.get_content()
    preview_lines = body.strip().splitlines()[:4]

    print("-" * 36)
    print("Subject")
    print(config.EMAIL_SUBJECT)
    print("To")
    print(email)
    print("Company")
    print(company)
    print("HR")
    print(hr_name)
    print("Body Preview")
    for line in preview_lines:
        print(line)
    print("...")
    print("-" * 36)

    answer = input("Continue? (Y/N): ").strip().lower()
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

    return max(progress.last_successful_row + 1, 1)


# ---------------------------------------------------------------------------
# Terminal dashboard — refreshed before every email
# ---------------------------------------------------------------------------
def display_dashboard(
    row: int,
    total: int,
    company: str,
    hr_name: str,
    email: str,
    resume_found: bool,
    pdf_found: bool,
    smtp_connected: bool,
    sent_today: int,
    failed: int,
    skipped: int,
    elapsed_seconds: float,
    remaining_seconds: float,
    average_seconds: float,
) -> None:
    """
    Print the full status dashboard, refreshed before every send attempt.
    Combines overall system/run status with the current contact's details.
    """
    remaining_today = max(config.DAILY_LIMIT - sent_today, 0)
    remaining_contacts = max(total - row + 1, 0)

    print("=" * 42)
    print("Email Automation Dashboard")
    print("=" * 42)
    print("Resume")
    print("FOUND" if resume_found else "MISSING")
    print("PDF")
    print("FOUND" if pdf_found else "MISSING")
    print("SMTP")
    print("CONNECTED" if smtp_connected else "DISCONNECTED")
    print("Current Row")
    print(f"{row} / {total}")
    print("Company")
    print(company or "N/A")
    print("HR")
    print(hr_name or "N/A")
    print("Email")
    print(email)
    print("Today's Limit")
    print(config.DAILY_LIMIT)
    print("Already Sent Today")
    print(sent_today)
    print("Remaining Today")
    print(remaining_today)
    print("Remaining Contacts")
    print(remaining_contacts)
    print("Failed")
    print(failed)
    print("Skipped")
    print(skipped)
    print("Elapsed Time")
    print(format_time(elapsed_seconds))
    print("Average Per Email")
    print(f"{int(average_seconds)} sec")
    print("Estimated Finish Today")
    print(format_time(remaining_seconds))
    print("=" * 42)


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
    print(format_time(execution_seconds))
    print("Average Time Per Email")
    avg = execution_seconds / sent if sent else 0.0
    print(format_time(avg))
    print("Progress Saved")
    print("=" * 37)


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------
def run() -> None:  # noqa: C901 - orchestration function; complexity comes
    """Run the full email-sending workflow end to end."""              # from sequential, single-pass workflow steps, not nested logic.
    log_event(logger, "Application Start")

    validate_configuration()
    verify_prerequisites()

    csv_logger = CSVLogger(config.CSV_LOG)

    resume_found_flag = config.PROGRESS_FILE.exists()
    progress = Progress.load(config.PROGRESS_FILE)
    progress.reset_if_new_day()
    progress.save(config.PROGRESS_FILE)

    # --- Parse PDF, validate emails, remove duplicates ----------------------
    try:
        contacts_df, stats = parse_hr_contacts(config.PDF_PATH)
        log_event(logger, "PDF Loaded", f"{stats.remaining} valid contacts")
    except FileNotFoundError as exc:
        print_failure("HR contacts PDF not found.", str(exc))
        sys.exit(1)
    except ValueError as exc:
        print_failure("HR contacts PDF could not be parsed.", str(exc))
        sys.exit(1)
    except (UnicodeDecodeError, PermissionError) as exc:
        print_failure("Could not read the HR contacts PDF.", str(exc))
        sys.exit(1)

    # --- Calculate remaining contacts (already-sent lookup via CSV) --------
    try:
        already_sent: Set[str] = csv_logger.already_sent_emails()
    except (OSError, csv.Error) as exc:
        print_failure("Could not read the send log.", str(exc))
        sys.exit(1)

    already_sent_count = int(contacts_df["Email"].str.lower().isin(already_sent).sum())

    # --- Display summary + ask confirmation ---------------------------------
    display_startup_summary(stats, already_sent_count, resume_found_flag, True)

    if stats.remaining - already_sent_count <= 0:
        print_info("No pending contacts to send to. Nothing to do. Exiting.")
        log_event(logger, "Application Exit", "no pending contacts")
        return

    if not confirm_send():
        print_info("User declined to proceed. Exiting without sending anything.")
        log_event(logger, "Application Exit", "user declined")
        return

    email_address, app_password = load_credentials()

    start_row = resolve_start_row(progress)
    total_rows = len(contacts_df)

    sent_count = 0
    failed_count = 0
    skipped_count = 0
    run_start_time = datetime.now()
    preview_shown = False

    try:
        with EmailSender(email_address, app_password, config.RESUME_PATH) as sender:
            for _, row_data in contacts_df.iterrows():
                row_number = int(row_data["SNo"])

                if row_number < start_row:
                    continue

                if progress.emails_sent_today >= config.DAILY_LIMIT:
                    progress.save(config.PROGRESS_FILE)
                    log_event(logger, "Daily Limit Reached", str(progress.emails_sent_today))
                    display_daily_limit_reached(progress.emails_sent_today)
                    return

                company = row_data["Company"] or "N/A"
                hr_name = row_data["Name"] or "Hiring Manager"
                email = row_data["Email"]

                # One-time preview + final confirmation before the very
                # first email of this run is sent.
                if not preview_shown and email.strip().lower() not in already_sent and validate_email(email):
                    preview_shown = True
                    if not preview_first_email(sender, hr_name, company, email):
                        print_info("User declined to proceed after preview. Exiting without sending anything.")
                        log_event(logger, "Application Exit", "user declined after preview")
                        return

                elapsed = (datetime.now() - run_start_time).total_seconds()
                processed = sent_count + failed_count + skipped_count
                average_seconds = (elapsed / processed) if processed else DEFAULT_AVERAGE_SEND_SECONDS
                remaining_contacts = max(total_rows - row_number + 1, 0)
                estimated_remaining = estimate_remaining_time(
                    average_seconds, remaining_contacts, config.BREAK_AFTER, AVERAGE_BREAK_SECONDS
                )

                display_dashboard(
                    row=row_number,
                    total=total_rows,
                    company=company,
                    hr_name=hr_name,
                    email=email,
                    resume_found=True,
                    pdf_found=True,
                    smtp_connected=sender.is_connected,
                    sent_today=progress.emails_sent_today,
                    failed=failed_count,
                    skipped=skipped_count,
                    elapsed_seconds=elapsed,
                    remaining_seconds=estimated_remaining,
                    average_seconds=average_seconds,
                )

                # Duplicate check against the CSV log (authoritative,
                # survives progress.json deletion).
                if email.strip().lower() in already_sent:
                    skipped_count += 1
                    print_skip("Already Sent", "Skipping")
                    csv_logger.log(row_number, company, hr_name, email, "SKIPPED", "Already sent")
                    log_event(logger, "Email Skipped", f"row {row_number}: already sent")
                    continue

                if not validate_email(email):
                    skipped_count += 1
                    print_skip("Invalid Email", "Skipped")
                    csv_logger.log(row_number, company, hr_name, email, "SKIPPED", "Invalid email")
                    log_event(logger, "Email Skipped", f"row {row_number}: invalid email")
                    continue

                try:
                    sender.send_email(hr_name=hr_name, company_name=company, recipient_email=email)
                except EmailSenderError as exc:
                    failed_count += 1
                    print_failure("Email Failed", str(exc))
                    csv_logger.log(row_number, company, hr_name, email, "FAILED", str(exc))
                    log_event(logger, "Email Failed", f"row {row_number}: {exc}")
                    continue

                sent_count += 1
                already_sent.add(email.strip().lower())
                progress.mark_sent(row_number)
                progress.save(config.PROGRESS_FILE)
                csv_logger.log(row_number, company, hr_name, email, "SENT")
                log_event(logger, "Email Sent", f"row {row_number}: {email}")
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
        log_event(logger, "Application Exit", f"SMTP connection error: {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        progress.save(config.PROGRESS_FILE)
        display_interrupted(progress.last_successful_row)
        log_event(logger, "Application Exit", "interrupted by user")
        sys.exit(130)
    except PermissionError as exc:
        print_failure("A required file could not be accessed.", str(exc))
        progress.save(config.PROGRESS_FILE)
        log_event(logger, "Application Exit", f"permission error: {exc}")
        sys.exit(1)
    except (json.JSONDecodeError, csv.Error, UnicodeDecodeError) as exc:
        print_failure("A data file (progress/log) could not be read or written.", str(exc))
        progress.save(config.PROGRESS_FILE)
        log_event(logger, "Application Exit", f"data file error: {exc}")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001 - top-level safety net
        traceback.print_exc()
        log_exception(logger, exc)
        progress.save(config.PROGRESS_FILE)
        print_failure("An unexpected error occurred. Progress has been saved.")
        log_event(logger, "Application Exit", f"unexpected error: {exc}")
        sys.exit(1)

    execution_seconds = (datetime.now() - run_start_time).total_seconds()
    display_final_summary(total_rows, sent_count, failed_count, skipped_count, execution_seconds)
    log_event(logger, "Application Exit", "completed normally")


if __name__ == "__main__":
    run()