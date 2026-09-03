"""
Envio de correos del diagnostico (SMTP). No bloquea el flujo si no hay SMTP
configurado: en ese caso solo registra en consola (util en la maqueta).
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

CUPPERLAB_PHONE = os.getenv("CUPPERLAB_PHONE", "+34 600 000 000")
CUPPERLAB_EMAIL = os.getenv("CUPPERLAB_EMAIL", "soporte@cupperlab.com")
CUPPERLAB_SITE = os.getenv("CUPPERLAB_SITE", "https://cupperlab.com")
CUPPERLAB_CAL = os.getenv("CUPPERLAB_CALENDLY", "")


def smtp_configured() -> bool:
    return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_FROM"))


def _send(to_addr: str, subject: str, html: str, reply_to: str | None = None,
          attachment: bytes | None = None, attachment_name: str = "diagnostico.pdf") -> tuple[bool, str]:
    host = os.getenv("SMTP_HOST", "")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "")
    pwd = os.getenv("SMTP_PASS", "")
    sender = os.getenv("SMTP_FROM", user or CUPPERLAB_EMAIL)
    from_name = os.getenv("SMTP_FROM_NAME", "Cupperlab")

    if not smtp_configured():
        print(f"[email:DRY-RUN] Para: {to_addr} | Asunto: {subject} | adjunto: {bool(attachment)}")
        return False, "SMTP no configurado (modo maqueta)."

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{sender}>"
    msg["To"] = to_addr
    if reply_to:
        msg["Reply-To"] = reply_to
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(html, "html", "utf-8"))
    msg.attach(alt)
    if attachment:
        part = MIMEApplication(attachment, _subtype="pdf")
        part.add_header("Content-Disposition", "attachment", filename=attachment_name)
        msg.attach(part)

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=20) as sv:
                if user:
                    sv.login(user, pwd)
                sv.sendmail(sender, [to_addr], msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=20) as sv:
                sv.ehlo()
                try:
                    sv.starttls(context=ssl.create_default_context())
                    sv.ehlo()
                except smtplib.SMTPException:
                    pass
                if user:
                    sv.login(user, pwd)
                sv.sendmail(sender, [to_addr], msg.as_string())
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        print(f"[email:ERROR] {exc}")
        return False, str(exc)


def send_client_report(to_addr: str, name: str, html_body: str,
                       pdf: bytes | None = None, pdf_name: str = "Diagnostico_Cupperlab.pdf") -> tuple[bool, str]:
    subject = "Tu diagnostico de visibilidad en Google y en la IA"
    return _send(to_addr, subject, html_body, reply_to=CUPPERLAB_EMAIL,
                 attachment=pdf, attachment_name=pdf_name)


def send_lead_notification(lead: dict) -> tuple[bool, str]:
    to_addr = os.getenv("LEAD_INBOX", CUPPERLAB_EMAIL)
    subject = f"[Lead diagnostico] {lead.get('domain','')} — score {lead.get('score','')}/100"
    rows = "".join(
        f"<tr><td style='padding:4px 10px;color:#7b8694'>{k}</td>"
        f"<td style='padding:4px 10px;color:#11151c'><b>{v}</b></td></tr>"
        for k, v in lead.items()
    )
    html = f"""<div style="font-family:Arial,sans-serif">
      <h2 style="color:#11151c">Nuevo lead del diagnostico</h2>
      <table style="border-collapse:collapse;font-size:14px">{rows}</table>
    </div>"""
    return _send(to_addr, subject, html, reply_to=lead.get("email"))
