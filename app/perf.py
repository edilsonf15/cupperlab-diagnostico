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
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            ctx = browser.new_context(**dev)
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="load", timeout=30000)
            except Exception:  # noqa: BLE001
                # aun asi intentamos leer metricas de lo que cargo
                pass
            try:
                page.wait_for_timeout(600)
                lcp = float(page.evaluate(_LCP_JS) or 0)
                mets = page.evaluate(_METRICS_JS) or {}
            except Exception:  # noqa: BLE001
                browser.close()
                return None
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

    return {
        "performance": max(0, min(100, perf)),
        "fcp": _fmt(fcp),
        "lcp": _fmt(lcp),
        "tbt": "—",
        "si": _fmt(ttfb),
        "source": "device",
    }


async def measure_device(url: str, mobile: bool = True) -> dict | None:
    """Mide velocidad con dispositivo emulado (sin throttling). Corre en un hilo."""
    try:
        return await asyncio.to_thread(_measure, url, mobile)
    except Exception:  # noqa: BLE001
        return None
