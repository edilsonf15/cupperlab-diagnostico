"""
Motor de diagnostico rapido SEO + GEO de Cupperlab.

Corre en vivo, con presupuesto de tiempo (<50 s), lo que se puede medir sin
accesos: salud tecnica, on-page y preparacion para la IA (GEO/LLMO). No inventa
nada: cada senal se comprueba contra el dominio real. Lo que no se puede medir
sin accesos (PageSpeed sin API key, indexacion exacta) se marca como pendiente.
"""

from __future__ import annotations

import asyncio
import os
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import httpx
from bs4 import BeautifulSoup

from i18n import L  # idioma del analisis (ES/EN)

# ---- Presupuestos de tiempo (segundos) ---------------------------------------
TOTAL_BUDGET = float(os.getenv("ANALYSIS_TOTAL_BUDGET", "45"))
HOME_TIMEOUT = 12.0
FILE_TIMEOUT = 8.0
LINK_TIMEOUT = 9.0
PSI_TIMEOUT = 60.0
AI_WAIT = float(os.getenv("AI_GEO_WAIT", "26"))
LINK_SAMPLE = 22          # URLs (enlaces internos + sitemap) a comprobar para 404
LINK_CONCURRENCY = 8

# UA de navegador real: muchos sitios (Cloudflare, WAFs) devuelven una página de
# RETO/bloqueo a los bots. Con un UA de bot, el análisis se hacía sobre esa
# página de reto (p. ej. la marca salía "Cloudflare") en vez del sitio real.
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.6",
    "Upgrade-Insecure-Requests": "1",
}

GEO_SCHEMA_TYPES = {
    "organization", "localbusiness", "website", "webpage", "product",
    "faqpage", "article", "breadcrumblist", "service", "professionalservice",
}


# ---- Utilidades --------------------------------------------------------------

def normalize_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    p = urlparse(raw)
    netloc = p.netloc or p.path
    return f"{p.scheme}://{netloc}".rstrip("/")


def domain_of(url: str) -> str:
    return urlparse(url).netloc.replace("www.", "")


@dataclass
class Check:
    key: str
    label: str
    earned: float
    possible: float
    ok: bool
    detail: str = ""


@dataclass
class Result:
    url: str = ""
    final_url: str = ""
    domain: str = ""
    reachable: bool = False
    error: str = ""
    analyzed_at: str = ""
    elapsed: float = 0.0
    score: int = 0
    grade: str = ""
    categories: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    signals: dict = field(default_factory=dict)
    ssl_cert: dict = field(default_factory=dict)
    findings_good: list = field(default_factory=list)
    findings_improve: list = field(default_factory=list)
    psi: dict | None = None
    geo_ai: dict | None = None
    authority: dict | None = None
    html: str = ""  # HTML final de la home (no se serializa: main lo saca del dict)


# ---- Fetchers ----------------------------------------------------------------

async def _get(client: httpx.AsyncClient, url: str, timeout: float, method: str = "GET"):
    try:
        t0 = time.perf_counter()
        r = await client.request(method, url, timeout=timeout, follow_redirects=True)
        return r, time.perf_counter() - t0
    except Exception as exc:  # noqa: BLE001
        return exc, 0.0


def html_text(r) -> str:
    """Decodifica el HTML con el charset CORRECTO. httpx `.text` adivina mal cuando el
    servidor no declara charset y produce mojibake (últimas -> �ltimas), que luego rompe
    la detección de títulos/H1/textos. Prioriza: charset del header -> <meta charset> del
    propio HTML -> UTF-8. Es la forma fiable de leer páginas con acentos."""
    try:
        raw = r.content
    except Exception:  # noqa: BLE001
        return r.text if hasattr(r, "text") else ""
    if not raw:
        return ""
    enc = None
    _CHARSET = re.compile(r"charset=['\"]?([\w-]+)", re.I)
    m = _CHARSET.search(r.headers.get("content-type", ""))
    if m:
        enc = m.group(1)
    if not enc:
        head = raw[:4096].decode("ascii", "ignore")
        mm = _CHARSET.search(head)
        enc = mm.group(1) if mm else "utf-8"
    if enc.lower() in ("iso-8859-1", "latin-1", "latin1", "ascii", "us-ascii"):
        # muchos servidores declaran latin-1 por defecto sirviendo UTF-8 real: si el
        # contenido decodifica limpio como UTF-8, ese es el bueno.
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            pass
    try:
        return raw.decode(enc, errors="replace")
    except (LookupError, TypeError):
        return raw.decode("utf-8", errors="replace")


async def fetch_home(client: httpx.AsyncClient, url: str):
    # Reintenta https ANTES de caer a http: un fallo transitorio (timeout, corte
    # de red) no significa que el sitio no tenga SSL. Sin esto, un timeout
    # esporádico marcaba "sin HTTPS" en falso y hacía variar el resultado entre
    # corridas.
    last: Exception | None = None
    for _ in range(3):
        r, dt = await _get(client, url, HOME_TIMEOUT)
        if not isinstance(r, Exception):
            return r, "", dt
        last = r
    # Solo tras fallar https de verdad varias veces, prueba http://
    alt = url.replace("https://", "http://", 1)
    r, dt = await _get(client, alt, HOME_TIMEOUT)
    if isinstance(r, Exception):
        return None, str(last or r), 0.0
    return r, "", dt


async def fetch_text(client: httpx.AsyncClient, url: str):
    r, _ = await _get(client, url, FILE_TIMEOUT)
    if isinstance(r, Exception):
        return None, None
    return r.status_code, (html_text(r) if r.status_code < 400 else None)


async def check_status(client: httpx.AsyncClient, url: str):
    r, _ = await _get(client, url, LINK_TIMEOUT, method="GET")
    if isinstance(r, Exception):
        return url, None            # error de red, NO es un 404 real
    return url, r.status_code


# ---- Parsers ----------------------------------------------------------------

def parse_home(html: str, base_url: str) -> dict:
    soup = BeautifulSoup(html or "", "html.parser")
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""

    def meta(name=None, prop=None):
        if name:
            tag = soup.find("meta", attrs={"name": re.compile(f"^{name}$", re.I)})
        else:
            tag = soup.find("meta", attrs={"property": re.compile(f"^{prop}$", re.I)})
        return (tag.get("content") or "").strip() if tag else ""

    h1s = [h.get_text(strip=True) for h in soup.find_all("h1")]
    h2s = soup.find_all("h2")
    h3s = soup.find_all("h3")

    # hreflang / idiomas
    hreflangs = []
    for lk in soup.find_all("link", attrs={"rel": re.compile("alternate", re.I)}):
        hl = lk.get("hreflang")
        if hl:
            hreflangs.append(hl.strip().lower())
    hreflangs = sorted(set(hreflangs))

    # cobertura de ALT en imagenes
    imgs = soup.find_all("img")
    img_total = len(imgs)
    img_alt = sum(1 for i in imgs if (i.get("alt") or "").strip())

    # JSON-LD structured data. @type puede ser string o lista (["LocalBusiness","Store"]),
    # así que capturamos ambos, no solo el string suelto (falso negativo si viene en array).
    schema_types = []
    for s in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        raw = s.string or s.get_text() or ""
        for m in re.findall(r'"@type"\s*:\s*(\[[^\]]*\]|"[^"]+")', raw):
            for t in re.findall(r'"([^"]+)"', m):
                schema_types.append(t.strip())
    has_sameas = bool(re.search(r'"sameAs"', html or ""))

    html_tag = soup.find("html")
    lang = (html_tag.get("lang") if html_tag else "") or ""

    canonical_tag = soup.find("link", attrs={"rel": re.compile("canonical", re.I)})
    canonical = canonical_tag.get("href") if canonical_tag else ""

    favicon = bool(soup.find("link", attrs={"rel": re.compile("icon", re.I)}))
    viewport = meta(name="viewport")
    # Meta robots del home: noindex/nofollow aquí es CRÍTICO (te saca de Google/IA)
    meta_robots = (meta(name="robots") or "").lower()
    robots_noindex = "noindex" in meta_robots
    robots_nofollow = "nofollow" in meta_robots

    text = soup.get_text(" ", strip=True)
    word_count = len(text.split())

    schema_low = [t.lower() for t in schema_types]
    # FAQ / Q&A: schema FAQPage/QAPage (señal fiable) o BASTANTES titulares en forma de
    # pregunta (>=3). Con solo 2 dábamos FAQ por cualquier par de titulares con "?".
    heading_texts = [h.get_text(strip=True) for h in (h2s + h3s)]
    q_headings = sum(1 for t in heading_texts if t.endswith("?"))
    has_faq = ("faqpage" in schema_low) or ("qapage" in schema_low) or q_headings >= 3
    # Ficha de contacto (NAP): tel: link, schema de contacto/direccion o telefono real en texto
    has_phone = (bool(soup.find("a", href=re.compile(r"^tel:", re.I)))
                 or bool(re.search(r"(?:\+|\b00)\s?\d[\d\s().\-]{6,}\d", text)))
    has_contact = (has_phone
                   or any(x in schema_low for x in ("contactpoint", "postaladdress", "localbusiness")))
    # Señales locales (para "Presencia local y reputación") — sin APIs nuevas.
    # Dirección: exige un VALOR real de calle, no solo que exista el @type PostalAddress
    # o la clave "streetAddress" vacía (una Organization con solo addressCountry daba
    # dirección en falso, p. ej. ainoa.app).
    html_low = (html or "").lower()
    _itemprop_addr = soup.find(attrs={"itemprop": re.compile("streetAddress", re.I)})
    has_address = (
        bool(re.search(r'"streetaddress"\s*:\s*"[^"]{4,}"', html_low))
        or (bool(_itemprop_addr) and len((_itemprop_addr.get_text(strip=True) or "")) >= 4))
    # Mapa incrustado: solo cuenta un iframe de mapa (un enlace a Maps NO es un mapa incrustado).
    has_map = bool(soup.find("iframe", src=re.compile(
        r"google\.[a-z.]+/maps|maps\.google|/maps/embed|openstreetmap|mapbox", re.I)))
    has_hours = ("openinghours" in html_low or "opening_hours" in html_low)
    has_geo = ("geocoordinates" in schema_low or '"latitude"' in html_low)

    return {
        "title": title,
        "description": meta(name="description"),
        "h1_count": len(h1s),
        "h1_first": h1s[0] if h1s else "",
        "h2_count": len(h2s),
        "lang": lang.strip(),
        "h3_count": len(h3s),
        "hreflangs": hreflangs,
        "img_total": img_total,
        "img_alt": img_alt,
        "canonical": bool(canonical),
        "favicon": favicon,
        "viewport": bool(viewport),
        "meta_robots": meta_robots,
        "robots_noindex": robots_noindex,
        "robots_nofollow": robots_nofollow,
        "og_title": meta(prop="og:title"),
        "og_image": meta(prop="og:image"),
        "og_desc": meta(prop="og:description"),
        "og_site_name": meta(prop="og:site_name"),
        "twitter": bool(meta(name="twitter:card")),
        "schema_types": sorted(set(t.lower() for t in schema_types)),
        "schema_raw_types": sorted(set(schema_types)),
        "has_sameas": has_sameas,
        "word_count": word_count,
        "has_faq": has_faq,
        "has_contact": has_contact,
        "has_phone": has_phone,
        "has_address": has_address,
        "has_map": has_map,
        "has_hours": has_hours,
        "has_geo": has_geo,
    }


