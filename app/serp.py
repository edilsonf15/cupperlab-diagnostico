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


# Dominios que NO son competidores reales de un negocio: plataformas, redes, medios,
# marketplaces, directorios y gigantes globales genéricos. Se excluyen de "quién sale en
# tu lugar" y de la lista de competidores (el cliente quiere ver competencia de verdad).
_NOT_COMPETITOR = (
    "youtube.", "facebook.", "instagram.", "linkedin.", "twitter.", "x.com", "tiktok.",
    "pinterest.", "reddit.", "quora.", "wikipedia.", "wordpress.", "blogspot.", "medium.",
    "google.", "bing.", "amazon.", "mercadolibre.", "aliexpress.", "ebay.", "temu.", "shein.",
    "canva.", "notion.", "github.", "gitlab.", "spotify.", "netflix.", "apple.", "microsoft.",
    "lenovo.", "sap.", "oracle.", "ibm.", "dell.", "hp.com", "intel.", "cisco.", "adobe.",
    "shopify.", "wix.", "godaddy.", "cloudflare.", "hostinger.", "yelp.", "tripadvisor.",
    "booking.", "glassdoor.", "indeed.", "crunchbase.", "trustpilot.", "paginasamarillas",
    "cylex", "europages", "einforma", "empresite", "gov", ".edu", "slideshare.", "issuu.",
    "scribd.", "coursera.", "udemy.", "gartner.", "forbes.", "statista.",
    # Directorios de software / reseñas / comparadores (no son competidores del cliente)
    "capterra.", "getapp.", "g2.com", "softwareadvice.", "trustradius.", "clutch.co",
    "goodfirms.", "sortlist.", "appvizer.", "producthunt.",
    # Suites/gigantes SaaS que salen en cualquier búsqueda genérica
    "salesforce.", "hubspot.", "zoho.", "creatio.", "forcemanager.", "zendesk.", "freshworks.",
    "monday.com", "pipedrive.", "odoo.", "sap.com", "orsys.",
    # Portales de empleo / formación (salen en búsquedas de servicios)
    "computrabajo.", "elempleo.", "infojobs.", "bumeran.", "occ.com", "academia.",
    "academy", ".academy",
    # Herramientas/plataformas globales que no son competencia del cliente
    "docusign.", "kimi.ai", "manychat.", "zapier.", "n8n.", "make.com", "dialogflow.",
    "openai.", "anthropic.", "perplexity.", "deepseek.", "mistral.")


# Títulos que delatan un artículo/listículo/guía, NO un competidor real. "Quién sale en
# tu lugar" debe ser una empresa rival, no un post "Top 5 mejores…" ni una guía de precios.
_LISTICLE_RX = re.compile(
    r"(\btop\s*\d+|\b\d+\s+mejores|\bmejores\b|\branking\b|\bcomparativa\b|\bgu[ií]as?\b|"
    r"\btipos\s+de\b|\bqu[eé]\s+es\b|\bc[oó]mo\s+funciona|\bpor\s+qu[eé]\b|\bventajas\b|"
    r"\bprecios?\b|\bejemplos?\b|\bofertas?\s+de\s+(trabajo|empleo)|\bempleo\b|\bvacante|"
    r"\bcurso\b|\bglosario\b|\bdirectorio\b|\blistado\b|\bimprescindibles\b|\bdestacad)",
    re.I)


def _is_listicle(title: str) -> bool:
    return bool(_LISTICLE_RX.search(str(title or "")))


def _is_competitor(d: str, title: str = "") -> bool:
    return bool(d) and not any(s in d for s in _NOT_COMPETITOR) and not _is_listicle(title)


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


