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
# Widget incrustable del Horario de citas de Google (disponibilidad real + Meet).
# Se muestra DENTRO de nuestra pagina /agenda con la marca Cupperlab.
GOOGLE_BOOK_EMBED = os.getenv("GOOGLE_BOOK_EMBED",
    "https://calendar.google.com/calendar/appointments/schedules/"
    "AcZssZ2p34ipVMTHQjmCuPGkKCAIWcoNVXoZjvuduNCODtp720N1wXxEV_oAUM2QvKscKmKcWPK_nla8?gv=true")


def smtp_configured() -> bool:
    return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_FROM"))


def _send(to_addr: str, subject: str, html: str, reply_to: str | None = None,
          attachment: bytes | None = None, attachment_name: str = "diagnostico.pdf",
          ics: str | None = None) -> tuple[bool, str]:
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
    if ics:
        # Parte de calendario: Gmail/Google Calendar la reconocen como invitacion
        cal = MIMEText(ics, "calendar", "utf-8")
        cal.replace_header("Content-Type", 'text/calendar; charset="utf-8"; method=REQUEST')
        msg.attach(cal)
        # Ademas como .ics descargable (respaldo universal)
        ib = MIMEApplication(ics.encode("utf-8"), _subtype="ics")
        ib.add_header("Content-Disposition", "attachment", filename="reunion-cupperlab.ics")
        msg.attach(ib)

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
                       pdf: bytes | None = None, pdf_name: str = "Diagnostico_Cupperlab.pdf",
                       lang: str = "es") -> tuple[bool, str]:
    en = str(lang or "es").strip().lower().startswith("en")
    subject = ("Your visibility diagnosis on Google and AI" if en
               else "Tu diagnostico de visibilidad en Google y en la IA")
    return _send(to_addr, subject, html_body, reply_to=CUPPERLAB_EMAIL,
                 attachment=pdf, attachment_name=pdf_name)


def send_booking(client_email: str, client_name: str, inv: dict, phone: str,
                 site_url: str = "") -> tuple[bool, str]:
    """Envia la invitacion de calendario al cliente y al equipo (con .ics)."""
    when = inv.get("when_txt", "")
    gcal = inv.get("gcal_link", "")
    ics = inv.get("ics", "")
    # 1) al cliente: confirmacion + invitacion (correo branded)
    html_c = f"""<div style="margin:0;background:#eef2f6;padding:28px 12px;font-family:'Helvetica Neue',Arial,sans-serif">
    <table role="presentation" width="520" cellpadding="0" cellspacing="0" align="center" style="width:520px;max-width:100%;margin:auto;background:#fff;border-radius:18px;overflow:hidden;box-shadow:0 10px 40px rgba(14,19,25,.10)">
      <tr><td style="height:6px;background:linear-gradient(90deg,#1cbce4,#0f9bc2 45%,#f46434);font-size:1px;line-height:6px">&nbsp;</td></tr>
      <tr><td style="padding:34px 36px 10px;text-align:center">
        <div style="font-size:44px;line-height:1">✅</div>
        <div style="font-family:Georgia,serif;font-size:24px;color:#0e1319;font-weight:bold;margin-top:10px">Reunion confirmada</div>
        <div style="color:#5a6675;font-size:14px;margin-top:8px">Hola <b style="color:#0e1319">{client_name or ''}</b>, tu sesion de 30 minutos con Cupperlab queda para:</div>
      </td></tr>
      <tr><td style="padding:6px 36px 4px">
        <table width="100%" cellpadding="0" cellspacing="0" style="background:#f6f9fb;border:1px solid #e6ebf0;border-radius:14px"><tr><td style="padding:18px 22px;text-align:center">
          <div style="font-family:'Courier New',monospace;font-size:10px;letter-spacing:1.5px;color:#0f9bc2">TU CITA · 30 MIN</div>
          <div style="font-size:20px;color:#0e1319;font-weight:bold;margin-top:6px">{when}</div>
        </td></tr></table>
      </td></tr>
      <tr><td style="padding:16px 36px 4px;text-align:center">
        <div style="color:#5a6675;font-size:13.5px;line-height:1.6">Te adjuntamos la invitacion: acepta para que se anada sola a tu calendario. O anadela con un clic:</div>
        <div style="margin:16px 0 6px"><a href="{gcal}" style="display:inline-block;background:#f46434;color:#fff;text-decoration:none;font-weight:bold;padding:13px 26px;border-radius:11px;font-size:14px">📅 Anadir a Google Calendar</a></div>
      </td></tr>
      <tr><td style="padding:14px 36px 30px;text-align:center;border-top:1px solid #eef1f4;margin-top:12px">
        <div style="color:#7b8694;font-size:12.5px;line-height:1.6">¿Necesitas cambiarla? Responde a este correo o llama al <b style="color:#0e1319">{phone}</b>.</div>
        <div style="color:#0e1319;font-family:Georgia,serif;font-size:15px;margin-top:14px">Mejoramos tu rentabilidad.</div>
      </td></tr>
    </table></div>"""
    ok1, _ = _send(client_email, f"Tu reunion con Cupperlab · {when}", html_c,
                   reply_to=CUPPERLAB_EMAIL, ics=ics)
    # 2) al equipo: aviso + misma invitacion
    team = os.getenv("LEAD_INBOX", CUPPERLAB_EMAIL)
    html_t = f"""<div style="font-family:Arial,sans-serif;color:#283038">
      <h2 style="color:#0e1319">Nueva reunion agendada</h2>
      <p><b>{when}</b> · con <b>{client_name}</b> ({client_email})</p>
      <p><a href="{gcal}">Anadir a Google Calendar</a></p></div>"""
    _send(team, f"[Reunion] {when} — {client_name}", html_t, reply_to=client_email, ics=ics)
    return ok1, "ok"


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
