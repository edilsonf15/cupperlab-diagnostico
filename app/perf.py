"""
Medicion de velocidad con dispositivo propio (Playwright/Chromium), sin el
throttling 4G que aplica PageSpeed en movil. Da una lectura justa de "movil en
buena conexion" con las metricas Core Web Vitals reales (FCP, LCP, carga).

Se usa para la tarjeta de velocidad MOVIL; el escritorio sigue con PageSpeed.
"""

from __future__ import annotations

import asyncio

# iPhone 13 aprox., sin throttling de red ni CPU
_MOBILE = {
    "viewport": {"width": 390, "height": 844},
    "device_scale_factor": 3,
    "is_mobile": True,
    "has_touch": True,
    "user_agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                   "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
                   "Mobile/15E148 Safari/604.1"),
}
_DESKTOP = {
    "viewport": {"width": 1350, "height": 940},
    "device_scale_factor": 1,
    "is_mobile": False,
    "has_touch": False,
    "user_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
}

_LCP_JS = """() => new Promise((resolve) => {
  let lcp = 0;
  try {
    const po = new PerformanceObserver((list) => {
      for (const e of list.getEntries()) lcp = e.renderTime || e.loadTime || e.startTime || lcp;
    });
    po.observe({ type: 'largest-contentful-paint', buffered: true });
  } catch (e) {}
  setTimeout(() => resolve(lcp), 700);
})"""

_METRICS_JS = """() => {
  const paint = performance.getEntriesByName('first-contentful-paint')[0];
  const nav = performance.getEntriesByType('navigation')[0] || {};
  return {
    fcp: paint ? paint.startTime : 0,
    ttfb: nav.responseStart || 0,
    dcl: nav.domContentLoadedEventEnd || 0,
    load: nav.loadEventEnd || 0,
  };
}"""

# Detecta analitica/pixeles ejecutando el sitio de verdad (muchos inyectan GA/GTM por JS)
_ANALYTICS_JS = """() => {
  const o = {ga4:[], ua:[], gtm:[], tools:{}};
  try { const g = window.google_tag_manager || {};
    for (const k of Object.keys(g)) {
      if (/^G-/.test(k)) o.ga4.push(k);
      else if (/^GTM-/.test(k)) o.gtm.push(k);
      else if (/^UA-/.test(k)) o.ua.push(k);
      else if (/^AW-/.test(k)) o.tools.ads = true;
    }
  } catch(e){}
  try { if (Array.isArray(window.dataLayer)) o.tools.datalayer = true; } catch(e){}
  try { if (typeof window.gtag === 'function') o.tools.gtag = true; } catch(e){}
  try { if (typeof window.ga === 'function' || window.GoogleAnalyticsObject) o.tools.ua_lib = true; } catch(e){}
  try { if (typeof window.fbq === 'function') o.tools.fb = true; } catch(e){}
  try { if (typeof window.clarity === 'function') o.tools.clarity = true; } catch(e){}
  try { if (typeof window.hj === 'function') o.tools.hotjar = true; } catch(e){}
  try { if (typeof window.ttq !== 'undefined' && window.ttq) o.tools.tiktok = true; } catch(e){}
  try { if (window._linkedin_partner_id) o.tools.linkedin = true; } catch(e){}
  return o;
}"""

import re as _re  # noqa: E402


