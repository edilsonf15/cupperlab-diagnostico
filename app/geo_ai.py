"""
Consulta REAL a la IA (GEO / LLMO) — replica el termómetro de la skill de
Cupperlab: le preguntamos directamente a un modelo de IA por la marca y por su
categoria de servicio, y medimos si la IA la conoce y si la recomienda.

Se activa solo si hay ANTHROPIC_API_KEY en el entorno. Sin key, devuelve None y
el informe usa las senales GEO heuristicas (schema, llms.txt, etc.).
"""

from __future__ import annotations

import asyncio
import os
import re
from urllib.parse import urlparse

import httpx

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
AI_BUDGET = float(os.getenv("AI_GEO_BUDGET", "22"))


def _provider() -> tuple[str, str, str]:
    """Devuelve (proveedor, key, modelo). Gemini (capa gratis) tiene prioridad."""
    gk = os.getenv("GEMINI_API_KEY", "").strip()
    if gk:
        return "gemini", gk, GEMINI_MODEL
    ak = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if ak:
        return "anthropic", ak, ANTHROPIC_MODEL
    return "", "", ""

STOP = {
    "inicio", "home", "bienvenido", "bienvenida", "web", "sitio", "oficial",
    "the", "and", "para", "empresa", "servicios", "productos",
}


def _looks_like_domain(s: str) -> bool:
    s = (s or "").strip().lower()
    return bool(re.match(r"^(https?://)?(www\.)?[a-z0-9-]+\.[a-z]{2,}", s)) or " " not in s and "." in s


def derive_brand(meta: dict, domain: str) -> str:
    osn = (meta.get("og_site_name") or "").strip()
    if osn and not _looks_like_domain(osn):
        return osn
    title = (meta.get("title") or "").strip()
    if title:
        # el nombre de marca suele ir tras el ultimo separador o antes del primero
        parts = re.split(r"\s[|\-–—:·]\s", title)
        parts = [p.strip() for p in parts if p.strip()]
        if parts:
            cand = min(parts, key=len) if len(parts) > 1 else parts[0]
            if 2 <= len(cand) <= 40 and cand.lower() not in STOP:
                return cand
    root = urlparse("https://" + domain).netloc or domain
    root = root.split(".")[0]
    return root.capitalize()


def derive_service(meta: dict) -> str:
    desc = (meta.get("description") or "").strip()
    if desc:
        return desc[:160]
    return (meta.get("title") or "").strip()[:120]


async def _ask(client: httpx.AsyncClient, provider: str, key: str, model: str,
               prompt: str, max_tokens: int = 700, grounded: bool = False) -> str:
    if provider == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

        async def _call(use_grounding: bool):
            body = {"contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.3}}
            if use_grounding:
                body["tools"] = [{"google_search": {}}]   # busca en vivo, como la app de Gemini
            else:
                body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
            return await client.post(url, params={"key": key}, json=body, timeout=AI_BUDGET)

        r = await _call(grounded)
        if grounded and r.status_code != 200:
            # el grounding gratis puede estar limitado: reintenta sin busqueda en vivo
            r = await _call(False)
        r.raise_for_status()
        data = r.json()
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts).strip()

    if provider == "openai":
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        if grounded:
            # Responses API con busqueda web en vivo (como la app de ChatGPT)
            r = await client.post("https://api.openai.com/v1/responses", headers=headers,
                                  json={"model": model, "tools": [{"type": "web_search"}],
                                        "input": prompt}, timeout=AI_BUDGET)
            r.raise_for_status()
            data = r.json()
            txt = ""
            for o in data.get("output", []):
                for c in (o.get("content") or []):
                    if c.get("type") == "output_text":
                        txt += c.get("text", "")
            return txt.strip()
        r = await client.post("https://api.openai.com/v1/chat/completions", headers=headers,
                              json={"model": model, "messages": [{"role": "user", "content": prompt}],
                                    "max_tokens": max_tokens, "temperature": 0.3}, timeout=AI_BUDGET)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    # anthropic
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    body = {"model": model, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]}
    r = await client.post(ANTHROPIC_URL, json=body, headers=headers, timeout=AI_BUDGET)
    r.raise_for_status()
    data = r.json()
    return "".join(b.get("text", "") for b in data.get("content", [])).strip()


OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# ccTLD -> (nombre de pais, codigo gl). El pais se toma del dominio real.
CCTLD = {
    "es": ("España", "es"), "mx": ("México", "mx"), "ar": ("Argentina", "ar"),
    "cl": ("Chile", "cl"), "co": ("Colombia", "co"), "pe": ("Perú", "pe"),
    "uy": ("Uruguay", "uy"), "ec": ("Ecuador", "ec"), "bo": ("Bolivia", "bo"),
    "py": ("Paraguay", "py"), "ve": ("Venezuela", "ve"), "pa": ("Panamá", "pa"),
    "gt": ("Guatemala", "gt"), "cr": ("Costa Rica", "cr"), "sv": ("El Salvador", "sv"),
    "hn": ("Honduras", "hn"), "ni": ("Nicaragua", "ni"), "do": ("República Dominicana", "do"),
    "pr": ("Puerto Rico", "pr"), "us": ("Estados Unidos", "us"), "pt": ("Portugal", "pt"),
    "br": ("Brasil", "br"), "fr": ("Francia", "fr"), "it": ("Italia", "it"),
    "de": ("Alemania", "de"), "uk": ("Reino Unido", "gb"), "gb": ("Reino Unido", "gb"),
}


def _country_from_domain(domain: str) -> tuple[str, str] | None:
    """Detecta (pais, codigo) por el TLD de pais. None si es un TLD generico."""
    host = (domain or "").strip().lower().split("/")[0].split(":")[0]
    tld = host.rsplit(".", 1)[-1] if "." in host else ""
    if tld in CCTLD and tld not in ("com", "net", "org"):
        return CCTLD[tld]
    return None


def _openai_engine() -> dict | None:
    """Unico motor: ChatGPT (OpenAI) con busqueda web en vivo."""
    ok = os.getenv("OPENAI_API_KEY", "").strip()
    if not ok:
        return None
    return {"name": "ChatGPT", "provider": "openai", "key": ok,
            "model": OPENAI_MODEL, "grounded": True}


def _parse_know(txt: str) -> tuple[bool, str]:
    txt = (txt or "").strip()
    knows = bool(txt) and "NO_LA_CONOZCO" not in txt.upper()
    return knows, re.sub(r"\s+", " ", txt)[:600]


_DOM_RE = re.compile(r"([a-z0-9][a-z0-9\-]{1,}\.[a-z]{2,}(?:\.[a-z]{2,})?)", re.I)


def _parse_reco(txt: str, brand: str) -> tuple[bool | None, list[dict], str]:
    """Parsea competidores como {name, domain}. Espera lineas 'Nombre | dominio.com'."""
    included = None
    m = re.search(r"INCLUIDA:\s*(SI|SÍ|NO)", txt or "", re.I)
    if m:
        included = m.group(1).upper().startswith("S")
    body = re.split(r"\bINCLUIDA:", txt or "", 1)[0].strip()
    comps = []
    seen = set()
    for line in body.splitlines():
        line = re.sub(r"^\s*\d+[\.\)]\s*", "", line).strip(" .-*•\t")
        if not line or len(line) < 3:
            continue
        dm = _DOM_RE.search(line)
        domain = dm.group(1).lower() if dm else None
        # nombre = lo que va antes del separador o del dominio
        name = re.split(r"\s*[|\-–—:]\s*", line)[0].strip()
        if domain and domain in name:
            name = name.replace(domain, "").strip(" |-–—:")
        name = re.sub(r"\(.*?\)", "", name).strip(" .|-–—:")
        if not name or len(name) > 48 or brand.lower() in name.lower():
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        comps.append({"name": name, "domain": domain})
        if len(comps) >= 5:
            break
    return included, comps, re.sub(r"\s+", " ", body)[:400]