def _merge_meta(a: dict, b: dict) -> dict:
    """Combina las senales del HTML estatico (a) con las del renderizado (b),
    quedandose con lo MEJOR (union de schema, OR de booleanos, max de conteos)."""
    if not b:
        return a
    out = dict(a)
    out["schema_types"] = sorted(set(a.get("schema_types", [])) | set(b.get("schema_types", [])))
    out["schema_raw_types"] = sorted(set(a.get("schema_raw_types", [])) | set(b.get("schema_raw_types", [])))
    out["hreflangs"] = sorted(set(a.get("hreflangs", [])) | set(b.get("hreflangs", [])))
    for k in ("has_faq", "has_contact", "has_sameas", "canonical", "favicon", "viewport", "twitter",
              "has_phone", "has_address", "has_map", "has_hours", "has_geo"):
        out[k] = bool(a.get(k)) or bool(b.get(k))
    for k in ("word_count", "h1_count", "h2_count", "h3_count", "img_total", "img_alt"):
        out[k] = max(a.get(k, 0) or 0, b.get(k, 0) or 0)
    for k in ("title", "description", "og_title", "og_image", "og_desc", "og_site_name", "lang", "h1_first"):
        out[k] = a.get(k) or b.get(k)
    return out


def parse_sitemap_locs(xml_text: str) -> tuple[list[str], bool]:
    """Devuelve (locs, is_index)."""
    locs = []
    is_index = False
    try:
        root = ET.fromstring(xml_text.encode("utf-8", "ignore"))
        tag = root.tag.lower()
        is_index = tag.endswith("sitemapindex")
        for loc in root.iter():
            if loc.tag.lower().endswith("loc") and loc.text:
                locs.append(loc.text.strip())
    except Exception:  # noqa: BLE001
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml_text or "")
        is_index = "<sitemapindex" in (xml_text or "").lower()
    return locs, is_index


def categorize_sitemap(locs: list[str]) -> dict:
    """Clasifica las URLs del sitemap por tipo (como la skill): paginas reales vs
    etiquetas/tags vs fichas/descargas vs noticias/entradas."""
    comp = {"paginas": 0, "etiquetas": 0, "fichas": 0, "entradas": 0, "otras": 0}
    for u in locs:
        p = u.lower()
        if re.search(r"/(tag|tags|etiqueta|categoria|category|author|autor)/", p):
            comp["etiquetas"] += 1
        elif re.search(r"(wpfd_file|/descargas?/|/downloads?/|\.pdf$|/documento)", p):
            comp["fichas"] += 1
        elif re.search(r"/(blog|noticias?|news|actualidad|post|\d{4}/\d{2})/", p):
            comp["entradas"] += 1
        elif re.search(r"/(feed|comments|attachment)/", p):
            comp["otras"] += 1
        else:
            comp["paginas"] += 1
    comp["total"] = len(locs)
    return comp


# ---- Deteccion de pais real (por contenido, NO solo por TLD) -----------------

# Prefijos telefonicos -> (pais, codigo gl). Orden: mas largos primero.
_CALL_CODES = [
    ("593", "Ecuador", "ec"), ("591", "Bolivia", "bo"), ("595", "Paraguay", "py"),
    ("598", "Uruguay", "uy"), ("502", "Guatemala", "gt"), ("503", "El Salvador", "sv"),
    ("504", "Honduras", "hn"), ("505", "Nicaragua", "ni"), ("506", "Costa Rica", "cr"),
    ("507", "Panamá", "pa"), ("509", "Haití", "ht"), ("351", "Portugal", "pt"),
    ("52", "México", "mx"), ("57", "Colombia", "co"), ("54", "Argentina", "ar"),
    ("56", "Chile", "cl"), ("51", "Perú", "pe"), ("58", "Venezuela", "ve"),
    ("55", "Brasil", "br"), ("34", "España", "es"), ("44", "Reino Unido", "gb"),
    ("33", "Francia", "fr"), ("39", "Italia", "it"), ("49", "Alemania", "de"),
    ("1", "Estados Unidos", "us"),
]

# Menciones de pais/ciudad/moneda/ID fiscal como respaldo (cuando no hay tel: claro).
# Cobertura MUNDIAL: nombres en ES e EN, ciudades principales e identificadores locales.
_GEO_HINTS = {
    # ---- Iberia / Europa ----
    "es": ("España", ["españa", "spain", "madrid", "barcelona", "valencia", "sevilla", "málaga", "bilbao", " iva "]),
    "pt": ("Portugal", ["portugal", "lisboa", "lisbon", "porto", "oporto", " nif "]),
    "gb": ("Reino Unido", ["united kingdom", "reino unido", "london", "londres", "manchester", "u.k.", "england", "british", "£"]),
    "de": ("Alemania", ["alemania", "germany", "deutschland", "berlin", "berlín", "münchen", "munich", "hamburg", "frankfurt", "gmbh"]),
    "fr": ("Francia", ["francia", "france", "paris", "parís", "lyon", "marseille", "français"]),
    "it": ("Italia", ["italia", "italy", "roma", "milano", "milán", "napoli", "italiano"]),
    "nl": ("Países Bajos", ["países bajos", "netherlands", "amsterdam", "holanda", "nederland"]),
    "ie": ("Irlanda", ["ireland", "irlanda", "dublin", "dublín"]),
    "ch": ("Suiza", ["switzerland", "suiza", "zürich", "zurich", "geneva", "ginebra"]),
    # ---- Norteamérica ----
    "us": ("Estados Unidos", ["united states", "u.s.a", "new york", "miami", "los angeles", "texas", "california", "chicago", " ein "]),
    "ca": ("Canadá", ["canada", "canadá", "toronto", "vancouver", "montreal", "montréal", "ontario", "quebec"]),
    "mx": ("México", ["méxico", "mexico", "cdmx", "guadalajara", "monterrey", "puebla", "querétaro", " rfc "]),
    # ---- LatAm ----
    "co": ("Colombia", ["colombia", "bogotá", "bogota", "medellín", "medellin", "cali", "barranquilla", " nit "]),
    "ar": ("Argentina", ["argentina", "buenos aires", "córdoba", "rosario", "mendoza", " cuit "]),
    "cl": ("Chile", ["chile", "santiago", "valparaíso", "concepción", " rut "]),
    "pe": ("Perú", ["perú", "peru", "lima", "arequipa", "trujillo", " ruc ", "soles"]),
    "uy": ("Uruguay", ["uruguay", "montevideo"]),
    "ec": ("Ecuador", ["ecuador", "quito", "guayaquil"]),
    "bo": ("Bolivia", ["bolivia", "la paz", "santa cruz de la sierra"]),
    "ve": ("Venezuela", ["venezuela", "caracas", "maracaibo"]),
    "py": ("Paraguay", ["paraguay", "asunción", "asuncion"]),
    "gt": ("Guatemala", ["guatemala"]),
    "cr": ("Costa Rica", ["costa rica", "san josé de costa rica"]),
    "pa": ("Panamá", ["panamá", "panama", "ciudad de panamá"]),
    "do": ("República Dominicana", ["república dominicana", "republica dominicana", "santo domingo", "dominican"]),
    "br": ("Brasil", ["brasil", "brazil", "são paulo", "sao paulo", "rio de janeiro", "brasília", "brasilia", "cnpj"]),
    # ---- Resto ----
    "au": ("Australia", ["australia", "sydney", "melbourne", "brisbane"]),
    "in": ("India", ["india", "mumbai", "delhi", "bangalore", "bengaluru", "₹"]),
    "ae": ("Emiratos Árabes", ["united arab emirates", "dubai", "dubái", "abu dhabi"]),
}

# Monedas y codigos ISO -> pais (señal fuerte). USD/EUR se omiten por ambiguos.
_CURRENCY_SIGNS = {
    "cop": "co", "mxn": "mx", "ars": "ar", "clp": "cl", "pen": "pe", "uyu": "uy",
    "bob": "bo", "pyg": "py", "gtq": "gt", "crc": "cr", "dop": "do", "brl": "br",
    "r$": "br", "gbp": "gb", "£": "gb", "cad": "ca", "c$": "ca", "aud": "au",
    "chf": "ch", "inr": "in", "₹": "in", "aed": "ae",
    "nit": "co", "rfc": "mx", "cuit": "ar", "cnpj": "br", "cpf": "br",
}


