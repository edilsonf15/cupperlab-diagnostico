"""
perf2 — Núcleo de RENDIMIENTO de última generación.

Mide Core Web Vitals y las auditorías de velocidad de un sitio con datos de
Google (PageSpeed Insights = Lighthouse lab + CrUX campo), en MÓVIL y ESCRITORIO,
y añade una sonda propia de cabeceras para lo que PSI no expone directo (CDN y
compresión de la respuesta). Devuelve, por estrategia:

  - metrics: LCP, CLS, INP (o proxy TBT), TTFB  → valor + sub-score + veredicto + fuente
  - audits : imágenes modernas, lazy-load, minificación, terceros, compresión, caché
  - infra  : CDN detectado, content-encoding (br/gzip)
  - score  : la nota de rendimiento de Google (0-100)

`build_findings(model)` traduce todo a hallazgos accionables (resultado, qué
significa y plan de mejora) en lenguaje de cliente.

Todo dato es real y verificado en vivo. Nada se inventa: si un dato no está, se
dice ("sin datos de campo suficientes"). Español neutro, sin guion largo.
"""

from __future__ import annotations

import asyncio
import os

import httpx

PSI_API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Umbrales oficiales de Google (Core Web Vitals). (bueno, malo)
TH = {
    "lcp": (2500.0, 4000.0),   # ms
    "cls": (0.10, 0.25),       # sin unidad
    "inp": (200.0, 500.0),     # ms (campo)
    "tbt": (200.0, 600.0),     # ms (laboratorio, proxy de INP)
    "ttfb": (800.0, 1800.0),   # ms
}


# --------------------------------------------------------------------------- #
# Utilidades de puntuación
# --------------------------------------------------------------------------- #
def _lin(v: float | None, good: float, poor: float) -> int | None:
    """100 si v<=good, 0 si v>=poor, lineal en medio. None si no hay dato."""
    if v is None:
        return None
    if v <= good:
        return 100
    if v >= poor:
        return 0
    return round(100 * (poor - v) / (poor - good))


def _rate(score: int | None) -> str:
    if score is None:
        return "sin_datos"
    if score >= 80:
        return "optimo"
    if score >= 50:
        return "mejorable"
    return "critico"


def _ms(v: float | None) -> str:
    if v is None:
        return "—"
    if v < 1000:
        return f"{round(v)} ms"
    return f"{v / 1000:.1f} s".replace(".", ",")


def _kb(bytes_: float | None) -> int:
    return round((bytes_ or 0) / 1024)


# --------------------------------------------------------------------------- #
# Llamada a PSI
# --------------------------------------------------------------------------- #
async def _psi(client: httpx.AsyncClient, url: str, strategy: str) -> dict | None:
    key = os.getenv("GOOGLE_PSI_API_KEY", "").strip()
    if not key:
        return None
    params = {"url": url, "strategy": strategy, "key": key, "category": "performance"}
    try:
        r = await client.get(PSI_API, params=params, timeout=55)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:  # noqa: BLE001
        return None


PSI_BUDGET = float(os.getenv("STAGE_BUDGET_PSI", "60"))   # tope por estrategia (móvil / escritorio)


async def _psi_retry(url: str, strategy: str, tries: int = 2) -> tuple[dict | None, str]:
    """(datos, nota). Con tope de tiempo: PSI tarda 15-40 s por estrategia; más de 2
    intentos dentro de 60 s no aporta y antes dejaba la barra clavada minutos."""
    note = ""
    key = os.getenv("GOOGLE_PSI_API_KEY", "").strip()
    if not key:
        return None, "Sin clave de PageSpeed"
    t0 = asyncio.get_event_loop().time()
    async with httpx.AsyncClient(headers={"User-Agent": _UA}) as client:
        for i in range(tries):
            left = PSI_BUDGET - (asyncio.get_event_loop().time() - t0)
            if left < 8:
                note = "PageSpeed no respondió dentro del tiempo"
                break
            try:
                r = await client.get(PSI_API, params={"url": url, "strategy": strategy, "key": key,
                                                      "category": "performance"},
                                     timeout=min(55.0, left))
                if r.status_code == 200:
                    data = r.json()
                    if data.get("lighthouseResult"):
                        return data, ""
                    note = "PageSpeed devolvió una respuesta sin datos"
                else:
                    try:
                        msg = (r.json().get("error") or {}).get("message", "")[:120]
                    except Exception:  # noqa: BLE001
                        msg = ""
                    note = f"PageSpeed HTTP {r.status_code}" + (f": {msg}" if msg else "")
                    if r.status_code in (400, 403):
                        break  # URL no analizable / clave sin permiso: no insistir
            except Exception as exc:  # noqa: BLE001
                note = f"PageSpeed: {type(exc).__name__}"
            await asyncio.sleep(2.0 + i)
    return None, note or "PageSpeed no disponible"