async def _query_engine(client, eng: dict, q_know: str, q_reco: str, brand: str) -> dict:
    try:
        know, reco = await asyncio.gather(
            _ask(client, eng["provider"], eng["key"], eng["model"], q_know,
                 max_tokens=1024, grounded=eng["grounded"]),
            _ask(client, eng["provider"], eng["key"], eng["model"], q_reco,
                 max_tokens=1024, grounded=eng["grounded"]),
            return_exceptions=True,
        )
    except Exception as exc:  # noqa: BLE001
        return {"name": eng["name"], "error": str(exc)}

    know_err = isinstance(know, Exception)
    know_txt = "" if know_err else (know or "")
    reco_txt = "" if isinstance(reco, Exception) else (reco or "")

    # Si el motor no dio respuesta util a la marca, es INCONCLUSO (no "no te conoce")
    if not know_txt.strip():
        reason = str(know) if know_err else "sin respuesta"
        limited = "429" in reason or "quota" in reason.lower() or "rate" in reason.lower()
        return {"name": eng["name"], "grounded": eng["grounded"],
                "error": "limite temporal de la IA" if limited else "sin respuesta",
                "knows": None, "know_raw": "", "recommended": None, "competitors": [], "reco_raw": ""}

    knows, know_raw = _parse_know(know_txt)
    included, comps, reco_raw = _parse_reco(reco_txt, brand)
    return {"name": eng["name"], "grounded": eng["grounded"], "error": None,
            "knows": knows, "know_raw": know_raw,
            "recommended": included, "competitors": comps, "reco_raw": reco_raw}