_GL_NAME = {gl: name for gl, (name, _k) in _GEO_HINTS.items()}
_GL_NAME.update({"co": "Colombia", "es": "España", "mx": "México", "ar": "Argentina",
                 "cl": "Chile", "pe": "Perú", "pa": "Panamá", "pt": "Portugal",
                 "uy": "Uruguay", "ec": "Ecuador", "bo": "Bolivia", "ve": "Venezuela",
                 "gt": "Guatemala", "cr": "Costa Rica", "do": "República Dominicana",
                 "us": "Estados Unidos", "de": "Alemania", "fr": "Francia", "it": "Italia",
                 "gb": "Reino Unido", "br": "Brasil"})

# ccTLD -> pais (señal global fuerte). Los genericos NO cuentan (uso mundial).
_GENERIC_TLD = {"com", "net", "org", "app", "io", "co", "ai", "dev", "me", "xyz",
                "online", "site", "tech", "store", "info", "biz", "gg", "to", "cc"}
_CCTLD = {gl: gl for gl in _GL_NAME}  # es->es, fr->fr, br->br...
_CCTLD.update({"uk": "gb"})  # .co.uk / .uk -> Reino Unido


def detect_country(html: str, domain: str) -> dict:
    """Detecta el pais REAL de operacion con VOTACION PONDERADA de varias señales:
    enlaces tel:, telefonos en texto, menciones de pais/ciudad/moneda e idioma-region.
    Una señal debil (un numero suelto que empieza por un prefijo) NO puede ganarle al
    nombre del pais escrito o a la moneda. El TLD es el ultimo recurso.
    Devuelve {name, gl, source}."""
    h = (html or "")
    low = h.lower()
    votes: dict[str, float] = {}
    src: dict[str, str] = {}

    def add(gl: str, w: float, source: str):
        if not gl:
            return
        votes[gl] = votes.get(gl, 0.0) + w
        if votes.get(gl, 0) == w or source == "contenido":
            src.setdefault(gl, source)

    # 1) Enlaces tel: (la señal MAS fiable) -> peso alto
    for m in re.finditer(r'(?:href=["\']tel:|data-tel=["\'])\s*(\+?\d[\d\s().\-]{6,}\d)', h, re.I):
        digs = re.sub(r"\D", "", m.group(1))
        if not (8 <= len(digs) <= 15):
            continue
        for code, name, gl in _CALL_CODES:
            if digs.startswith(code):
                add(gl, 4.0, "telefono"); break

    # 2) Telefonos en texto con prefijo internacional -> peso BAJO (puede ser ruido)
    for m in re.finditer(r"(?:\+|\b00)\s?(\d[\d\s().\-]{7,}\d)", h):
        digs = re.sub(r"\D", "", m.group(1))
        if not (9 <= len(digs) <= 15):
            continue
        for code, name, gl in _CALL_CODES:
            if digs.startswith(code):
                # el codigo "1" (EE.UU./Canada) es MUY ambiguo: exige 11 digitos exactos
                if code == "1" and len(digs) != 11:
                    break
                add(gl, 1.0, "telefono"); break

    # 2b) ccTLD del dominio -> voto fuerte (señal global fiable; genericos no cuentan)
    host0 = (domain or "").lower().split("/")[0].split(":")[0]
    tld0 = host0.rsplit(".", 1)[-1] if "." in host0 else ""
    if tld0 in _CCTLD and tld0 not in _GENERIC_TLD:
        add(_CCTLD[tld0], 2.5, "tld")
    elif tld0 == "co":
        # .co es el ccTLD de Colombia (aunque se venda como generico): voto
        # moderado, lo pueden superar señales fuertes (moneda/contenido) de otro
        # pais. Sin esto, koaj.co (Colombia) se confundia con España.
        add("co", 1.8, "tld")

    # 3) Menciones de contenido: el NOMBRE del pais pesa mucho; ciudades, menos.
    for gl, (name, kws) in _GEO_HINTS.items():
        score = 0.0
        for kw in kws:
            c = low.count(kw)
            if not c:
                continue
            heavy = kw == name.lower() or kw in (name.lower(), name.lower().split()[0])
            score += min(c, 4) * (3.0 if heavy else 1.5)
        if score:
            add(gl, score, "contenido")

    # 3b) Moneda / codigo ISO / ID fiscal (señal fuerte y global)
    for sign, gl in _CURRENCY_SIGNS.items():
        # con separadores para evitar coincidencias dentro de palabras
        n = len(re.findall(r"(?<![a-z])" + re.escape(sign) + r"(?![a-z])", low))
        if n:
            add(gl, min(n, 4) * 3.0, "moneda")

    # 4) Idioma-region declarado (es-CO, en-GB, de-DE...) y hreflang con region -> fiable
    for m in re.finditer(r'(?:lang|hreflang)=["\'][a-z]{2}-([a-z]{2})', h, re.I):
        reg = m.group(1).lower()
        if reg in _GL_NAME:
            add(reg, 3.0, "idioma")

    if votes:
        gl = max(votes, key=votes.get)
        name = _GL_NAME.get(gl) or next((n for c, n, g in _CALL_CODES if g == gl), gl.upper())
        return {"name": name, "gl": gl, "source": src.get(gl, "contenido")}

    return {"name": "", "gl": "", "source": "desconocido"}


# ---- Analisis de robots.txt (que bloquea, que conviene bloquear) -------------

def analyze_robots(robots_text: str) -> dict:
    """Analiza robots.txt por grupos de user-agent. 'blocks_all' SOLO si el grupo
    global (User-agent: *) bloquea todo el sitio (evita falsos positivos cuando un
    Disallow: / esta bajo un bot concreto). Reconoce buenas practicas ya aplicadas."""
    txt = robots_text or ""
    groups: dict[str, dict] = {}
    cur = None
    for raw in txt.splitlines():
        l = raw.strip()
        if not l or l.startswith("#"):
            continue
        low = l.lower()
        if low.startswith("user-agent:"):
            cur = l.split(":", 1)[1].strip() or "*"
            groups.setdefault(cur, {"disallow": [], "allow": []})
        elif low.startswith("disallow:") and cur is not None:
            p = l.split(":", 1)[1].strip()
            if p:
                groups[cur]["disallow"].append(p)
        elif low.startswith("allow:") and cur is not None:
            groups[cur]["allow"].append(l.split(":", 1)[1].strip())

    star = groups.get("*", {"disallow": [], "allow": []})
    all_dis = [d for g in groups.values() for d in g["disallow"]]
    blocks_all = ("/" in star["disallow"]) and not any(a in ("/", "/*", "") for a in star["allow"])
    has_sitemap = bool(re.search(r"(?im)^\s*sitemap:", txt))

    # ¿Bloquea a los bots de IA? (si los bloqueas, la IA no puede leerte ni citarte)
    ai_bots = ["gptbot", "chatgpt-user", "oai-searchbot", "google-extended", "ccbot",
               "perplexitybot", "claudebot", "anthropic-ai", "applebot-extended",
               "bytespider", "meta-externalagent", "amazonbot"]
    ai_blocked = []
    for ua, g in groups.items():
        if ua.lower() in ai_bots and "/" in g["disallow"] and not any(a in ("/", "") for a in g["allow"]):
            ai_blocked.append(ua)
    if blocks_all:
        ai_blocked = ai_bots[:]
    joined = " ".join(d.lower() for d in all_dis)

    checks = {
        "el buscador interno": ["?s=", "/search", "/buscar", "?q=", "/?s"],
        "el area de administracion": ["wp-admin", "/admin", "wp-login", "/login"],
        "los feeds y adjuntos": ["/feed", "attachment", "/wp-json", "/trackback"],
        "los filtros con parametros (?)": ["*?", "/*?"],
        "las paginas de etiqueta/tag": ["/tag", "/etiqueta", "/label"],
    }
    good_blocks = [name for name, kws in checks.items() if any(k in joined for k in kws)]
    suggest = [name for name, kws in checks.items() if not any(k in joined for k in kws)]

    # ---- ¿Bloquea algo NECESARIO y significativo? -----------------------------
    # Solo miramos las reglas que afectan a Google/rastreadores normales: el grupo
    # global (*) y, si existe, el de googlebot. Un Disallow bajo un bot concreto no
    # afecta a Google.
    relevant = list(star["disallow"])
    for ua, g in groups.items():
        if ua.lower() in ("googlebot", "googlebot-image", "google"):
            relevant += g["disallow"]
    relevant = [d for d in dict.fromkeys(relevant) if d and d != "/"]

    # a) Recursos que Google NECESITA para pintar la pagina (CSS/JS/assets).
    #    Bloquearlos hace que Google vea la web "rota" y penaliza. OJO: en WordPress
    #    el CSS/JS vive en /wp-content/themes y /wp-content/plugins; bloquear
    #    /wp-content/uploads/wc-logs, woocommerce_uploads, cache, etc. es CORRECTO
    #    (son logs, descargas privadas y temporales, no recursos de render).
    render_kw = [".css", ".js", "/css/", "/js/", "/assets", "/static", "/media/",
                 "/dist", "/build", "/_next", "/wp-content/themes", "/wp-content/plugins",
                 "/wp-includes", "/sites/default/files", "/themes/", "/scripts/", "/styles/"]
    # Subrutas de WordPress/WooCommerce que es NORMAL y recomendable bloquear
    # (privado/sistema/temporales): nunca son recursos de render ni "contenido real".
    wp_benign = ["/wp-content/uploads", "wc-logs", "woocommerce_uploads",
                 "woocommerce_transient", "woocommerce_", "/wp-content/cache",
                 "/wp-content/upgrade", "/wp-content/backup", "wp-snapshots",
                 "/wp-content/mu-plugins", "duplicator", "/wp-content/uploads/wpforms"]
    blocks_render = [d for d in relevant
                     if any(k in d.lower() for k in render_kw)
                     and not any(b in d.lower() for b in wp_benign)]

    # b) Directorios que PARECEN contenido real (no admin/sistema/parametros).
    benign_kw = ["wp-admin", "wp-login", "/admin", "/login", "/cart", "/checkout",
                 "/carrito", "/search", "/buscar", "?s=", "?q=", "/?", "*?", "?",
                 "/feed", "attachment", "/wp-json", "/trackback", "/cgi-bin",
                 "/tag", "/etiqueta", "/label", "/api", "/account", "/cuenta",
                 "/wp-includes", "/wp-content", "/comments", "/print", "/tmp", "/private",
                 "/thank", "/gracias", "/out/", "/go/", "/redirect"] + wp_benign
    def _looks_content(d: str) -> bool:
        dl = d.lower()
        if any(k in dl for k in benign_kw):
            return False
        if dl in blocks_render or any(k in dl for k in render_kw):
            return False
        core = dl.strip("/").split("/")[0].replace("*", "")
        # un segmento con letras (no solo parametros/comodines) parece una seccion real
        return bool(re.search(r"[a-z]{2,}", core)) and not core.startswith("?")
    blocks_content = [d for d in relevant if _looks_content(d)]

    return {
        "present": bool(txt.strip()),
        "disallow_count": len(all_dis),
        "blocks_all": blocks_all,
        "has_sitemap": has_sitemap,
        "disallow_sample": all_dis[:8],
        "good_blocks": good_blocks,
        "suggest_block": suggest[:3],
        "ai_blocked": ai_blocked,
        "blocks_render": blocks_render[:6],
        "blocks_content": blocks_content[:6],
    }


