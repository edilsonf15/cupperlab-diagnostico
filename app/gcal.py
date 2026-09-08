"""
Fase 2 del agendamiento: disponibilidad REAL leyendo Google Calendar y creando
los eventos automaticamente. Usa una CUENTA DE SERVICIO (sin flujo OAuth de
usuario): el equipo comparte su calendario con el email de la cuenta de servicio.

Se activa SOLO si estan estas variables de entorno:
  GOOGLE_CAL_CREDENTIALS = el JSON de la cuenta de servicio (contenido o ruta)
  GOOGLE_CAL_ID          = el ID del calendario (normalmente el email de Google)

Si no estan, el agendamiento sigue funcionando con huecos de horario fijo.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import httpx

_SCOPES = ["https://www.googleapis.com/auth/calendar"]
_API = "https://www.googleapis.com/calendar/v3"


def cal_id() -> str:
    return os.getenv("GOOGLE_CAL_ID", "").strip()


def enabled() -> bool:
    return bool(os.getenv("GOOGLE_CAL_CREDENTIALS", "").strip() and cal_id())


def _creds():
    raw = os.getenv("GOOGLE_CAL_CREDENTIALS", "").strip()
    if not raw:
        return None
    try:
        from google.oauth2 import service_account  # noqa: PLC0415
        from google.auth.transport.requests import Request  # noqa: PLC0415
        if raw.startswith("{"):
            info = json.loads(raw)
        else:  # ruta a un archivo
            with open(raw, encoding="utf-8") as f:
                info = json.load(f)
        creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
        creds.refresh(Request())
        return creds
    except Exception as exc:  # noqa: BLE001
        print(f"[gcal:CREDS] {exc}")
        return None


def _token() -> str:
    c = _creds()
    return c.token if c else ""


async def busy(time_min: datetime, time_max: datetime) -> list[dict]:
    """Intervalos ocupados del calendario en la ventana (para descartar huecos)."""
    tok = _token()
    if not tok:
        return []
    body = {"timeMin": time_min.astimezone(timezone.utc).isoformat(),
            "timeMax": time_max.astimezone(timezone.utc).isoformat(),
            "items": [{"id": cal_id()}]}
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{_API}/freeBusy", headers={"Authorization": f"Bearer {tok}"},
                             json=body, timeout=15)
            r.raise_for_status()
            cals = r.json().get("calendars", {})
            return (cals.get(cal_id(), {}) or {}).get("busy", []) or []
    except Exception as exc:  # noqa: BLE001
        print(f"[gcal:busy] {exc}")
        return []


async def create_event(start: datetime, end: datetime, summary: str, desc: str,
                       attendee_email: str, attendee_name: str = "") -> bool:
    """Crea el evento en el calendario del equipo (con el cliente como invitado)."""
    tok = _token()
    if not tok:
        return False
    ev = {
        "summary": summary, "description": desc,
        "start": {"dateTime": start.astimezone(timezone.utc).isoformat(), "timeZone": "UTC"},
        "end": {"dateTime": end.astimezone(timezone.utc).isoformat(), "timeZone": "UTC"},
        "attendees": [{"email": attendee_email, "displayName": attendee_name or attendee_email}],
        "reminders": {"useDefault": True},
    }
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{_API}/calendars/{cal_id()}/events",
                             headers={"Authorization": f"Bearer {tok}"},
                             params={"sendUpdates": "all"}, json=ev, timeout=15)
            r.raise_for_status()
            return True
    except Exception as exc:  # noqa: BLE001
        print(f"[gcal:create] {exc}")
        return False
