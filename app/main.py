"""
Landing de diagnóstico SEO + GEO de Cupperlab (FastAPI) — motor v2.

Flujo: el cliente envía su web + datos -> job en SQLite -> pipeline por etapas con
presupuesto de tiempo (tope global ANALYSIS_HARD_TIMEOUT, 120 s) -> resultado en
pantalla -> PDF + correo + lead en segundo plano.

Principios v2 (ver ARQUITECTURA.md):
  - Lo que es un HECHO se mide con datos (Places, Serper, PageSpeed, crawl). A la IA
    solo se le pregunta lo que solo la IA puede responder (test GEO).
  - Ningún módulo desaparece en silencio: lo que no se pudo medir va en
    `result.not_measured` con el motivo, y la dimensión se pinta en gris.
  - Un LLM nunca pisa un dato medido (país, ficha, categoría).
  - Caché 24 h por dominio+idioma+versión del motor (SQLite, sobrevive deploys).
El contrato de la API (job_id / progress / done / result) NO cambia.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402

load_dotenv()

from analyzer import (analyze, result_to_dict, normalize_url, apply_ai_to_result,  # noqa: E402
                      apply_analytics, finalize_score)
import geo_ai  # noqa: E402
import places  # noqa: E402
import serp  # noqa: E402
import store  # noqa: E402
import emailer  # noqa: E402
import report_pdf  # noqa: E402
import perf2  # noqa: E402
import perf as _perf  # noqa: E402
import onpage as _onpage  # noqa: E402
import dims  # noqa: E402
import findings as _findings  # noqa: E402
import i18n  # noqa: E402
from i18n import L  # noqa: E402

BASE = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE.parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
LEADS_FILE = DATA_DIR / "leads.jsonl"
REPORTS_DIR = DATA_DIR / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
if not PUBLIC_BASE_URL or "localhost" in PUBLIC_BASE_URL or "127.0.0.1" in PUBLIC_BASE_URL:
    PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL_FALLBACK", "https://analisis.cupperlab.com").rstrip("/")

RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_HOUR", "30"))
HARD_TIMEOUT = float(os.getenv("ANALYSIS_HARD_TIMEOUT", "120"))
B_CRAWL = float(os.getenv("STAGE_BUDGET_CRAWL", "50"))       # home + render + señales técnicas
B_ONPAGE = float(os.getenv("STAGE_BUDGET_ONPAGE", "65"))     # rastreo multipágina (corre en paralelo)
B_PSI = float(os.getenv("STAGE_BUDGET_PSI", "60")) + 8
B_PLACES = float(os.getenv("STAGE_BUDGET_PLACES", "10"))
B_SERP = float(os.getenv("STAGE_BUDGET_SERP", "20"))
B_AI = float(os.getenv("AI_GEO_BUDGET", "45")) + 25           # P1 + ronda + P3
B_ANALYTICS = float(os.getenv("STAGE_BUDGET_ANALYTICS", "25"))
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT_JOBS", "3"))
_sem = asyncio.Semaphore(MAX_CONCURRENT)

app = FastAPI(title="Cupperlab · Diagnostico SEO + GEO", docs_url=None, redoc_url=None)


@app.middleware("http")
async def _embed_headers(request, call_next):
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = (
        "frame-ancestors 'self' https://cupperlab.com https://*.cupperlab.com")
    if "x-frame-options" in resp.headers:
        del resp.headers["X-Frame-Options"]
    return resp


app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")
_bg_tasks: set = set()
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ANALYSES_SINCE_CLEANUP = 0


@app.on_event("startup")
async def _startup():
    store.cleanup()


def _domain_key(u: str) -> str:
    d = re.sub(r"^https?://", "", (u or "").strip(), flags=re.I)
    return d.replace("www.", "").split("/")[0].split("?")[0].split(":")[0].lower()


def _client_ip(req: Request) -> str:
    fwd = req.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return req.client.host if req.client else "unknown"


def _ctx() -> dict:
    return {
        "phone": emailer.CUPPERLAB_PHONE, "email": emailer.CUPPERLAB_EMAIL,
        "site": emailer.CUPPERLAB_SITE, "calendly": emailer.CUPPERLAB_CAL,
        "agenda_url": (PUBLIC_BASE_URL + "/agenda") if PUBLIC_BASE_URL else "/agenda",
        "year": datetime.now().year,
    }


# ------------------------------------------------------------------ rutas
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, **_ctx()})


@app.get("/agenda", response_class=HTMLResponse)
async def agenda(request: Request):
    return templates.TemplateResponse("agenda.html", {
        "request": request, "book_embed": emailer.GOOGLE_BOOK_EMBED, **_ctx()})


@app.post("/api/book")
async def api_book(request: Request):
    import booking  # noqa: PLC0415
    if not store.rate_ok(_client_ip(request), RATE_LIMIT):
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
    if not re.fullmatch(r"[a-f0-9]{16,40}", token or ""):
        return JSONResponse({"error": "no encontrado"}, status_code=404)
    p = REPORTS_DIR / f"{token}.pdf"
    if not p.exists():
        return JSONResponse({"error": "no encontrado"}, status_code=404)
    return FileResponse(str(p), media_type="application/pdf",
                        headers={"Content-Disposition": 'inline; filename="Diagnostico_Cupperlab.pdf"'})


@app.get("/salud")
async def salud():
    return {"ok": True, "engine": store.ENGINE_VERSION, "smtp": emailer.smtp_configured(),
            "places": bool(places._key()), "serper": serp.enabled(),
            "ai_engines": [e["name"] for e in geo_ai._engines()],
            "psi": bool(os.getenv("GOOGLE_PSI_API_KEY", "").strip()),
            "ts": datetime.now(timezone.utc).isoformat()}


@app.post("/api/analyze")
async def api_analyze(request: Request):
    ip = _client_ip(request)
    if not store.rate_ok(ip, RATE_LIMIT):
        return JSONResponse(
            {"error": "Has alcanzado el limite de analisis por hora. Escribenos y lo hacemos contigo."},
            status_code=429)
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
    # País elegido por el usuario en el formulario (código ISO de 2 letras). Es
    # AUTORITATIVO: el análisis de IA/competencia se hace en ESE país (fin de adivinar).
    forced_cc = re.sub(r"[^a-z]", "", (body.get("country") or "").strip().lower())[:2]
    if forced_cc and places.country_name(forced_cc).upper() == forced_cc.upper():
        forced_cc = ""   # código de país desconocido: se ignora (se detecta como antes)
    if not url:
        return JSONResponse({"error": "Escribe la direccion de tu web."}, status_code=400)
    if not EMAIL_RE.match(email):
        return JSONResponse({"error": "Escribe un correo valido para enviarte el diagnostico."}, status_code=400)

    job_id = uuid.uuid4().hex
    store.job_create(job_id, url, lang, "Conectando con tu web...")
    lead = {"ts": datetime.now(timezone.utc).isoformat(), "name": name, "email": email,
            "phone": phone, "company": company, "ip": ip, "lang": lang, "country": forced_cc}
    task = asyncio.create_task(_run_job(job_id, url, email, name, lead, lang, forced_cc))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return JSONResponse({"job_id": job_id})


@app.get("/api/status/{job_id}")
async def api_status(job_id: str):
    j = store.job_get(job_id)
    if not j:
        return JSONResponse({"error": "Analisis no encontrado."}, status_code=404)
    return JSONResponse({"progress": j["progress"], "stage": j["stage"], "done": j["done"],
                         "error": j["error"], "result": j["result"],
                         "contact": _ctx() if j["done"] else None})


# --------------------------------------------------------------- progreso
async def _progress_ticker(job_id: str, expected: float = 75.0, cap: float = 99.4) -> None:
    """La barra avanza por TIEMPO (ease-out) y nunca se para; los hitos reales la adelantan."""
    start = time.monotonic()
    try:
        while True:
            await asyncio.sleep(0.7)
            j = store.job_get(job_id)
            if not j or j["done"]:
                return
            p = float(j["progress"] or 0)
            frac = (time.monotonic() - start) / max(20.0, expected)
            if frac < 1.0:
                target = 5.0 + (0.92 * cap - 5.0) * (1.0 - (1.0 - frac) ** 1.7)
            else:
                target = p + (cap - p) * 0.06
            if target > p:
                store.job_progress(job_id, round(min(target, cap), 2))
    except asyncio.CancelledError:  # noqa: PERF203
        return


async def _stage(coro, budget: float, name: str):
    """Ejecuta una etapa con presupuesto. Devuelve (valor|None, nota_error|'')."""
    try:
        return await asyncio.wait_for(coro, timeout=budget), ""
    except asyncio.TimeoutError:
        print(f"[{name}] timeout tras {budget:.0f}s")
        return None, L(f"{name}: sin respuesta en {budget:.0f} s", f"{name}: no answer within {budget:.0f}s")
    except Exception as exc:  # noqa: BLE001
        print(f"[{name}:ERROR] {exc}")
        return None, f"{name}: {type(exc).__name__}"


# --------------------------------------------------------------- pipeline
_ECOM_HINTS = ("carrito", "cart", "añadir al carrito", "add to cart", "comprar ahora", "checkout",
               "envío gratis", "envio gratis", "envíos a toda", "envios a toda", "free shipping",
               "woocommerce", "shopify", "cdn.shopify.com", "prestashop", "vtex")


def _scope(meta: dict, pl: dict | None, home_html_low: str) -> str:
    """'ciudad' (negocio local) o 'pais' (vende/atiende en todo el país)."""
    ecom = any(h in home_html_low for h in _ECOM_HINTS) or \
        any(t in ("product", "offer", "itemlist") for t in (meta.get("schema_types") or []))
    local_type = False
    if pl and pl.get("found"):
        t = (pl.get("category_type") or "")
        local_type = not t.endswith("_store") or t in ("furniture_store", "home_goods_store")
        if pl.get("address") and not ecom:
            return "ciudad"
        if pl.get("address") and ecom and local_type:
            return "ciudad"
        return "pais"
    if meta.get("has_address") and not ecom:
        return "ciudad"
    return "pais"


async def _run_job(job_id: str, url: str, email: str, name: str, lead: dict, lang: str = "es",
                   forced_cc: str = "") -> None:
    global _ANALYSES_SINCE_CLEANUP
    i18n.set_lang(lang)
    dom_key = _domain_key(url)
    ck = store.cache_key(dom_key + ("|" + forced_cc if forced_cc else ""), lang)
    cached = store.cache_get(ck)
    if cached:
        data, ai = cached
        data = json.loads(json.dumps(data))
        data["from_cache"] = True
        store.job_finish(job_id, data)
        await _build_and_send(data, email, name, lead, ai)
        return

    async with _sem:
        tick = asyncio.create_task(_progress_ticker(job_id))
        try:
            await asyncio.wait_for(_pipeline(job_id, url, lang, forced_cc), timeout=HARD_TIMEOUT + 15)
        except asyncio.TimeoutError:
            print(f"[job] tope global superado ({HARD_TIMEOUT}s)")
            if not store.job_is_done(job_id):
                store.job_finish(job_id, None, L("El análisis tardó demasiado. Inténtalo de nuevo.",
                                                 "The analysis took too long. Please try again."))
        except Exception as exc:  # noqa: BLE001
            print(f"[job:ERROR] {type(exc).__name__}: {exc}")
            if not store.job_is_done(job_id):
                store.job_finish(job_id, None, L("No pudimos completar el análisis. Inténtalo de nuevo.",
                                                 "We couldn't complete the analysis. Please try again."))
        finally:
            tick.cancel()

    j = store.job_get(job_id)
    if j and j.get("result"):
        await _build_and_send(j["result"], email, name, lead, j["result"].get("geo_ai"))
    _ANALYSES_SINCE_CLEANUP += 1
    if _ANALYSES_SINCE_CLEANUP >= 25:
        _ANALYSES_SINCE_CLEANUP = 0
        store.cleanup()


async def _pipeline(job_id: str, url: str, lang: str, forced_cc: str = "") -> None:
    t0 = time.monotonic()
    not_measured: list[dict] = []

    def nm(key: str, name: str, note: str):
        not_measured.append({"key": key, "name": name, "note": note})

    # ---- 1) Home + render + señales técnicas (analyzer) ----
    store.job_progress(job_id, 8, L("Revisando SEO y salud técnica...", "Checking SEO and technical health..."))
    res, err = await _stage(analyze(url), B_CRAWL, L("Lectura de la web", "Site read"))
    if res is None or not res.reachable:
        store.job_finish(job_id, None, (res.error if res else "") or
                         L("No pudimos acceder a la web.", "We couldn't reach the website."))
        return
    data = result_to_dict(res)
    data["lang"] = lang
    data["engine_version"] = store.ENGINE_VERSION
    domain = data.get("domain", "")
    final_url = data.get("final_url") or f"https://{domain}"
    meta = data.get("meta") or {}
    home_low = (getattr(res, "html", "") or "").lower()[:400_000]

    # ---- 2) Lanzar EN PARALELO todo lo que no depende de la IA ----
    perf_task = asyncio.create_task(_stage(perf2.measure(final_url), B_PSI, "PageSpeed"))
    analytics_task = asyncio.create_task(_stage(_perf.measure_device(final_url, mobile=True), B_ANALYTICS, "Analytics"))
    onpage_task = asyncio.create_task(_stage(_onpage.audit(final_url), B_ONPAGE, "On-page"))

    # Identidad medida: marca (crawl) + ciudad/país (crawl) -> Places confirma/corrige
    brand = geo_ai.derive_brand(meta, domain) or domain
    # País: 0) el que ELIGIÓ el usuario en el formulario MANDA sobre todo (fin de adivinar
    # y de errores tipo "colombiana -> Australia"). Si no lo eligió: 1) ccTLD, 2) Places
    # (dirección real), 3) detección por contenido (identificador fiscal/teléfono), 4) nunca IA.
    cc_tld = geo_ai.country_from_domain(domain)
    if forced_cc:
        gl = forced_cc
        country = places.country_name(forced_cc)
    elif cc_tld:
        country, gl = cc_tld
    else:
        country = (meta.get("country") or "").strip()
        gl = (meta.get("gl") or "").strip()
    city_hint = (meta.get("city") or "").strip()
    store.job_progress(job_id, 22, L("Buscando tu ficha de Google...", "Looking up your Google listing..."))
    pl_res, pl_err = await _stage(places.resolve(brand, domain, city_hint, gl, lang), B_PLACES, "Places")
    pl = (pl_res or {}).get("data") if isinstance(pl_res, dict) else None
    data["places"] = pl_res or {"status": "failed", "source": "places_api", "note": pl_err, "data": {"found": None}}
    if pl and pl.get("found"):
        # La ficha (dirección real de Google) MANDA sobre la detección por contenido,
        # salvo que el ccTLD ya fije el país (un .co es Colombia, sin discusión).
        if pl.get("city"):
            city_hint = pl["city"]
        if pl.get("country_code") and not cc_tld and not forced_cc:
            country, gl = pl.get("country") or places.country_name(pl["country_code"]), pl["country_code"].lower()
        meta["city"] = city_hint
    meta["country"], meta["gl"] = country, gl or (geo_ai.gl_from_name(country) if country else "")
    meta["zona"] = ", ".join(x for x in (city_hint, country) if x)
    data["meta"] = meta
    if isinstance(pl_res, dict) and pl_res.get("status") != "ok":
        nm("gbp", L("Ficha de Google", "Google listing"), pl_res.get("note") or pl_err or "")

    scope = _scope(meta, pl, home_low)
    category = places.category_label(pl, lang) if pl and pl.get("found") else ""
    snippet = " · ".join(x for x in (meta.get("title"), meta.get("description"), meta.get("h1_first")) if x)[:600]
    identity = {"brand": brand, "domain": domain, "city": city_hint, "country": country,
                "cc": (gl or "").upper(), "gl": gl, "scope": scope, "category": category,
                "snippet": snippet, "places_found": bool(pl and pl.get("found"))}
    data["identity"] = identity

    # ---- 3) Test GEO (IA) ----
    store.job_progress(job_id, 40, L("Preguntándole a la IA como lo haría un cliente...",
                                     "Asking the AI the way a customer would..."))
    ai, ai_err = await _stage(geo_ai.run_geo(identity, lang), B_AI, L("Consulta a la IA", "AI query"))
    if not isinstance(ai, dict):
        ai = {"available": True, "status": "failed", "error": ai_err or "sin respuesta", "brand": brand,
              "limited": True, "note": ai_err, "competitors": [], "questions": [], "sources": [], "engines": []}
    if ai.get("status") in ("failed", "skipped") or ai.get("error"):
        nm("geo", L("Visibilidad en la IA", "AI visibility"), ai.get("note") or ai.get("error") or "")
    # Ficha de Google: SOLO datos de Places (la IA ya no opina). Claves legacy para pantalla/PDF.
    if pl and pl.get("found") is not None:
        ai["gbp"] = bool(pl.get("found"))
        ai["gbp_reviews_n"] = pl.get("reviews") if pl.get("found") else None
        ai["gbp_rating"] = pl.get("rating") if pl.get("found") else None
        ai["gbp_category"] = (pl.get("category_label") or "") if pl.get("found") else ""
        ai["gbp_source"] = "places_api"
        ai["gbp_maps_url"] = pl.get("maps_url") if pl.get("found") else ""
    else:
        ai["gbp"] = None
        ai["gbp_reviews_n"] = None
        ai["gbp_rating"] = None
        ai["gbp_category"] = ""
    apply_ai_to_result(data, ai)

    # ---- 4) Google real (Serper): marca, búsquedas de cliente, indexación ----
    store.job_progress(job_id, 60, L("Consultando Google y tu competencia...", "Checking Google and your competitors..."))
    queries = ai.get("category_queries") or []
    if not queries:
        # IA caída o sin clave: Google se mide igual con búsquedas neutras de la categoría medida
        cat = category or (L("este tipo de negocio", "this kind of business"))
        where = identity.get("city") if scope == "ciudad" else country
        queries = [f"{cat} {where}".strip(), L(f"mejores {cat} {where}", f"best {cat} {where}").strip(),
                   L(f"{cat} recomendados {where}", f"recommended {cat} {where}").strip()] if category else []
        ai["category_queries"] = queries
    sr, sr_err = await _stage(serp.run(domain, brand, queries, gl or "es",
                                       (data.get("signals") or {}).get("sitemap_total", 0)), B_SERP, "Google")
    if isinstance(sr, dict) and sr.get("status") == "ok":
        data["google"], data["indexation"] = sr["google"], sr["indexation"]
        data["serp"] = {"status": "ok", "source": "serper", "elapsed_ms": sr.get("elapsed_ms")}
        ix = data["indexation"] or {}
        if ix.get("broken_indexed"):
            ex = ", ".join(b["url"] for b in ix["broken_indexed"][:2])
            data.setdefault("findings_improve", []).insert(0, {
                "title": L(f"{len(ix['broken_indexed'])} página(s) indexada(s) en Google dan error 404",
                           f"{len(ix['broken_indexed'])} page(s) indexed by Google return 404"),
                "detail": L(f"Google tiene indexadas páginas que ya no existen: {ex}. Hay que redirigirlas o recuperarlas.",
                            f"Google has indexed pages that no longer exist: {ex}. Redirect or restore them."),
                "severity": "alto"})
        # Competencia: IA (citada de verdad) + Google top 5. Dominios de la IA sin web -> Serper.
        # Para el ámbito país usamos el país (no la ciudad de la sede) al resolver dominios.
        comps = list(ai.get("competitors") or [])
        _place = country if (identity.get("scope") == "pais") else (identity.get("city") or country)
        missing = [c["name"] for c in comps if not c.get("domain")][:8]
        if missing:
            found, _ = await _stage(serp.find_domains(missing, _place, gl or "es"), 10, "Dominios")
            for c in comps:
                if not c.get("domain") and found and c["name"] in found:
                    c["domain"] = found[c["name"]]
        own = domain.replace("www.", "")
        # Fiabilidad: una marca REAL tiene web encontrable. Los nombres que ni Serper resuelve
        # suelen ser ruido/alucinación de la IA (marcas pequeñas inventadas): van al final y
        # solo se muestran si hacen falta para llegar a un mínimo.
        with_dom = [c for c in comps if c.get("domain")]
        without_dom = [c for c in comps if not c.get("domain")]
        known = {c.get("domain") for c in with_dom}
        google_comps = []
        for e in (sr["google"].get("competitors_full") or []):
            d = e["domain"]
            # Solo competidores CONSISTENTES en Google (aparecen en >=2 de las búsquedas
            # de la categoría = jugadores reales del mercado), no un dominio suelto/débil.
            if (e.get("hits") or 0) < 2:
                continue
            if d in known or d == own or any(d.endswith(s) for s in (
                    "google.com", "facebook.com", "instagram.com", "youtube.com", "wikipedia.org",
                    "linkedin.com", "tiktok.com", "amazon.es", "amazon.com", "mercadolibre.com.co")):
                continue
            google_comps.append({"name": (e.get("title") or d).split(" - ")[0].split(" | ")[0][:48], "domain": d,
                                 "cited_by": ["Google"], "hits": e["hits"], "source": "google"})
            known.add(d)
        # Primero marcas verificables (IA con web + Google real); los sin web, solo de relleno.
        ranked = with_dom + google_comps
        if len(ranked) < 4:
            ranked += without_dom
        ai["competitors"] = ranked[:8]
    else:
        data["google"], data["indexation"] = None, None
        note = (sr or {}).get("note") if isinstance(sr, dict) else sr_err
        data["serp"] = {"status": "skipped" if not serp.enabled() else "failed", "source": "serper", "note": note}
        nm("google", L("Posición en Google e indexación", "Google ranking & indexation"), note or "")

    # ---- 5) Velocidad ----
    store.job_progress(job_id, 74, L("Midiendo la velocidad en móvil y escritorio...", "Measuring mobile & desktop speed..."))
    perf_model, perf_err = await perf_task
    if isinstance(perf_model, dict):
        data["perf2"] = perf_model
        data["psi_full"] = perf2.to_legacy_psi(perf_model)
        for strat, label in (("mobile", L("Velocidad en móvil", "Mobile speed")),
                             ("desktop", L("Velocidad en escritorio", "Desktop speed"))):
            if not perf_model.get(strat):
                nm(f"perf_{strat}", label, (perf_model.get("notes") or {}).get(strat) or "")
    else:
        data["perf2"], data["psi_full"] = None, None
        nm("perf_mobile", L("Velocidad en móvil", "Mobile speed"), perf_err or "")
        nm("perf_desktop", L("Velocidad en escritorio", "Desktop speed"), perf_err or "")

    analytics, _ = await analytics_task
    try:
        analytics = (analytics or {}).get("analytics") if isinstance(analytics, dict) else None
        if analytics and isinstance(data.get("psi_full"), dict):
            data["psi_full"]["analytics"] = analytics
        apply_analytics(data, analytics)
    except Exception as exc:  # noqa: BLE001
        print(f"[analytics:ERROR] {exc}")

    # ---- 6) On-page multipágina ----
    op, op_err = await onpage_task
    if isinstance(op, dict):
        op.pop("pages", None)
        data["onpage"] = op
        _loc = op.get("local") or {}
        for k in ("has_phone", "has_address", "has_map", "has_hours", "has_geo", "has_testimonials"):
            meta[k] = bool(meta.get(k)) or bool(_loc.get(k))
        data["meta"] = meta
    else:
        data["onpage"] = None
        nm("onpage", L("SEO on-page", "On-page SEO"), op_err or "")
        nm("content", L("Contenido", "Content"), op_err or "")

    # ---- 7) Puntuaciones, dimensiones, hallazgos ----
    try:
        finalize_score(data)
    except Exception as exc:  # noqa: BLE001
        print(f"[score:ERROR] {exc}")
    try:
        data["dims"] = dims.compute(data)
    except Exception as exc:  # noqa: BLE001
        print(f"[dims:ERROR] {exc}"); data["dims"] = []
    present = {d["key"] for d in data["dims"]}
    data["not_measured"] = [x for x in not_measured if x["key"] not in present]
    try:
        data["findings"] = _findings.compute(data, lang)
    except Exception as exc:  # noqa: BLE001
        print(f"[findings:ERROR] {exc}"); data["findings"] = []
    data["elapsed_total"] = round(time.monotonic() - t0, 1)
    data["from_cache"] = False

    # La clave de ESCRITURA debe ser IDÉNTICA a la de LECTURA de _run_job (incluye
    # el país elegido): si no, con país seleccionado la caché nunca acierta, el sitio
    # se re-analiza en cada vista y el score baila (73 en pantalla, 78 en el correo).
    _wkey = _domain_key(url) + ("|" + forced_cc if forced_cc else "")
    store.cache_put(store.cache_key(_wkey, lang), data, ai)
    store.job_progress(job_id, 98, L("Preparando tu diagnóstico...", "Preparing your report..."))
    store.job_finish(job_id, data)
    print(f"[job] {domain} listo en {data['elapsed_total']}s · dims={len(data['dims'])} "
          f"no_medido={[x['key'] for x in data['not_measured']]} ia={ai.get('status')} "
          f"places={data['places'].get('status')} serp={data['serp'].get('status')}")


# ------------------------------------------------------------ PDF + correo
async def _build_and_send(data: dict, email: str, name: str, lead: dict, ai) -> None:
    domain = data.get("domain", "")
    pdf_bytes = None
    for attempt in range(2):
        try:
            pdf_bytes = await asyncio.to_thread(report_pdf.build_pdf, data, _ctx(), name or domain)
            break
        except Exception as exc:  # noqa: BLE001
            print(f"[pdf:ERROR] intento {attempt + 1}: {exc}")
            await asyncio.sleep(2)
    report_url = ""
    if pdf_bytes:
        try:
            token = uuid.uuid4().hex
            (REPORTS_DIR / f"{token}.pdf").write_bytes(pdf_bytes)
            report_url = f"{PUBLIC_BASE_URL}/reporte/{token}.pdf"
        except Exception as exc:  # noqa: BLE001
            print(f"[report-save:ERROR] {exc}")
    from urllib.parse import urlencode  # noqa: PLC0415
    ctxd = _ctx()
    book_url = ctxd["agenda_url"] + "?" + urlencode({"n": name or "", "e": email or "", "d": domain or ""})
    email_html = templates.get_template("email_report.html").render(
        r=data, name=name or domain, report_url=report_url, book_url=book_url,
        lang=data.get("lang", "es"), **ctxd)
    _lang = data.get("lang", "es")
    _stub = "Cupperlab_SEO_GEO_Report" if _lang == "en" else "Diagnostico_Cupperlab"
    pdf_name = f"{_stub}_{domain.replace('.', '_')}.pdf"
    email_sent = False
    for attempt in range(3):
        try:
            email_sent, _ = await asyncio.to_thread(
                emailer.send_client_report, email, name, email_html, pdf_bytes, pdf_name, _lang)
            break
        except Exception as exc:  # noqa: BLE001
            print(f"[email:ERROR] intento {attempt + 1}: {exc}")
            await asyncio.sleep(3 * (attempt + 1))
    lead.update(domain=domain, url=data.get("final_url"), score=data.get("score"),
                grade=data.get("grade"), email_sent=email_sent, from_cache=bool(data.get("from_cache")),
                ai_knows=bool(ai and ai.get("knows_brand")) if ai else None)
    try:
        store.lead_add(lead)
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
