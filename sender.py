"""
sender.py
---------
Handles the Gmail SMTP (SSL) connection and construction/sending of
individual application emails with the resume attached.
"""

from __future__ import annotations

import logging
import mimetypes
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

import config

logger = logging.getLogger("email_automation")


class EmailSenderError(Exception):
    """Raised when the email sender cannot connect, authenticate, or send."""


class EmailSender:
    """
    Thin wrapper around smtplib.SMTP_SSL that manages a single persistent
    connection to Gmail for the duration of a sending run, and builds
    outgoing messages with the resume attached.
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

    def connect(self) -> None:
        """
        Open and authenticate the SMTP_SSL connection to Gmail.

        Raises:
            EmailSenderError: If connection or authentication fails.
        """
        self.close()
        try:
            context = ssl.create_default_context()
            self._connection = smtplib.SMTP_SSL(
                config.SMTP_SERVER, config.SMTP_PORT, context=context, timeout=30
            )
            self._connection.login(self.sender_email, self.app_password)
            logger.info("Authenticated with Gmail SMTP as %s", self.sender_email)
        except smtplib.SMTPAuthenticationError as exc:
            raise EmailSenderError(
                "Gmail authentication failed. Verify EMAIL and APP_PASSWORD in .env "
                "and that you are using a 16-character Gmail App Password, not your "
                "regular account password."
            ) from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise EmailSenderError(f"Failed to connect to Gmail SMTP server: {exc}") from exc

    def close(self) -> None:
        """Gracefully close the SMTP connection if it is open."""
        if self._connection is not None:
            try:
                self._connection.quit()
            except (smtplib.SMTPException, OSError):
                pass
            finally:
                self._connection = None

    def __enter__(self) -> "EmailSender":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _build_message(self, hr_name: str, company_name: str, recipient_email: str) -> EmailMessage:
        """
        Build a fully-formed EmailMessage with subject, personalised body,
        and the resume attached.

        Args:
            hr_name: Recipient's name, used to replace {HR_NAME}.
            company_name: Recipient's company, used to replace {COMPANY_NAME}.
            recipient_email: The "To" address.

        Returns:
            A ready-to-send EmailMessage instance.
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
        message.set_content(body)

        mime_type, _ = mimetypes.guess_type(str(self.resume_path))
        maintype, subtype = ("application", "pdf")
        if mime_type:
            maintype, subtype = mime_type.split("/", 1)

        with open(self.resume_path, "rb") as fh:
            attachment_data = fh.read()

        message.add_attachment(
            attachment_data,
            maintype=maintype,
            subtype=subtype,
            filename=self.resume_path.name,
        )
        return message

    def send(self, hr_name: str, company_name: str, recipient_email: str) -> None:
        """
        Send a single personalised application email.

        Args:
            hr_name: Recipient's name to substitute into the template.
            company_name: Recipient's company to substitute into the template.
            recipient_email: The recipient's email address.

        Raises:
            EmailSenderError: If not connected, or if sending fails.
        """
        message = self._build_message(hr_name, company_name, recipient_email)
        last_error: Optional[Exception] = None

        for attempt in range(1, config.SMTP_MAX_RETRIES + 1):
            try:
                if self._connection is None:
                    self.connect()
                if self._connection is None:  # Defensive guard for type checkers.
                    raise EmailSenderError("SMTP connection could not be opened.")
                self._connection.send_message(message)
                return
            except (smtplib.SMTPException, OSError, EmailSenderError) as exc:
                last_error = exc
                self.close()
                if attempt < config.SMTP_MAX_RETRIES:
                    logger.warning(
                        "SMTP attempt %d/%d failed for %s; reconnecting...",
                        attempt,
                        config.SMTP_MAX_RETRIES,
                        recipient_email,
                    )

        raise EmailSenderError(
            f"Failed to send email to {recipient_email} after "
            f"{config.SMTP_MAX_RETRIES} attempts: {last_error}"
        ) from last_error
