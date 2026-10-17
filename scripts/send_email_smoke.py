"""One-shot SMTP delivery smoke test for an isolated Railway utility service.

Requires SYNPORA_SMTP_HOST, SYNPORA_SMTP_PORT, SYNPORA_SMTP_USERNAME,
SYNPORA_SMTP_PASSWORD, SYNPORA_SMTP_FROM, and SYNPORA_EMAIL_TEST_RECIPIENT.
Never connect this utility to the production database.
"""
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage


def main() -> int:
    host = os.getenv("SYNPORA_SMTP_HOST", "").strip()
    port_raw = os.getenv("SYNPORA_SMTP_PORT", "587").strip()
    username = os.getenv("SYNPORA_SMTP_USERNAME", "").strip()
    password = os.getenv("SYNPORA_SMTP_PASSWORD", "")
    sender = os.getenv("SYNPORA_SMTP_FROM", "").strip()
    recipient = os.getenv("SYNPORA_EMAIL_TEST_RECIPIENT", "").strip()

    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        port = 0

    if not (host and username and password and sender and recipient and 1 <= port <= 65535):
        print("SMTP delivery smoke test blocked: required configuration is missing or invalid.", file=sys.stderr)
        return 2

    message = EmailMessage()
    message["Subject"] = "SYNPORA – E-Mail-Zustellungstest"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        "Dies ist eine einmalige technische Testnachricht von SYNPORA.\n\n"
        "Sie prueft den tatsaechlichen E-Mail-Versand ueber den konfigurierten SMTP-Dienst. "
        "Es ist keine Aktion erforderlich.\n"
    )

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=10, context=ssl.create_default_context()) as smtp:
                smtp.login(username, password)
                refused = smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=10) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
                smtp.login(username, password)
                refused = smtp.send_message(message)
        if refused:
            print("SMTP delivery smoke test failed: recipient rejected.", file=sys.stderr)
            return 1
        print("SMTP delivery smoke test: message accepted by SMTP server.")
        return 0
    except Exception as exc:
        # Deliberately omit host details, addresses, credentials, and provider response text.
        print("SMTP delivery smoke test failed: " + type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
