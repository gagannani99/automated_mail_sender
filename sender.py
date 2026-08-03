"""
sender.py
---------
All email-sending logic lives here: the Gmail SMTP (SSL) connection,
message construction with the resume attached, retry-on-failure
handling, and the human-like random delay / break functions (including
their countdown displays, which are rendered via utils.countdown).
"""

from __future__ import annotations

import logging
import mimetypes
import random
import smtplib
import ssl
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

import config
from logger import LOGGER_NAME, log_event, print_info
from utils import countdown

logger = logging.getLogger(LOGGER_NAME)

# SMTP/network exceptions treated as transient and therefore retried.
# ConnectionResetError, ConnectionAbortedError, and TimeoutError are all
# OSError subclasses and would be caught by the trailing OSError anyway;
# they are listed explicitly so _describe_error() can give each one a
# distinct, human-readable message.
_TRANSIENT_ERRORS = (
    smtplib.SMTPConnectError,
    smtplib.SMTPServerDisconnected,
    smtplib.SMTPException,
    TimeoutError,
    ConnectionResetError,
    ConnectionAbortedError,
    OSError,
)


class EmailSenderError(Exception):
    """Raised when the email sender cannot connect, authenticate, or send
    an email after exhausting all configured retries."""


def _describe_error(exc: BaseException) -> str:
    """
    Translate a raw SMTP/network exception into a short, human-readable
    reason string suitable for terminal display and CSV logging.

    Args:
        exc: The caught exception.

    Returns:
        A concise, user-facing description of what went wrong.
    """
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "SMTP Authentication Failed"
    if isinstance(exc, smtplib.SMTPConnectError):
        return "Could Not Connect To SMTP Server"
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return "SMTP Connection Dropped"
    if isinstance(exc, TimeoutError):
        return "Network Timeout"
    if isinstance(exc, ConnectionResetError):
        return "Connection Reset By Server"
    if isinstance(exc, ConnectionAbortedError):
        return "Connection Aborted"
    if isinstance(exc, smtplib.SMTPException):
        return f"SMTP Error: {exc}"
    if isinstance(exc, OSError):
        return f"Network Error: {exc}"
    return str(exc)


