"""
Posicion en buscadores (marca vs categoria), competencia e indexacion, con un
SCRIPT propio y GRATIS (sin API de pago):

  - DuckDuckGo HTML (sin key, siempre disponible): proxy fiable de la posicion
    organica en la web. Es el motor por defecto.
  - Serper.dev (opcional, si SERPER_API_KEY): posicion en Google exacta.

Tambien estima la INDEXACION con el operador site: (cuantas/ que paginas ve el
buscador) y la contrasta con el sitemap.
"""

from __future__ import annotations

import asyncio
import os
import re
from urllib.parse import unquote, urlparse

import httpx

TIMEOUT = 14.0
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "es-ES,es;q=0.9,en;q=0.6"}

# gl (pais) -> region DuckDuckGo (kl)
_KL = {"co": "co-es", "us": "us-en", "es": "es-es", "mx": "mx-es", "ar": "ar-es",
       "cl": "cl-es", "pe": "pe-es", "ec": "ec-es", "uk": "uk-en", "br": "br-pt"}


def provider() -> str:
    if os.getenv("SEARCH_PROVIDER", "").strip().lower() == "serper" or os.getenv("SERPER_API_KEY", "").strip():
        return "serper"
    return "ddg"  # gratis, sin key, siempre disponible


def _root(u: str) -> str:
    try:
        netloc = urlparse(u if "://" in u else "https://" + u).netloc
        return netloc.replace("www.", "").lower()
    except Exception:  # noqa: BLE001
        return ""


def _parse_uddg(html: str) -> list[str]:
    out = []
    for enc in re.findall(r'uddg=([^&"]+)', html):
        dom = _root(unquote(enc))
        if dom and "duckduckgo.com" not in dom and dom not in out:
            out.append(dom)
    if not out:  # endpoint lite: enlaces directos
        for u in re.findall(r'href="(https?://[^"]+)"', html):
            dom = _root(u)
            if dom and "duckduckgo.com" not in dom and dom not in out:
                out.append(dom)
    return out[:10]


async def _ddg(client: httpx.AsyncClient, query: str, gl: str) -> list[str]:
    kl = _KL.get(gl, "wt-wt")
    endpoints = ["https://html.duckduckgo.com/html/", "https://lite.duckduckgo.com/lite/"]
    for attempt, url in enumerate(endpoints):
        try:
            r = await client.post(url, data={"q": query, "kl": kl}, headers=HEADERS,
                                  timeout=TIMEOUT, follow_redirects=True)
            if r.status_code == 200:
                out = _parse_uddg(r.text)
                if out:
                    return out
            if attempt == 0:
                await asyncio.sleep(1.2)   # backoff antes de probar el endpoint lite
        except Exception as exc:  # noqa: BLE001
            print(f"[ddg:ERROR] {query[:40]} -> {exc}")
    return []


async def _serper(client: httpx.AsyncClient, query: str, gl: str) -> list[str]:
    try:
        r = await client.post("https://google.serper.dev/search",
                              headers={"X-API-KEY": os.getenv("SERPER_API_KEY", "").strip(),
                                       "Content-Type": "application/json"},
                              json={"q": query, "gl": gl, "num": 10}, timeout=TIMEOUT)
        r.raise_for_status()
        return [_root(o.get("link", "")) for o in r.json().get("organic", []) if o.get("link")]
    except Exception as exc:  # noqa: BLE001
        print(f"[serper:ERROR] {exc}")
        return []


async def _run(client, prov, query, gl):
    return await (_serper(client, query, gl) if prov == "serper" else _ddg(client, query, gl))


def _position(domain: str, results: list[str]) -> int | None:
    dr = domain.replace("www.", "").lower()
    for i, d in enumerate(results, 1):
        if d == dr or d.endswith("." + dr) or dr.endswith("." + d):
            return i
    return None