# ---- Deteccion de analitica/pixeles (y duplicados = plus) --------------------

def detect_analytics(html: str) -> dict:
    h = html or ""
    ga4 = sorted(set(re.findall(r"G-[A-Z0-9]{6,}", h)))
    ua = sorted(set(re.findall(r"UA-\d{4,}-\d+", h)))
    gtm = sorted(set(re.findall(r"GTM-[A-Z0-9]{5,}", h)))
    aw = sorted(set(re.findall(r"AW-\d{6,}", h)))
    fb_ids = sorted(set(re.findall(r"fbq\(\s*['\"]init['\"]\s*,\s*['\"](\d{6,})['\"]", h)))
    gtag_loads = len(re.findall(r"gtag/js\?id=", h))

    tools = []
    if ga4:
        tools.append("Google Analytics 4")
    if ua:
        tools.append("Universal Analytics (obsoleto)")
    if gtm:
        tools.append("Google Tag Manager")
    if aw:
        tools.append("Google Ads")
    if fb_ids or re.search(r"connect\.facebook\.net|fbq\(", h):
        tools.append("Meta Pixel")
    if re.search(r"clarity\.ms|clarity\(", h):
        tools.append("Microsoft Clarity")
    if re.search(r"static\.hotjar\.com|hotjar", h):
        tools.append("Hotjar")
    if re.search(r"analytics\.tiktok\.com", h):
        tools.append("TikTok Pixel")
    if re.search(r"snap\.licdn\.com|_linkedin_partner_id", h):
        tools.append("LinkedIn Insight")

    dup = []
    if len(ga4) > 1:
        dup.append(f"{len(ga4)} mediciones GA4 distintas ({', '.join(ga4)})")
    if gtag_loads > 1:
        dup.append(f"la libreria de Google (gtag.js) se carga {gtag_loads} veces")
    if ga4 and gtm:
        dup.append("GA4 cargado directo Y por Tag Manager (posible doble conteo)")
    if len(fb_ids) > 1:
        dup.append(f"{len(fb_ids)} Meta Pixel distintos")
    for _id in ga4:
        if len(re.findall(re.escape(_id), h)) >= 3:
            dup.append(f"el ID {_id} aparece repetido en la pagina")
            break

    return {"tools": tools, "ga4": ga4, "ua": ua, "gtm": gtm, "fb": fb_ids,
            "has_any": bool(tools), "duplicated": bool(dup), "dup_notes": dup[:3]}


# ---- Escaner de seguridad / tecnologia (headers, versiones, archivos expuestos) --

# Rutas sensibles: GET de solo lectura + firma para evitar falsos positivos (soft-404)
_SENSITIVE = [
    ("/.env", "Variables de entorno (.env) accesibles: pueden contener contrasenas y claves",
     re.compile(r"(?im)^\s*[A-Z0-9_]{2,}\s*=|APP_KEY|DB_PASSWORD|SECRET")),
    ("/.git/HEAD", "Repositorio Git expuesto (/.git): se puede descargar tu codigo",
     re.compile(r"ref:\s*refs/")),
    ("/.git/config", "Configuracion Git expuesta (/.git/config)",
     re.compile(r"\[core\]|repositoryformatversion")),
    ("/wp-config.php.bak", "Copia de la configuracion de WordPress accesible",
     re.compile(r"DB_PASSWORD|DB_NAME|DB_USER")),
    ("/phpinfo.php", "phpinfo() expuesto: filtra rutas y versiones del servidor",
     re.compile(r"phpinfo\(\)|PHP Version")),
    ("/server-status", "Estado del servidor Apache expuesto (server-status)",
     re.compile(r"Apache Server Status|Server Version")),
    ("/wp-json/wp/v2/users", "Enumeracion de usuarios de WordPress (wp-json)",
     re.compile(r'"slug"\s*:')),
]

_SEC_HEADERS = {
    "HSTS (fuerza HTTPS)": "strict-transport-security",
    "Content-Security-Policy": "content-security-policy",
    "X-Frame-Options (anti-clickjacking)": "x-frame-options",
    "X-Content-Type-Options": "x-content-type-options",
    "Referrer-Policy": "referrer-policy",
    "Permissions-Policy": "permissions-policy",
}


def _ssl_cert_sync(host: str) -> dict:
    """Comprueba el certificado SSL real: válido, quién lo emite y días para caducar."""
    import socket  # noqa: PLC0415
    import ssl  # noqa: PLC0415
    from datetime import datetime  # noqa: PLC0415
    host = (host or "").split("/")[0].split(":")[0]
    if not host:
        return {"valid": None, "error": "sin host"}
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, 443), timeout=7) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ss:
                cert = ss.getpeercert()
        exp = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
        days = (exp - datetime.utcnow()).days
        issuer = dict(x[0] for x in cert.get("issuer", ())).get("organizationName", "")
        return {"valid": True, "days_left": days, "issuer": issuer}
    except ssl.SSLCertVerificationError as exc:  # cert caducado, dominio no coincide, autofirmado
        return {"valid": False, "error": str(exc)[:140]}
    except Exception as exc:  # noqa: BLE001
        return {"valid": None, "error": str(exc)[:140]}


async def scan_security(client: httpx.AsyncClient, base_url: str, headers: dict, html: str) -> dict:
    h = {k.lower(): v for k, v in dict(headers or {}).items()}
    root = base_url.rstrip("/")

    # 1) Cabeceras de seguridad
    present = [name for name, key in _SEC_HEADERS.items() if key in h]
    missing = [name for name, key in _SEC_HEADERS.items() if key not in h]

    # 2) Fugas de version / tecnologia
    leaks = []
    server = (h.get("server") or "").strip()
    if server and re.search(r"\d", server):
        leaks.append(f"Server: {server}")
    xp = (h.get("x-powered-by") or "").strip()
    if xp:
        leaks.append(f"X-Powered-By: {xp}")
    gen = re.search(r'name=["\']generator["\']\s+content=["\']([^"\']+)["\']', html or "", re.I)
    if gen and re.search(r"\d", gen.group(1)):
        leaks.append(f"Generator: {gen.group(1).strip()}")

    # CMS / stack
    cms = ""
    low_html = (html or "").lower()
    if "wp-content" in low_html or "wordpress" in (xp + (gen.group(1) if gen else "")).lower():
        cms = "WordPress"
    elif "cdn.shopify.com" in low_html or "shopify" in server.lower():
        cms = "Shopify"
    elif "wix.com" in low_html:
        cms = "Wix"
    elif "static.parastorage" in low_html:
        cms = "Wix"

    # 3) Cookies inseguras
    setc = (h.get("set-cookie") or "").lower()
    cookie_flags = []
    if setc:
        if "secure" not in setc:
            cookie_flags.append("cookies sin Secure")
        if "httponly" not in setc:
            cookie_flags.append("cookies sin HttpOnly")

    # 4) Archivos/paths sensibles expuestos (GET de solo lectura, en paralelo)
    exposed = []

    async def probe(path, desc, sig):
        try:
            r = await client.get(root + path, timeout=LINK_TIMEOUT, follow_redirects=False)
            if r.status_code == 200 and len(r.text) < 300000 and sig.search(r.text or ""):
                exposed.append({"path": path, "what": desc})
        except Exception:  # noqa: BLE001
            pass

    try:
        await asyncio.gather(*(probe(p, d, s) for p, d, s in _SENSITIVE))
    except Exception:  # noqa: BLE001
        pass

    https_ok = root.lower().startswith("https://")

    # 5) Certificado SSL real (validez + caducidad)
    ssl_info = {}
    try:
        if https_ok:
            ssl_info = await asyncio.to_thread(_ssl_cert_sync, urlparse(root).netloc)
    except Exception:  # noqa: BLE001
        ssl_info = {}

    # 6) Contenido mixto: recursos http:// cargados en una página https
    mixed = []
    if https_ok and html:
        for m in re.findall(r'(?:src|href)=["\'](http://[^"\']+)', html, re.I):
            if re.search(r"\.(js|css|png|jpe?g|gif|webp|svg|woff2?|mp4|ico)(\?|$)", m, re.I):
                if m not in mixed:
                    mixed.append(m)
        mixed = mixed[:5]

    cert_bad = ssl_info.get("valid") is False
    cert_soon = ssl_info.get("valid") is True and isinstance(ssl_info.get("days_left"), int) and ssl_info["days_left"] < 15
    penalty = (len(exposed) * 45 + len(missing) * 6 + len(leaks) * 5 + len(cookie_flags) * 5
               + (0 if https_ok else 25) + (30 if cert_bad else 0) + (10 if cert_soon else 0)
               + (12 if mixed else 0))
    score = max(0, 100 - penalty)
    return {
        "score": score,
        "https": https_ok,
        "headers_present": present,
        "headers_missing": missing,
        "leaks": leaks,
        "cms": cms,
        "cookie_flags": cookie_flags,
        "exposed": exposed,
        "ssl": ssl_info,
        "mixed": mixed,
    }