# --------------------------------------------------------------------------- #
# Parseo de una estrategia (movil o escritorio)
# --------------------------------------------------------------------------- #
def _audit_num(aud: dict, key: str) -> float | None:
    a = aud.get(key)
    if not a:
        return None
    v = a.get("numericValue")
    return float(v) if v is not None else None


def _opportunity(aud: dict, key: str) -> dict:
    """Auditoría de ahorro. applicable=False si Lighthouse no la evaluó."""
    a = aud.get(key) or {}
    mode = a.get("scoreDisplayMode")
    score = a.get("score")
    det = a.get("details") or {}
    return {
        "applicable": bool(mode not in ("notApplicable", "error") and (score is not None or det)),
        "score": score,                                   # None|0..1
        "passed": score == 1,
        "savings_kb": _kb(det.get("overallSavingsBytes")),
        "savings_ms": round(det.get("overallSavingsMs") or 0),
        "items": len(det.get("items") or []),
    }


def _third_party(aud: dict) -> dict:
    a = aud.get("third-party-summary") or {}
    det = a.get("details") or {}
    items = det.get("items") or []
    total_block = 0.0
    total_kb = 0.0
    top: list[dict] = []
    for it in items:
        block = float(it.get("blockingTime") or 0)
        size = float(it.get("transferSize") or 0)
        total_block += block
        total_kb += size / 1024
        top.append({"name": it.get("entity") or "—",
                    "block_ms": round(block), "kb": round(size / 1024)})
    top.sort(key=lambda x: x["block_ms"], reverse=True)
    return {"count": len(items), "block_ms": round(total_block),
            "kb": round(total_kb), "top": top[:5]}


def _crux(data: dict) -> dict:
    """Datos de campo reales. Página primero, luego origen. category: FAST/AVERAGE/SLOW."""
    page = (data.get("loadingExperience") or {}).get("metrics") or {}
    origin = (data.get("originLoadingExperience") or {}).get("metrics") or {}

    def g(key: str):
        m = page.get(key) or origin.get(key)
        if not m:
            return None
        return m.get("percentile")

    src = "pagina" if page else ("origen" if origin else None)
    cls_raw = g("CUMULATIVE_LAYOUT_SHIFT_SCORE")
    return {
        "source": src,
        "lcp": g("LARGEST_CONTENTFUL_PAINT_MS"),
        "cls": (cls_raw / 100.0) if cls_raw is not None else None,
        "inp": g("INTERACTION_TO_NEXT_PAINT"),
        "ttfb": g("EXPERIMENTAL_TIME_TO_FIRST_BYTE"),
        "fcp": g("FIRST_CONTENTFUL_PAINT_MS"),
    }


def _metric(name: str, value: float | None, unit_good: float, unit_poor: float,
            source: str) -> dict:
    score = _lin(value, unit_good, unit_poor)
    disp = (f"{value:.2f}".replace(".", ",") if name == "cls" and value is not None
            else _ms(value))
    return {"value": value, "display": disp, "score": score,
            "rate": _rate(score), "source": source}


def _parse(data: dict) -> dict:
    lh = data.get("lighthouseResult") or {}
    aud = lh.get("audits") or {}
    cats = lh.get("categories") or {}
    perf = cats.get("performance", {}).get("score")
    crux = _crux(data)

    # Valores finales: CAMPO (CrUX) si existe, si no LABORATORIO.
    lcp_v = crux["lcp"] if crux["lcp"] is not None else _audit_num(aud, "largest-contentful-paint")
    lcp_src = "campo" if crux["lcp"] is not None else "laboratorio"
    cls_v = crux["cls"] if crux["cls"] is not None else _audit_num(aud, "cumulative-layout-shift")
    cls_src = "campo" if crux["cls"] is not None else "laboratorio"
    ttfb_v = crux["ttfb"] if crux["ttfb"] is not None else _audit_num(aud, "server-response-time")
    ttfb_src = "campo" if crux["ttfb"] is not None else "laboratorio"

    metrics = {
        "lcp": _metric("lcp", lcp_v, *TH["lcp"], lcp_src),
        "cls": _metric("cls", cls_v, *TH["cls"], cls_src),
        "ttfb": _metric("ttfb", ttfb_v, *TH["ttfb"], ttfb_src),
    }
    # INP: solo campo real; si no hay, proxy TBT de laboratorio (con honestidad).
    if crux["inp"] is not None:
        metrics["inp"] = _metric("inp", crux["inp"], *TH["inp"], "campo")
        metrics["inp"]["is_proxy"] = False
    else:
        tbt = _audit_num(aud, "total-blocking-time")
        metrics["inp"] = _metric("inp", tbt, *TH["tbt"], "laboratorio")
        metrics["inp"]["is_proxy"] = True

    audits = {
        "images_modern": _opportunity(aud, "modern-image-formats"),
        "images_sized": _opportunity(aud, "uses-responsive-images"),
        "lazy": _opportunity(aud, "offscreen-images"),
        "minify_js": _opportunity(aud, "unminified-javascript"),
        "minify_css": _opportunity(aud, "unminified-css"),
        "text_compression": _opportunity(aud, "uses-text-compression"),
        "cache": _opportunity(aud, "uses-long-cache-ttl"),
        "third_party": _third_party(aud),
    }

    return {
        "score": round(perf * 100) if perf is not None else None,
        "field_source": crux["source"],   # de dónde salen los datos de campo
        "metrics": metrics,
        "audits": audits,
    }


