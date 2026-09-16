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
from geo_ai import run_ai_geo, run_ai_geo_fast, analyze_content  # noqa: E402
from search import check_google, check_indexation, check_gbp  # noqa: E402
import emailer  # noqa: E402
import report_pdf  # noqa: E402
import perf2  # noqa: E402
import perf as _perf  # noqa: E402
import onpage as _onpage  # noqa: E402
import dims  # noqa: E402

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


@app.middleware("http")
async def _embed_headers(request, call_next):
    """Permite incrustar el diagnóstico DENTRO de cupperlab.com (bloque del CMS),
    sin recuadro. frame-ancestors sustituye a X-Frame-Options."""
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = (
        "frame-ancestors 'self' https://cupperlab.com https://*.cupperlab.com")
    # MutableHeaders (Starlette) no tiene .pop(); usar del con guardia.
    if "x-frame-options" in resp.headers:
        del resp.headers["X-Frame-Options"]
    return resp


app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

_hits: dict[str, list[float]] = defaultdict(list)
_bg_tasks: set = set()
_jobs: dict = {}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Caché de resultado por dominio: garantiza que re-analizar el MISMO sitio da el
# MISMO resultado (fin del "da un % y luego otro"). Se vacía al reiniciar el
# contenedor (deploy), así que tras corregir el motor sí sale un resultado nuevo.
_RESULT_CACHE: dict = {}
_RESULT_TTL = float(os.getenv("RESULT_CACHE_TTL", "1800"))  # 30 min


def _cache_key(u: str, lang: str) -> str:
    d = re.sub(r"^https?://", "", (u or "").strip(), flags=re.I)
    d = d.replace("www.", "").split("/")[0].split("?")[0].split(":")[0].lower()
    return f"{d}|{lang}"


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
        j["progress"] = max(j.get("progress", 0), progress)  # el progreso solo sube
        j["stage"] = stage


