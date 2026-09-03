"""
Robot de AUTORIDAD para la IA — sin API keys, sin scrapear chats.

Comprueba la presencia de la marca en las fuentes publicas de las que TODAS las
IA (ChatGPT, Gemini, Claude, Perplexity, AI Overviews) aprenden y a las que mas
citan: Wikipedia, Wikidata y senales de entidad. Es el mejor indicador gratuito
de "como te ve la IA" sin preguntarle en vivo. La skill de Cupperlab lo destaca:
"Wikipedia, la fuente que mas citan las IA para describir empresas".

APIs 100% gratuitas y publicas (sin key): MediaWiki API de Wikipedia y Wikidata.
"""

from __future__ import annotations

import re
import unicodedata

import httpx

TIMEOUT = 9.0


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _tokens(s: str) -> set[str]:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return {t for t in re.split(r"[^a-z0-9]+", s.lower()) if len(t) > 2}


async def _wikipedia(client: httpx.AsyncClient, brand: str, lang: str) -> dict | None:
    url = f"https://{lang}.wikipedia.org/w/api.php"
    params = {"action": "query", "list": "search", "srsearch": brand,
              "format": "json", "srlimit": 3}
    try:
        r = await client.get(url, params=params, timeout=TIMEOUT)
        data = r.json()
        hits = data.get("query", {}).get("search", [])
    except Exception:  # noqa: BLE001
        return None
    nb = _norm(brand)
    bt = _tokens(brand)
    for h in hits:
        title = h.get("title", "")
        nt = _norm(title)
        tt = _tokens(title)
        # match: titulo == marca, o la marca contiene todos los tokens del titulo (o viceversa)
        if nt == nb or (bt and tt and (bt <= tt or tt <= bt)):
            return {"title": title, "url": f"https://{lang}.wikipedia.org/wiki/{title.replace(' ', '_')}", "lang": lang}
    return None


async def _wikidata(client: httpx.AsyncClient, brand: str, domain: str) -> dict | None:
    url = "https://www.wikidata.org/w/api.php"
    params = {"action": "wbsearchentities", "search": brand, "language": "es",
              "format": "json", "limit": 5, "type": "item"}
    try:
        r = await client.get(url, params=params, timeout=TIMEOUT)
        data = r.json()
        hits = data.get("search", [])
    except Exception:  # noqa: BLE001
        return None
    nb = _norm(brand)
    bt = _tokens(brand)
    for h in hits:
        label = h.get("label", "")
        if _norm(label) == nb or (bt and _tokens(label) and (bt <= _tokens(label) or _tokens(label) <= bt)):
            return {"label": label, "id": h.get("id"), "description": h.get("description", "")}
    return None


async def check_authority(brand: str, domain: str) -> dict:
    """Devuelve senales de autoridad para la IA. Nunca lanza: en error, marca lo
    comprobado como desconocido y sigue."""
    result = {
        "brand": brand,
        "wikipedia": None,     # dict o None
        "wikidata": None,
        "checked": True,
        "score": 0,
    }
    if not brand:
        result["checked"] = False
        return result
    try:
        async with httpx.AsyncClient(follow_redirects=True,
                                     headers={"User-Agent": "CupperlabDiagnostico/1.0 (soporte@cupperlab.com)"}) as client:
            import asyncio
            wp_es, wp_en, wd = await asyncio.gather(
                _wikipedia(client, brand, "es"),
                _wikipedia(client, brand, "en"),
                _wikidata(client, brand, domain),
                return_exceptions=True,
            )
        wp = (wp_es if isinstance(wp_es, dict) else None) or (wp_en if isinstance(wp_en, dict) else None)
        result["wikipedia"] = wp
        result["wikidata"] = wd if isinstance(wd, dict) else None
    except Exception:  # noqa: BLE001
        result["checked"] = False
        return result

    score = 0
    if result["wikipedia"]:
        score += 55
    if result["wikidata"]:
        score += 45
    result["score"] = min(score, 100)
    return result