# --------------------------------------------------------------------------- #
# Sonda de cabeceras: CDN + compresión (lo que PSI no expone claro)
# --------------------------------------------------------------------------- #
_CDN_SIGNS = [
    ("cf-ray", "Cloudflare"), ("cf-cache-status", "Cloudflare"),
    ("x-vercel-id", "Vercel"), ("x-vercel-cache", "Vercel"),
    ("x-amz-cf-id", "Amazon CloudFront"), ("x-amz-cf-pop", "Amazon CloudFront"),
    ("x-fastly-request-id", "Fastly"), ("fastly-io-info", "Fastly"),
    ("x-akamai-transformed", "Akamai"), ("x-akamai-request-id", "Akamai"),
    ("x-sucuri-id", "Sucuri"), ("x-cache-handler", None),
    ("x-bunnycdn-cache", "BunnyCDN"), ("x-hw", "StackPath"),
]


async def _headers_probe(url: str) -> dict:
    out = {"cdn": None, "encoding": None, "brotli": False, "gzip": False,
           "server": None, "hsts": False}
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": _UA, "Accept-Encoding": "br, gzip, deflate"},
            follow_redirects=True, verify=False, timeout=12,
        ) as client:
            r = await client.get(url)
            h = {k.lower(): v for k, v in r.headers.items()}
    except Exception:  # noqa: BLE001
        return out

    enc = (h.get("content-encoding") or "").lower()
    out["encoding"] = enc or None
    out["brotli"] = "br" in enc
    out["gzip"] = "gzip" in enc or "deflate" in enc
    out["server"] = h.get("server")
    out["hsts"] = "strict-transport-security" in h

    server = (h.get("server") or "").lower()
    via = (h.get("via") or "").lower()
    for sign, name in _CDN_SIGNS:
        if sign in h and name:
            out["cdn"] = name
            break
    if not out["cdn"]:
        if "cloudflare" in server:
            out["cdn"] = "Cloudflare"
        elif "cloudfront" in via or "cloudfront" in server:
            out["cdn"] = "Amazon CloudFront"
        elif "vercel" in server:
            out["cdn"] = "Vercel"
        elif "fastly" in via or "fastly" in server:
            out["cdn"] = "Fastly"
        elif "akamai" in via or "akamai" in server:
            out["cdn"] = "Akamai"
    return out


# --------------------------------------------------------------------------- #
# Entrada principal
# --------------------------------------------------------------------------- #
def _from_device(dev: dict | None) -> dict | None:
    """Convierte la medición del navegador propio (perf.measure_device) a la forma de una
    estrategia perf2 {score, metrics, audits}, para el respaldo cuando PageSpeed cae."""
    if not dev or dev.get("performance") is None:
        return None
    lcp_v = dev.get("lcp_ms") or None
    ttfb_v = dev.get("ttfb_ms") or None
    metrics = {"lcp": _metric("lcp", lcp_v, *TH["lcp"], "navegador propio")}
    if ttfb_v:
        metrics["ttfb"] = _metric("ttfb", ttfb_v, *TH["ttfb"], "navegador propio")
    return {"score": int(dev["performance"]), "field_source": "navegador propio",
            "metrics": metrics, "audits": {}}