def _analytics_from(js: dict, reqs: list[str], html: str = "") -> dict:
    js = js or {}
    reqs = reqs or []
    h = html or ""
    ga4 = set(js.get("ga4") or [])
    gtm = set(js.get("gtm") or [])
    ua = set(js.get("ua") or [])
    fb = set()
    # Escaneo del HTML renderizado (capta tags presentes aunque no se hayan ejecutado)
    ga4 |= set(_re.findall(r"G-[A-Z0-9]{6,}", h))
    gtm |= set(_re.findall(r"GTM-[A-Z0-9]{5,}", h))
    ua |= set(_re.findall(r"UA-\d{4,}-\d+", h))
    fb |= set(_re.findall(r"fbq\(\s*['\"]init['\"]\s*,\s*['\"](\d{6,})['\"]", h))
    gtag_loads = 0
    tj = js.get("tools") or {}
    ads = bool(tj.get("ads")) or bool(_re.search(r"AW-\d{6,}", h))
    clarity = bool(tj.get("clarity")) or ("clarity.ms" in h)
    hotjar = bool(tj.get("hotjar")) or ("hotjar" in h)
    tiktok = bool(tj.get("tiktok")) or ("analytics.tiktok.com" in h)
    linkedin = bool(tj.get("linkedin")) or ("snap.licdn.com" in h) or ("_linkedin_partner_id" in h)
    fb_lib = bool(tj.get("fb")) or ("connect.facebook.net" in h) or bool(fb)

    for u in reqs:
        ul = u.lower()
        if "googletagmanager.com/gtag/js" in ul:
            gtag_loads += 1
            for _id in _re.findall(r"[?&]id=(G-[A-Z0-9]+)", u):
                ga4.add(_id)
            for _id in _re.findall(r"[?&]id=(AW-\d+)", u):
                ads = True
        if "googletagmanager.com/gtm.js" in ul:
            for _id in _re.findall(r"[?&]id=(GTM-[A-Z0-9]+)", u):
                gtm.add(_id)
        if "connect.facebook.net" in ul or "facebook.com/tr" in ul:
            fb_lib = True
            for _id in _re.findall(r"[?&]id=(\d{6,})", u):
                fb.add(_id)
        if "clarity.ms" in ul:
            clarity = True
        if "static.hotjar.com" in ul or "script.hotjar.com" in ul:
            hotjar = True
        if "analytics.tiktok.com" in ul:
            tiktok = True
        if "snap.licdn.com" in ul:
            linkedin = True

    tools = []
    if ga4:
        tools.append("Google Analytics 4")
    if ua:
        tools.append("Universal Analytics (obsoleto)")
    if gtm:
        tools.append("Google Tag Manager")
    if ads:
        tools.append("Google Ads")
    if fb or fb_lib:
        tools.append("Meta Pixel")
    if clarity:
        tools.append("Microsoft Clarity")
    if hotjar:
        tools.append("Hotjar")
    if tiktok:
        tools.append("TikTok Pixel")
    if linkedin:
        tools.append("LinkedIn Insight")

    dup = []
    if len(ga4) > 1:
        dup.append(f"{len(ga4)} mediciones GA4 distintas ({', '.join(sorted(ga4))})")
    if gtag_loads > 1:
        dup.append(f"la libreria de Google (gtag.js) se carga {gtag_loads} veces")
    if ga4 and gtm:
        dup.append("GA4 cargado directo Y por Tag Manager (posible doble conteo)")
    if len(fb) > 1:
        dup.append(f"{len(fb)} Meta Pixel distintos")

    return {"tools": tools, "ga4": sorted(ga4), "ua": sorted(ua), "gtm": sorted(gtm),
            "fb": sorted(fb), "has_any": bool(tools), "duplicated": bool(dup),
            "dup_notes": dup[:3], "source": "render"}


def _lin(v: float, good: float, poor: float) -> int:
    """Puntaje lineal 100..0 entre 'good' (<=good=100) y 'poor' (>=poor=0)."""
    if v <= good:
        return 100
    if v >= poor:
        return 0
    return round(100 * (poor - v) / (poor - good))


def _fmt(ms: float) -> str:
    if not ms or ms <= 0:
        return "—"
    return f"{ms / 1000:.1f} s"


def _measure(url: str, mobile: bool = True) -> dict | None:
    from playwright.sync_api import sync_playwright  # import perezoso

    dev = _MOBILE if mobile else _DESKTOP
    try:
        analytics = None
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            ctx = browser.new_context(**dev)
            page = ctx.new_page()
            reqs: list[str] = []
            if mobile:
                page.on("request", lambda req: reqs.append(req.url) if len(reqs) < 400 else None)
            try:
                page.goto(url, wait_until="load", timeout=30000)
            except Exception:  # noqa: BLE001
                # aun asi intentamos leer metricas de lo que cargo
                pass
            try:
                page.wait_for_timeout(900 if mobile else 600)
                lcp = float(page.evaluate(_LCP_JS) or 0)
                mets = page.evaluate(_METRICS_JS) or {}
            except Exception:  # noqa: BLE001
                browser.close()
                return None
            if mobile:
                try:
                    js = page.evaluate(_ANALYTICS_JS) or {}
                    try:
                        rendered = page.content()
                    except Exception:  # noqa: BLE001
                        rendered = ""
                    analytics = _analytics_from(js, reqs, rendered)
                except Exception:  # noqa: BLE001
                    analytics = None
            browser.close()
    except Exception:  # noqa: BLE001
        return None

    fcp = float(mets.get("fcp") or 0)
    load = float(mets.get("load") or 0)
    ttfb = float(mets.get("ttfb") or 0)
    if fcp <= 0 and lcp <= 0 and load <= 0:
        return None
    if lcp <= 0:
        lcp = load or fcp

    # Pesos alineados a Core Web Vitals (sin throttling: umbrales reales de CWV)
    fcp_s = _lin(fcp, 1800, 4000)
    lcp_s = _lin(lcp, 2500, 4500)
    load_s = _lin(load, 3500, 8500)
    perf = round(0.30 * fcp_s + 0.50 * lcp_s + 0.20 * load_s)

    out = {
        "performance": max(0, min(100, perf)),
        "fcp": _fmt(fcp),
        "lcp": _fmt(lcp),
        "tbt": "—",
        "si": _fmt(ttfb),
        "source": "device",
    }
    if mobile and analytics is not None:
        out["analytics"] = analytics
    return out


async def measure_device(url: str, mobile: bool = True) -> dict | None:
    """Mide velocidad con dispositivo emulado (sin throttling). Corre en un hilo."""
    try:
        return await asyncio.to_thread(_measure, url, mobile)
    except Exception:  # noqa: BLE001
        return None
