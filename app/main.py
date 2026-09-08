"""
Landing de diagnostico rapido SEO + GEO de Cupperlab (FastAPI).

Flujo: el cliente envia su web + datos -> analisis en vivo (<50 s) -> resultado
en pantalla + correo al cliente con el resumen y el telefono de Cupperlab +
aviso de lead al equipo. Pensado para dockerizar y desplegar en Plesk.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
# Carga .env desde la raíz del proyecto (padre de /app), sin depender del cwd
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

load_dotenv()

from analyzer import (analyze, result_to_dict, normalize_url, apply_ai_to_result,  # noqa: E402
                      apply_analytics, fetch_psi_full, finalize_score)
from geo_ai import run_ai_geo  # noqa: E402
from search import check_google, check_indexation  # noqa: E402
import emailer  # noqa: E402
import report_pdf  # noqa: E402

BASE = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE.parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
LEADS_FILE = DATA_DIR / "leads.jsonl"
REPORTS_DIR = DATA_DIR / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
# En el correo los enlaces DEBEN ser absolutos. Si la variable no apunta a un
# dominio publico (vacia o localhost), usamos el dominio real de produccion.
if not PUBLIC_BASE_URL or "localhost" in PUBLIC_BASE_URL or "127.0.0.1" in PUBLIC_BASE_URL:
    PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL_FALLBACK", "https://analisis.cupperlab.com").rstrip("/")

RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_HOUR", "30"))
ANALYSIS_HARD_TIMEOUT = float(os.getenv("ANALYSIS_HARD_TIMEOUT", "50"))

app = FastAPI(title="Cupperlab · Diagnostico SEO + GEO", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

_hits: dict[str, list[float]] = defaultdict(list)
_bg_tasks: set = set()
_jobs: dict = {}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _client_ip(req: Request) -> str:
    fwd = req.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return req.client.host if req.client else "unknown"


def _rate_ok(ip: str) -> bool:
    now = time.time()
    window = now - 3600
    _hits[ip] = [t for t in _hits[ip] if t > window]
    if len(_hits[ip]) >= RATE_LIMIT:
        return False
    _hits[ip].append(now)
    return True


def _ctx() -> dict:
    return {
        "phone": emailer.CUPPERLAB_PHONE,
        "email": emailer.CUPPERLAB_EMAIL,
        "site": emailer.CUPPERLAB_SITE,
        "calendly": emailer.CUPPERLAB_CAL,
        "agenda_url": (PUBLIC_BASE_URL + "/agenda") if PUBLIC_BASE_URL else "/agenda",
        "year": datetime.now().year,
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, **_ctx()})


@app.get("/agenda", response_class=HTMLResponse)
async def agenda(request: Request):
    # Envuelve el Horario de citas de Google (disponibilidad real + Meet) con la
    # marca Cupperlab. Si no hay embed configurado, usa nuestro selector propio.
    return templates.TemplateResponse("agenda.html", {
        "request": request, "book_embed": emailer.GOOGLE_BOOK_EMBED, **_ctx()})


@app.post("/api/book")
async def api_book(request: Request):
    import booking  # noqa: PLC0415
    if not _rate_ok(_client_ip(request)):
        return JSONResponse({"error": "Demasiadas solicitudes. Intentalo mas tarde."}, status_code=429)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "Solicitud invalida."}, status_code=400)
    name = (body.get("name") or "").strip()[:80]
    email = (body.get("email") or "").strip()[:120]
    slot = (body.get("slot") or "").strip()
    domain = (body.get("domain") or "").strip()[:120]
    if not name:
        return JSONResponse({"error": "Falta tu nombre."}, status_code=400)
    if not EMAIL_RE.match(email):
        return JSONResponse({"error": "Necesitamos un correo valido."}, status_code=400)
    note = f"web analizada: {domain}" if domain else ""
    inv = booking.build_invite(slot, name, email, emailer.CUPPERLAB_EMAIL, emailer.CUPPERLAB_PHONE, note)
    if not inv:
        return JSONResponse({"error": "Elige un hueco valido."}, status_code=400)
    # Fase 2: si el Google Calendar esta conectado, crea el evento de verdad
    try:
        import gcal  # noqa: PLC0415
        if gcal.enabled():
            from datetime import timedelta as _td  # noqa: PLC0415
            start = inv["start"]
            await gcal.create_event(start, start + _td(minutes=30), inv["title"],
                                    inv.get("desc", note), email, name)
    except Exception as exc:  # noqa: BLE001
        print(f"[book:gcal] {exc}")
    try:
        await asyncio.to_thread(emailer.send_booking, email, name, inv, emailer.CUPPERLAB_PHONE)
    except Exception as exc:  # noqa: BLE001
        print(f"[book:ERROR] {exc}")
    return JSONResponse({"ok": True, "when": inv["when_txt"], "gcal": inv["gcal_link"]})


@app.get("/reporte/{token}.pdf")
async def reporte(token: str):
    # token seguro (hex). Sirve el PDF alojado del diagnostico.
    if not re.fullmatch(r"[a-f0-9]{16,40}", token or ""):
        return JSONResponse({"error": "no encontrado"}, status_code=404)
    p = REPORTS_DIR / f"{token}.pdf"
    if not p.exists():
        return JSONResponse({"error": "no encontrado"}, status_code=404)
    return FileResponse(str(p), media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="Diagnostico_Cupperlab.pdf"'})


@app.get("/salud")
async def salud():
    return {"ok": True, "smtp": emailer.smtp_configured(), "ts": datetime.now(timezone.utc).isoformat()}


def _set(job_id: str, progress: int, stage: str) -> None:
    j = _jobs.get(job_id)
    if j:
        j["progress"] = progress
        j["stage"] = stage


@app.post("/api/analyze")
async def api_analyze(request: Request):
    ip = _client_ip(request)
    if not _rate_ok(ip):
        return JSONResponse(
            {"error": "Has alcanzado el limite de analisis por hora. Escribenos y lo hacemos contigo."},
            status_code=429,
        )
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}

    url = normalize_url(body.get("url", ""))
    name = (body.get("name") or "").strip()[:80]
    email = (body.get("email") or "").strip()[:120]
    phone = (body.get("phone") or "").strip()[:40]
    company = (body.get("company") or "").strip()[:120]
    lang = "en" if (body.get("lang") or "es").strip().lower().startswith("en") else "es"

    if not url:
        return JSONResponse({"error": "Escribe la direccion de tu web."}, status_code=400)
    if not EMAIL_RE.match(email):
        return JSONResponse({"error": "Escribe un correo valido para enviarte el diagnostico."}, status_code=400)

    job_id = uuid.uuid4().hex
    _jobs[job_id] = {"progress": 3, "stage": "Conectando con tu web...", "done": False,
                     "error": None, "result": None, "ts": time.time()}
    lead = {"ts": datetime.now(timezone.utc).isoformat(), "name": name, "email": email,
            "phone": phone, "company": company, "ip": ip, "lang": lang}
    task = asyncio.create_task(_run_job(job_id, url, email, name, lead, lang))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return JSONResponse({"job_id": job_id})


@app.get("/api/status/{job_id}")
async def api_status(job_id: str):
    j = _jobs.get(job_id)
    if not j:
        return JSONResponse({"error": "Analisis no encontrado."}, status_code=404)
    return JSONResponse({
        "progress": j["progress"], "stage": j["stage"], "done": j["done"],
        "error": j["error"], "result": j["result"], "contact": _ctx() if j["done"] else None,
    })


async def _run_job(job_id: str, url: str, email: str, name: str, lead: dict, lang: str = "es") -> None:
    import i18n  # noqa: PLC0415
    i18n.set_lang(lang)  # idioma del analisis (lo leen analyzer, geo_ai, report_pdf)
    """Analisis REAL por etapas, con progreso. El mismo resultado que ve la pantalla
    es el que va al correo (mismo dict de datos)."""
    try:
        # 1) SEO + salud tecnica (rastreo en vivo)
        _set(job_id, 8, "Revisando SEO y salud tecnica...")
        res = await analyze(url)
        if not res.reachable:
            _jobs[job_id].update(done=True, error=res.error or "No pudimos acceder a la web.")
            return
        data = result_to_dict(res)
        data["lang"] = lang
        domain = data.get("domain", "")
        final_url = data.get("final_url") or f"https://{domain}"
        _set(job_id, 26, "Analizando titulos, textos e imagenes...")

        # La velocidad (PageSpeed) es lo mas lento: la lanzamos EN PARALELO desde ya
        psi_task = asyncio.create_task(fetch_psi_full(final_url))

        # 2) Consulta REAL a la IA
        _set(job_id, 40, "Preguntandole a la IA por tu marca y tu servicio...")
        try:
            ai = await run_ai_geo(domain, data.get("meta", {}))
        except Exception as exc:  # noqa: BLE001
            print(f"[ai:ERROR] {exc}"); ai = None
        ai = ai if isinstance(ai, dict) else None
        apply_ai_to_result(data, ai)

        # 3) Competencia / posicion (mientras la velocidad sigue midiendo en paralelo)
        _set(job_id, 58, "Buscando a tu competencia...")
        try:
            if ai and ai.get("category_queries"):
                data["google"] = await check_google(
                    domain, ai.get("brand", domain), ai["category_queries"], ai.get("gl", "es"))
            else:
                data["google"] = None
        except Exception as exc:  # noqa: BLE001
            print(f"[search:ERROR] {exc}"); data["google"] = None
        # Indexacion real con site:dominio + 404 sobre lo INDEXADO en Google
        try:
            data["indexation"] = await check_indexation(
                domain, (data.get("signals") or {}).get("sitemap_total", 0),
                ai.get("gl", "es") if ai else "es")
            ix = data.get("indexation") or {}
            if ix.get("broken_indexed"):
                ex = ", ".join(b["url"] for b in ix["broken_indexed"][:2])
                data.setdefault("findings_improve", []).insert(0, {
                    "title": f"{len(ix['broken_indexed'])} pagina(s) INDEXADA(s) en Google dan error 404",
                    "detail": f"Google tiene indexadas paginas que ya no existen: {ex}. "
                              "Restan confianza y desperdician rastreo; hay que redirigirlas o recuperarlas.",
                    "severity": "alto"})
        except Exception as exc:  # noqa: BLE001
            print(f"[index:ERROR] {exc}"); data["indexation"] = None

        # 4) Recoge la velocidad (ya venia corriendo en paralelo)
        _set(job_id, 74, "Midiendo la velocidad en movil y escritorio...")
        try:
            data["psi_full"] = await psi_task
        except Exception as exc:  # noqa: BLE001
            print(f"[psi:ERROR] {exc}"); data["psi_full"] = None
        # analitica real detectada con el navegador (ajusta el hallazgo)
        try:
            if isinstance(data.get("psi_full"), dict):
                apply_analytics(data, data["psi_full"].get("analytics"))
        except Exception as exc:  # noqa: BLE001
            print(f"[analytics:ERROR] {exc}")

        # Recalcula el score con la velocidad real ya incorporada
        try:
            finalize_score(data)
        except Exception as exc:  # noqa: BLE001
            print(f"[score:ERROR] {exc}")

        # 5) Resultado LISTO para la pantalla (mismos datos que el correo)
        _set(job_id, 96, "Preparando tu diagnostico...")
        _jobs[job_id].update(result=data, progress=100, stage="Listo", done=True)

        # 6) PDF + correo (mismo dict de datos) — no bloquea la pantalla
        await _build_and_send(data, email, name, lead, ai)
    except Exception as exc:  # noqa: BLE001
        print(f"[job:ERROR] {exc}")
        if job_id in _jobs and not _jobs[job_id]["done"]:
            _jobs[job_id].update(done=True, error="No pudimos completar el analisis. Intentalo de nuevo.")


async def _build_and_send(data: dict, email: str, name: str, lead: dict, ai) -> None:
    domain = data.get("domain", "")
    # 1) PDF primero (para poder alojarlo y enlazarlo en el correo)
    try:
        pdf_bytes = await asyncio.to_thread(report_pdf.build_pdf, data, _ctx(), name or domain)
    except Exception as exc:  # noqa: BLE001
        print(f"[pdf:ERROR] {exc}"); pdf_bytes = None

    # 2) Aloja el PDF y arma el enlace publico "Abrelo aqui"
    report_url = ""
    if pdf_bytes:
        try:
            token = uuid.uuid4().hex
            (REPORTS_DIR / f"{token}.pdf").write_bytes(pdf_bytes)
            report_url = f"{PUBLIC_BASE_URL}/reporte/{token}.pdf" if PUBLIC_BASE_URL else f"/reporte/{token}.pdf"
        except Exception as exc:  # noqa: BLE001
            print(f"[report-save:ERROR] {exc}")

    # 3) Correo con el enlace + el adjunto. El boton de agendar lleva al cliente
    #    YA identificado (nombre, correo y su web analizada) a /agenda.
    from urllib.parse import urlencode  # noqa: PLC0415
    ctxd = _ctx()
    # El boton lleva a NUESTRA pagina /agenda (marca Cupperlab) que dentro incrusta
    # el horario real de Google. Lleva al cliente ya identificado.
    book_url = ctxd["agenda_url"] + "?" + urlencode({"n": name or "", "e": email or "", "d": domain or ""})
    email_html = templates.get_template("email_report.html").render(
        r=data, name=name or domain, report_url=report_url, book_url=book_url,
        lang=data.get("lang", "es"), **ctxd)
    _lang = data.get("lang", "es")
    _stub = "Cupperlab_SEO_GEO_Report" if _lang == "en" else "Diagnostico_Cupperlab"
    pdf_name = f"{_stub}_{domain.replace('.', '_')}.pdf"
    email_sent = False
    try:
        email_sent, _ = await asyncio.to_thread(
            emailer.send_client_report, email, name, email_html, pdf_bytes, pdf_name, _lang)
    except Exception as exc:  # noqa: BLE001
        print(f"[email:ERROR] {exc}")
    lead.update(domain=domain, url=data.get("final_url"), score=data.get("score"),
                grade=data.get("grade"), email_sent=email_sent,
                ai_knows=bool(ai and ai.get("knows_brand")) if ai else None)
    try:
        with LEADS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(lead, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        print(f"[lead:ERROR] {exc}")
    try:
        await asyncio.to_thread(emailer.send_lead_notification, lead)
    except Exception as exc:  # noqa: BLE001
        print(f"[lead-mail:ERROR] {exc}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)