class EmailSender:
    """
    Manages a single persistent SMTP_SSL connection to Gmail for an
    entire sending run, builds outgoing messages with the resume
    attached, and provides the human-like pacing helpers (random_delay,
    take_break) used between sends.
    """

    def __init__(self, sender_email: str, app_password: str, resume_path: Path) -> None:
        """
        Args:
            sender_email: The Gmail address to send from.
            app_password: The Gmail App Password (NOT the account password).
            resume_path: Path to the resume PDF to attach to every email.

        Raises:
            FileNotFoundError: If the resume file does not exist.
        """
        if not resume_path.is_file():
            raise FileNotFoundError(f"Resume file not found at: {resume_path}")

        self.sender_email = sender_email
        self.app_password = app_password
        self.resume_path = resume_path
        self._connection: Optional[smtplib.SMTP_SSL] = None
        self.emails_sent_since_break = 0

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------
    def connect_smtp(self) -> None:
        """
        Open and authenticate the SMTP_SSL connection to Gmail. Retries
        up to config.MAX_RETRIES times (waiting config.RETRY_DELAY_SECONDS
        between attempts) on transient connection errors. Authentication
        failures are not retried, since retrying won't fix bad credentials.

        Raises:
            EmailSenderError: If authentication fails, or connection
                fails after all retries are exhausted.
        """
        last_error: Optional[Exception] = None

        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                context = ssl.create_default_context()
                self._connection = smtplib.SMTP_SSL(
                    config.SMTP_SERVER, config.SMTP_PORT, context=context, timeout=30
                )
                self._connection.login(self.sender_email, self.app_password)
                log_event(logger, "SMTP Connected", f"as {self.sender_email}")
                return
            except smtplib.SMTPAuthenticationError as exc:
                raise EmailSenderError(_describe_error(exc)) from exc
            except _TRANSIENT_ERRORS as exc:
                last_error = exc
                reason = _describe_error(exc)
                logger.warning(
                    "SMTP connection attempt %d/%d failed: %s", attempt, config.MAX_RETRIES, reason
                )
                if attempt < config.MAX_RETRIES:
                    print_info(
                        f"Connection attempt {attempt} failed ({reason}). "
                        f"Retrying in {int(config.RETRY_DELAY_SECONDS)} seconds..."
                    )
                    time.sleep(config.RETRY_DELAY_SECONDS)

        raise EmailSenderError(
            f"Failed to connect to Gmail SMTP server after {config.MAX_RETRIES} "
            f"attempts: {_describe_error(last_error) if last_error else 'unknown error'}"
        )

    def disconnect(self) -> None:
        """Gracefully close the SMTP connection if it is open."""
        if self._connection is not None:
            try:
                self._connection.quit()
            except smtplib.SMTPException:
                pass
            finally:
                self._connection = None
                log_event(logger, "SMTP Disconnected")

    def __enter__(self) -> "EmailSender":
        self.connect_smtp()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()

    @property
    def is_connected(self) -> bool:
        """Whether the SMTP connection is currently open."""
        return self._connection is not None

    # ------------------------------------------------------------------
    # Message construction
    # ------------------------------------------------------------------
    def create_email(self, hr_name: str, company_name: str, recipient_email: str) -> EmailMessage:
        """
        Build a personalised EmailMessage (subject, From/To, and body
        with only {HR_NAME} and {COMPANY_NAME} replaced), without the
        resume attached yet.

        Args:
            hr_name: Recipient's name, substituted for {HR_NAME}.
            company_name: Recipient's company, substituted for {COMPANY_NAME}.
            recipient_email: The "To" address.

        Returns:
            An EmailMessage instance with subject, headers, and body set.
        """
        body = config.EMAIL_BODY_TEMPLATE.replace(
            config.PLACEHOLDER_HR_NAME, hr_name
        ).replace(
            config.PLACEHOLDER_COMPANY_NAME, company_name
        )

        message = EmailMessage()
        message["Subject"] = config.EMAIL_SUBJECT
        message["From"] = self.sender_email
        message["To"] = recipient_email
        message.set_content(body, charset="utf-8")
        return message

    def attach_resume(self, message: EmailMessage) -> None:
        """
        Attach the resume PDF to an EmailMessage as a MIME attachment.

        Args:
            message: The EmailMessage to attach the resume to (mutated
                in place).

        Raises:
            PermissionError: If the resume file cannot be read.
        """
        mime_type, _ = mimetypes.guess_type(str(self.resume_path))
        maintype, subtype = ("application", "pdf")
        if mime_type:
            maintype, subtype = mime_type.split("/", 1)

        try:
            with open(self.resume_path, "rb") as fh:
                attachment_data = fh.read()
        except PermissionError as exc:
            raise PermissionError(f"Permission denied while reading resume: {self.resume_path}") from exc

        message.add_attachment(
            attachment_data,
            maintype=maintype,
            subtype=subtype,
            filename=self.resume_path.name,
        )

    # ------------------------------------------------------------------
    # Sending, with automatic reconnect + retry on transient errors
    # ------------------------------------------------------------------
    def send_email(self, hr_name: str, company_name: str, recipient_email: str) -> None:
        """
        Build, attach, and send a single personalised application email.
        Reuses the existing SMTP connection. On a transient network/SMTP
        error, automatically reconnects and retries up to
        config.MAX_RETRIES times (waiting config.RETRY_DELAY_SECONDS
        between attempts) before giving up.

        Args:
            hr_name: Recipient's name to substitute into the template.
            company_name: Recipient's company to substitute into the template.
            recipient_email: The recipient's email address.

        Raises:
            EmailSenderError: If sending fails after all retries.
        """
        message = self.create_email(hr_name, company_name, recipient_email)
        self.attach_resume(message)

        last_error: Optional[Exception] = None

        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                if self._connection is None:
                    self.connect_smtp()
                self._connection.send_message(message)
                return
            except smtplib.SMTPServerDisconnected as exc:
                last_error = exc
                logger.warning(
                    "SMTP connection dropped; reconnecting (attempt %d/%d)...",
                    attempt, config.MAX_RETRIES,
                )
                self._connection = None
                if attempt < config.MAX_RETRIES:
                    time.sleep(config.RETRY_DELAY_SECONDS)
            except _TRANSIENT_ERRORS as exc:
                last_error = exc
                logger.warning(
                    "Transient error sending to %s (attempt %d/%d): %s",
                    recipient_email, attempt, config.MAX_RETRIES, _describe_error(exc),
                )
                if attempt < config.MAX_RETRIES:
                    time.sleep(config.RETRY_DELAY_SECONDS)

        raise EmailSenderError(
            f"{_describe_error(last_error) if last_error else 'Unknown error'} "
            f"(failed after {config.MAX_RETRIES} attempts)"
        )

    # ------------------------------------------------------------------
    # Human-like pacing: random delay between emails, periodic breaks
    # ------------------------------------------------------------------
    @staticmethod
    def random_delay() -> None:
        """
        Sleep for a random duration between config.MIN_DELAY and
        config.MAX_DELAY seconds, displaying a live countdown on a
        single terminal line.
        """
        duration = random.uniform(config.MIN_DELAY, config.MAX_DELAY)
        countdown(int(round(duration)), label="Waiting {time} seconds before next email...")

    @staticmethod
    def take_break() -> None:
        """
        Sleep for a random duration between config.BREAK_MINUTES_MIN and
        config.BREAK_MINUTES_MAX minutes, displaying a banner and a live
        MM:SS countdown. Intended to be called after every
        config.BREAK_AFTER successful sends.
        """
        minutes = random.uniform(config.BREAK_MINUTES_MIN, config.BREAK_MINUTES_MAX)
        total_seconds = int(round(minutes * 60))

        print("=" * 35)
        print("Taking Human-Like Break")
        print("Duration")
        print(f"{minutes:.1f} Minutes")
        print("=" * 35)
        countdown(total_seconds, label="Break Remaining {time}")