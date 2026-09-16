"""
Detección de la ficha de Google Business SIN API de pago: abre Google Maps con el
navegador (Playwright, el mismo que ya se usa para velocidad/PDF) y busca el negocio
por NOMBRE + ZONA y por DOMINIO. Si aparece una ficha que coincide, devuelve
{found, name, category, reviews, rating, maps_url}. Best-effort: si Google no deja
(consentimiento, bloqueo) devuelve found=None (desconocido) y no se afirma en falso.
"""
from __future__ import annotations

import asyncio
import re
import urllib.parse

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _site_in_panel(page, dom: str) -> bool:
    """¿El campo 'sitio web' del PANEL de la ficha apunta al dominio del negocio?
    (Fiable: no confundir con el eco del texto buscado en el HTML de la búsqueda)."""
    if not dom:
        return False
    try:
        el = (page.query_selector('a[data-item-id="authority"]')
              or page.query_selector('a[aria-label*="sitio web" i]')
              or page.query_selector('a[aria-label*="website" i]'))
        if not el:
            return False
        blob = ((el.get_attribute("href") or "") + " " + (el.get_attribute("aria-label") or "")).lower()
        return dom in blob
    except Exception:  # noqa: BLE001
        return False


def _place_match(page, is_place: bool, bnorm: str, dom: str, name: str) -> bool:
    """Solo es la ficha del negocio si estamos en una /maps/place Y coincide el NOMBRE
    con la marca O el sitio web del panel es el dominio. Sin esto, Maps abría un
    negocio cualquiera y lo dábamos por ficha del cliente (falso positivo)."""
    if not is_place:
        return False
    if bnorm and bnorm in _norm(name):
        return True
    return _site_in_panel(page, dom)


def _scrape(brand: str, place: str, domain: str) -> dict:
    from playwright.sync_api import sync_playwright  # import perezoso

    dom = (domain or "").split("/")[0].replace("www.", "").lower()
    bnorm = _norm(brand)
    queries = [f"{brand} {place}".strip(), brand, dom]
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            ctx = browser.new_context(locale="es-ES", user_agent=_UA)
            # cookies para saltar el muro de consentimiento de Google (UE)
            ctx.add_cookies([
                {"name": "SOCS", "value": "CAISNQgQEitib3", "domain": ".google.com", "path": "/"},
                {"name": "CONSENT", "value": "YES+cb.20210328-17-p0.es+FX+000",
                 "domain": ".google.com", "path": "/"},
            ])
            page = ctx.new_page()
            found = None
            for q in queries:
                if not q:
                    continue
                url = "https://www.google.com/maps/search/" + urllib.parse.quote(q)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=22000)
                    page.wait_for_timeout(3200)
                except Exception:  # noqa: BLE001
                    continue
                cur = page.url
                html = page.content()
                low = html.lower()
                # ¿Maps abrió una ficha concreta (place) que coincide con la marca/dominio?
                is_place = "/maps/place/" in cur
                name = ""
                try:
                    h1 = page.query_selector("h1")
                    name = (h1.inner_text() if h1 else "") or ""
                except Exception:  # noqa: BLE001
                    name = ""
                match = _place_match(page, is_place, bnorm, dom, name)
                # Si es una LISTA, entra en el 1er resultado SOLO si su etiqueta
                # contiene la marca (no un negocio cualquiera), y re-verifica.
                if not match and not is_place:
                    try:
                        art = page.query_selector('[role="feed"] a[aria-label]') or page.query_selector('a[href*="/maps/place/"]')
                        al = (art.get_attribute("aria-label") if art else "") or ""
                        if art and bnorm and bnorm in _norm(al):
                            art.click()
                            page.wait_for_timeout(2600)
                            cur = page.url
                            is_place = "/maps/place/" in cur
                            h1b = page.query_selector("h1")
                            name = (h1b.inner_text() if h1b else "") or name or al
                            match = _place_match(page, is_place, bnorm, dom, name)
                    except Exception:  # noqa: BLE001
                        pass
                if not match:
                    continue
                # valoración + nº de reseñas: primero del panel de la ficha (fiable).
                # reviews=None => no se pudo leer (distinto de 0 reseñas reales).
                rating = None
                reviews = None
                try:
                    rel = page.query_selector('[role="img"][aria-label*="estrella"], [role="img"][aria-label*="star"]')
                    ral = (rel.get_attribute("aria-label") if rel else "") or ""
                    mrt = re.search(r"([0-5][.,]\d)", ral)
                    if mrt:
                        rating = mrt.group(1).replace(",", ".")
                except Exception:  # noqa: BLE001
                    pass
                try:
                    rvb = (page.query_selector('button[aria-label*="reseñ" i]')
                           or page.query_selector('button[aria-label*="review" i]')
                           or page.query_selector('[aria-label*="reseñ" i]'))
                    ral2 = (rvb.get_attribute("aria-label") if rvb else "") or ""
                    mv = re.search(r"(\d[\d.,]*)", ral2)
                    if mv:
                        reviews = int(re.sub(r"[^\d]", "", mv.group(1)))
                except Exception:  # noqa: BLE001
                    pass
                txt = page.inner_text("body")[:8000]
                if reviews is None:
                    mrev = (re.search(r"(\d[\d.,]*)\s*(?:rese|review|opinion)", txt, re.I)
                            or re.search(r"[0-5][.,]\d\s*\((\d[\d.,]*)\)", txt))
                    if mrev:
                        reviews = int(re.sub(r"[^\d]", "", mrev.group(1)))
                if rating is None:
                    mr = re.search(r"\b([0-5][.,]\d)\b\s*(?:\((\d[\d.,]*)\)|★|estrella|star)", txt, re.I)
                    if mr:
                        rating = mr.group(1).replace(",", ".")
                        if reviews is None and mr.group(2):
                            reviews = int(re.sub(r"[^\d]", "", mr.group(2)))
                # categoría: botón de categoría del panel
                category = ""
                try:
                    cat_el = page.query_selector('button[jsaction*="category"]')
                    category = (cat_el.inner_text() if cat_el else "") or ""
                except Exception:  # noqa: BLE001
                    category = ""
                found = {"found": True, "name": name.strip()[:80], "category": category.strip()[:80],
                         "reviews": reviews, "rating": rating, "maps_url": cur}
                break
            browser.close()
            return found or {"found": False, "reviews": 0}
    except Exception as exc:  # noqa: BLE001
        return {"found": None, "error": str(exc), "reviews": 0}


async def check_gbp_scrape(brand: str, place: str = "", domain: str = "") -> dict | None:
    """Versión asíncrona (corre el scraping en un hilo, como perf)."""
    if not brand:
        return None
    try:
        return await asyncio.to_thread(_scrape, brand, place, domain)
    except Exception as exc:  # noqa: BLE001
        return {"found": None, "error": str(exc), "reviews": 0}