async def _progress_ticker(job_id: str, expected: float = 90.0, cap: float = 99.4) -> None:
    """Mueve la barra ACORDE a la duración real del análisis y NUNCA la deja parada.
    En vez de un paso fijo (que se clava si el trabajo dura más), calcula el objetivo
    por TIEMPO transcurrido contra una duración esperada, con curva de desaceleración
    (ease-out). Si el análisis se pasa del tiempo esperado, sigue subiendo de forma
    asintótica (pasos cada vez más pequeños pero SIEMPRE > 0) hacia el tope, así el
    cliente ve movimiento constante y no abandona. Los hitos reales (_set) la adelantan;
    al terminar, salta a 100."""
    start = time.monotonic()
    try:
        while True:
            await asyncio.sleep(0.6)
            j = _jobs.get(job_id)
            if not j or j.get("done"):
                return
            p = float(j.get("progress", 0) or 0)
            el = time.monotonic() - start
            frac = el / max(20.0, expected)
            if frac < 1.0:
                # ease-out: rápido al principio, más lento cerca del final esperado
                eased = 1.0 - (1.0 - frac) ** 1.7
                target = 5.0 + (0.92 * cap - 5.0) * eased
            else:
                # pasado el tiempo esperado: acércate al tope sin llegar nunca (nunca se para)
                target = p + (cap - p) * 0.06
            if target > p:
                j["progress"] = round(min(target, cap), 2)
    except asyncio.CancelledError:  # noqa: PERF203
        return


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
    _ck = _cache_key(url, lang)
    # Cache-hit: mismo dominio analizado hace poco -> devolvemos el MISMO resultado
    # (consistencia total). Igual enviamos el correo/PDF a este nuevo lead.
    _c = _RESULT_CACHE.get(_ck)
    if _c and (time.time() - _c[0]) < _RESULT_TTL:
        try:
            data = json.loads(json.dumps(_c[1])); _ai = _c[2]
            _jobs[job_id].update(result=data, progress=100, stage="Listo", done=True)
            await _build_and_send(data, email, name, lead, _ai)
        except Exception as exc:  # noqa: BLE001
            print(f"[cache:ERROR] {exc}")
        return
    _tick = asyncio.create_task(_progress_ticker(job_id))
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

        # La velocidad (PageSpeed) es lo mas lento: la lanzamos EN PARALELO desde ya.
        # Motor nuevo perf2 (CWV + auditorias, movil+escritorio) + analitica por dispositivo.
        perf_task = asyncio.create_task(perf2.measure(final_url))
        analytics_task = asyncio.create_task(_perf.measure_device(final_url, mobile=True))
        # Ficha de Google Business SIN API (scraping de Maps), en paralelo desde ya.
        try:
            from gbp import check_gbp_scrape  # noqa: PLC0415
            from geo_ai import derive_brand  # noqa: PLC0415
            _brand0 = derive_brand(data.get("meta", {}), domain) or domain
            _place0 = (data.get("meta", {}) or {}).get("country", "")
            gbp_task = asyncio.create_task(check_gbp_scrape(_brand0, _place0, domain))
        except Exception as exc:  # noqa: BLE001
            print(f"[gbp:launch:ERROR] {exc}"); gbp_task = None
        # Auditoria SEO on-page AVANZADA (multi-pagina), en paralelo
        onpage_task = asyncio.create_task(_onpage.audit(final_url))

        # 2) Consulta REAL a la IA (motor OPTIMIZADO: 1 sola busqueda en vivo)
        _set(job_id, 40, "Preguntandole a la IA por tu marca y tu servicio...")
        try:
            ai = await run_ai_geo_fast(domain, data.get("meta", {}), lang)
        except Exception as exc:  # noqa: BLE001
            print(f"[ai:ERROR] {exc}"); ai = None
        ai = ai if isinstance(ai, dict) else None
        # La evaluacion de contenido sale de la MISMA llamada (sin coste extra)
        if ai and isinstance(ai.get("content"), dict):
            data["content_ai"] = ai["content"]
        apply_ai_to_result(data, ai)

        # 2b) Ficha de Google Business SIN gastar IA: primero el scraping de Maps
        # (lanzado en paralelo arriba); si no concluyó, cae a la Places API si hay clave.
        try:
            _gb = None
            if gbp_task is not None:
                try:
                    _gb = await gbp_task
                except Exception as exc:  # noqa: BLE001
                    print(f"[gbp:scrape:ERROR] {exc}"); _gb = None
            if (not _gb or _gb.get("found") is None):   # respaldo por Places API (si está)
                _gb = await check_gbp(
                    (ai.get("brand") if ai else "") or domain,
                    (ai.get("zona") or ai.get("country") or "") if ai else "",
                    domain) or _gb
            geo = data.get("geo_ai")
            if isinstance(_gb, dict) and isinstance(geo, dict) and _gb.get("found") is not None:
                geo["gbp"] = bool(_gb.get("found"))
                if _gb.get("found"):
                    # None = no se pudo leer el nº (distinto de 0 reseñas reales)
                    geo["gbp_reviews_n"] = _gb.get("reviews")
                    if _gb.get("category"):
                        geo["gbp_category"] = _gb.get("category")
                    if _gb.get("rating") is not None:
                        geo["gbp_rating"] = _gb.get("rating")
                else:
                    # No hay ficha: no dejes reseñas/categoría/valoración que la IA hubiera
                    # supuesto (si no, se contradice "sin ficha" con "N reseñas").
                    geo["gbp_reviews_n"] = None
                    geo.pop("gbp_category", None)
                    geo.pop("gbp_rating", None)
        except Exception as exc:  # noqa: BLE001
            print(f"[gbp:ERROR] {exc}")

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
        perf_model = None
        try:
            perf_model = await perf_task
        except Exception as exc:  # noqa: BLE001
            print(f"[perf2:ERROR] {exc}"); perf_model = None
        # Respaldo: si perf2 falla por completo, usa el motor antiguo
        if not perf_model:
            try:
                data["psi_full"] = await fetch_psi_full(final_url)
            except Exception as exc:  # noqa: BLE001
                print(f"[psi-fallback:ERROR] {exc}"); data["psi_full"] = None
        else:
            data["perf2"] = perf_model
            data["psi_full"] = perf2.to_legacy_psi(perf_model)
        # analitica real detectada con el navegador (ajusta el hallazgo)
        try:
            analytics = await analytics_task
            analytics = (analytics or {}).get("analytics") if isinstance(analytics, dict) else None
            if analytics and isinstance(data.get("psi_full"), dict):
                data["psi_full"]["analytics"] = analytics
            apply_analytics(data, analytics)
        except Exception as exc:  # noqa: BLE001
            print(f"[analytics:ERROR] {exc}")

        # Auditoria on-page multi-pagina (ya venia en paralelo)
        try:
            op = await onpage_task
            if isinstance(op, dict):
                op.pop("pages", None)  # aligera el payload de pantalla (issues/totals/score)
                data["onpage"] = op
                # Señales locales/contacto de TODO el sitio (no solo la home): la home
                # a veces no lleva la dirección/mapa, que sí están en /contacto.
                _loc = op.get("local") or {}
                _m = data.get("meta") or {}
                for _k in ("has_phone", "has_address", "has_map", "has_hours",
                           "has_geo", "has_testimonials"):
                    _m[_k] = bool(_m.get(_k)) or bool(_loc.get(_k))
                data["meta"] = _m
        except Exception as exc:  # noqa: BLE001
            print(f"[onpage:ERROR] {exc}"); data["onpage"] = None

        # Recalcula el score con la velocidad real ya incorporada
        try:
            finalize_score(data)
        except Exception as exc:  # noqa: BLE001
            print(f"[score:ERROR] {exc}")

        # Fuente ÚNICA de las 9 dimensiones (misma para pantalla, PDF y correo)
        try:
            data["dims"] = dims.compute(data)
        except Exception as exc:  # noqa: BLE001
            print(f"[dims:ERROR] {exc}"); data["dims"] = []

        # Fuente ÚNICA de los HALLAZGOS agrupados (mismos en pantalla, PDF y correo)
        try:
            import findings as _findings  # noqa: PLC0415
            data["findings"] = _findings.compute(data, lang)
        except Exception as exc:  # noqa: BLE001
            print(f"[findings:ERROR] {exc}"); data["findings"] = []

        # Guarda en caché por dominio: re-analizar da el MISMO resultado.
        try:
            _RESULT_CACHE[_ck] = (time.time(), json.loads(json.dumps(data)), ai)
        except Exception as exc:  # noqa: BLE001
            print(f"[cache:store:ERROR] {exc}")

        # 5) Resultado LISTO para la pantalla (mismos datos que el correo)
        _tick.cancel()
        _set(job_id, 98, "Preparando tu diagnostico...")
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
