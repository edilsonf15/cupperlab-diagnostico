"""
serp — Google REAL vía Serper.dev (sustituye al scraping de DuckDuckGo, que está
bloqueado y devolvía null en todos los análisis).

Una sola petición HTTP por análisis (Serper acepta un array de consultas):
  - "{marca}"                 -> ¿tu dominio es el nº1 para tu propia marca?
  - 3 búsquedas de cliente    -> posición del dominio y top 5 (competencia real en Google)
  - site:{dominio}            -> indexación (nº aproximado + URLs indexadas)

Coste: 5 créditos por análisis (~0,005 $). 2.500 gratis al crear la cuenta.
gl/hl según el país detectado, así el top 5 es el que ve el cliente en su país.

Salida (contrato v2) + los dos objetos legacy que leen PDF y pantalla:
  google     = {provider, brand_query{query, position, others[]}, category[{query, position, top[], top_full[]}], competitors[]}
  indexation = {indexed, sample_count, indexed_urls[], indexed_estimate, broken_indexed[], sitemap_total, conclusion, provider}
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from urllib.parse import urlparse

import httpx

SERPER_URL = "https://google.serper.dev/search"
TIMEOUT = 12.0
_HL = {"es": "es", "co": "es", "mx": "es", "ar": "es", "cl": "es", "pe": "es", "ec": "es",
       "uy": "es", "py": "es", "bo": "es", "ve": "es", "pa": "es", "cr": "es", "gt": "es",
       "sv": "es", "hn": "es", "ni": "es", "do": "es", "pr": "es", "us": "en", "gb": "en",
       "pt": "pt", "br": "pt", "fr": "fr", "it": "it", "de": "de"}


def _root(u: str) -> str:
    try:
        netloc = urlparse(u if "://" in u else "https://" + u).netloc
        return netloc.replace("www.", "").lower()
    except Exception:  # noqa: BLE001
        return ""


def _position(domain: str, results: list[str]) -> int | None:
    dr = domain.replace("www.", "").lower()
    for i, d in enumerate(results, 1):
        if d == dr or d.endswith("." + dr) or dr.endswith("." + d):
            return i
    return None


def enabled() -> bool:
    return bool(os.getenv("SERPER_API_KEY", "").strip())


async def _batch(queries: list[dict]) -> list[dict] | None:
    key = os.getenv("SERPER_API_KEY", "").strip()
    if not key:
        return None
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        for attempt in range(2):
            try:
                r = await c.post(SERPER_URL, json=queries,
                                 headers={"X-API-KEY": key, "Content-Type": "application/json"})
                if r.status_code == 200:
                    data = r.json()
                    return data if isinstance(data, list) else [data]
                if r.status_code in (429, 500, 502, 503) and attempt == 0:
                    await asyncio.sleep(1.5)
                    continue
                print(f"[serper] HTTP {r.status_code}: {r.text[:200]}")
                return None
            except Exception as exc:  # noqa: BLE001
                print(f"[serper:ERROR] {exc}")
                if attempt == 1:
                    return None
                await asyncio.sleep(1.0)
    return None


async def run(domain: str, brand: str, category_queries: list[str], gl: str = "es",
              sitemap_total: int = 0, check_broken: bool = True) -> dict:
    """Ejecuta todas las consultas de Google de un análisis en UNA petición."""
    t0 = time.monotonic()
    out = {"status": "skipped", "source": "serper", "elapsed_ms": 0, "note": "",
           "google": None, "indexation": None}
    if not enabled():
        out["note"] = "Sin clave de Serper: posición en Google e indexación no medidas"
        return out
    gl = (gl or "es").lower()
    hl = _HL.get(gl, "es")
    dr = domain.replace("www.", "").lower()
    cats = [q for q in (category_queries or []) if q][:3]
    qs = [{"q": brand, "gl": gl, "hl": hl, "num": 10}]
    qs += [{"q": q, "gl": gl, "hl": hl, "num": 10} for q in cats]
    qs.append({"q": f"site:{dr}", "gl": gl, "hl": hl, "num": 20})

    res = await _batch(qs)
    out["elapsed_ms"] = round((time.monotonic() - t0) * 1000)
    if not res or len(res) < len(qs):
        out["status"] = "failed"
        out["note"] = out["note"] or "Google (Serper) no respondió"
        return out

    def organic(r: dict) -> list[dict]:
        return [o for o in (r.get("organic") or []) if o.get("link")]

    # 1) Marca
    b = organic(res[0])
    b_doms = [_root(o["link"]) for o in b]
    google = {"provider": "Google (Serper)",
              "brand_query": {"query": brand, "position": _position(dr, b_doms),
                              "others": [d for d in b_doms[:5] if d and d != dr]},
              "category": [], "competitors": [], "competitors_full": []}
    # 2) Búsquedas de cliente
    comp: dict[str, dict] = {}
    for i, q in enumerate(cats, start=1):
        o = organic(res[i])
        doms = [_root(x["link"]) for x in o]
        google["category"].append({
            "query": q, "position": _position(dr, doms),
            "top": [d for d in doms[:5] if d],
            "top_full": [{"title": x.get("title", ""), "link": x.get("link", ""), "domain": _root(x["link"])}
                         for x in o[:5]],
        })
        for x in o[:6]:
            d = _root(x["link"])
            if d and d != dr and not d.endswith("." + dr):
                e = comp.setdefault(d, {"domain": d, "title": x.get("title", ""), "hits": 0})
                e["hits"] += 1
    ranked = sorted(comp.values(), key=lambda e: -e["hits"])
    google["competitors"] = [e["domain"] for e in ranked][:6]
    google["competitors_full"] = ranked[:8]

    # 3) Indexación
    s = res[-1]
    urls = [o["link"] for o in organic(s) if dr in _root(o["link"])]
    est = None
    try:
        tr = (s.get("searchInformation") or {}).get("totalResults")
        if tr is not None:
            est = int(str(tr).replace(",", "").replace(".", ""))
    except Exception:  # noqa: BLE001
        est = None
    idx = est if isinstance(est, int) else len(urls)
    broken: list[dict] = []
    if check_broken and urls:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0 (compatible; CupperlabBot/2.0)"}) as c:
            sem = asyncio.Semaphore(6)
            _not_broken = {401, 403, 405, 429, 451, 503, 999}

            async def chk(u):
                async with sem:
                    try:
                        rr = await c.get(u)
                        if rr.status_code >= 400 and rr.status_code not in _not_broken:
                            broken.append({"url": u, "status": rr.status_code})
                    except Exception:  # noqa: BLE001
                        pass
            try:
                await asyncio.wait_for(asyncio.gather(*(chk(u) for u in urls[:12])), timeout=12)
            except asyncio.TimeoutError:
                pass
    conclusion = ""
    if sitemap_total and idx:
        if idx < sitemap_total * 0.7:
            conclusion = (f"Tu mapa del sitio declara {sitemap_total} páginas y Google indexa unas {idx}: "
                          f"quedan del orden de {max(sitemap_total - idx, 0)} sin indexar.")
        else:
            conclusion = f"Google indexa unas {idx} de las {sitemap_total} páginas de tu mapa: buena cobertura."
    elif idx:
        conclusion = f"Google tiene indexadas del orden de {idx} páginas de tu sitio."
    indexation = {"indexed": bool(urls) or bool(idx), "sample_count": len(urls),
                  "indexed_urls": urls[:10], "indexed_estimate": idx, "broken_indexed": broken,
                  "sitemap_total": sitemap_total, "conclusion": conclusion, "provider": "Google (Serper)"}
    out.update(status="ok", google=google, indexation=indexation,
               elapsed_ms=round((time.monotonic() - t0) * 1000))
    return out


async def find_domains(names: list[str], place: str, gl: str = "es") -> dict[str, str]:
    """Dominio oficial de cada nombre de negocio (para competidores que la IA citó sin
    web). Una petición batch. Devuelve {nombre: dominio}."""
    names = [n for n in dict.fromkeys(n.strip() for n in names if n and n.strip())][:8]
    if not names or not enabled():
        return {}
    hl = _HL.get(gl, "es")
    res = await _batch([{"q": f"{n} {place}".strip(), "gl": gl, "hl": hl, "num": 3} for n in names])
    out = {}
    if not res:
        return out
    _skip = ("google.", "facebook.", "instagram.", "linkedin.", "youtube.", "tiktok.", "wikipedia.",
             "yelp.", "tripadvisor.", "amazon.", "mercadolibre.", "paginasamarillas", "habitissimo",
             "infobel", "cylex", "europages", "einforma", "empresite", "tuugo")
    for n, r in zip(names, res):
        for o in (r.get("organic") or []):
            d = _root(o.get("link", ""))
            if d and not any(s in d for s in _skip):
                out[n] = d
                break
    return out
