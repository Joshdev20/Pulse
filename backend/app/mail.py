"""Outbound email.

Only password reset needs to send mail, so this stays deliberately small. With
no SMTP host configured the message is logged instead of sent, which keeps the
app fully usable in development and in a demo deployment that has no mail
provider. The reset flow itself is unaware of the difference.
"""

import logging
import smtplib
from email.message import EmailMessage

from .config import settings

logger = logging.getLogger("pulse.mail")


def send_email(to: str, subject: str, body: str) -> bool:
    """Send ``body`` to ``to``. Returns whether it went out over SMTP.

    Never raises: a mail outage must not turn a password reset request into a
    500, which would also leak that the address exists.
    """
    if not settings.mail_configured:
        logger.warning(
            "SMTP is not configured; email to %s was not sent.\nSubject: %s\n%s",
            to,
            subject,
            body,
        )
        return False

    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            if settings.smtp_starttls:
                server.starttls()
            if settings.smtp_username and settings.smtp_password:
                server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(message)
        return True
    except (OSError, smtplib.SMTPException):
        logger.exception("Failed to send email to %s", to)
        return False


def send_password_reset(to: str, reset_url: str, ttl_minutes: int) -> bool:
    return send_email(
        to,
        "Reset your Pulse password",
        (
            "We received a request to reset your Pulse password.\n\n"
            f"Open this link to choose a new one:\n{reset_url}\n\n"
            f"The link expires in {ttl_minutes} minutes and can only be used once.\n"
            "If you did not ask for this, you can ignore this email.\n"
        ),
    )