async def _search_one(query: dict) -> dict | None:
    """Una sola consulta a Serper como OBJETO (no array). Devuelve el dict crudo (incluye
    'error' si Serper lo reporta) o None si la conexión falla."""
    key = os.getenv("SERPER_API_KEY", "").strip()
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(SERPER_URL, json=query,
                             headers={"X-API-KEY": key, "Content-Type": "application/json"})
            if r.status_code == 200:
                d = r.json()
                return d if isinstance(d, dict) else (d[0] if isinstance(d, list) and d else None)
            return {"error": f"HTTP {r.status_code}: {r.text[:160]}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:160]}


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
    qs.append({"q": f"site:{dr}", "gl": gl, "hl": hl, "num": 10})

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
        # "quién sale en tu lugar": SOLO competidores reales (fuera plataformas/gigantes).
        top_real = [x for x in o if _is_competitor(_root(x["link"]), x.get("title", "")) and _root(x["link"]) != dr]
        google["category"].append({
            "query": q, "position": _position(dr, doms),
            "top": [_root(x["link"]) for x in top_real[:5]],
            "top_full": [{"title": x.get("title", ""), "link": x.get("link", ""), "domain": _root(x["link"])}
                         for x in top_real[:5]],
        })
        for x in top_real[:6]:
            d = _root(x["link"])
            if d and d != dr and not d.endswith("." + dr):
                e = comp.setdefault(d, {"domain": d, "title": x.get("title", ""), "hits": 0})
                e["hits"] += 1
    ranked = sorted(comp.values(), key=lambda e: -e["hits"])
    google["competitors"] = [e["domain"] for e in ranked][:6]
    google["competitors_full"] = ranked[:8]

    # 3) Indexación (site:dominio). El batch a veces devuelve vacío la consulta con
    #    operador site:, así que si viene sin resultados, la relanzamos suelta.
    def _parse_total(r: dict):
        si = r.get("searchInformation") or {}
        tr = si.get("totalResults")
        if tr is None:
            tr = r.get("totalResults")   # por si viene en la raíz
        try:
            return int(re.sub(r"[^\d]", "", str(tr))) if tr not in (None, "") else None
        except Exception:  # noqa: BLE001
            return None

    NUM = 10
    s = res[-1]
    site_dbg = {"organic_n": len(organic(s)), "total_raw": (s.get("searchInformation") or {}).get("totalResults"),
                "keys": list(s.keys())[:12], "error": str(s.get("error"))[:200] if s.get("error") else None,
                "site_q": f"site:{dr}", "requery": False}
    urls = [o["link"] for o in organic(s) if dr in _root(o["link"])]
    est = _parse_total(s)          # total REAL de Google, si Serper lo trae
    sample_n = len(organic(s))
    if not urls and est is None:
        # el batch no trajo nada: reintenta la consulta site: sola (objeto, no array)
        s2 = await _search_one({"q": f"site:{dr}", "gl": gl, "hl": hl, "num": NUM})
        if isinstance(s2, dict):
            s = s2
            site_dbg.update(requery=True, organic_n2=len(organic(s)),
                            total_raw2=(s.get("searchInformation") or {}).get("totalResults"),
                            error2=str(s.get("error"))[:200] if s.get("error") else None)
            urls = [o["link"] for o in organic(s) if dr in _root(o["link"])]
            est = _parse_total(s)
            sample_n = len(organic(s))
    # ¿La muestra llegó al tope (num)? Entonces hay MÁS páginas y la muestra NO es el total.
    capped = sample_n >= NUM
    # Serper no siempre devuelve el total en site:; si está topado, pedimos una muestra
    # mayor (num=100) para intentar el conteo real o al menos un rango mayor.
    if est is None and capped:
        s3 = await _search_one({"q": f"site:{dr}", "gl": gl, "hl": hl, "num": 100})
        if isinstance(s3, dict):
            n3 = len(organic(s3))
            e3 = _parse_total(s3)
            site_dbg.update(big=True, organic_n3=n3,
                            total_raw3=(s3.get("searchInformation") or {}).get("totalResults"),
                            error3=str(s3.get("error"))[:160] if s3.get("error") else None)
            if e3 is not None:
                est = e3
            if n3 > sample_n:
                sample_n = n3
                if not urls:
                    urls = [o["link"] for o in organic(s3) if dr in _root(o["link"])]
            capped = (n3 >= 100) if n3 else capped
    # idx = total real si lo hay; si la muestra está topada, DESCONOCIDO (None, no un
    # número bajo falso); si no está topada, la muestra ES el nº real (pocas páginas).
    if est is not None:
        idx = est
    elif capped:
        idx = None
    else:
        idx = sample_n
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
    if isinstance(idx, int) and idx and sitemap_total:
        # tenemos el TOTAL real de Google y el del sitemap: comparación fiable
        if idx < sitemap_total * 0.7:
            conclusion = (f"Tu mapa del sitio declara {sitemap_total} páginas y Google indexa unas {idx}: "
                          f"quedan del orden de {max(sitemap_total - idx, 0)} sin indexar.")
        else:
            conclusion = f"Google indexa unas {idx} de las {sitemap_total} páginas de tu mapa: buena cobertura."
    elif isinstance(idx, int) and idx:
        conclusion = f"Google tiene indexadas del orden de {idx} páginas de tu sitio."
    elif idx is None and (urls or capped):
        # indexado confirmado por muestra, pero Google no nos da el total exacto por aquí
        _n = max(sample_n, len(urls))
        conclusion = (f"Google tiene tu sitio indexado (confirmamos una muestra de {_n}+ páginas). "
                      f"El número exacto se revisa en Search Console.")
    indexation = {"indexed": bool(urls) or bool(idx) or capped, "sample_count": len(urls),
                  "indexed_urls": urls[:10], "indexed_estimate": idx, "count_capped": bool(idx is None and (urls or capped)),
                  "broken_indexed": broken, "sitemap_total": sitemap_total, "conclusion": conclusion,
                  "provider": "Google (Serper)", "_debug": site_dbg}
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
