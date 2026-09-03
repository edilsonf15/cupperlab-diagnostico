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


def _engines() -> list[dict]:
    """Motores de IA disponibles segun las keys. Gemini usa busqueda en vivo."""
    engines = []
    gk = os.getenv("GEMINI_API_KEY", "").strip()
    if gk:
        engines.append({"name": "Gemini", "provider": "gemini", "key": gk,
                        "model": GEMINI_MODEL, "grounded": True})
    ok = os.getenv("OPENAI_API_KEY", "").strip()
    if ok:
        engines.append({"name": "ChatGPT", "provider": "openai", "key": ok,
                        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"), "grounded": True})
    ak = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if ak:
        engines.append({"name": "Claude", "provider": "anthropic", "key": ak,
                        "model": ANTHROPIC_MODEL, "grounded": False})
    return engines


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
    engines = _engines()
    if not engines:
        return None

    brand = derive_brand(meta, domain)
    service = derive_service(meta) or f"servicios de {brand}"

    q_know = (
        f"¿Conoces la empresa o marca \"{brand}\" (sitio web {domain})? Si la conoces con "
        f"certeza, describe en 2-3 frases a que se dedica. Si NO tienes informacion fiable, "
        f"responde EXACTAMENTE con NO_LA_CONOZCO y nada mas. No te inventes datos."
    )
    q_reco = (
        f"Busca en la web TIENDAS o EMPRESAS REALES que compitan DIRECTAMENTE con \"{brand}\" ({domain}): "
        f"el mismo tipo de negocio y la misma zona. Contexto del negocio: \"{service}\". "
        f"Dame 4 o 5 competidores REALES. Para cada uno, una linea con este formato exacto: "
        f"Nombre de la empresa | dominio.com  (su sitio web). "
        f"MUY IMPORTANTE: solo empresas/tiendas reales con web propia. NO listes tipos de producto, "
        f"materiales ni categorias (por ejemplo 'nylon', 'algodon', 'muebles') — eso NO son empresas. "
        f"Al final, en una linea aparte, escribe \"INCLUIDA: SI\" si recomendarias a \"{brand}\", o \"INCLUIDA: NO\"."
    )
    q_gap = (
        f"En 2 frases y en lenguaje sencillo de negocio: ¿que le falta a la empresa \"{brand}\" ({domain}) "
        f"para que la IA la reconozca y la recomiende cuando alguien pide su tipo de servicio (sin nombrarla)? "
        f"Se concreto y accionable. No uses vinetas."
    )
    q_queries = (
        f"Para la empresa \"{brand}\" ({service}), genera lo que un cliente escribiria en Google "
        f"para encontrar ese servicio SIN conocer la marca. Devuelve EXACTAMENTE 3 busquedas de "
        f"categoria, una por linea, sin numerar. Luego una ultima linea \"PAIS: xx\" con el codigo "
        f"de pais de 2 letras del mercado (es, us, mx, co, ar...). Nada mas."
    )

    try:
        async with httpx.AsyncClient() as client:
            # genera las busquedas con un motor fiable (OpenAI si esta; si no, el primero)
            gen = next((e for e in engines if e["provider"] == "openai"), engines[0])
            g_ground = gen["grounded"]
            tasks = [_query_engine(client, e, q_know, q_reco, brand) for e in engines]
            tasks.append(_ask(client, gen["provider"], gen["key"], gen["model"], q_queries, max_tokens=250))
            tasks.append(_ask(client, gen["provider"], gen["key"], gen["model"], q_gap, max_tokens=280, grounded=g_ground))
            results = await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as exc:  # noqa: BLE001
        return {"available": True, "error": str(exc), "brand": brand}

    gap_txt = "" if isinstance(results[-1], Exception) else (results[-1] or "")
    per_engine = [r for r in results[:-2] if isinstance(r, dict) and "name" in r]
    answered = [e for e in per_engine if not e.get("error") and e.get("knows") is not None]
    if not per_engine:
        return {"available": True, "error": "sin respuesta de los motores", "brand": brand}
    if not answered:
        # todos los motores fallaron (p.ej. limite temporal): no fingimos un veredicto
        return {"available": True, "error": "limite temporal de la IA", "brand": brand,
                "engines": {e["name"]: e for e in per_engine},
                "engine_names": [e["name"] for e in per_engine]}
    queries_txt = "" if isinstance(results[-2], Exception) else (results[-2] or "")

    # Busquedas de categoria + mercado
    cat_queries = []
    gl = "es"
    for ln in queries_txt.splitlines():
        ln = ln.strip(" -•\t")
        mp = re.match(r"(?i)PAIS:\s*([a-z]{2})", ln)
        if mp:
            gl = mp.group(1).lower()
        elif 4 < len(ln) < 80 and "PAIS" not in ln.upper():
            cat_queries.append(ln)
    cat_queries = cat_queries[:3]

    # Agregado: SOLO motores que respondieron de verdad
    engines_map = {e["name"]: e for e in per_engine}   # incluye estado de los que fallaron
    knows_any = any(e["knows"] for e in answered)
    reco_any = any(e.get("recommended") is True for e in answered)
    reco_all_no = all(e.get("recommended") is False for e in answered if e.get("recommended") is not None)
    knower = next((e for e in answered if e["knows"]), None)
    comps = []
    seen = set()
    for e in answered:
        for c in e.get("competitors", []):
            nm = (c.get("name") if isinstance(c, dict) else str(c)).strip()
            if nm and nm.lower() not in seen:
                seen.add(nm.lower())
                comps.append(c if isinstance(c, dict) else {"name": nm, "domain": None})

    gap = re.sub(r"\s+", " ", gap_txt).strip()[:400]
    n = len(answered)
    score = round(100 * (sum(1 for e in answered if e["knows"]) * 0.5 +
                         sum(1 for e in answered if e.get("recommended") is True) * 0.5) / n)

    return {
        "available": True,
        "brand": brand,
        "service": service,
        "engines": engines_map,
        "engine_names": [e["name"] for e in per_engine],
        "answered_names": [e["name"] for e in answered],
        "knows_brand": knows_any,
        "brand_description": (knower or answered[0]).get("know_raw", "")[:400] if knower else "",
        "recommended": True if reco_any else (False if reco_all_no else None),
        "competitors": comps[:6],
        "gap": gap,
        "category_queries": cat_queries,
        "gl": gl,
        "ai_score": score,
    }
