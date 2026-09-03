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


def _parse_companies(body: str) -> list[dict]:
    """Parsea TODAS las empresas de la lista (incluida la marca si aparece).
    Formato esperado por linea: 'Nombre | dominio.com'. Devuelve {name, domain}."""
    out, seen = [], set()
    for line in (body or "").splitlines():
        line = re.sub(r"^\s*\d+[\.\)]\s*", "", line).strip(" .-*•\t")
        if not line or len(line) < 3:
            continue
        dm = _DOM_RE.search(line)
        domain = dm.group(1).lower() if dm else None
        name = re.split(r"\s*[|\-–—:]\s*", line)[0].strip()
        if domain and domain in name:
            name = name.replace(domain, "").strip(" |-–—:")
        name = re.sub(r"\(.*?\)", "", name).strip(" .|-–—:")
        if not name or len(name) > 48:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "domain": domain})
    return out


def _is_brand_row(c: dict, brand_l: str, dom_root: str) -> bool:
    """True si esta fila de la lista ES la marca (por nombre o por dominio)."""
    nm = (c.get("name") or "").lower()
    dom = (c.get("domain") or "").lower().replace("www.", "")
    if brand_l and (brand_l in nm or nm in brand_l) and len(nm) >= 3:
        return True
    if dom_root and dom and (dom_root == dom or dom_root in dom or dom in dom_root):
        return True
    return False


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
    # si no, por TLD; si no, se lo preguntamos a ChatGPT (entra al sitio y lo deduce).
    meta_country = (meta.get("country") or "").strip()
    meta_gl = (meta.get("gl") or "").strip()
    det = _country_from_domain(domain)
    if meta_country:
        country, gl = meta_country, (meta_gl or (det[1] if det else "es"))
    elif det:
        country, gl = det
    else:
        country, gl = "", ""
    have_country = bool(country)

    # SIN búsqueda web a proposito: mide si la IA CONOCE la marca de por si (no si
    # encuentra la URL). Es el reconocimiento real que importa para el GEO.
    q_know = (
        f"Sin usar búsqueda web, solo con lo que ya sabes: ¿conoces la empresa o marca \"{brand}\" "
        f"(sitio {domain})? Si la conoces con certeza, describe en 2-3 frases a qué se dedica. "
        f"Si NO tienes información fiable propia sobre ella, responde EXACTAMENTE con NO_LA_CONOZCO "
        f"y nada más. No inventes ni supongas por el nombre."
    )
    q_country = (
        f"Usa búsqueda web y entra en {full_url}. ¿En qué PAÍS está basado y opera principalmente este "
        f"negocio? Fíjate en el idioma, el prefijo telefónico, las direcciones o ciudades, la moneda y el "
        f"dominio. Responde SOLO así, sin nada más: Nombre del país|cc  (cc = código ISO de 2 letras, "
        f"p. ej. España|es, México|mx, Colombia|co, Argentina|ar). Si dudas, deduce por el idioma y el dominio."
    )

    try:
        async with httpx.AsyncClient() as client:
            # Ronda 0: si no sabemos el pais por TLD/contenido, ChatGPT lo determina mirando el sitio
            if not have_country:
                try:
                    rc = await _ask(client, prov, key, model, q_country, max_tokens=60, grounded=True)
                    mc = re.search(r"([A-Za-zÁÉÍÓÚÑÜáéíóúñü .'-]{2,40})\|\s*([A-Za-z]{2})", rc or "")
                    if mc:
                        country = mc.group(1).strip(" .|-")[:40]
                        gl = mc.group(2).lower()
                        have_country = bool(country)
                except Exception:  # noqa: BLE001
                    pass
            if not country:
                lang = (meta.get("lang") or "").lower()
                if lang.startswith("es"):
                    country, gl = "España", "es"
            if not gl:
                gl = "es"
            ctx_pais = f"en {country}" if country else "en su país"
            pais_txt = country or "su país"

            # Las busquedas SIEMPRE enfocadas al pais real del negocio
            q_queries = (
                f"Para el negocio de \"{brand}\" (sitio {full_url}; contexto: \"{service}\"), "
                f"escribe EXACTAMENTE 3 búsquedas que un cliente {ctx_pais} escribiría para encontrar ese tipo "
                f"de servicio SIN conocer la marca (búsquedas de categoría). Enfócalas SIEMPRE en {pais_txt}; "
                f"no uses otro país. Una por línea, sin numerar y sin la palabra PAIS."
            )
            q_gap = (
                f"Usa búsqueda web sobre {full_url}. En 2 frases y en lenguaje sencillo de negocio: "
                f"¿qué le falta a \"{brand}\" para que la IA la reconozca y la recomiende cuando alguien pide su "
                f"tipo de servicio {ctx_pais} (sin nombrar la marca)? Sé concreto y accionable. Sin viñetas."
            )
            q_gbp = (
                f"Usa búsqueda web (Google Maps). ¿La empresa \"{brand}\" ({full_url}) tiene una ficha de "
                f"Google Business / Google Maps activa {ctx_pais}? Responde SOLO una palabra: SI, NO o DUDOSO."
            )
            # Reconocimiento CON la web (buscando): para el contraste "sin web / con web"
            q_know_web = (
                f"Usa búsqueda web y visita {full_url}. ¿Qué es \"{brand}\" y a qué se dedica? "
                f"Describe en 1-2 frases lo que veas en el sitio. Si no encuentras el sitio, responde NO_ENCONTRADO."
            )

            # Ronda 1: busquedas + reconocimiento (sin y con web) + gap + ficha Google Business
            r_queries, r_know, r_gap, r_gbp, r_kw = await asyncio.gather(
                _ask(client, prov, key, model, q_queries, max_tokens=250, grounded=False),
                _ask(client, prov, key, model, q_know, max_tokens=500, grounded=False),
                _ask(client, prov, key, model, q_gap, max_tokens=300, grounded=True),
                _ask(client, prov, key, model, q_gbp, max_tokens=20, grounded=True),
                _ask(client, prov, key, model, q_know_web, max_tokens=300, grounded=True),
                return_exceptions=True,
            )
            queries_txt = "" if isinstance(r_queries, Exception) else (r_queries or "")
            know_txt = "" if isinstance(r_know, Exception) else (r_know or "")
            gap_txt = "" if isinstance(r_gap, Exception) else (r_gap or "")
            gbp_up = ("" if isinstance(r_gbp, Exception) else (r_gbp or "")).strip().upper()
            gbp = True if gbp_up.startswith(("SI", "SÍ", "YES")) else (False if gbp_up.startswith("NO") else None)
            kw_txt = ("" if isinstance(r_kw, Exception) else (r_kw or "")).strip()
            knows_with_web = bool(kw_txt) and "NO_ENCONTRADO" not in kw_txt.upper()
            web_desc = re.sub(r"\s+", " ", kw_txt).strip()[:400] if knows_with_web else ""

            # Parseo de las 3 busquedas de categoria
            cat_queries = []
            for ln in queries_txt.splitlines():
                ln = ln.strip(" -•\t0123456789.)")
                if 4 < len(ln) < 90 and "PAIS" not in ln.upper():
                    cat_queries.append(ln)
            cat_queries = cat_queries[:3]

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
    brand_l = brand.lower().strip()
    dom_root = domain.lower().split("/")[0].replace("www.", "")
    questions = []
    comps, seen = [], set()
    appears = 0
    valid = 0
    for i, search in enumerate(cat_queries or [service]):
        res = cat_results[i] if i < len(cat_results) else None
        txt = "" if (res is None or isinstance(res, Exception)) else (res or "")
        if not txt.strip():
            questions.append({"q": search, "appears": None, "named": [], "answer": ""})
            continue
        valid += 1
        # Solo la LISTA de recomendadas (antes de 'INCLUIDA:'). "Apareces" SOLO si la
        # marca esta en esa lista parseada; NO si tu nombre sale suelto en el texto
        # (la IA suele nombrarte para decir que NO te recomienda -> falso positivo).
        body = re.split(r"\bINCLUIDA:", txt, 1)[0]
        rows = _parse_companies(body)
        appears_here = any(_is_brand_row(c, brand_l, dom_root) for c in rows)
        if appears_here:
            appears += 1
        named = []
        for c in rows:
            if _is_brand_row(c, brand_l, dom_root):
                continue
            nm = (c.get("name") or "").strip()
            named.append(nm)
            if nm and nm.lower() not in seen:
                seen.add(nm.lower())
                comps.append(c)
        # Guarda un extracto legible de la respuesta real de la IA (para mostrarlo)
        excerpt = re.sub(r"\s+", " ", body).strip()[:300]
        questions.append({"q": search, "appears": appears_here,
                          "named": named[:4], "answer": excerpt})

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
        "knows_with_web": knows_with_web,
        "web_description": web_desc,
        "recommended": recommended,
        "gbp": gbp,
        "competitors": comps[:6],
        "questions": questions,
        "gap": gap,
        "category_queries": cat_queries,
        "gl": gl,
        "ai_score": score,
    }