async def check_google(domain: str, brand: str, category_queries: list[str], gl: str = "es") -> dict | None:
    prov = provider()
    out = {"provider": "Google (Serper)" if prov == "serper" else "busqueda web (DuckDuckGo)",
           "brand_query": None, "category": [], "competitors": []}
    comp: dict[str, int] = {}
    any_results = False
    try:
        async with httpx.AsyncClient() as client:
            brand_results = await _run(client, prov, brand, gl)
            any_results = any_results or bool(brand_results)
            out["brand_query"] = {"query": brand, "position": _position(domain, brand_results),
                                  "others": [d for d in brand_results[:5] if d and d != domain.replace("www.", "")]}
            for q in category_queries[:3]:
                await asyncio.sleep(0.6)   # amable con el buscador
                res = await _run(client, prov, q, gl)
                any_results = any_results or bool(res)
                out["category"].append({"query": q, "position": _position(domain, res),
                                        "top": [d for d in res[:5] if d]})
                for d in res[:6]:
                    if d and d != domain.replace("www.", ""):
                        comp[d] = comp.get(d, 0) + 1
        out["competitors"] = [d for d, _ in sorted(comp.items(), key=lambda x: -x[1])][:6]
    except Exception as exc:  # noqa: BLE001
        print(f"[search:ERROR] {exc}")
        return None
    if not any_results:
        return None
    return out


def _parse_uddg_urls(html: str) -> list[str]:
    """URLs COMPLETAS (no solo dominios) de los resultados de DuckDuckGo."""
    out = []
    for enc in re.findall(r'uddg=([^&"]+)', html):
        u = unquote(enc).split("#")[0]
        if u.startswith("http") and "duckduckgo.com" not in u and u not in out:
            out.append(u)
    if not out:
        for u in re.findall(r'href="(https?://[^"#]+)"', html):
            if "duckduckgo.com" not in u and u not in out:
                out.append(u)
    return out[:15]


async def _indexed_urls(client: httpx.AsyncClient, prov: str, domain: str, gl: str) -> list[str]:
    q = f"site:{domain}"
    if prov == "serper":
        try:
            r = await client.post("https://google.serper.dev/search",
                                  headers={"X-API-KEY": os.getenv("SERPER_API_KEY", "").strip(),
                                           "Content-Type": "application/json"},
                                  json={"q": q, "gl": gl, "num": 15}, timeout=TIMEOUT)
            r.raise_for_status()
            return [o.get("link", "") for o in r.json().get("organic", []) if o.get("link")]
        except Exception as exc:  # noqa: BLE001
            print(f"[serper-index:ERROR] {exc}")
            return []
    kl = _KL.get(gl, "wt-wt")
    for url in ("https://html.duckduckgo.com/html/", "https://lite.duckduckgo.com/lite/"):
        try:
            r = await client.post(url, data={"q": q, "kl": kl}, headers=HEADERS,
                                  timeout=TIMEOUT, follow_redirects=True)
            if r.status_code == 200:
                urls = _parse_uddg_urls(r.text)
                if urls:
                    return urls
            await asyncio.sleep(1.0)
        except Exception as exc:  # noqa: BLE001
            print(f"[ddg-index:ERROR] {exc}")
    return []


async def check_indexation(domain: str, sitemap_total: int = 0, gl: str = "es") -> dict | None:
    """Paginas que el buscador tiene indexadas (con site:dominio) y comprueba si
    alguna de esas paginas INDEXADAS da error 404 (peor que un 404 del sitemap)."""
    prov = provider()
    dr = domain.replace("www.", "").lower()
    broken_indexed: list[dict] = []
    try:
        async with httpx.AsyncClient(headers=HEADERS) as client:
            urls = await _indexed_urls(client, prov, domain, gl)
            if not urls:
                return None  # el buscador no devolvio: no podemos determinar (no inventar)
            own = [u for u in urls if dr in _root(u)]
            # comprueba el estado HTTP real de una muestra de lo indexado
            sem = asyncio.Semaphore(6)

            async def chk(u):
                async with sem:
                    try:
                        rr = await client.get(u, timeout=10.0, follow_redirects=True)
                        if rr.status_code >= 400:
                            broken_indexed.append({"url": u, "status": rr.status_code})
                    except Exception:  # noqa: BLE001
                        pass
            if own:
                await asyncio.gather(*(chk(u) for u in own[:10]))
    except Exception as exc:  # noqa: BLE001
        print(f"[index:ERROR] {exc}")
        return None
    return {
        "indexed": bool(own),
        "sample_count": len(own),
        "indexed_urls": own[:10],
        "broken_indexed": broken_indexed,
        "sitemap_total": sitemap_total,
        "provider": "Google (Serper)" if prov == "serper" else "DuckDuckGo",
    }
