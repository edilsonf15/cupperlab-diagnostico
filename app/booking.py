"""
Agendamiento propio de Cupperlab (sin dependencias de pago): genera huecos de
30 min en horario laboral y una invitacion de calendario (.ics) que Gmail/Google
Calendar anaden solos al calendario del equipo Y del cliente. Tambien crea el
enlace 'Anadir a Google Calendar' de 1 clic como respaldo.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
    _TZ = ZoneInfo(os.getenv("BOOK_TZ", "Europe/Madrid"))
except Exception:  # noqa: BLE001
    _TZ = timezone.utc

SLOT_MIN = 30
# Horario laboral configurable (hora local). Por defecto 10:00-18:00, L-V.
_HOUR_START = int(os.getenv("BOOK_HOUR_START", "10"))
_HOUR_END = int(os.getenv("BOOK_HOUR_END", "18"))
_DAYS_AHEAD = int(os.getenv("BOOK_DAYS_AHEAD", "12"))
_MEET_TITLE = os.getenv("BOOK_TITLE", "Diagnostico Cupperlab (30 min)")

_DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
_MESES = ["", "ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def available_days() -> list[dict]:
    """Proximos dias laborables con sus huecos de 30 min (hora local Madrid)."""
    now = datetime.now(_TZ)
    out = []
    added = 0
    i = 0
    while added < 7 and i < _DAYS_AHEAD + 10:
        day = (now + timedelta(days=i)).date()
        i += 1
        if day.weekday() >= 5:      # sabado/domingo fuera
            continue
        slots = []
        for h in range(_HOUR_START, _HOUR_END):
            for mnt in (0, 30):
                start = datetime(day.year, day.month, day.day, h, mnt, tzinfo=_TZ)
                if start <= now + timedelta(hours=2):   # margen de 2h
                    continue
                slots.append({"value": start.strftime("%Y-%m-%dT%H:%M"),
                              "label": start.strftime("%H:%M")})
        if slots:
            out.append({
                "label": f"{_DIAS[day.weekday()]} {day.day} {_MESES[day.month]}",
                "short": f"{day.day}/{day.month:02d}",
                "slots": slots,
            })
            added += 1
    return out


def _fmt_ics(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _parse_slot(value: str) -> datetime | None:
    try:
        naive = datetime.strptime(value[:16], "%Y-%m-%dT%H:%M")
        return naive.replace(tzinfo=_TZ)
    except Exception:  # noqa: BLE001
        return None


def build_invite(slot_value: str, client_name: str, client_email: str,
                 organizer_email: str, phone: str, note: str = "") -> dict | None:
    """Devuelve {ics, gcal_link, when_txt, start} para el hueco elegido."""
    start = _parse_slot(slot_value)
    if not start:
        return None
    end = start + timedelta(minutes=SLOT_MIN)
    uid = f"{uuid.uuid4().hex}@cupperlab.com"
    desc = (f"Reunion de 30 minutos con Cupperlab para revisar tu diagnostico de "
            f"visibilidad en Google y en la IA, y el plan de mejora.")
    if note:
        desc += f" Nota del cliente: {note}"
    desc += f" Telefono Cupperlab: {phone}."
    org = organizer_email or "clientes@cupperlab.com"

    ics = "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Cupperlab//Agenda//ES",
        "CALSCALE:GREGORIAN", "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_fmt_ics(datetime.now(timezone.utc))}",
        f"DTSTART:{_fmt_ics(start)}",
        f"DTEND:{_fmt_ics(end)}",
        f"SUMMARY:{_MEET_TITLE}",
        f"DESCRIPTION:{desc}",
        "LOCATION:Videollamada / Telefono",
        f"ORGANIZER;CN=Cupperlab:mailto:{org}",
        f"ATTENDEE;CN={client_name or 'Cliente'};ROLE=REQ-PARTICIPANT;RSVP=TRUE:mailto:{client_email}",
        f"ATTENDEE;CN=Cupperlab;ROLE=CHAIR:mailto:{org}",
        "STATUS:CONFIRMED", "SEQUENCE:0", "TRANSP:OPAQUE",
        "BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:Reunion Cupperlab",
        "TRIGGER:-PT30M", "END:VALARM",
        "END:VEVENT", "END:VCALENDAR",
    ])

    gcal = ("https://calendar.google.com/calendar/render?action=TEMPLATE"
            f"&text={quote(_MEET_TITLE)}"
            f"&dates={_fmt_ics(start)}/{_fmt_ics(end)}"
            f"&details={quote(desc)}"
            f"&add={quote(client_email)}")

    when_txt = start.strftime("%A %d/%m a las %H:%M")
    # traducimos el dia al espanol
    when_txt = f"{_DIAS[start.weekday()]} {start.day} {_MESES[start.month]} · {start.strftime('%H:%M')}"
    return {"ics": ics, "gcal_link": gcal, "when_txt": when_txt,
            "start": start, "title": _MEET_TITLE}
