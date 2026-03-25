from __future__ import annotations

from email.message import EmailMessage
import smtplib

import httpx

from ..config import get_settings

settings = get_settings()


class NotificationService:
    def send_telegram_alert(self, text: str) -> None:
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            return
        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
        with httpx.Client(timeout=5.0) as client:
            client.post(url, json={"chat_id": settings.telegram_chat_id, "text": text})

    def send_email_alert(self, subject: str, body: str) -> None:
        if not settings.smtp_host or not settings.smtp_to:
            return
        msg = EmailMessage()
        msg["From"] = settings.smtp_from or settings.smtp_username
        msg["To"] = settings.smtp_to
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            if settings.smtp_username and settings.smtp_password:
                server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(msg)

    def send_risk_alert(self, user_id: int, reason: str) -> dict[str, str]:
        text = f"[A1phquest Risk Alert] user_id={user_id}, reason={reason}"
        return self._send_dual_channel_alert(
            subject="A1phquest Risk Alert",
            text=text,
        )

    def send_security_alert(self, user_id: int, title: str, details: str) -> dict[str, str]:
        text = f"[A1phquest Security Alert] user_id={user_id}, title={title}, details={details}"
        return self._send_dual_channel_alert(
            subject="A1phquest Security Alert",
            text=text,
        )

    def _send_dual_channel_alert(self, *, subject: str, text: str) -> dict[str, str]:
        """
        Return per-channel delivery status so callers can persist alert traceability
        in audit logs even when one channel fails.
        """
        result: dict[str, str] = {}
        try:
            self.send_telegram_alert(text)
            result["telegram"] = "sent"
        except Exception as exc:
            result["telegram"] = self._format_error(exc)
        try:
            self.send_email_alert(subject, text)
            result["email"] = "sent"
        except Exception as exc:
            result["email"] = self._format_error(exc)
        return result

    def _format_error(self, exc: Exception) -> str:
        name = exc.__class__.__name__
        message = str(exc).strip()
        if not message:
            return f"failed:{name}"
        safe_message = message[:120]
        return f"failed:{name}:{safe_message}"