# ---- PageSpeed Insights (opcional, requiere API key) -------------------------

async def fetch_psi(client: httpx.AsyncClient, url: str, strategy: str = "mobile") -> dict | None:
    key = os.getenv("GOOGLE_PSI_API_KEY", "").strip()
    if not key:
        return None
    api = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
    params = {"url": url, "key": key, "strategy": strategy,
              "category": ["performance", "seo"]}
    try:
        r = await client.get(api, params=params, timeout=PSI_TIMEOUT)
        if r.status_code != 200:
            return None
        data = r.json()
        lh = data.get("lighthouseResult", {})
        cats = lh.get("categories", {})
        aud = lh.get("audits", {})

        def dv(k):
            return aud.get(k, {}).get("displayValue", "")

        perf = cats.get("performance", {}).get("score")
        seo = cats.get("seo", {}).get("score")
        return {
            "performance": round(perf * 100) if perf is not None else None,
            "seo": round(seo * 100) if seo is not None else None,
            "fcp": dv("first-contentful-paint"),
            "lcp": dv("largest-contentful-paint"),
            "tbt": dv("total-blocking-time"),
            "si": dv("speed-index"),
        }
    except Exception:  # noqa: BLE001
        return None


async def _psi_desktop(url: str, tries: int = 2) -> dict | None:
    """Escritorio con PageSpeed (Lighthouse). None si no hay API key."""
    if not os.getenv("GOOGLE_PSI_API_KEY", "").strip():
        return None
    async with httpx.AsyncClient(headers=HEADERS) as client:
        for _ in range(tries):
            r = await fetch_psi(client, url, "desktop")
            if r:
                return r
            await asyncio.sleep(1.5)
    return None


async def fetch_psi_full(url: str) -> dict | None:
    """Velocidad movil + escritorio medidas IGUAL (misma metodologia real) para que
    sean comparables. El navegador propio se usa solo para detectar analitica y como
    respaldo si la medicion oficial falla."""
    import perf  # noqa: PLC0415
    key = os.getenv("GOOGLE_PSI_API_KEY", "").strip()

    async def _psi(strat, tries=4):
        if not key:
            return None
        async with httpx.AsyncClient(headers=HEADERS) as client:
            for i in range(tries):
                r = await fetch_psi(client, url, strat)
                if r and r.get("performance") is not None:
                    return r
                await asyncio.sleep(2.0 + i)   # backoff creciente ante rate-limit
        return None

    try:
        # movil con mas reintentos (es el que mas suele fallar por rate-limit)
        m, d, dev = await asyncio.gather(
            _psi("mobile", tries=5), _psi("desktop", tries=3),
            perf.measure_device(url, mobile=True),
            return_exceptions=True,
        )
        m = m if isinstance(m, dict) else None
        d = d if isinstance(d, dict) else None
        dev = dev if isinstance(dev, dict) else None
        analytics = dev.pop("analytics", None) if dev else None
        # respaldo con dispositivo propio SOLO si la medicion oficial falla
        if not m and dev:
            m = dev
        if not d:
            try:
                d = await perf.measure_device(url, mobile=False)
            except Exception:  # noqa: BLE001
                d = None
        if not m and not d:
            return None
        return {"mobile": m, "desktop": d, "analytics": analytics}
    except Exception:  # noqa: BLE001
        return None


# ---- Motor principal ---------------------------------------------------------

async def _www_variants(domain: str) -> dict:
    """Comprueba www y no-www de la MISMA forma (sin pedirlo al cliente). Detecta si
    ambas sirven contenido por separado (DUPLICADO) o si una redirige a la otra
    (canonical unificado). Aquí saltan 'novedades' importantes."""
    host = (domain or "").split("/")[0].replace("www.", "").lower()
    if not host:
        return {}
    root_h, www_h = host, "www." + host

    async def probe(client, h):
        try:
            r = await client.get(f"https://{h}/", timeout=8)
            return {"status": r.status_code,
                    "final_host": urlparse(str(r.url)).netloc.lower(),
                    "https": str(r.url).lower().startswith("https://"),
                    "ok": r.status_code < 400}
        except Exception:  # noqa: BLE001
            return {"status": None, "final_host": "", "https": False, "ok": False}

    try:
        async with httpx.AsyncClient(headers=HEADERS, verify=False, follow_redirects=True) as c:
            root, www = await asyncio.gather(probe(c, root_h), probe(c, www_h))
    except Exception:  # noqa: BLE001
        return {}
    rf, wf = root.get("final_host", ""), www.get("final_host", "")
    both_ok = bool(root["ok"] and www["ok"])
    unified = bool(both_ok and rf and rf == wf)
    duplicate = bool(both_ok and rf and wf and rf != wf)   # ambas sirven por separado
    # una versión responde con error (resuelve pero 4xx/5xx) mientras la otra funciona:
    # debería redirigir (301), no dar error.
    www_broken = bool(root["ok"] and www["status"] is not None and www["status"] >= 400)
    root_broken = bool(www["ok"] and root["status"] is not None and root["status"] >= 400)
    one_fails = www_broken or root_broken
    broken_host = (www_h if www_broken else (root_h if root_broken else ""))
    canonical = wf if (unified and wf) else ((rf if root["ok"] else wf) or rf or wf)
    return {"root": root, "www": www, "both_ok": both_ok, "unified": unified,
            "duplicate": duplicate, "one_fails": one_fails, "broken_host": broken_host,
            "canonical_host": canonical, "prefers_www": bool(canonical.startswith("www."))}


async def _check_https_forced(domain: str) -> bool:
    """Prueba REAL: ¿http://dominio redirige a https? (buena practica tecnica)."""
    host = (domain or "").split("/")[0]
    if not host:
        return False
    try:
        async with httpx.AsyncClient(headers=HEADERS, verify=False, timeout=8,
                                     follow_redirects=True) as c:
            r = await c.get(f"http://{host}/")
            return str(r.url).lower().startswith("https://")
    except Exception:  # noqa: BLE001
        return False