async def measure(url: str) -> dict:
    """Modelo completo de rendimiento (móvil + escritorio + infra). SIEMPRE devuelve
    un dict con `status` y `notes`: si una estrategia falla, esa clave va a None (la
    pantalla la oculta) y `notes[estrategia]` explica por qué; main.py lo convierte en
    una entrada de `not_measured` para que el cliente vea "No medido (motivo)"."""
    m_res, d_res, infra = await asyncio.gather(
        _psi_retry(url, "mobile"),
        _psi_retry(url, "desktop"),
        _headers_probe(url),
        return_exceptions=True,
    )
    m_raw, m_note = m_res if isinstance(m_res, tuple) else (None, f"{type(m_res).__name__}")
    d_raw, d_note = d_res if isinstance(d_res, tuple) else (None, f"{type(d_res).__name__}")
    infra = infra if isinstance(infra, dict) else {}
    notes = {}
    if not m_raw:
        notes["mobile"] = m_note
    if not d_raw:
        notes["desktop"] = d_note

    if not m_raw and not d_raw:
        # RESPALDO: si PageSpeed (Google) no respondió en ninguna estrategia, medimos la
        # velocidad con NUESTRO navegador (Playwright, con throttling tipo Lighthouse), para
        # que las dos fichas SIEMPRE salgan. La velocidad es el dato clave: nunca falta.
        try:
            import perf as _perf  # noqa: PLC0415
            dm, dd = await asyncio.gather(_perf.measure_device(url, mobile=True),
                                          _perf.measure_device(url, mobile=False),
                                          return_exceptions=True)
            dm = dm if isinstance(dm, dict) else None
            dd = dd if isinstance(dd, dict) else None
        except Exception:  # noqa: BLE001
            dm = dd = None
        mob_fb, des_fb = _from_device(dm), _from_device(dd)
        if mob_fb or des_fb:
            parts_fb = [(s["score"], w) for s, w in ((mob_fb, 0.7), (des_fb, 0.3))
                        if s and s.get("score") is not None]
            sc = round(sum(v * w for v, w in parts_fb) / sum(w for _, w in parts_fb)) if parts_fb else None
            return {"engine": "device", "status": "ok" if (mob_fb and des_fb) else "partial",
                    "score": sc, "mobile": mob_fb, "desktop": des_fb, "infra": infra,
                    "notes": {"engine": "Medido con navegador propio (PageSpeed no disponible)"}}
        return {"engine": "psi", "status": "failed", "score": None, "mobile": None, "desktop": None,
                "infra": infra, "notes": notes}

    mobile = _parse(m_raw) if m_raw else None
    desktop = _parse(d_raw) if d_raw else None

    # Nota de la dimensión: móvil manda (indexación mobile-first).
    parts = []
    if mobile and mobile["score"] is not None:
        parts.append(("m", mobile["score"], 0.7))
    if desktop and desktop["score"] is not None:
        parts.append(("d", desktop["score"], 0.3))
    if parts:
        wsum = sum(w for _, _, w in parts)
        score = round(sum(s * w for _, s, w in parts) / wsum)
    else:
        score = None

    return {"engine": "psi", "status": "ok" if (mobile and desktop) else "partial", "score": score,
            "mobile": mobile, "desktop": desktop, "infra": infra, "notes": notes}


def to_legacy_psi(model: dict | None) -> dict | None:
    """Adapta el modelo perf2 a la forma antigua `psi_full` (mobile/desktop con
    performance/lcp/…) para que el cálculo de score y el correo sigan funcionando
    sin duplicar llamadas a PageSpeed."""
    if not model or not (model.get("mobile") or model.get("desktop")):
        return None

    def _state(sc):
        if sc is None:
            return "na"
        return "ok" if sc >= 90 else ("warn" if sc >= 50 else "bad")

    def leg(strat: dict | None) -> dict | None:
        if not strat:
            return None
        m = strat.get("metrics") or {}
        def disp(k):
            return (m.get(k) or {}).get("display") or "—"
        def st(k):
            return _state((m.get(k) or {}).get("score"))
        # Core Web Vitals para el PDF: LCP/CLS/INP con valor y semaforo
        cwv = {
            "lcp": {"v": disp("lcp"), "state": st("lcp")},
            "cls": {"v": disp("cls"), "state": st("cls")},
            "inp": {"v": disp("inp"), "state": st("inp"),
                    "proxy": bool((m.get("inp") or {}).get("is_proxy"))},
        }
        return {"performance": strat.get("score"),
                "lcp": disp("lcp"), "cls": disp("cls"),
                "tbt": disp("inp"), "si": disp("ttfb"),
                "cwv": cwv, "source": "psi"}

    return {"mobile": leg(model.get("mobile")),
            "desktop": leg(model.get("desktop"))}


# --------------------------------------------------------------------------- #
# Prueba directa:  python perf2.py https://dominio.com
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import json
    import sys

    _url = sys.argv[1] if len(sys.argv) > 1 else "https://cupperlab.com"
    _out = asyncio.run(measure(_url))
    print(json.dumps(_out, indent=2, ensure_ascii=False))
