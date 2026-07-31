"""Outbound email. SMTP when configured, console log otherwise (dev default).

Domain code calls `send_email(...)` directly for request-path sends, or enqueues
the "send_email" task for background sends.
"""

from __future__ import annotations

import re
from email.message import EmailMessage
from html import unescape

from app.core.config import get_settings
from app.core.logging import log
from app.core.queue import TaskContext, task

logger = log("email")


def _to_text(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"</p>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text).strip()


async def send_email(
    to: str,
    subject: str,
    html: str,
    *,
    reply_to: str | None = None,
    from_override: str | None = None,
    headers: dict[str, str] | None = None,
) -> bool:
    """Returns True if handed to SMTP (or logged in dev). Never raises for
    delivery problems — callers must not fail user requests on email trouble."""
    settings = get_settings()
    if not settings.smtp_host:
        logger.info("email (console) to=%s subject=%r\n%s", to, subject, _to_text(html))
        return True

    message = EmailMessage()
    message["From"] = from_override or settings.email_from
    message["To"] = to
    message["Subject"] = subject
    if reply_to:
        message["Reply-To"] = reply_to
    for key, value in (headers or {}).items():
        message[key] = value
    message.set_content(_to_text(html))
    message.add_alternative(html, subtype="html")

    try:
        import aiosmtplib

        await aiosmtplib.send(
            message,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_password,
            use_tls=settings.smtp_tls,
        )
        return True
    except Exception:
        logger.exception("failed to send email to %s", to)
        return False


@task("send_email")
async def send_email_task(ctx: TaskContext, **kwargs: object) -> None:
    await send_email(**kwargs)  # type: ignore[arg-type]