async def analyze(raw_url: str) -> Result:
    url = normalize_url(raw_url)
    res = Result(url=url, domain=domain_of(url),
                 analyzed_at=datetime.now(timezone.utc).isoformat())
    if not url:
        res.error = "URL vacia o invalida."
        return res

    t0 = time.perf_counter()
    async with httpx.AsyncClient(headers=HEADERS, verify=False,
                                 limits=httpx.Limits(max_connections=LINK_CONCURRENCY + 4)) as client:
        home, err, dt = await fetch_home(client, url)
        if home is None:
            res.error = f"No pudimos acceder al sitio: {err}"
            res.elapsed = time.perf_counter() - t0
            return res

        res.reachable = True
        res.final_url = str(home.url)
        home_html = html_text(home)
        home_status = home.status_code
        home_time = dt
        https_ok = str(home.url).lower().startswith("https://")
        # SSL real como fuente de verdad: aunque la home cayera a http por un
        # fallo transitorio, si el dominio sirve un certificado válido en 443, SÍ
        # tiene HTTPS. Evita el falso "sin SSL" y hace el resultado estable.
        res.ssl_cert = {}
        try:
            res.ssl_cert = await asyncio.to_thread(
                _ssl_cert_sync, urlparse(res.final_url).netloc or res.domain)
        except Exception:  # noqa: BLE001
            res.ssl_cert = {}
        if res.ssl_cert.get("valid") is True:
            https_ok = True

        meta = parse_home(home_html, res.final_url)
        # Analiza la pagina YA RENDERIZADA (JS ejecutado): capta schema, FAQ, contacto
        # y contenido que el rastreo estatico no ve en sitios modernos.
        try:
            import perf as _perf  # noqa: PLC0415
            rendered_html = await _perf.render_html(res.final_url)
        except Exception:  # noqa: BLE001
            rendered_html = ""
        if rendered_html and len(rendered_html) > 2000:
            try:
                meta = _merge_meta(meta, parse_home(rendered_html, res.final_url))
            except Exception:  # noqa: BLE001
                pass
            if len(rendered_html) > len(home_html or ""):
                home_html = rendered_html  # rendered para enlaces internos + pais
        # ¿La web está bloqueada por anti-bots (Cloudflare/WAF) y NO pudimos leer el
        # contenido real? Si es así, marcamos meta["blocked"] para NO inventar marca,
        # categoría, país ni competidores a partir de una página de reto.
        _bl = (home_html or "")
        _bl_low = _bl.lower()
        _blocked = (
            home_status >= 400
            or len(_bl) < 2500
            or bool(re.search(r"just a moment|attention required|cf-browser-verification|"
                              r"checking your browser|challenge-platform|_cf_chl_|access denied|"
                              r"acceso denegado|enable javascript and cookies to continue|"
                              r"verifying you are human|error 10\d\d", _bl_low))
        ) and not bool(re.search(r"<main|<article|<section", _bl_low))
        meta["blocked"] = bool(_blocked)
        # Pais real por contenido (telefono/menciones), no solo por TLD. Si la web está
        # bloqueada, NO deducimos país de la página de reto (daba "Estados Unidos" por la
        # ubicación del WAF): mejor dejarlo desconocido que afirmar un país falso.
        if _blocked:
            country = {"name": "", "gl": "", "source": ""}
        else:
            country = detect_country(home_html, res.domain)
        meta["country"] = country.get("name", "")
        meta["gl"] = country.get("gl", "")
        meta["country_source"] = country.get("source", "")
        res.html = home_html or ""
        # Analitica / pixeles instalados (y si estan duplicados)
        analytics = detect_analytics(home_html)
        res.meta = meta

        # Archivos clave en paralelo
        robots_task = fetch_text(client, urljoin(url + "/", "robots.txt"))
        sitemap_task = fetch_text(client, urljoin(url + "/", "sitemap.xml"))
        sitemap_idx_task = fetch_text(client, urljoin(url + "/", "sitemap_index.xml"))
        llms_task = fetch_text(client, urljoin(url + "/", "llms.txt"))

        (robots_status, robots_text), (sm_status, sm_text), \
            (smi_status, smi_text), (llms_status, llms_text), https_forced, www_info = await asyncio.gather(
                robots_task, sitemap_task, sitemap_idx_task, llms_task,
                _check_https_forced(res.domain), _www_variants(res.domain),
            )
        psi = None

        robots_ok = robots_status == 200 and bool(robots_text)
        sitemap_text = sm_text or smi_text
        sitemap_ok = bool(sitemap_text)
        sitemap_in_robots = bool(robots_text and re.search(r"(?i)sitemap:", robots_text))
        llms_ok = llms_status == 200 and bool(llms_text)
        res.psi = psi

        # ---- Muestreo de 404 -------------------------------------------------
        broken = 0
        checked = 0
        broken_examples = []
        sitemap_total = 0
        sitemap_comp = None
        pages_found = 0
        internal_links = 0
        try:
            if sitemap_text:
                locs, is_index = parse_sitemap_locs(sitemap_text)
                if is_index and locs:
                    # Suma TODOS los sub-sitemaps (hasta 15) para contar las páginas
                    # reales de verdad, no solo el primero.
                    children = locs[:15]
                    child_results = await asyncio.gather(
                        *(fetch_text(client, c) for c in children), return_exceptions=True)
                    all_child_locs = []
                    for cr in child_results:
                        if isinstance(cr, tuple) and cr[1]:
                            cl, _ = parse_sitemap_locs(cr[1])
                            all_child_locs += cl
                    if all_child_locs:
                        sitemap_total = len(all_child_locs)
                        locs = all_child_locs
                    else:
                        sitemap_total = len(locs)
                else:
                    sitemap_total = len(locs)
                if locs:
                    sitemap_comp = categorize_sitemap(locs)
                sample = random.sample(locs, min(LINK_SAMPLE, len(locs))) if locs else []
            else:
                sample = []

            # SIEMPRE: enlaces internos reales del home (lo que navega un visitante).
            # Es lo mas util para 404 y no depende de ningun servicio externo.
            internal = []
            if home_html:
                soup = BeautifulSoup(home_html, "html.parser")
                base_net = urlparse(res.final_url).netloc
                base_norm = res.final_url.split("#")[0].split("?")[0].rstrip("/")
                for a in soup.find_all("a", href=True):
                    href = urljoin(res.final_url, a["href"]).split("#")[0].split("?")[0]
                    if urlparse(href).netloc == base_net and href.rstrip("/") != base_norm \
                            and not re.search(r"\.(jpg|jpeg|png|gif|webp|svg|pdf|zip|css|js)$", href, re.I):
                        internal.append(href)
                internal = list(dict.fromkeys(internal))
            # Cuantas paginas tiene el sitio (descubiertas por NOSOTROS, fiable):
            # el mapa del sitio o, si no hay, los enlaces internos reales del home.
            pages_found = max(sitemap_total, len(internal))
            internal_links = len(internal)
            # combina enlaces internos (prioridad) + muestra del sitemap
            sample = list(dict.fromkeys(internal[:LINK_SAMPLE] + sample))[:LINK_SAMPLE]
            if sample:
                sem = asyncio.Semaphore(LINK_CONCURRENCY)

                async def bounded(u):
                    async with sem:
                        return await check_status(client, u)

                results = await asyncio.gather(*(bounded(u) for u in sample))
                for u, status in results:
                    if status is None:
                        continue  # error de red, no cuenta
                    checked += 1
                    if status >= 400:
                        broken += 1
                        if len(broken_examples) < 4:
                            broken_examples.append({"url": u, "status": status})
        except Exception:  # noqa: BLE001
            pass

        broken_ratio = (broken / checked) if checked else 0.0

        # Escaner de seguridad / tecnologia (headers, versiones, archivos expuestos)
        try:
            security = await scan_security(client, res.final_url, dict(home.headers), home_html)
        except Exception:  # noqa: BLE001
            security = None

    llms_len = len(llms_text or "")
    # Calidad del llms.txt (guia para la IA): no basta con que exista, debe estar
    # bien estructurado (secciones/titulos y enlaces a paginas clave).
    _lt = llms_text or ""
    _llms_sections = len(re.findall(r"(?m)^\s*#", _lt))
    _llms_links = len(re.findall(r"\]\(|https?://", _lt))
    if not llms_ok:
        llms_quality = "none"
    elif llms_len >= 300 and _llms_sections >= 2 and _llms_links >= 2:
        llms_quality = "good"
    else:
        llms_quality = "thin"
    res.elapsed = round(time.perf_counter() - t0, 1)

    res.signals = {
        "sitemap_comp": sitemap_comp,
        "www": www_info,
        "https": https_ok,
        "https_forced": bool(https_forced),
        "home_status": home_status,
        "home_time": round(home_time, 2),
        "robots": robots_ok,
        "sitemap": sitemap_ok,
        "sitemap_in_robots": sitemap_in_robots,
        "sitemap_total": sitemap_total,
        "pages_found": pages_found,
        "internal_links": internal_links,
        "llms_txt": llms_ok,
        "llms_len": llms_len,
        "llms_quality": llms_quality,
        "security": security,
        "links_checked": checked,
        "links_broken": broken,
        "broken_ratio": round(broken_ratio, 2),
        "broken_examples": broken_examples,
        "analytics": analytics,
        "robots_info": analyze_robots(robots_text if robots_ok else ""),
    }

    _score(res)
    return res


# ---- Scoring y hallazgos -----------------------------------------------------

def _vel_from_psi_full(pf: dict) -> int | None:
    """Velocidad global = media de movil y escritorio (0-100)."""
    pf = pf or {}
    vals = [(pf.get(d) or {}).get("performance") for d in ("mobile", "desktop")]
    vals = [v for v in vals if isinstance(v, (int, float))]
    return round(sum(vals) / len(vals)) if vals else None


def _overall_score(cats: dict, psi_full: dict, security: dict,
                   ai_score: int | None = None) -> tuple[int, str]:
    """Score global. La IA es un EJE PRINCIPAL: el reconocimiento real de la IA
    (ai_score) y la preparacion para la IA (GEO) pesan casi el 40% juntos, asi la
    nota refleja de verdad la visibilidad en IA y no queda inflada."""
    # pesos pro-IA y exigentes (suman 1.0 con ai_score presente)
    parts = [((cats.get("tecnico") or {}).get("score", 0), 0.17),
             ((cats.get("onpage") or {}).get("score", 0), 0.15),
             ((cats.get("geo") or {}).get("score", 0), 0.18)]
    if isinstance(ai_score, (int, float)):
        parts.append((ai_score, 0.20))
    vel = _vel_from_psi_full(psi_full)
    if vel is not None:
        parts.append((vel, 0.15))
    sec = (security or {}).get("score")
    if isinstance(sec, (int, float)):
        parts.append((sec, 0.15))
    tot = sum(pw for _, pw in parts)
    overall = round(sum(sv * pw for sv, pw in parts) / tot) if tot else 0
    return overall, _grade(overall)


def finalize_score(data: dict) -> dict:
    """Recalcula score/grade con TODO ya presente (velocidad real + IA). main.py lo
    llama al terminar. Idempotente."""
    cats = data.get("categories") or {}
    security = (data.get("signals") or {}).get("security") or {}
    ai = data.get("geo_ai") or {}
    ai_score = ai.get("ai_score") if (ai.get("available") and not ai.get("error")) else None
    overall, grade = _overall_score(cats, data.get("psi_full") or {}, security, ai_score)
    data["score"] = overall
    data["grade"] = grade
    return data