async def run_ai_geo(domain: str, meta: dict) -> dict | None:
    """GEO con UN solo motor: ChatGPT (OpenAI) con busqueda web en vivo.

    Hace 3 preguntas de CATEGORIA reales (las que escribiria un cliente en su pais)
    y comprueba en cada una si la marca aparece. Al probar el mismo sitio con 3
    fraseos, el veredicto deja de contradecirse. Competencia enfocada en el pais
    del dominio real.
    """
    eng = _openai_engine()
    if not eng:
        return None
    prov, key, model = eng["provider"], eng["key"], eng["model"]

    brand = derive_brand(meta, domain)
    service = derive_service(meta) or f"servicios de {brand}"
    full_url = domain if domain.startswith("http") else f"https://{domain}"

    # Pais REAL: primero lo detectado por contenido (telefono/menciones) en el crawl;
    # si no, por TLD; si no, lo infiere el modelo (PAIS: xx).
    meta_country = (meta.get("country") or "").strip()
    meta_gl = (meta.get("gl") or "").strip()
    det = _country_from_domain(domain)
    if meta_country:
        country, gl = meta_country, (meta_gl or (det[1] if det else "es"))
    elif det:
        country, gl = det
    else:
        country, gl = "", "es"
    have_country = bool(country)

    q_know = (
        f"Usa búsqueda web. Visita y consulta el sitio {full_url}. "
        f"¿Qué es \"{brand}\" y a qué se dedica? Si encuentras el sitio y su información, "
        f"descríbelo en 2-3 frases. Si NO encuentras información fiable de ESE sitio en concreto, "
        f"responde EXACTAMENTE con NO_LA_CONOZCO y nada más. No inventes datos."
    )
    ctx_pais = f"en {country}" if country else "en su país"
    q_queries = (
        f"Para el negocio de \"{brand}\" (sitio {full_url}; contexto: \"{service}\"), "
        f"escribe EXACTAMENTE 3 búsquedas que un cliente {ctx_pais} escribiría para encontrar ese tipo "
        f"de servicio SIN conocer la marca (búsquedas de categoría, con ciudad/país si aplica). "
        f"Una por línea, sin numerar."
        + ("" if have_country else " Luego una última línea 'PAIS: xx' con el código de país de 2 letras del mercado.")
    )
    q_gap = (
        f"Usa búsqueda web sobre {full_url}. En 2 frases y en lenguaje sencillo de negocio: "
        f"¿qué le falta a \"{brand}\" para que la IA la reconozca y la recomiende cuando alguien pide su "
        f"tipo de servicio {ctx_pais} (sin nombrar la marca)? Sé concreto y accionable. Sin viñetas."
    )

    try:
        async with httpx.AsyncClient() as client:
            # Ronda 1: genera las 3 busquedas + reconocimiento de marca + gap (en paralelo)
            r_queries, r_know, r_gap = await asyncio.gather(
                _ask(client, prov, key, model, q_queries, max_tokens=250, grounded=False),
                _ask(client, prov, key, model, q_know, max_tokens=600, grounded=True),
                _ask(client, prov, key, model, q_gap, max_tokens=300, grounded=True),
                return_exceptions=True,
            )
            queries_txt = "" if isinstance(r_queries, Exception) else (r_queries or "")
            know_txt = "" if isinstance(r_know, Exception) else (r_know or "")
            gap_txt = "" if isinstance(r_gap, Exception) else (r_gap or "")

            # Parseo de las 3 busquedas (+ PAIS si el dominio era generico)
            cat_queries = []
            for ln in queries_txt.splitlines():
                ln = ln.strip(" -•\t0123456789.)")
                mp = re.match(r"(?i)PAIS:\s*([a-z]{2})", ln)
                if mp and not have_country:
                    gl = mp.group(1).lower()
                    country = next((v[0] for v in CCTLD.values() if v[1] == gl), country)
                elif 4 < len(ln) < 90 and "PAIS" not in ln.upper():
                    cat_queries.append(ln)
            cat_queries = cat_queries[:3]
            pais_txt = country or "su país"

            # Ronda 2: por cada busqueda de categoria, ¿aparece la marca? ¿a quien nombra?
            def _q_cat(search: str) -> str:
                return (
                    f"Usa búsqueda web. Un cliente en {pais_txt} busca en un asistente de IA: \"{search}\". "
                    f"Respóndele como lo harías de verdad: nombra 4-5 EMPRESAS REALES (con su dominio) que "
                    f"recomendarías en {pais_txt}, una por línea con formato 'Nombre | dominio.com'. "
                    f"Solo empresas reales con web propia; NADA de tipos de producto, materiales ni categorías. "
                    f"Al final, en una línea aparte, escribe 'INCLUIDA: SI' si entre ellas está \"{brand}\" "
                    f"({domain}), o 'INCLUIDA: NO'."
                )
            cat_tasks = [_ask(client, prov, key, model, _q_cat(s), max_tokens=700, grounded=True)
                         for s in cat_queries] or \
                        [_ask(client, prov, key, model, _q_cat(service), max_tokens=700, grounded=True)]
            cat_results = await asyncio.gather(*cat_tasks, return_exceptions=True)
    except Exception as exc:  # noqa: BLE001
        return {"available": True, "error": str(exc), "brand": brand}

    if not know_txt.strip() and all(isinstance(x, Exception) or not x for x in cat_results):
        low = (str(r_know) if isinstance(r_know, Exception) else "").lower()
        limited = any(t in low for t in ("429", "quota", "rate"))
        return {"available": True, "brand": brand,
                "error": "límite temporal de la IA" if limited else "sin respuesta de la IA"}

    knows_brand, know_raw = _parse_know(know_txt)

    # Agrega los 3 fraseos: aparece / no aparece + competencia (union, del pais)
    questions = []
    comps, seen = [], set()
    appears = 0
    valid = 0
    for i, search in enumerate(cat_queries or [service]):
        res = cat_results[i] if i < len(cat_results) else None
        txt = "" if (res is None or isinstance(res, Exception)) else (res or "")
        if not txt.strip():
            questions.append({"q": search, "appears": None, "named": []})
            continue
        included, qcomps, _ = _parse_reco(txt, brand)
        valid += 1
        if included is True:
            appears += 1
        named = []
        for c in qcomps:
            nm = (c.get("name") if isinstance(c, dict) else str(c)).strip()
            named.append(nm)
            if nm and nm.lower() not in seen and brand.lower() not in nm.lower():
                seen.add(nm.lower())
                comps.append(c if isinstance(c, dict) else {"name": nm, "domain": None})
        questions.append({"q": search, "appears": included, "named": named[:4]})

    if valid == 0 and not know_txt.strip():
        return {"available": True, "brand": brand, "error": "sin respuesta de la IA"}

    recommended = None
    if valid:
        recommended = True if appears >= 2 else (False if appears == 0 else None)
    reco_frac = (appears / valid) if valid else 0.0
    score = round(100 * (0.5 * (1 if knows_brand else 0) + 0.5 * reco_frac))
    gap = re.sub(r"\s+", " ", gap_txt).strip()[:400]

    return {
        "available": True,
        "brand": brand,
        "service": service,
        "country": country or "",
        "engine_names": ["ChatGPT"],
        "answered_names": ["ChatGPT"],
        "knows_brand": knows_brand,
        "brand_description": know_raw[:400] if knows_brand else "",
        "recommended": recommended,
        "competitors": comps[:6],
        "questions": questions,
        "gap": gap,
        "category_queries": cat_queries,
        "gl": gl,
        "ai_score": score,
    }
