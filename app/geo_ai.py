"""
geo_ai v2 — Test GEO/LLMO: ¿qué contesta la IA cuando un cliente pregunta?

Qué cambia respecto a v1 (y por qué):
  * La IA ya NO es fuente de hechos. Ficha de Google, reseñas, categoría, ciudad y
    país llegan MEDIDOS (places.py + crawl) en `identity`. Aquí solo se pregunta lo
    que únicamente la IA puede responder: qué dice de la marca y a quién recomienda.
  * La pregunta a la IA es la del CLIENTE (P2), texto libre, sin "actúa como analista"
    ni formatos. Una llamada por búsqueda y por motor: así cada respuesta es real y
    distinta (en v1 iban las 3 en una llamada y el modelo copiaba la misma lista).
  * La estructuración se hace después, con un modelo mini y JSON con esquema (P3).
    Se acaban los parsers por regex sobre texto con citas y markdown.
  * Reconocimiento (P4) con JSON: `conoce` solo cuenta si el sector y el país que la
    IA cree coinciden con los medidos (evita homónimos).
  * Competidores = los que las IAs citaron de verdad (+ Google real vía serp.py en
    main). Ninguna lista "de memoria".
  * Presupuesto de tiempo por llamada y global. Cada motor que falla se marca
    "sin respuesta" y no arrastra a los demás.

Salida: mantiene TODAS las claves que leen pantalla, PDF y correo (ver ARQUITECTURA.md)
y añade `share_of_voice`, `by_engine`, `status`, `prompts_version`.

Prompts: P1..P4 abajo (y en PROMPTS.md). Sin ejemplos con marcas reales: anclan.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from urllib.parse import urlparse

import httpx

from i18n import L
import places as _places

PROMPTS_VERSION = "2.0"
AI_BUDGET = float(os.getenv("AI_GEO_BUDGET", "45"))          # tope global del bloque IA
CALL_TIMEOUT = float(os.getenv("AI_CALL_TIMEOUT", "30"))      # tope por llamada
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")      # mini: P1/P3/P4 y P2 en ChatGPT
OPENAI_MODEL_STRONG = os.getenv("OPENAI_MODEL_STRONG", OPENAI_MODEL)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

# La IA a veces nombra como "competidor" a gigantes globales o herramientas genéricas
# (Salesforce, HubSpot, Zoho, ChatGPT…) que NO son la competencia real del cliente.
# Se filtran para que "quién capta tu demanda" sean rivales de su mismo tamaño y sector.
_NOT_COMP_NAME = (
    "salesforce", "hubspot", "zoho", "pipedrive", "microsoft", "dynamics", "sap", "oracle",
    "ibm", "google", "openai", "chatgpt", "gpt-", "gemini", "copilot", "anthropic", "claude",
    "aws", "amazon", "meta ", "notion", "monday", "slack", "zendesk", "freshworks", "intercom",
    "docusign", "dialogflow", "watson", "einstein", "upwork", "freelancer", "fiverr", "canva",
    "kimi", "deepseek", "mistral", "perplexity", "wordpress", "shopify", "wix", "odoo",
    "manychat", "zapier", "n8n", "whatsapp", "telegram", "whatsapp business")
_GENERIC_COMP = {
    "", "ia", "ai", "crm", "erp", "chatbot", "chatbots", "bot", "software", "saas",
    "agente de ia", "agentes de ia", "inteligencia artificial", "dialogflow",
    "asistente virtual", "asistentes virtuales", "consultoría", "consultoria", "freelancers"}


def _is_real_competitor(name: str) -> bool:
    """True si el nombre parece una empresa rival real (no un gigante global ni un genérico)."""
    n = (name or "").strip().lower()
    if len(n) < 3 or n in _GENERIC_COMP:
        return False
    return not any(g in n for g in _NOT_COMP_NAME)
PERPLEXITY_MODEL = os.getenv("PERPLEXITY_MODEL", "sonar")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
# Motores que "responden al cliente" (P2). Por defecto 2 para contener coste; añade
# gemini en AI_ENGINES si quieres la matriz de 3.
DEFAULT_ENGINES = "openai,perplexity"

# --------------------------------------------------------------------------- #
# Marca (se conserva de v1: funciona bien)
# --------------------------------------------------------------------------- #
STOP = {"inicio", "home", "bienvenido", "bienvenida", "web", "sitio", "oficial",
        "the", "and", "para", "empresa", "servicios", "productos"}
_JUNK_BRAND = ("cloudflare", "just a moment", "attention required", "access denied",
               "acceso denegado", "checking your browser", "please wait", "un momento",
               "403 forbidden", "forbidden", "error 1", "ddos", "security check",
               "are you human", "captcha", "site not found", "account suspended",
               "under construction", "sucuri", "imperva", "incapsula")


def _is_junk_brand(s: str) -> bool:
    s = (s or "").strip().lower()
    return any(j in s for j in _JUNK_BRAND)


def _looks_like_domain(s: str) -> bool:
    s = (s or "").strip().lower()
    return bool(re.match(r"^(https?://)?(www\.)?[a-z0-9-]+\.[a-z]{2,}", s)) or (" " not in s and "." in s)


def derive_brand(meta: dict, domain: str) -> str:
    root = urlparse("https://" + domain).netloc or domain
    root = re.sub(r"^www\.", "", root).split(".")[0]
    root_norm = re.sub(r"[^a-z0-9]", "", root.lower())
    # 1) Organization.name del JSON-LD si el crawl lo trajo
    for k in ("org_name", "schema_org_name"):
        v = (meta.get(k) or "").strip()
        if v and not _is_junk_brand(v) and 2 <= len(v) <= 40:
            return v
    osn = (meta.get("og_site_name") or "").strip()
    if osn and not _looks_like_domain(osn) and not _is_junk_brand(osn):
        return osn
    title = (meta.get("title") or "").strip()
    if title and not _is_junk_brand(title):
        parts = re.split(r"\s[|\-–—:·]\s", title)
        parts = [p.strip() for p in parts if p.strip() and not _is_junk_brand(p)
                 and p.strip().lower() not in STOP]
        if parts:
            for p in parts:
                pn = re.sub(r"[^a-z0-9]", "", p.lower())
                if root_norm and (root_norm in pn or pn in root_norm):
                    words = p.split()
                    acc = ""
                    for i, w in enumerate(words):
                        acc += re.sub(r"[^a-z0-9]", "", w.lower())
                        if root_norm in acc or acc == root_norm:
                            cand = " ".join(words[:i + 1])
                            if 2 <= len(cand) <= 40:
                                return cand
                    cand = " ".join(words[:4])
                    if 2 <= len(cand) <= 40:
                        return cand
            cand = parts[0]
            if 2 <= len(cand) <= 40:
                return cand
    return root.capitalize()


# ccTLD -> (país, gl)
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
_NAME_GL = {
    "españa": "es", "spain": "es", "méxico": "mx", "mexico": "mx", "argentina": "ar",
    "chile": "cl", "colombia": "co", "perú": "pe", "peru": "pe", "uruguay": "uy",
    "ecuador": "ec", "bolivia": "bo", "paraguay": "py", "venezuela": "ve", "panamá": "pa",
    "panama": "pa", "guatemala": "gt", "costa rica": "cr", "república dominicana": "do",
    "estados unidos": "us", "united states": "us", "usa": "us", "portugal": "pt",
    "brasil": "br", "brazil": "br", "francia": "fr", "france": "fr", "italia": "it",
    "italy": "it", "alemania": "de", "germany": "de", "reino unido": "gb", "united kingdom": "gb",
}


def gl_from_name(name: str) -> str:
    return _NAME_GL.get((name or "").strip().lower(), "")


def country_from_domain(domain: str) -> tuple[str, str] | None:
    host = (domain or "").strip().lower().split("/")[0].split(":")[0]
    tld = host.rsplit(".", 1)[-1] if "." in host else ""
    if tld in CCTLD:
        return CCTLD[tld]
    return None


# --------------------------------------------------------------------------- #
# Motores
# --------------------------------------------------------------------------- #
def _engines() -> list[dict]:
    names = [n.strip().lower() for n in os.getenv("AI_ENGINES", DEFAULT_ENGINES).split(",") if n.strip()]
    out = []
    for n in names:
        if n == "openai" and os.getenv("OPENAI_API_KEY", "").strip():
            out.append({"name": "ChatGPT", "provider": "openai", "key": os.getenv("OPENAI_API_KEY").strip(),
                        "model": OPENAI_MODEL_STRONG})
        elif n == "perplexity" and os.getenv("PERPLEXITY_API_KEY", "").strip():
            out.append({"name": "Perplexity", "provider": "perplexity",
                        "key": os.getenv("PERPLEXITY_API_KEY").strip(), "model": PERPLEXITY_MODEL})
        elif n == "gemini" and os.getenv("GEMINI_API_KEY", "").strip():
            out.append({"name": "Gemini", "provider": "gemini", "key": os.getenv("GEMINI_API_KEY").strip(),
                        "model": GEMINI_MODEL})
    return out


def _mini() -> dict | None:
    """Modelo barato para P1/P3/P4 (sin búsqueda). OpenAI > Gemini > Anthropic."""
    if os.getenv("OPENAI_API_KEY", "").strip():
        return {"provider": "openai", "key": os.getenv("OPENAI_API_KEY").strip(), "model": OPENAI_MODEL}
    if os.getenv("GEMINI_API_KEY", "").strip():
        return {"provider": "gemini", "key": os.getenv("GEMINI_API_KEY").strip(), "model": GEMINI_MODEL}
    if os.getenv("ANTHROPIC_API_KEY", "").strip():
        return {"provider": "anthropic", "key": os.getenv("ANTHROPIC_API_KEY").strip(), "model": ANTHROPIC_MODEL}
    return None


def _strip_schema_for_gemini(schema: dict) -> dict:
    """Gemini no acepta additionalProperties ni algunos keywords."""
    if isinstance(schema, dict):
        return {k: _strip_schema_for_gemini(v) for k, v in schema.items()
                if k not in ("additionalProperties", "$schema", "strict")}
    if isinstance(schema, list):
        return [_strip_schema_for_gemini(x) for x in schema]
    return schema


def _json_from_text(txt: str):
    txt = (txt or "").strip()
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt, flags=re.I | re.M)
    try:
        return json.loads(txt)
    except Exception:  # noqa: BLE001
        m = re.search(r"\{.*\}", txt, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:  # noqa: BLE001
                return None
    return None


async def _call(client: httpx.AsyncClient, eng: dict, prompt: str, *, grounded: bool = False,
                schema: dict | None = None, schema_name: str = "out", max_tokens: int = 700) -> dict:
    """Llamada uniforme. Devuelve {text, json, citations[], error}.
    grounded=True -> búsqueda web en vivo (texto libre). schema -> salida JSON validada."""
    prov, key, model = eng["provider"], eng["key"], eng["model"]
    out = {"text": "", "json": None, "citations": [], "error": None}
    try:
        if prov == "openai":
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            body = {"model": model, "input": prompt, "max_output_tokens": max_tokens}
            if grounded:
                body["tools"] = [{"type": "web_search"}]
            if schema:
                body["text"] = {"format": {"type": "json_schema", "name": schema_name,
                                           "schema": schema, "strict": True}}
            r = await client.post("https://api.openai.com/v1/responses", headers=headers, json=body,
                                  timeout=CALL_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            txt = ""
            for o in data.get("output", []):
                for c in (o.get("content") or []):
                    if c.get("type") == "output_text":
                        txt += c.get("text", "")
                        for a in (c.get("annotations") or []):
                            u = a.get("url") or (a.get("url_citation") or {}).get("url")
                            if u:
                                out["citations"].append({"url": u, "title": a.get("title") or ""})
            out["text"] = txt.strip()
        elif prov == "perplexity":
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            body = {"model": model, "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens, "temperature": 0}
            if schema:
                body["response_format"] = {"type": "json_schema", "json_schema": {"schema": schema}}
            r = await client.post("https://api.perplexity.ai/chat/completions", headers=headers, json=body,
                                  timeout=CALL_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            out["text"] = ((data.get("choices") or [{}])[0].get("message", {}).get("content") or "").strip()
            for u in (data.get("citations") or []):
                out["citations"].append({"url": u, "title": ""})
            for sr in (data.get("search_results") or []):
                if sr.get("url"):
                    out["citations"].append({"url": sr["url"], "title": sr.get("title", "")})
        elif prov == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            gen = {"maxOutputTokens": max_tokens, "temperature": 0}
            body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": gen}
            if grounded:
                body["tools"] = [{"google_search": {}}]
            elif schema:
                gen["responseMimeType"] = "application/json"
                gen["responseSchema"] = _strip_schema_for_gemini(schema)
            r = await client.post(url, params={"key": key}, json=body, timeout=CALL_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            cand = (data.get("candidates") or [{}])[0]
            parts = (cand.get("content") or {}).get("parts") or []
            out["text"] = "".join(p.get("text", "") for p in parts).strip()
            gm = cand.get("groundingMetadata") or {}
            for ch in (gm.get("groundingChunks") or []):
                w = ch.get("web") or {}
                if w.get("uri"):
                    out["citations"].append({"url": w["uri"], "title": w.get("title") or ""})
        elif prov == "anthropic":
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
            p = prompt + ("\n\nResponde ÚNICAMENTE con el JSON, sin texto alrededor." if schema else "")
            body = {"model": model, "max_tokens": max_tokens, "temperature": 0,
                    "messages": [{"role": "user", "content": p}]}
            r = await client.post("https://api.anthropic.com/v1/messages", json=body, headers=headers,
                                  timeout=CALL_TIMEOUT)
            r.raise_for_status()
            out["text"] = "".join(b.get("text", "") for b in r.json().get("content", [])).strip()
        else:
            out["error"] = f"proveedor desconocido: {prov}"
            return out
        if schema:
            out["json"] = _json_from_text(out["text"])
            if out["json"] is None:
                out["error"] = "json inválido"
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        out["error"] = f"HTTP {code}" + (" (límite/cuota)" if code in (401, 403, 429) else "")
        try:
            out["error"] += ": " + exc.response.text[:160].replace("\n", " ")
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    return out


# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #
SCHEMA_P1 = {"type": "object", "additionalProperties": False,
             "properties": {"categoria": {"type": "string"},
                            "busquedas": {"type": "array", "items": {"type": "string"},
                                          "minItems": 3, "maxItems": 3}},
             "required": ["categoria", "busquedas"]}

SCHEMA_P3 = {"type": "object", "additionalProperties": False,
             "properties": {"respuestas": {"type": "array", "items": {
                 "type": "object", "additionalProperties": False,
                 "properties": {"id": {"type": "integer"},
                                "negocios": {"type": "array", "items": {
                                    "type": "object", "additionalProperties": False,
                                    "properties": {"nombre": {"type": "string"},
                                                   "dominio": {"type": ["string", "null"]}},
                                    "required": ["nombre", "dominio"]}}},
                 "required": ["id", "negocios"]}}},
             "required": ["respuestas"]}

SCHEMA_P4 = {"type": "object", "additionalProperties": False,
             "properties": {"conoce": {"type": "boolean"}, "descripcion": {"type": "string"},
                            "sector": {"type": "string"}, "pais": {"type": "string"},
                            "confianza": {"type": "string", "enum": ["alta", "media", "baja"]}},
             "required": ["conoce", "descripcion", "sector", "pais", "confianza"]}


def prompt_p1(idt: dict, lang: str) -> str:
    scope = idt.get("scope") or "pais"
    if lang == "en":
        return (
            "You are a potential customer, not an analyst. Using the data below, write how a customer "
            "would search for this kind of business in an AI assistant if they did NOT know the brand.\n\n"
            "Measured data (do not question it):\n"
            f"- Business: {idt['brand']}\n"
            f"- Category according to Google: {idt.get('category') or 'unknown'}\n"
            f"- Location: {idt.get('city') or '-'}, {idt.get('country') or '-'}\n"
            f"- Scope: {'city' if scope == 'ciudad' else 'country'}   (city = local business; country = sells/serves nationwide)\n"
            f"- Text from its website: \"{idt.get('snippet', '')}\"\n\n"
            "Return JSON with:\n"
            "- \"categoria\": what a customer would call this kind of business, 2-6 words, lowercase, no brand name. "
            "Be SPECIFIC about the style, segment and price tier if the text shows it (e.g. \"affordable casual "
            "youth fashion\", \"men's formal wear\", \"jeans and streetwear\", \"fast-food restaurant\"), so competitors "
            "match its SAME tier; avoid an over-generic category and slogans.\n"
            f"- \"busquedas\": exactly 3 different sentences, 5 to 12 words, exactly as a customer in {idt.get('country') or 'that country'} "
            f"would type them into an AI chat. If scope is city, all 3 include \"{idt.get('city')}\"; if country, none includes a city and "
            f"at most one mentions \"{idt.get('country')}\". One of the three must ask for recommendations of good brands or "
            "businesses of that SAME type, style and price range (\"recommend me good brands of…\", \"which shops of … do you "
            "suggest\", \"what are good options for …\"): direct competitors at its same tier, NOT luxury/designer if the brand "
            "isn't, NOR much smaller businesses. Do not use the brand or phrases from its website.")
    return (
        "Eres un cliente potencial, no un analista. Con los datos de abajo, escribe cómo buscaría un cliente "
        "este tipo de negocio en un asistente de IA si NO conociera la marca.\n\n"
        "Datos medidos (no los cuestiones):\n"
        f"- Negocio: {idt['brand']}\n"
        f"- Categoría según Google: {idt.get('category') or 'desconocida'}\n"
        f"- Ubicación: {idt.get('city') or '-'}, {idt.get('country') or '-'}\n"
        f"- Ámbito: {scope}   (ciudad = negocio local; pais = vende/atiende en todo el país)\n"
        f"- Texto de su web: \"{idt.get('snippet', '')}\"\n\n"
        "Devuelve JSON con:\n"
        "- \"categoria\": cómo llamaría un cliente a este tipo de negocio, en 2-6 palabras, en minúsculas, sin la marca. "
        "Sé ESPECÍFICO con el estilo, el segmento y el nivel de precio si se nota en el texto (p. ej. \"moda casual "
        "juvenil asequible\", \"ropa formal de hombre\", \"jeans y ropa urbana\", \"restaurante de comida rápida\"), "
        "para acertar con competidores de su MISMO nivel; evita una categoría demasiado genérica y los eslóganes.\n"
        f"- \"busquedas\": exactamente 3 frases distintas, de 5 a 12 palabras, tal y como las escribiría un cliente en "
        f"{idt.get('country') or 'ese país'} en un chat de IA. Si el ámbito es \"ciudad\", las 3 incluyen \"{idt.get('city')}\"; "
        f"si es \"pais\", ninguna incluye ciudad y como mucho una menciona \"{idt.get('country')}\". Una de las tres debe pedir "
        "recomendaciones de buenas marcas o negocios de ESE MISMO tipo, estilo y rango de precio "
        "(\"recomiéndame buenas marcas de…\", \"qué tiendas de … me recomiendas\", \"cuáles son buenas opciones de …\"): "
        "competidores directos a su mismo nivel, NI de lujo/diseñador si la marca no lo es, NI negocios mucho más pequeños. "
        "No uses la marca ni frases de su web.")


def prompt_p2(query: str, where: str, lang: str) -> str:
    # La pregunta del cliente, tal cual. Sin instrucciones ni formato: así responde la IA de verdad.
    if lang == "en":
        return f"I'm in {where}. {query}" if where else query
    return f"Estoy en {where}. {query}" if where else query


def prompt_p3(answers: list[str], lang: str) -> str:
    numbered = "\n\n".join(f"[{i + 1}]\n{a[:2500]}" for i, a in enumerate(answers))
    if lang == "en":
        return (
            "Below are answers from AI assistants to a customer's questions. Extract the specific businesses, brands "
            "or professionals each answer recommends or mentions, in order of appearance.\n\nRules:\n"
            "- Only named entities (companies, stores, brands, professionals). Do not include categories, cities, "
            "review platforms or directories (maps, social networks, generic marketplaces) unless the answer recommends "
            "them as the business itself.\n"
            "- If the answer gives a domain or URL for that business, put it in \"dominio\" without protocol or www. "
            "Otherwise null. Never invent domains.\n"
            "- If an answer recommends no specific business (only generic advice), return an empty list for it.\n"
            "- Keep names as written. Return one entry per answer id, ids 1.." + str(len(answers)) + ".\n\n"
            f"Answers:\n{numbered}")
    return (
        "Te paso respuestas de asistentes de IA a preguntas de un cliente. Extrae los negocios, marcas o profesionales "
        "concretos que cada respuesta recomienda o menciona, en el orden en que aparecen.\n\nReglas:\n"
        "- Solo entidades con nombre propio (empresas, tiendas, marcas, profesionales). No incluyas categorías, ciudades, "
        "plataformas de reseñas ni directorios (mapas, redes sociales, marketplaces genéricos) salvo que la respuesta los "
        "recomiende como el negocio en sí.\n"
        "- Si la respuesta da un dominio o URL para ese negocio, inclúyelo en \"dominio\" sin protocolo ni www. Si no, null. "
        "No inventes dominios.\n"
        "- Si una respuesta no recomienda ningún negocio concreto (solo consejos genéricos), devuelve una lista vacía para ella.\n"
        f"- Respeta el nombre tal y como aparece. Devuelve una entrada por respuesta, ids 1..{len(answers)}.\n\n"
        f"Respuestas:\n{numbered}")


def prompt_p4(brand: str, domain: str, lang: str, grounded: bool) -> str:
    if lang == "en":
        pre = "Use web search. " if grounded else "Without using web search, only from what you already know: "
        return (
            f"{pre}Do you know the company \"{brand}\" whose website is {domain}?\n\nAnswer in JSON:\n"
            "- \"conoce\": true only if you have concrete information about THAT company (not another one with a similar name). "
            "false if you don't know it or could only guess from the name.\n"
            f"- \"descripcion\": if conoce=true, one sentence of at most 30 words about what it does, starting with \"{brand}\". "
            "If false, empty string.\n"
            "- \"sector\": in 2-4 words, what it does according to what you know. Empty if unknown.\n"
            "- \"pais\": country where it operates according to what you know. Empty if unknown.\n"
            "- \"confianza\": \"alta\", \"media\" or \"baja\".\n"
            "Do not invent. conoce=false is better than a guess.")
    pre = "Usa búsqueda web. " if grounded else "Sin usar búsqueda web, solo con lo que ya sabes: "
    return (
        f"{pre}¿Conoces la empresa \"{brand}\" cuyo sitio web es {domain}?\n\nResponde en JSON:\n"
        "- \"conoce\": true solo si tienes información concreta sobre ESA empresa (no sobre otra con nombre parecido). "
        "false si no la conoces o solo puedes suponer por el nombre.\n"
        f"- \"descripcion\": si conoce=true, una frase de máximo 30 palabras sobre qué hace, que empiece por \"{brand}\". "
        "Si false, cadena vacía.\n"
        "- \"sector\": en 2-4 palabras, a qué se dedica según lo que sabes. Vacío si no sabes.\n"
        "- \"pais\": país donde opera según lo que sabes. Vacío si no sabes.\n"
        "- \"confianza\": \"alta\", \"media\" o \"baja\".\n"
        "No inventes. Es preferible conoce=false que una suposición.")


# --------------------------------------------------------------------------- #
# Utilidades de comparación
# --------------------------------------------------------------------------- #
_STOPW = {"de", "del", "la", "el", "los", "las", "y", "en", "para", "con", "a", "the", "of", "and",
          "tienda", "empresa", "servicios", "store", "company", "shop", "online"}


# Sinónimos frecuentes -> raíz canónica (para comparar el sector que cree la IA con el medido)
_SYN = [
    ("mueble", ("mueble", "muebles", "mobiliario", "furniture", "interiorismo", "interior", "decoracion", "decor", "hogar", "home")),
    ("legal", ("abogado", "abogados", "juridico", "juridica", "legal", "law", "lawyer", "lawyers", "bufete", "despacho")),
    ("dental", ("dental", "dentista", "odontologia", "odontologo", "dentist", "dentistry", "clinica")),
    ("salud", ("salud", "medico", "medica", "medicina", "health", "clinic", "terapia", "fisioterapia", "acupuntura")),
    ("moda", ("ropa", "moda", "fashion", "clothing", "apparel", "textil", "prendas", "boutique")),
    ("calzado", ("calzado", "zapatos", "zapateria", "shoes", "footwear", "sneakers")),
    ("belleza", ("estetica", "belleza", "beauty", "peluqueria", "salon", "spa", "cosmetica")),
    ("marketing", ("marketing", "publicidad", "agencia", "agency", "advertising", "digital", "seo", "branding")),
    ("software", ("software", "tecnologia", "technology", "tech", "app", "saas", "informatica", "ia", "inteligencia")),
    ("inmobiliaria", ("inmobiliaria", "inmuebles", "real", "estate", "propiedades", "pisos", "viviendas")),
    ("restauracion", ("restaurante", "restaurant", "gastronomia", "comida", "food", "cafeteria", "bar", "catering")),
    ("turismo", ("hotel", "turismo", "tourism", "travel", "viajes", "alojamiento", "hostel")),
    ("formacion", ("academia", "formacion", "educacion", "school", "escuela", "cursos", "training", "colegio")),
    ("automocion", ("taller", "coches", "automocion", "automotive", "vehiculos", "car", "cars", "mecanica")),
    ("construccion", ("reformas", "construccion", "construction", "obras", "arquitectura", "contractor")),
    ("finanzas", ("seguros", "insurance", "asesoria", "contable", "fiscal", "gestoria", "finanzas", "accounting")),
    ("mascotas", ("veterinario", "veterinaria", "mascotas", "pet", "pets", "animales")),
    ("deporte", ("gimnasio", "gym", "fitness", "deporte", "sports", "entrenamiento")),
    ("plastico", ("plastico", "plasticos", "plastic", "inyeccion", "moldes", "envases", "packaging")),
]
_SYN_MAP = {w: root for root, ws in _SYN for w in ws}


def _tokens(s: str) -> set[str]:
    out = set()
    for w in re.split(r"[^a-z0-9]+", _places._norm(s)):
        if len(w) > 2 and w not in _STOPW:
            out.add(_SYN_MAP.get(w, w))
    return out


def sector_compatible(ai_sector: str, category: str, snippet: str = "") -> bool:
    """True si el sector que cree la IA casa con la categoría medida (o no hay categoría)."""
    if not category and not snippet:
        return True
    a = _tokens(ai_sector)
    if not a:
        return False
    ref = _tokens(category) | _tokens(snippet)
    if a & ref:
        return True
    # raíces (muebl~mueble~mobiliario no casan por token; usa prefijos de 5)
    pa = {w[:5] for w in a}
    pr = {w[:5] for w in ref}
    return bool(pa & pr)


def country_compatible(ai_country: str, country: str, cc: str) -> bool:
    if not country and not cc:
        return True
    if not ai_country:
        return True  # la IA no se pronuncia: no penaliza
    g = gl_from_name(ai_country)
    if g and cc:
        return g == cc.lower()
    return _places._norm(ai_country) == _places._norm(country)


def _host(u: str) -> str:
    return _places._host(u)


def _is_brand(item: dict, brand: str, dom: str) -> bool:
    d = _host(item.get("dominio") or item.get("domain") or "")
    if d and dom and (d == dom or d.endswith("." + dom) or dom.endswith("." + d)):
        return True
    return _places.name_similarity(item.get("nombre") or item.get("name") or "", brand) >= 0.85


# --------------------------------------------------------------------------- #
# Motor principal
# --------------------------------------------------------------------------- #
async def run_geo(identity: dict, lang: str = "es") -> dict:
    """identity = {brand, domain, city, country, cc, gl, scope('ciudad'|'pais'),
    category (de Places, en lenguaje de cliente), snippet (título+desc+H1), places_found}.
    Devuelve el dict geo_ai (claves legacy + nuevas)."""
    t0 = time.monotonic()
    # Marca NACIONAL (ámbito país): la ciudad de su sede (p. ej. la de una cadena) NO debe
    # localizar las búsquedas ni la zona; si no, la IA devuelve tienditas de ese municipio en
    # vez de sus competidores reales de nivel nacional. Solo los negocios "ciudad" usan ciudad.
    identity = dict(identity)
    if (identity.get("scope") or "pais") == "pais":
        identity["city"] = ""
    brand = identity.get("brand") or identity.get("domain")
    domain = _host(identity.get("domain", ""))
    engines = _engines()
    mini = _mini()
    base = {
        "available": bool(engines or mini), "status": "skipped", "prompts_version": PROMPTS_VERSION,
        "brand": brand, "domain": domain, "sector": identity.get("category") or "",
        "zona": ", ".join(x for x in (identity.get("city"), identity.get("country")) if x),
        "country": identity.get("country") or "", "gl": identity.get("gl") or "",
        "engine_names": [e["name"] for e in engines], "answered_names": [], "engines": [],
        "recognition": "none", "knows": False, "knows_brand": False, "knows_with_web": False,
        "brand_description": "", "mentions": "", "recommended": None, "reco_hits": 0, "reco_total": 0,
        "share_of_voice": None, "questions": [], "competitors": [], "sources": [],
        "category_queries": [], "keywords": [], "entities": [], "gap": "", "content": None,
        "gbp": None, "gbp_reviews_n": None, "gbp_rating": None, "gbp_category": "",
        "ai_score": None, "limited": False, "error": None, "note": "", "by_engine": {},
        "debug": [], "elapsed_ms": 0,
    }
    if not engines or not mini:
        base["note"] = "Sin motores de IA configurados"
        base["error"] = "no_engines"
        return base

    where = identity.get("city") if identity.get("scope") == "ciudad" else identity.get("country")
    where = where or identity.get("country") or ""

    async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}) as client:
        # ---- P1: categoría en lenguaje de cliente + 3 búsquedas reales ----
        p1 = await _call(client, mini, prompt_p1(identity, lang), schema=SCHEMA_P1, schema_name="p1",
                         max_tokens=300)
        queries: list[str] = []
        category = identity.get("category") or ""
        if p1["json"]:
            category = (p1["json"].get("categoria") or category or "").strip().lower()[:60]
            queries = [q.strip() for q in (p1["json"].get("busquedas") or []) if q and q.strip()][:3]
        else:
            base["debug"].append(f"P1: {p1['error']}")
        # Filtro anti-eslogan: ninguna búsqueda puede contener la marca ni una frase de la web
        snip_tok = _tokens(identity.get("snippet", ""))
        clean = []
        for q in queries:
            qt = _tokens(q)
            if brand and _places._norm(brand) in _places._norm(q):
                continue
            if snip_tok and len(qt & snip_tok) >= max(4, int(len(qt) * 0.7)):
                continue
            clean.append(q)
        queries = clean
        if len(queries) < 3:
            cat = category or (L("este tipo de negocio", "this kind of business"))
            fill = ([L(f"dónde encontrar {cat} en {where}", f"where to find {cat} in {where}"),
                     L(f"recomiéndame {cat} en {where}", f"recommend me {cat} in {where}"),
                     L(f"qué {cat} hay en {where}", f"which {cat} are there in {where}")]
                    if where else [f"{L('mejores', 'best')} {cat}", L(f"recomiéndame {cat}", f"recommend me {cat}"),
                                   L(f"qué {cat} hay", f"which {cat} are there")])
            for f in fill:
                if len(queries) >= 3:
                    break
                if f not in queries:
                    queries.append(f)
        base["category_queries"] = queries
        base["sector"] = category or base["sector"]

        # ---- P2 (cliente, por motor y búsqueda) + P4 (reconocimiento, motor primario) ----
        primary = engines[0]
        tasks = {}
        for e in engines:
            for i, q in enumerate(queries):
                tasks[(e["name"], i)] = _call(client, e, prompt_p2(q, where, lang), grounded=True, max_tokens=700)
        tasks["p4_mem"] = _call(client, mini, prompt_p4(brand, domain, lang, False), schema=SCHEMA_P4,
                                schema_name="p4", max_tokens=250)
        # Con búsqueda: JSON estricto no se combina con web_search en todos los proveedores;
        # se pide JSON en el prompt y se parsea con tolerancia.
        tasks["p4_web"] = _call(client, primary, prompt_p4(brand, domain, lang, True), grounded=True,
                                max_tokens=300)
        keys = list(tasks.keys())
        try:
            results = await asyncio.wait_for(asyncio.gather(*tasks.values(), return_exceptions=True),
                                             timeout=AI_BUDGET)
        except asyncio.TimeoutError:
            results = [Exception("timeout global IA")] * len(keys)
        res = {k: (r if isinstance(r, dict) else {"text": "", "json": None, "citations": [], "error": str(r)})
               for k, r in zip(keys, results)}

        # ---- P3: extracción de negocios de todas las respuestas del cliente ----
        answers, amap = [], []
        for e in engines:
            for i in range(len(queries)):
                r = res[(e["name"], i)]
                if r["text"] and not r["error"]:
                    answers.append(r["text"])
                    amap.append((e["name"], i))
        extracted: dict[tuple, list[dict]] = {}
        if answers:
            p3 = await _call(client, mini, prompt_p3(answers, lang), schema=SCHEMA_P3, schema_name="p3",
                             max_tokens=1500)
            if p3["json"]:
                for row in p3["json"].get("respuestas") or []:
                    try:
                        idx = int(row.get("id")) - 1
                    except Exception:  # noqa: BLE001
                        continue
                    if 0 <= idx < len(amap):
                        extracted[amap[idx]] = [b for b in (row.get("negocios") or []) if b.get("nombre")]
            else:
                base["debug"].append(f"P3: {p3['error']}")

    # ---- Medición por motor ----
    by_engine, engines_out, questions = {}, [], []
    comp_agg: dict[str, dict] = {}
    total_mentions = 0
    brand_mentions = 0
    for e in engines:
        rows, hits, valid, named_all = [], 0, 0, []
        for i, q in enumerate(queries):
            r = res[(e["name"], i)]
            ok = bool(r["text"]) and not r["error"]
            items = extracted.get((e["name"], i), [])
            appears = None
            pos = None
            named = []
            if ok:
                valid += 1
                appears = False
                for k, it in enumerate(items, 1):
                    if _is_brand(it, brand, domain):
                        appears = True
                        pos = pos or k
                    else:
                        nm = (it.get("nombre") or "").strip()
                        if nm and _is_real_competitor(nm):
                            named.append(nm)
                            key = _places._norm(nm)
                            c = comp_agg.setdefault(key, {"name": nm, "domain": _host(it.get("dominio") or ""),
                                                          "cited_by": set(), "hits": 0})
                            if not c["domain"] and it.get("dominio"):
                                c["domain"] = _host(it["dominio"])
                            c["cited_by"].add(e["name"])
                            c["hits"] += 1
                if appears:
                    hits += 1
                total_mentions += len(items)
                brand_mentions += 1 if appears else 0
            rows.append({"q": q, "appears": appears, "position": pos, "named": named[:5],
                         "answer": (r["text"] or "")[:600], "error": r["error"]})
            named_all += named
        by_engine[e["name"]] = {"provider": e["provider"], "model": e["model"], "hits": hits, "valid": valid,
                                "rows": rows, "error": None if valid else "sin respuesta"}
        engines_out.append({"name": e["name"], "provider": e["provider"], "knows": None, "recognition": None,
                            "recommended": (None if not valid else (True if hits >= max(1, (valid + 1) // 2) else
                                                                  (False if hits == 0 else None))),
                            "reco_hits": hits, "reco_total": valid, "cites": 0, "sources": [], "proof": ""})
        if valid:
            base["answered_names"].append(e["name"])

    # Preguntas agregadas (legacy: una fila por búsqueda; appears = en algún motor)
    for i, q in enumerate(queries):
        per = [by_engine[e["name"]]["rows"][i] for e in engines]
        valid_rows = [p for p in per if p["appears"] is not None]
        questions.append({
            "q": q,
            "appears": (any(p["appears"] for p in valid_rows) if valid_rows else None),
            "named": list(dict.fromkeys(n for p in valid_rows for n in p["named"]))[:5],
            "answer": next((p["answer"] for p in valid_rows if p["answer"]), ""),
            "by_engine": {e["name"]: {"appears": per[j]["appears"], "position": per[j]["position"]}
                          for j, e in enumerate(engines)},
        })
    valid_total = sum(v["valid"] for v in by_engine.values())
    hits_total = sum(v["hits"] for v in by_engine.values())

    # ---- Reconocimiento (P4) ----
    mem = res["p4_mem"]["json"] or {}
    webj = res["p4_web"]["json"] or _json_from_text(res["p4_web"]["text"]) or {}
    cat_ref = identity.get("category") or base["sector"]

    def _ok(j: dict) -> bool:
        return bool(j.get("conoce")) and j.get("confianza") != "baja" \
            and sector_compatible(j.get("sector", ""), cat_ref, identity.get("snippet", "")) \
            and country_compatible(j.get("pais", ""), identity.get("country", ""), identity.get("cc", ""))
    knows_mem = _ok(mem)
    knows_web = _ok(webj)
    recognition = "strong" if knows_mem else ("weak" if knows_web else "none")
    desc = (webj.get("descripcion") if knows_web else "") or (mem.get("descripcion") if knows_mem else "") or ""
    desc = re.sub(r"\s+", " ", desc).strip()[:300]
    if mem.get("conoce") and not knows_mem:
        base["debug"].append(f"P4 mem descartado: sector='{mem.get('sector')}' pais='{mem.get('pais')}' conf={mem.get('confianza')}")

    # Fuentes que cita la IA al describir la marca (externas al propio dominio)
    srcs, seen = [], set()
    for c in res["p4_web"]["citations"]:
        h = _host(c.get("url", ""))
        if not h or h in seen or "vertexaisearch" in h or "googleusercontent" in h:
            continue
        seen.add(h)
        own = bool(domain and (h == domain or h.endswith("." + domain)))
        srcs.append({"domain": h, "url": c.get("url"), "own": own, "title": c.get("title", "")})
    if engines_out:
        engines_out[0].update(knows=knows_mem or knows_web, recognition=recognition,
                              cites=len([s for s in srcs if not s["own"]]), sources=srcs[:8], proof=desc)

    # ---- Competidores citados de verdad por las IAs ----
    # Preferimos los FUERTES y consistentes: los que varios motores repiten (los líderes
    # que dominan la respuesta), no menciones sueltas de marcas pequeñas/artesanales
    # (que salían y parecían "los más malos"). Si hay pocos fuertes, completamos por fuerza.
    comps = sorted(comp_agg.values(), key=lambda c: (-len(c["cited_by"]), -c["hits"]))
    # SOLO competencia con CONSENSO real: citada por 2+ motores de IA. Nada de menciones
    # sueltas de una sola IA (suelen ser marcas diminutas o inventadas: el ruido que hacía
    # que la lista pareciera floja). Si no hay consenso, la lista se completa después con
    # quien REALMENTE rankea en Google para la categoría (main.py), que es competencia real.
    strong = [c for c in comps if len(c["cited_by"]) >= 2]
    competitors = [{"name": c["name"], "domain": c["domain"] or None, "cited_by": sorted(c["cited_by"]),
                    "hits": c["hits"], "source": "ai"} for c in strong[:6]]

    # ---- Agregados ----
    share = (brand_mentions / total_mentions) if total_mentions else None
    if valid_total:
        recommended = True if hits_total >= max(1, (valid_total + 1) // 2) else (False if hits_total == 0 else None)
    else:
        recommended = None
    knows = knows_mem or knows_web
    reco_frac = (hits_total / valid_total) if valid_total else 0.0
    ai_score = round(100 * (0.4 * (1 if knows_mem else (0.55 if knows_web else 0)) + 0.6 * reco_frac)) \
        if (valid_total or knows) else None
    failed_engines = [n for n, v in by_engine.items() if not v["valid"]]
    limited = bool(valid_total == 0 and any("límite" in (r.get("error") or "") or "429" in (r.get("error") or "")
                                             for k, r in res.items() if isinstance(k, tuple)))

    base.update({
        "status": "ok" if valid_total else ("failed" if failed_engines else "partial"),
        "recognition": recognition, "knows": knows, "knows_brand": knows, "knows_with_web": knows_web,
        "brand_description": desc, "mentions": desc, "proof": desc,
        "recommended": recommended, "reco_hits": hits_total, "reco_total": valid_total,
        "share_of_voice": (round(share, 3) if share is not None else None),
        "questions": questions, "competitors": competitors, "sources": srcs[:8],
        "engines": engines_out, "by_engine": by_engine, "ai_score": ai_score, "limited": limited,
        "note": ("" if not failed_engines else L(f"Sin respuesta de: {', '.join(failed_engines)}",
                                                 f"No answer from: {', '.join(failed_engines)}")),
        "error": ("sin respuesta de la IA" if valid_total == 0 and not knows else None),
        "elapsed_ms": round((time.monotonic() - t0) * 1000),
    })
    return base


# Compat: main.py v1 importaba estos nombres.
run_ai_geo_fast = None  # eliminado en v2 (usar run_geo)