def _score(res: Result) -> None:
    m = res.meta
    s = res.signals
    tecnico: list[Check] = []
    onpage: list[Check] = []
    geo: list[Check] = []
    good = res.findings_good
    improve = res.findings_improve

    def add(cat, key, label, earned, possible, ok, detail=""):
        cat.append(Check(key, label, earned, possible, ok, detail))

    # ---------- Tecnico y accesibilidad (graduado: distingue webs bien montadas
    #            de webs con carencias, no todo el mundo saca 100) ----------
    add(tecnico, "https", "Conexion segura (HTTPS)", 15 if s["https"] else 0, 15, s["https"])
    hf = s.get("https_forced")
    add(tecnico, "https_forced", "Fuerza HTTPS (redirige desde http)", 10 if hf else 0, 10, bool(hf),
        "http redirige a https" if hf else "http NO redirige a https")
    ok200 = 200 <= s["home_status"] < 300
    add(tecnico, "status", "La web responde correctamente", 10 if ok200 else 0, 10, ok200)
    # Tiempo de respuesta (TTFB) con umbrales exigentes y graduados
    ht = s["home_time"]
    speed = 12 if ht < 0.6 else (9 if ht < 1.2 else (6 if ht < 2 else (3 if ht < 3.5 else 0)))
    add(tecnico, "speed", "Tiempo de respuesta del servidor", speed, 12, ht < 1.2, f"{ht}s")
    add(tecnico, "robots", "Archivo robots.txt", 8 if s["robots"] else 0, 8, s["robots"])
    idx_ok = not m.get("robots_noindex")
    add(tecnico, "indexable", "La web permite indexarse (no 'noindex')", 10 if idx_ok else 0, 10, idx_ok,
        "Indexable por Google y la IA" if idx_ok else "META ROBOTS = noindex: NO apareces en Google ni en la IA")
    # Sitemap: presente Y anunciado en robots.txt (lo ideal) vale mas
    if s["sitemap"] and s.get("sitemap_in_robots"):
        sm_pts, sm_ok, sm_det = 10, True, "presente y enlazado en robots.txt"
    elif s["sitemap"]:
        sm_pts, sm_ok, sm_det = 6, False, "presente, pero no aparece en robots.txt"
    else:
        sm_pts, sm_ok, sm_det = 0, False, "no encontrado"
    add(tecnico, "sitemap", "Mapa del sitio (sitemap)", sm_pts, 10, sm_ok, sm_det)
    add(tecnico, "viewport", "Preparada para movil", 8 if m["viewport"] else 0, 8, m["viewport"])
    # Analitica instalada: sin medicion no se puede mejorar con datos
    an_any = bool((s.get("analytics") or {}).get("has_any"))
    add(tecnico, "analytics", "Medicion / analitica instalada", 8 if an_any else 0, 8, an_any,
        ", ".join((s.get("analytics") or {}).get("tools", [])[:2]) or "sin analitica detectada")
    health404 = round(19 * (1 - s["broken_ratio"])) if s["links_checked"] else 11
    add(tecnico, "links", "Enlaces sin errores (404)", health404, 19, s["broken_ratio"] < 0.1,
        f'{s["links_broken"]}/{s["links_checked"]} rotos')

    # ---------- SEO on-page ----------
    tlen = len(m["title"])
    tscore = 20 if 25 <= tlen <= 65 else (10 if m["title"] else 0)
    add(onpage, "title", "Titulo de pagina", tscore, 20, bool(m["title"]), f"{tlen} car.")
    dlen = len(m["description"])
    dscore = 20 if 70 <= dlen <= 165 else (10 if m["description"] else 0)
    add(onpage, "desc", "Descripcion (meta description)", dscore, 20, bool(m["description"]), f"{dlen} car.")
    h1ok = m["h1_count"] == 1
    h1score = 15 if h1ok else (8 if m["h1_count"] > 1 else 0)
    add(onpage, "h1", "Titular principal (H1)", h1score, 15, h1ok, f'{m["h1_count"]} H1')
    add(onpage, "canonical", "URL canonica", 10 if m["canonical"] else 0, 10, m["canonical"])
    add(onpage, "lang", "Idioma declarado", 10 if m["lang"] else 0, 10, bool(m["lang"]), m["lang"])
    wc = m["word_count"]
    wscore = 15 if wc >= 400 else round(15 * wc / 400) if wc else 0
    add(onpage, "content", "Contenido suficiente", wscore, 15, wc >= 300, f"{wc} palabras")
    ogok = bool(m["og_title"] and m["og_image"])
    add(onpage, "og", "Vista previa al compartir (Open Graph)", 10 if ogok else (5 if m["og_title"] else 0),
        10, ogok)
    it = m.get("img_total", 0); ia = m.get("img_alt", 0)
    cov = (ia / it) if it else 1.0
    add(onpage, "alt", "Imagenes con texto ALT", round(10 * cov), 10, cov >= 0.7,
        f"{ia}/{it}" if it else "sin imagenes")

    # ---------- GEO / preparacion para la IA (todo lo que hace que la IA te lea,
    #            te entienda y te recomiende por delante de la competencia) ----------
    rob = s.get("robots_info") or {}
    ai_ok = not rob.get("ai_blocked")
    add(geo, "ai_crawlers", "La IA puede leer tu sitio (rastreo IA permitido)", 15 if ai_ok else 0, 15, ai_ok,
        "Rastreo de IA permitido" if ai_ok else ("Bloqueas bots de IA: " + ", ".join(rob.get("ai_blocked", [])[:4])))
    schema_set = set(m["schema_types"])
    valuable = schema_set & {"faqpage", "qapage", "organization", "localbusiness", "professionalservice",
                             "product", "article", "howto", "service", "review"}
    schema_pts = 18 if valuable else (9 if schema_set else 0)
    add(geo, "schema", "Datos estructurados utiles (schema)", schema_pts, 18, bool(valuable),
        ", ".join(m["schema_raw_types"][:4]) or "sin datos estructurados")
    geo_entity = bool(schema_set & {"organization", "localbusiness", "professionalservice"}) or m["has_sameas"]
    add(geo, "entity", "Tu marca como entidad reconocible", 14 if geo_entity else 0, 14, geo_entity,
        "Organization/sameAs presentes" if geo_entity else "sin ficha de entidad ni sameAs")
    add(geo, "faq", "Contenido en preguntas y respuestas (FAQ)", 12 if m.get("has_faq") else 0, 12, m.get("has_faq"),
        "FAQ / Q&A presente" if m.get("has_faq") else "sin FAQ ni preguntas frecuentes")
    wc = m.get("word_count", 0)
    content_pts = 12 if wc >= 700 else (round(12 * wc / 700) if wc else 0)
    add(geo, "content_ia", "Contenido suficiente para citar", content_pts, 12, wc >= 500, f"{wc} palabras")
    heading_ok = m["h1_count"] == 1 and m["h2_count"] >= 3
    add(geo, "headings", "Estructura de titulares clara", 9 if heading_ok else (5 if (m["h1_count"] >= 1 and m["h2_count"] >= 1) else 0),
        9, heading_ok, f'{m["h1_count"]} H1 / {m["h2_count"]} H2')
    add(geo, "contact", "Ficha de contacto (nombre, telefono, direccion)", 6 if m.get("has_contact") else 0, 6, m.get("has_contact"),
        "Datos de contacto visibles" if m.get("has_contact") else "sin telefono/direccion clara")
    _lq = s.get("llms_quality", "none" if not s["llms_txt"] else "thin")
    _lq_pts = 6 if _lq == "good" else (3 if _lq == "thin" else 0)
    _lq_det = ("presente y bien estructurado" if _lq == "good"
               else ("presente pero pobre: amplíalo con tus servicios y páginas clave" if _lq == "thin"
                     else "no existe: guía a los buscadores con IA sobre tu web"))
    add(geo, "llms", "Guia para buscadores con IA (llms.txt)", _lq_pts, 6, _lq == "good", _lq_det)
    dlen = len(m["description"])
    add(geo, "desc_ia", "Resumen que la IA puede citar", 5 if 70 <= dlen <= 165 else (2 if dlen else 0),
        5, bool(m["description"]), f"{dlen} car.")
    add(geo, "title_ia", "Titulo descriptivo", 3 if m["title"] else 0, 3, bool(m["title"]))

    def cat_score(checks):
        earned = sum(c.earned for c in checks)
        possible = sum(c.possible for c in checks)
        return round(100 * earned / possible) if possible else 0

    # PSI opcional: mezcla en tecnico si esta disponible
    psi_note = None
    t_score = cat_score(tecnico)
    if res.psi and res.psi.get("performance") is not None:
        t_score = round(0.6 * t_score + 0.4 * res.psi["performance"])
        psi_note = res.psi

    cats = {
        "tecnico": {"label": "Salud tecnica", "score": t_score,
                    "checks": [c.__dict__ for c in tecnico]},
        "onpage": {"label": "SEO on-page (Google)", "score": cat_score(onpage),
                   "checks": [c.__dict__ for c in onpage]},
        "geo": {"label": "Preparacion para la IA (GEO)", "score": cat_score(geo),
                "checks": [c.__dict__ for c in geo]},
    }
    res.categories = cats

    # Velocidad y seguridad TAMBIEN cuentan en el score (si no, dos webs muy
    # distintas dan lo mismo). La velocidad real (psi_full) se adjunta despues,
    # asi que main.py llama a finalize_score() para recomputar con ella.
    pf = getattr(res, "psi_full", None) or ({"mobile": res.psi} if res.psi else {})
    overall, grade = _overall_score(cats, pf, s.get("security") or {})
    res.score = overall
    res.grade = grade

    # ---------- Hallazgos en lenguaje de cliente ----------
    if m.get("robots_noindex"):
        improve.insert(0, {"title": L("Tu web se está bloqueando a sí misma (noindex)",
                                      "Your site is blocking itself (noindex)"),
                           "detail": L("La etiqueta meta robots dice 'noindex': le pides a Google y a la IA que NO "
                                       "te muestren. Es lo más grave que puede tener una web; hay que quitarlo ya.",
                                       "The meta robots tag says 'noindex': you're telling Google and AI NOT to show "
                                       "you. It's the worst thing a site can have; remove it right away."),
                           "severity": "alto"})
    if s["https"]:
        good.append(L("Tu web usa conexion segura (HTTPS).", "Your site uses a secure connection (HTTPS)."))
    else:
        improve.append({"title": L("Falta conexion segura (HTTPS)", "No secure connection (HTTPS)"),
                        "detail": L("Los navegadores y Google penalizan las webs sin candado de seguridad.",
                                    "Browsers and Google penalize sites without the security padlock."),
                        "severity": "alto"})
    if s["sitemap"]:
        good.append(L("Tienes mapa del sitio, Google sabe que paginas rastrear.",
                      "You have a sitemap, so Google knows which pages to crawl."))
    else:
        improve.append({"title": L("No encontramos el mapa del sitio", "We couldn't find your sitemap"),
                        "detail": L("Sin sitemap, Google y la IA tardan mas en descubrir tus paginas.",
                                    "Without a sitemap, Google and AI take longer to discover your pages."),
                        "severity": "medio"})
    if s["links_checked"] and s["links_broken"] > 0:
        ex = ", ".join(e["url"] for e in s["broken_examples"][:2])
        improve.append({"title": L(f'Encontramos {s["links_broken"]} enlace(s) roto(s) en la muestra',
                                   f'We found {s["links_broken"]} broken link(s) in the sample'),
                        "detail": L(f"Paginas que ya no existen restan confianza. Ejemplo: {ex}",
                                    f"Pages that no longer exist erode trust. Example: {ex}"),
                        "severity": "alto" if s["broken_ratio"] > 0.2 else "medio"})
    elif s["links_checked"]:
        good.append(L("En nuestra muestra no encontramos enlaces rotos.", "No broken links found in our sample."))

    # ---------- Analitica / medicion ----------
    an = s.get("analytics") or {}
    if an.get("duplicated"):
        improve.append({"title": L("Tu analitica esta duplicada", "Your analytics is duplicated"),
                        "detail": L("Detectamos medicion repetida (" + "; ".join(an.get("dup_notes", [])) +
                                    "). Eso infla visitas y conversiones y ensucia tus decisiones. Hay que dejar una sola.",
                                    "We detected repeated tracking (" + "; ".join(an.get("dup_notes", [])) +
                                    "). It inflates visits and conversions and muddies your decisions. Keep only one."),
                        "severity": "medio"})
    elif not an.get("has_any"):
        improve.append({"title": L("No detectamos analitica web", "No web analytics detected"),
                        "detail": L("Sin Google Analytics 4 ni Tag Manager no sabes que paginas te traen clientes "
                                    "ni de donde llegan. Es lo primero para poder mejorar con datos.",
                                    "Without Google Analytics 4 or Tag Manager you don't know which pages bring "
                                    "customers or where they come from. It's the first step to improve with data."),
                        "severity": "medio"})
    else:
        good.append(L("Tienes analitica instalada (" + ", ".join(an.get("tools", [])[:3]) + ").",
                      "You have analytics installed (" + ", ".join(an.get("tools", [])[:3]) + ")."))

    # ---------- Seguridad / tecnologia ----------
    sec = s.get("security") or {}
    if sec.get("exposed"):
        improve.insert(0, {"title": L(f"Seguridad: {len(sec['exposed'])} archivo(s) sensible(s) expuesto(s)",
                                      f"Security: {len(sec['exposed'])} sensitive file(s) exposed"),
                           "detail": L("; ".join(e["what"] for e in sec["exposed"][:3]) +
                                       ". Hay que bloquear el acceso cuanto antes.",
                                       "; ".join(e["what"] for e in sec["exposed"][:3]) +
                                       ". Block access as soon as possible."),
                           "severity": "alto"})
    if sec.get("headers_missing"):
        improve.append({"title": L("Faltan cabeceras de seguridad", "Missing security headers"),
                        "detail": L("No estan: " + ", ".join(sec["headers_missing"][:4]) +
                                    ". Protegen frente a clickjacking, sniffing y robo de sesion.",
                                    "Missing: " + ", ".join(sec["headers_missing"][:4]) +
                                    ". They protect against clickjacking, sniffing and session theft."),
                        "severity": "medio" if len(sec["headers_missing"]) >= 4 else "bajo"})
    if sec.get("leaks"):
        improve.append({"title": L("El servidor revela su version/tecnologia", "The server reveals its version/tech"),
                        "detail": L(", ".join(sec["leaks"][:3]) + ". Ocultarlo dificulta ataques dirigidos.",
                                    ", ".join(sec["leaks"][:3]) + ". Hiding it makes targeted attacks harder."),
                        "severity": "bajo"})
    if sec and not sec.get("exposed") and sec.get("score", 0) >= 75:
        good.append(L("Seguridad basica correcta: sin archivos sensibles expuestos.",
                      "Basic security is fine: no sensitive files exposed."))
    if s.get("llms_txt") and s.get("llms_len", 0) < 200:
        improve.append({"title": L("Tu guia para la IA (llms.txt) es muy corta", "Your AI guide (llms.txt) is too short"),
                        "detail": L("Existe pero apenas tiene contenido; conviene ampliarla con tus servicios y paginas clave.",
                                    "It exists but has almost no content; expand it with your services and key pages."),
                        "severity": "bajo"})

    if not m["description"]:
        improve.append({"title": L("Falta la descripcion de la pagina", "Missing meta description"),
                        "detail": L("Es el resumen que Google muestra en resultados y que la IA usa para citarte.",
                                    "It's the summary Google shows in results and that AI uses to cite you."),
                        "severity": "medio"})
    if m["h1_count"] != 1:
        improve.append({"title": L("El titular principal no esta bien definido", "The main heading (H1) isn't well defined"),
                        "detail": L(f'Detectamos {m["h1_count"]} titulares H1. Lo ideal es uno claro por pagina.',
                                    f'We found {m["h1_count"]} H1 headings. The ideal is one clear H1 per page.'),
                        "severity": "bajo"})

    if not res.categories["geo"] or cats["geo"]["score"] < 60:
        bits_es, bits_en = [], []
        if not m["schema_types"]:
            bits_es.append("sin datos estructurados"); bits_en.append("no structured data")
        if not s["llms_txt"]:
            bits_es.append("sin guia para IA (llms.txt)"); bits_en.append("no AI guide (llms.txt)")
        if not m["has_sameas"]:
            bits_es.append("marca poco definida como entidad"); bits_en.append("brand weakly defined as an entity")
        improve.append({"title": L("La IA aun no te entiende bien", "AI doesn't understand you well yet"),
                        "detail": L("Preparacion para buscadores con IA mejorable: " +
                                    (", ".join(bits_es) if bits_es else "faltan senales GEO") + ".",
                                    "AI readiness needs work: " +
                                    (", ".join(bits_en) if bits_en else "GEO signals missing") + "."),
                        "severity": "alto"})
    else:
        good.append("Tu web tiene buenas senales para los buscadores con IA.")

    if m["schema_types"]:
        good.append("Usas datos estructurados: ayudan a Google y a la IA a entenderte.")

    res.categories["_psi"] = psi_note


