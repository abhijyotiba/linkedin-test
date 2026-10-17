"""Email delivery over SMTP (stdlib). Set SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM. Optional."""
import os
import smtplib
from email.message import EmailMessage


def configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_FROM"))


def send(to: str, subject: str, html: str) -> None:
    host, port = os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587"))
    user, pw, sender = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS"), os.environ["SMTP_FROM"]
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, to, subject
    msg.set_content("This email is HTML; open it in a mail client that renders HTML.")
    msg.add_alternative(html, subtype="html")
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30) as s:
            if user:
                s.login(user, pw or "")
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls()
            if user:
                s.login(user, pw or "")
            s.send_message(msg)