def result_to_dict(res: Result) -> dict:
    d = res.__dict__.copy()
    d.pop("html", None)
    return d


def _grade(overall: int) -> str:
    return ("A" if overall >= 85 else "B" if overall >= 70 else
            "C" if overall >= 55 else "D" if overall >= 40 else "E")


def apply_analytics(data: dict, detected: dict | None) -> dict:
    """Reconcilia la analitica con lo detectado por el navegador real (mas fiable
    que el HTML estatico) y reescribe el hallazgo correspondiente."""
    if not detected:
        return data
    sig = data.setdefault("signals", {})
    static = sig.get("analytics") or {}
    # el render manda si detecto algo; si no, conserva lo estatico
    merged = detected if detected.get("has_any") else (static if static.get("has_any") else detected)
    # une herramientas de ambas fuentes
    tools = list(dict.fromkeys((merged.get("tools") or []) + (static.get("tools") or [])))
    merged = {**merged, "tools": tools, "has_any": bool(tools) or merged.get("has_any")}
    sig["analytics"] = merged

    imp = data.setdefault("findings_improve", [])
    good = data.setdefault("findings_good", [])
    imp[:] = [f for f in imp if not (isinstance(f, dict) and
              str(f.get("title", "")).startswith(("Tu analitica esta duplicada", "No detectamos analitica")))]
    good[:] = [g for g in good if not (isinstance(g, str) and g.startswith("Tienes analitica instalada"))]

    if merged.get("duplicated"):
        imp.insert(0, {"title": "Tu analitica esta duplicada",
                       "detail": "Detectamos medicion repetida (" + "; ".join(merged.get("dup_notes", [])) +
                                 "). Eso infla visitas y conversiones y ensucia tus decisiones. Hay que dejar una sola.",
                       "severity": "medio"})
    elif not merged.get("has_any"):
        imp.append({"title": "No detectamos analitica web",
                    "detail": "Sin Google Analytics 4 ni Tag Manager no sabes que paginas te traen clientes "
                              "ni de donde llegan. Es lo primero para poder mejorar con datos.",
                    "severity": "medio"})
    else:
        good.insert(0, "Tienes analitica instalada (" + ", ".join(tools[:3]) + ").")
    return data


def apply_ai_to_result(data: dict, ai: dict | None) -> dict:
    """Integra la consulta real a la IA en un result-dict ya calculado (se usa en
    segundo plano tras el resultado rapido). Ajusta la nota GEO, la global y los
    hallazgos, y adjunta geo_ai."""
    data["geo_ai"] = ai
    if not (ai and ai.get("available") and not ai.get("error")):
        return data

    # v2: el país/zona ya vienen MEDIDOS (crawl + Places). La IA no los toca.

    # El GEO se queda como HEURISTICO puro (preparacion de la web para la IA).
    # El reconocimiento real de la IA (ai_score) es su PROPIA dimension y entra en
    # el score global en finalize_score(). No se mezclan para no confundir.
    good = data.setdefault("findings_good", [])
    improve = data.setdefault("findings_improve", [])
    if ai.get("knows_brand"):
        good.insert(0, L("Cuando preguntan por tu marca a la IA, sabe quien eres y te describe bien.",
                         "When people ask the AI about your brand, it knows who you are and describes you well."))
    else:
        improve.insert(0, {
            "title": L("La IA no sabe quien eres", "AI doesn't know who you are"),
            "detail": L("Le preguntamos a la IA por tu empresa y no tiene informacion fiable de ti. "
                        "Cada vez mas clientes preguntan a la IA antes de decidir, y hoy no te encuentran.",
                        "We asked the AI about your company and it has no reliable info about you. More and more "
                        "customers ask AI before deciding, and today they don't find you."),
            "severity": "alto"})
    if ai.get("recommended") is True:
        good.insert(0, L("Cuando piden tu servicio a la IA, te incluye entre las opciones recomendadas.",
                         "When people ask the AI for your service, it includes you among the recommended options."))
    elif ai.get("recommended") is False:
        improve.insert(0, {
            "title": L("Cuando piden tu servicio, la IA recomienda a otros",
                       "When people ask for your service, AI recommends others"),
            "detail": L("Al pedirle recomendaciones de tu sector, la IA nombra a otras empresas antes que a la tuya: "
                        "pierdes a los clientes que aun no te conocen.",
                        "When asked for recommendations in your sector, the AI names other companies before yours: "
                        "you lose the customers who don't know you yet."),
            "severity": "alto"})
    # Ficha de Google Business (investigado con busqueda real)
    if ai.get("gbp") is False:
        improve.append({
            "title": L("No encontramos tu ficha de Google Business", "We couldn't find your Google Business profile"),
            "detail": L("Buscamos tu negocio en Google Maps (Places API) y no aparece una ficha con tu web. Crearla y verificarla es "
                        "clave para salir en el mapa, en las busquedas locales y en la IA local.",
                        "We searched Google/Maps and found no active listing. Creating and verifying it is key to "
                        "appear on the map, in local searches and in local AI."),
            "severity": "medio"})
    elif ai.get("gbp") is True:
        good.append(L("Tienes ficha de Google Business activa.", "You have an active Google Business profile."))
    return data
